"""Виджет терминала: рисует TerminalScreen, переводит клавиши в байты для программы, выделение и копирование.

Клавиши — как в xterm/Konsole: Ctrl+C прерывает программу, Ctrl+Shift+C / Ctrl+Shift+V — копировать / вставить,
Shift+PgUp / Shift+PgDn и колесо — история. Остальные сочетания окна (F5, Ctrl+S…) терминал не перехватывает,
пока в нём не печатают, — только когда он в фокусе (как встроенный терминал VS)."""
from PyQt6.QtCore import QPoint, QRect, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter
from PyQt6.QtWidgets import QAbstractScrollArea

from ..theme import C
from .screen import TerminalScreen

# 16 основных цветов (тёмная схема в духе Konsole «Breeze»)
ANSI = {"black": "#232627", "red": "#ED1515", "green": "#11D116", "brown": "#F67400", "yellow": "#F67400",
        "blue": "#1D99F3", "magenta": "#9B59B6", "cyan": "#1ABC9C", "white": "#FCFCFC",
        "brightblack": "#7F8C8D", "brightred": "#C0392B", "brightgreen": "#1CDC9A", "brightbrown": "#FDBC4B",
        "brightyellow": "#FDBC4B", "brightblue": "#3DAEE9", "brightmagenta": "#8E44AD", "brightcyan": "#16A085",
        "brightwhite": "#FFFFFF"}

KEYS = {Qt.Key.Key_Return: b"\r", Qt.Key.Key_Enter: b"\r", Qt.Key.Key_Backspace: b"\x7f", Qt.Key.Key_Tab: b"\t",
        Qt.Key.Key_Escape: b"\x1b", Qt.Key.Key_Insert: b"\x1b[2~", Qt.Key.Key_Delete: b"\x1b[3~",
        Qt.Key.Key_PageUp: b"\x1b[5~", Qt.Key.Key_PageDown: b"\x1b[6~",
        Qt.Key.Key_F1: b"\x1bOP", Qt.Key.Key_F2: b"\x1bOQ", Qt.Key.Key_F3: b"\x1bOR", Qt.Key.Key_F4: b"\x1bOS",
        Qt.Key.Key_F5: b"\x1b[15~", Qt.Key.Key_F6: b"\x1b[17~", Qt.Key.Key_F7: b"\x1b[18~",
        Qt.Key.Key_F8: b"\x1b[19~", Qt.Key.Key_F9: b"\x1b[20~", Qt.Key.Key_F10: b"\x1b[21~",
        Qt.Key.Key_F11: b"\x1b[23~", Qt.Key.Key_F12: b"\x1b[24~"}
CURSOR_KEYS = {Qt.Key.Key_Up: "A", Qt.Key.Key_Down: "B", Qt.Key.Key_Right: "C", Qt.Key.Key_Left: "D",
               Qt.Key.Key_Home: "H", Qt.Key.Key_End: "F"}


def key_bytes(key, modifiers, text, app_cursor=False):
    """Нажатие → байты для программы (или None — не наше)."""
    ctrl = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
    alt = bool(modifiers & Qt.KeyboardModifier.AltModifier)
    shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
    if key in CURSOR_KEYS:
        mod = 1 + shift + 2 * alt + 4 * ctrl
        if mod > 1:
            return ("\x1b[1;%d%s" % (mod, CURSOR_KEYS[key])).encode()
        return ("\x1bO" if app_cursor else "\x1b[").encode() + CURSOR_KEYS[key].encode()
    if key == Qt.Key.Key_Backtab:
        return b"\x1b[Z"
    if key in KEYS:
        data = KEYS[key]
        return b"\x1b" + data if alt and len(data) == 1 else data
    if ctrl and Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
        data = bytes([key - Qt.Key.Key_A + 1])
        return b"\x1b" + data if alt else data
    if ctrl and key in (Qt.Key.Key_BracketLeft, Qt.Key.Key_Backslash, Qt.Key.Key_BracketRight, Qt.Key.Key_Space):
        return {Qt.Key.Key_BracketLeft: b"\x1b", Qt.Key.Key_Backslash: b"\x1c",
                Qt.Key.Key_BracketRight: b"\x1d", Qt.Key.Key_Space: b"\x00"}[key]
    if text:
        data = text.encode("utf-8")
        return b"\x1b" + data if alt else data
    return None


def color(value, default):
    if value in (None, "default"):
        return QColor(default)
    if value in ANSI:
        return QColor(ANSI[value])
    if len(value) == 6:
        try:
            int(value, 16)
            return QColor("#" + value)
        except ValueError:
            pass
    return QColor(default)


class TerminalView(QAbstractScrollArea):
    """Один терминал. send(bytes) — всё, что ввели; resized(cols, rows)."""
    send = pyqtSignal(bytes)
    resized = pyqtSignal(int, int)
    title_changed = pyqtSignal(str)

    def __init__(self, font, parent=None):
        super().__init__(parent)
        self.term = TerminalScreen(80, 24, respond=lambda data: self.send.emit(data.encode("utf-8")))
        self.setFont(QFont(font))
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)
        self.viewport().setCursor(Qt.CursorShape.IBeamCursor)
        self._follow = True                 # прилипнуть к низу, пока пользователь не прокрутил вверх
        self._selection = None              # ((строка, столбец), (строка, столбец)) в общем счёте строк
        self._anchor = None
        self._title = ""
        self._repaint = QTimer(self, singleShot=True, interval=16, timeout=self._refresh)
        self.verticalScrollBar().valueChanged.connect(lambda v: (self.viewport().update(),
                                                                setattr(self, "_follow", self._at_bottom())))

    # --- размеры
    def cell(self):
        fm = QFontMetrics(self.font())
        return max(1, fm.horizontalAdvance("M")), max(1, fm.height())

    def set_code_font(self, font):
        self.setFont(QFont(font))
        self._fit()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()

    def _fit(self):
        cw, ch = self.cell()
        cols = max(10, (self.viewport().width() - 4) // cw)
        rows = max(3, self.viewport().height() // ch)
        if (cols, rows) != (self.term.cols, self.term.rows):
            self.term.resize(cols, rows)
            self.resized.emit(cols, rows)
            self._refresh()

    # --- данные
    def feed(self, data):
        self.term.feed(data)
        if self.term.title != self._title:
            self._title = self.term.title
            self.title_changed.emit(self._title)
        if not self._repaint.isActive():
            self._repaint.start()

    def _at_bottom(self):
        bar = self.verticalScrollBar()
        return bar.value() >= bar.maximum()

    def _refresh(self):
        bar = self.verticalScrollBar()
        bar.blockSignals(True)
        bar.setRange(0, self.term.history_size())
        bar.setPageStep(self.term.rows)
        if self._follow:
            bar.setValue(bar.maximum())
        bar.blockSignals(False)
        self.viewport().update()

    def scroll_to_bottom(self):
        self._follow = True
        self._refresh()

    # --- рисование
    def paintEvent(self, _e):
        p = QPainter(self.viewport())
        background, foreground = QColor(C["editor"]), QColor(C["text"])
        p.fillRect(self.viewport().rect(), background)
        cw, ch = self.cell()
        fm = QFontMetrics(self.font())
        first = self.verticalScrollBar().value()
        cx, cy, cursor_visible = self.term.cursor()
        base = QFont(self.font())
        for row in range(self.term.rows + 1):
            index = first + row
            if index >= self.term.total_lines():
                break
            line = self.term.line(index)
            y = row * ch
            for x in sorted(line.keys()) if hasattr(line, "keys") else range(self.term.cols):
                if x >= self.term.cols:
                    continue
                char = line[x]
                fg, bg = color(char.fg, foreground.name()), color(char.bg, background.name())
                if char.reverse:
                    fg, bg = bg, fg
                if self._selected(index, x):
                    bg = QColor(C["select"])
                if bg != background:
                    p.fillRect(QRect(2 + x * cw, y, cw, ch), bg)
                if char.data.strip():
                    f = QFont(base)
                    f.setBold(char.bold)
                    f.setItalic(char.italics)
                    f.setUnderline(char.underscore)
                    f.setStrikeOut(char.strikethrough)
                    p.setFont(f)
                    p.setPen(fg)
                    p.drawText(2 + x * cw, y + fm.ascent(), char.data)
            if index == cy and cursor_visible and self.hasFocus():
                p.fillRect(QRect(2 + cx * cw, y, cw, ch), QColor(C["text"]))
                char = self.term.line(index)[cx]
                if char.data.strip():
                    p.setFont(base)
                    p.setPen(background)
                    p.drawText(2 + cx * cw, y + fm.ascent(), char.data)
            elif index == cy and cursor_visible:
                p.setPen(QColor(C["text"]))
                p.drawRect(QRect(2 + cx * cw, y, cw - 1, ch - 1))
        p.end()

    # --- выделение
    def _cell_at(self, pos):
        cw, ch = self.cell()
        col = max(0, min(self.term.cols - 1, (pos.x() - 2) // cw))
        row = self.verticalScrollBar().value() + max(0, pos.y() // ch)
        return min(row, self.term.total_lines() - 1), col

    def _selected(self, row, col):
        if not self._selection:
            return False
        (r1, c1), (r2, c2) = sorted(self._selection)
        if not (r1 <= row <= r2):
            return False
        return (row != r1 or col >= c1) and (row != r2 or col <= c2)

    def selected_text(self):
        if not self._selection:
            return ""
        (r1, c1), (r2, c2) = sorted(self._selection)
        lines = [self.term.text(r, c1 if r == r1 else 0, (c2 + 1) if r == r2 else None) for r in range(r1, r2 + 1)]
        return "\n".join(lines)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._anchor = self._cell_at(e.position().toPoint())
            self._selection = None
            self.viewport().update()
        elif e.button() == Qt.MouseButton.MiddleButton:          # как в X11-терминалах
            self.paste(QGuiApplication.clipboard().text(QGuiApplication.clipboard().Mode.Selection))
        self.setFocus()

    def mouseMoveEvent(self, e):
        if self._anchor is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self._selection = (self._anchor, self._cell_at(e.position().toPoint()))
            self.viewport().update()

    def mouseReleaseEvent(self, e):
        text = self.selected_text()
        if text:
            QGuiApplication.clipboard().setText(text, QGuiApplication.clipboard().Mode.Selection)

    def mouseDoubleClickEvent(self, e):
        row, col = self._cell_at(e.position().toPoint())
        text = self.term.text(row) + " "
        a = b = col
        while a > 0 and not text[a - 1].isspace():
            a -= 1
        while b < len(text) - 1 and not text[b + 1].isspace():
            b += 1
        self._selection = ((row, a), (row, b))
        self.viewport().update()

    def copy(self):
        text = self.selected_text()
        if text:
            QGuiApplication.clipboard().setText(text)

    def paste(self, text=None):
        text = QGuiApplication.clipboard().text() if text is None else text
        if not text:
            return
        data = text.replace("\r\n", "\r").replace("\n", "\r").encode("utf-8")
        if self.term.bracketed_paste():
            data = b"\x1b[200~" + data + b"\x1b[201~"
        self.scroll_to_bottom()
        self.send.emit(data)

    # --- клавиатура
    def event(self, e):
        # Tab и Shift+Tab — программе (дополнение в оболочке), а не переход фокуса между виджетами
        if e.type() == e.Type.KeyPress and e.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            self.keyPressEvent(e)
            return True
        if e.type() == e.Type.ShortcutOverride:
            mods = e.modifiers()
            ctrl_shift = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
            if (mods & ctrl_shift) == ctrl_shift and e.key() in (Qt.Key.Key_C, Qt.Key.Key_V):
                e.accept()
                return True
            if mods & Qt.KeyboardModifier.ControlModifier and not mods & Qt.KeyboardModifier.ShiftModifier \
                    and Qt.Key.Key_A <= e.key() <= Qt.Key.Key_Z:
                e.accept()                  # Ctrl+C, Ctrl+D, Ctrl+R… — программе, а не командам окна
                return True
        return super().event(e)

    def keyPressEvent(self, e):
        mods, key = e.modifiers(), e.key()
        ctrl_shift = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
        if (mods & ctrl_shift) == ctrl_shift and key == Qt.Key.Key_C:
            self.copy()
            return
        if (mods & ctrl_shift) == ctrl_shift and key == Qt.Key.Key_V:
            self.paste()
            return
        if mods & Qt.KeyboardModifier.ShiftModifier and key in (Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
            bar = self.verticalScrollBar()
            bar.setValue(bar.value() + (-1 if key == Qt.Key.Key_PageUp else 1) * self.term.rows)
            return
        data = key_bytes(key, mods, e.text(), self.term.app_cursor())
        if data is None:
            super().keyPressEvent(e)
            return
        self._selection = None
        self.scroll_to_bottom()
        self.send.emit(data)

    def inputMethodEvent(self, e):
        if e.commitString():
            self.send.emit(e.commitString().encode("utf-8"))
        e.accept()

    def focusInEvent(self, e):
        super().focusInEvent(e)
        self.viewport().update()

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self.viewport().update()
