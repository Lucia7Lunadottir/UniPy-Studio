"""Редактор кода: номера строк, подсветка, подсказки, подсказки параметров, ошибки."""
import ast
import os
import re

from PyQt6.QtCore import Qt, QEvent, QRect, QSize, QTimer, pyqtSignal, QPoint, QRectF
from PyQt6.QtGui import (QColor, QCursor, QFont, QPainter, QTextFormat, QTextCursor, QStandardItemModel,
                         QStandardItem, QKeySequence, QTextCharFormat, QPalette, QFontMetrics)
from PyQt6.QtWidgets import (QPlainTextEdit, QWidget, QTextEdit, QCompleter, QLabel,
                             QStyledItemDelegate, QToolTip, QStyle, QApplication, QMessageBox)

from .highlighter import Highlighter, PygmentsHighlighter
from . import languages
from .intellisense import CSharpProvider, signature_html, hover_html, word_items
from . import theme
from .theme import C, kind_icon


CSHARP = CSharpProvider()
DEFAULT_HINT_PT = 11    # размер подсказок (список, параметры, справка); свой масштаб, отдельно от кода
ROLE_DETAIL = Qt.ItemDataRole.UserRole + 1
ROLE_DOC = Qt.ItemDataRole.UserRole + 2


ENCODING_NAME = {"utf-8": "UTF-8", "utf-8-sig": "UTF-8 с BOM", "utf-16": "UTF-16",
                 "cp1251": "Windows-1251", "latin-1": "Latin-1"}


def decode_source(raw):
    """Кодировка файла: BOM, затем UTF-8, затем Windows-1251 — так файлы с русскими комментариями
    из Visual Studio на русской Windows открываются без «�» и не портятся при сохранении."""
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig", raw[3:].decode("utf-8", errors="replace")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16", raw.decode("utf-16", errors="replace")
    for enc in ("utf-8", "cp1251"):
        try:
            return enc, raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return "latin-1", raw.decode("latin-1")


def lang_for(path):
    return languages.for_path(path).id


class LineNumberArea(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self):
        return QSize(self.editor.gutter_width(), 0)

    def paintEvent(self, event):
        self.editor.paint_gutter(event)


class CompletionDelegate(QStyledItemDelegate):
    """Строка списка подсказок: значок, имя, справа — что это."""

    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.editor = editor

    @property
    def font(self):
        # как в Visual Studio: свой небольшой размер, не растёт вместе с масштабом кода
        f = QFont(self.editor.font())
        f.setPointSizeF(self.editor.hint_pt())
        return f

    def detail_font(self):
        f = QFont(QApplication.font())
        f.setPointSizeF(self.editor.hint_pt() - 0.5)
        return f

    def paint(self, painter, option, index):
        painter.save()
        r = option.rect
        selected = option.state & QStyle.StateFlag.State_Selected
        painter.fillRect(r, QColor(C["completion_sel"] if selected else C["menu"]))
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        if icon:
            side = max(12, min(r.height() - 2, 20))
            icon.paint(painter, QRect(r.left() + 4, r.top() + (r.height() - side) // 2, side, side))
        painter.setFont(self.font)
        name = index.data(Qt.ItemDataRole.DisplayRole) or ""
        fm = QFontMetrics(self.font)
        painter.setPen(QColor(C["bright"] if selected else C["text"]))
        name_rect = QRect(r.left() + side_pad(r), r.top(), r.width() - side_pad(r) - 4, r.height())
        painter.drawText(name_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, name)
        detail = index.data(ROLE_DETAIL) or ""
        if detail:
            name_w = fm.horizontalAdvance(name) + 40
            avail = r.width() - name_w - 8
            if avail > 40:
                painter.setPen(QColor(C["hint"]))
                painter.setFont(self.detail_font())
                fm = QFontMetrics(self.detail_font())
                txt = fm.elidedText(detail, Qt.TextElideMode.ElideRight, avail)
                painter.drawText(QRect(r.right() - avail - 6, r.top(), avail, r.height()),
                                 Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, txt)
        painter.restore()

    def sizeHint(self, option, index):
        return QSize(200, max(16, QFontMetrics(self.font).height() + 3))


def side_pad(rect):
    """Отступ текста после значка в строке списка подсказок."""
    return max(12, min(rect.height() - 2, 20)) + 8


class CallTip(QLabel):
    """Подсказка параметров функции над строкой."""

    def __init__(self, parent, pt=DEFAULT_HINT_PT):
        super().__init__(parent, Qt.WindowType.ToolTip)
        self.setTextFormat(Qt.TextFormat.RichText)
        self.set_size(pt)
        self.hide()

    def set_size(self, pt):
        theme.style(self, "QLabel { background:@panel@; color:@text@; border:1px solid @tooltip_border@;"
                          " padding:3px 6px; font-size:%gpt; }" % pt)
        if self.isVisible():
            self.adjustSize()


class CodeEditor(QPlainTextEdit):
    cursor_moved = pyqtSignal(int, int)
    diagnostics_changed = pyqtSignal()
    goto_requested = pyqtSignal(str, int)
    busy_changed = pyqtSignal(str)     # текст «идёт работа» для строки состояния; "" — закончили
    references_found = pyqtSignal(str, object)   # слово, [{"path", "line", "col"}]
    rename_ready = pyqtSignal(str, object)       # новое имя, {"edits": …} | {"files": …} | {"error": …}

    def __init__(self, path, services, font):
        super().__init__()
        self.path = path
        self.services = services
        self.language = languages.for_path(path)      # всё о языке файла — в реестре languages.py
        self.lang = self.language.id
        self.diagnostics = []          # (строка, столбец, сообщение, "error"/"warning")
        self.lsp_diagnostics = None    # ошибки от языкового сервера (Roslyn); None — сервера нет
        self.encoding = "utf-8"
        self.newline = "\n"
        self._link = None              # слово под мышью при зажатом Ctrl — подчёркнуто, как ссылка в VS
        self.find_text = ""
        self._req = 0
        self._sig_req = 0
        self.set_code_font(font)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setCursorWidth(2)
        self._apply_palette()
        self.highlighter = self._make_highlighter()
        self.gutter = LineNumberArea(self)
        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._update_gutter)
        self.cursorPositionChanged.connect(self._on_cursor)
        self._update_gutter_width()

        # подсказки
        self.model = QStandardItemModel(self)
        self.completer = QCompleter(self.model, self)
        self.completer.setWidget(self)
        self.completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setModelSorting(QCompleter.ModelSorting.UnsortedModel)
        self.completer.setMaxVisibleItems(10)
        self.completer.setWrapAround(False)
        popup = self.completer.popup()
        self._delegate = CompletionDelegate(self, popup)
        popup.setItemDelegate(self._delegate)
        theme.style(popup, "QListView { background:@menu@; border:1px solid @tooltip_border@; }")
        popup.installEventFilter(self)
        popup.viewport().installEventFilter(self)
        self.completer.activated[str].connect(self.insert_completion)
        self.completer.highlighted[str].connect(self._on_highlighted)
        self.calltip = CallTip(self, self.hint_pt())
        self.doc_tip = CallTip(self, self.hint_pt())

        self._diag_timer = QTimer(self, singleShot=True, interval=500, timeout=self.run_diagnostics)
        self._hover_timer = QTimer(self, singleShot=True, interval=550, timeout=self._do_hover)
        self._hover_pos = None
        self.textChanged.connect(self._diag_timer.start)
        self.setMouseTracking(True)
        self._on_cursor()

    # ------------------------------------------------------------ файл
    def load(self):
        with open(self.path, "rb") as f:
            raw = f.read()
        self.encoding, text = decode_source(raw)
        self.newline = "\r\n" if "\r\n" in text else "\n"
        self.setPlainText(text.replace("\r\n", "\n").replace("\r", "\n"))
        self.document().setModified(False)
        self.run_diagnostics()

    def save(self):
        """Сохраняет в той же кодировке и с теми же переводами строк, что были у файла (как Visual Studio)."""
        text = self.toPlainText()
        if self.newline != "\n":
            text = text.replace("\n", self.newline)
        try:
            data = text.encode(self.encoding)
        except UnicodeEncodeError as e:
            bad = text[e.start:e.end]
            r = QMessageBox.question(
                self, "Кодировка", "Символ «%s» нельзя сохранить в кодировке %s.\n"
                "Сохранить файл в Юникоде (UTF-8 с BOM)?" % (bad, ENCODING_NAME.get(self.encoding, self.encoding)),
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel)
            if r != QMessageBox.StandardButton.Save:
                return False
            self.encoding = "utf-8-sig"
            data = text.encode(self.encoding)
        with open(self.path, "wb") as f:
            f.write(data)
        self.document().setModified(False)
        self.run_diagnostics()
        return True

    def encoding_label(self):
        return ENCODING_NAME.get(self.encoding, self.encoding.upper()) + ("  CRLF" if self.newline == "\r\n" else "")

    # ------------------------------------------------------------ поле номеров строк
    def gutter_width(self):
        digits = max(3, len(str(self.blockCount())))
        return 22 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_gutter_width(self, *_):
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def _update_gutter(self, rect, dy):
        if dy:
            self.gutter.scroll(0, dy)
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        cr = self.contentsRect()
        self.gutter.setGeometry(QRect(cr.left(), cr.top(), self.gutter_width(), cr.height()))

    def paint_gutter(self, event):
        p = QPainter(self.gutter)
        p.fillRect(event.rect(), QColor(C["editor"]))
        block = self.firstVisibleBlock()
        num = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        current = self.textCursor().blockNumber()
        err_lines = {d[0] - 1: d[3] for d in self.diagnostics}
        w = self.gutter.width()
        h = self.fontMetrics().height()
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                p.setPen(QColor(C["gutter_current"] if num == current else C["gutter_num"]))
                p.drawText(0, top, w - 14, h, Qt.AlignmentFlag.AlignRight, str(num + 1))
                if num in err_lines:
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QColor(C["error"] if err_lines[num] == "error" else C["warning"]))
                    p.drawEllipse(QRectF(3, top + h / 2 - 3.5, 7, 7))
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            num += 1
        p.end()

    # ------------------------------------------------------------ выделения
    def _on_cursor(self):
        c = self.textCursor()
        self.cursor_moved.emit(c.blockNumber() + 1, c.positionInBlock() + 1)
        self.update_selections()
        if self.calltip.isVisible():
            self.request_signatures()

    def update_selections(self):
        sels = []
        line = QTextEdit.ExtraSelection()
        line.format.setBackground(QColor(C["current_line"]))
        line.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        line.cursor = self.textCursor()
        line.cursor.clearSelection()
        sels.append(line)
        if self.find_text:
            doc = self.document()
            c = doc.find(self.find_text, 0)
            count = 0
            while not c.isNull() and count < 2000:
                s = QTextEdit.ExtraSelection()
                s.format.setBackground(QColor(C["find_bg"]))
                s.cursor = c
                sels.append(s)
                c = doc.find(self.find_text, c)
                count += 1
        sels.extend(self._bracket_selections())
        for line_no, col, msg, sev in self.diagnostics:
            block = self.document().findBlockByNumber(line_no - 1)
            if not block.isValid():
                continue
            s = QTextEdit.ExtraSelection()
            s.format.setUnderlineStyle(QTextCharFormat.UnderlineStyle.WaveUnderline)
            s.format.setUnderlineColor(QColor(C["error"] if sev == "error" else C["warning"]))
            s.format.setToolTip(msg)
            cur = QTextCursor(block)
            text = block.text()
            start = max(0, min(len(text), (col or 1) - 1))
            if start >= len(text.rstrip()):
                start = len(text) - len(text.lstrip())
            cur.setPosition(block.position() + start)
            m = re.match(r"\w+|\S", text[start:])
            length = len(m.group(0)) if m else 1
            if length <= 1:
                length = max(1, len(text.rstrip()) - start)
            cur.setPosition(block.position() + start + length, QTextCursor.MoveMode.KeepAnchor)
            s.cursor = cur
            sels.append(s)
        if self._link is not None:
            s = QTextEdit.ExtraSelection()
            s.format.setFontUnderline(True)
            s.format.setUnderlineColor(QColor(C["link"]))
            s.format.setForeground(QColor(C["link"]))
            s.cursor = self._link
            sels.append(s)
        self.setExtraSelections(sels)

    def _bracket_selections(self):
        text = self.toPlainText()
        pos = self.textCursor().position()
        pairs = {"(": ")", "[": "]", "{": "}"}
        rev = {v: k for k, v in pairs.items()}
        for p in (pos - 1, pos):
            if 0 <= p < len(text) and (text[p] in pairs or text[p] in rev):
                ch = text[p]
                if ch in pairs:
                    step, open_, close = 1, ch, pairs[ch]
                else:
                    step, open_, close = -1, ch, rev[ch]
                depth, i = 0, p
                limit = 20000
                while 0 <= i < len(text) and limit:
                    if text[i] == open_:
                        depth += 1
                    elif text[i] == close:
                        depth -= 1
                        if depth == 0:
                            out = []
                            for q in (p, i):
                                s = QTextEdit.ExtraSelection()
                                s.format.setBackground(QColor(C["bracket_bg"]))
                                s.cursor = self.textCursor()
                                s.cursor.setPosition(q)
                                s.cursor.setPosition(q + 1, QTextCursor.MoveMode.KeepAnchor)
                                out.append(s)
                            return out
                    i += step
                    limit -= 1
                return []
        return []

    # ------------------------------------------------------------ проверка ошибок
    def run_diagnostics(self, *, sync_server=True):
        text = self.toPlainText()
        diags = []
        svc = self.services.service_for(self.language)
        if sync_server and svc is not None:
            svc.sync(self.path, text)
        if self.lang == "python":
            try:
                tree = ast.parse(text, filename=self.path)
                diags.extend(python_warnings(tree, text))
            except SyntaxError as e:
                if self.lsp_diagnostics is None:        # pyright сам сообщает о синтаксисе
                    diags.append((e.lineno or 1, e.offset or 1, "Синтаксическая ошибка: %s" % e.msg, "error"))
            except Exception:
                pass
            if self.lsp_diagnostics is not None:
                diags = sorted(set(diags) | set(self.lsp_diagnostics))
        elif self.lang == "csharp":
            diags.extend(self.lsp_diagnostics if self.lsp_diagnostics is not None else check_csharp(text))
        elif self.lang == "json":
            import json
            try:
                json.loads(text) if text.strip() else None
            except json.JSONDecodeError as e:
                diags.append((e.lineno, e.colno, "Ошибка JSON: %s" % e.msg, "error"))
        elif self.lsp_diagnostics is not None:      # языки, у которых есть только анализатор
            diags = list(self.lsp_diagnostics)
        if diags != self.diagnostics:
            self.diagnostics = diags
            self.update_selections()
            self.gutter.update()
            self.diagnostics_changed.emit()

    def set_code_font(self, font):
        """Шрифт кода. Через собственный стиль редактора: глобальный QSS (font-size: 10pt)
        иначе перекрывает setFont() при каждом показе вкладки."""
        self.setStyleSheet('CodeEditor { font-family: "%s"; font-size: %gpt; }' % (font.family(), font.pointSizeF()))
        self.setFont(font)
        self.setTabStopDistance(QFontMetrics(font).horizontalAdvance(" ") * 4)
        if hasattr(self, "gutter"):
            self._update_gutter_width()

    def _make_highlighter(self):
        """Своя подсветка для основных языков, Pygments — для остальных (если он знает язык)."""
        if self.language.builtin_highlighter:
            return Highlighter(self.document(), self.lang)
        lexer = self.language.make_lexer(self.path)
        if lexer is not None:
            return PygmentsHighlighter(self.document(), lexer)
        return Highlighter(self.document(), "plain")

    # ------------------------------------------------------------ тема
    def _apply_palette(self):
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Text, QColor(C["text"]))
        pal.setColor(QPalette.ColorRole.Base, QColor(C["editor"]))
        self.setPalette(pal)

    def changeEvent(self, e):
        # шрифт кода задаётся стилем и применяется при показе — пересчитать поле номеров строк,
        # иначе оно наезжает на первые символы строк
        if e.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._update_gutter_width()
            self.setTabStopDistance(QFontMetrics(self.font()).horizontalAdvance(" ") * 4)
        super().changeEvent(e)

    def apply_theme(self):
        """Тема сменилась: фон и текст, подсветка кода, подчёркивания ошибок, номера строк."""
        self._apply_palette()
        self.highlighter.rebuild()
        self.update_selections()
        self.gutter.update()
        self.viewport().update()

    # ------------------------------------------------------------ размер подсказок
    def hint_pt(self):
        return float(getattr(self.services, "hint_size", DEFAULT_HINT_PT))

    def apply_hint_size(self):
        pt = self.hint_pt()
        self.calltip.set_size(pt)
        self.doc_tip.set_size(pt)
        popup = self.completer.popup()
        popup.doItemsLayout()
        if popup.isVisible():
            self.show_completions_geometry()

    def _tip_html(self, html_):
        return "<div style='font-size:%gpt'>%s</div>" % (self.hint_pt(), html_)

    def eventFilter(self, obj, e):
        # Ctrl+колесо над списком подсказок меняет размер подсказок, а не кода
        if e.type() == QEvent.Type.Wheel and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.services.zoom_hints(1 if e.angleDelta().y() > 0 else -1)
            return True
        return super().eventFilter(obj, e)

    def set_lsp_diagnostics(self, diags):
        self.lsp_diagnostics = diags
        # Applying an LSP reply must not request another LSP diagnostic round.
        self.run_diagnostics(sync_server=False)

    # ------------------------------------------------------------ ввод
    def keyPressEvent(self, e):
        key, mods, text = e.key(), e.modifiers(), e.text()
        if key == Qt.Key.Key_Control:
            self._update_link(self.viewport().mapFromGlobal(QCursor.pos()), True)
        popup = self.completer.popup()
        if popup.isVisible() and key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Tab,
                                         Qt.Key.Key_Escape, Qt.Key.Key_Backtab):
            e.ignore()
            return
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        if ctrl and mods & Qt.KeyboardModifier.ShiftModifier and key == Qt.Key.Key_Space:
            self.request_signatures(force=True)
            return
        if ctrl and key == Qt.Key.Key_Space:
            self.request_completion(force=True)
            return
        if key == Qt.Key.Key_Escape:
            self.calltip.hide()
            self.doc_tip.hide()
            super().keyPressEvent(e)
            return
        if ctrl and key == Qt.Key.Key_Slash:
            self.toggle_comment()
            return
        if ctrl and key == Qt.Key.Key_D:
            self.duplicate_line()
            return
        if key == Qt.Key.Key_F12 and not mods & Qt.KeyboardModifier.ShiftModifier:
            self.go_to_definition()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not ctrl:
            self._newline()
            return
        if key == Qt.Key.Key_Tab:
            self._indent(+1)
            return
        if key == Qt.Key.Key_Backtab:
            self._indent(-1)
            return
        if key == Qt.Key.Key_Backspace and self._smart_backspace():
            return
        if text and text in "([{\"'" and self._auto_pair(text):
            pass
        elif text and text in ")]}\"'" and self._skip_closing(text):
            pass
        else:
            super().keyPressEvent(e)
            if text == "}" and self.language.braces:
                self._dedent_brace()

        # что делать с подсказками после нажатия
        if text and (text.isalnum() or text == "_"):
            if len(self.word_before_cursor()) >= 1:
                self.request_completion()
        elif text == ".":
            self.request_completion(force=True)
        elif key == Qt.Key.Key_Backspace:
            if popup.isVisible():
                if self.word_before_cursor():
                    self.request_completion()
                else:
                    popup.hide()
        elif text:
            popup.hide()
        if text in ("(", ","):
            self.request_signatures(force=True)
        elif text == ")":
            self.request_signatures()

    def word_before_cursor(self):
        c = self.textCursor()
        line = c.block().text()[:c.positionInBlock()]
        m = re.search(r"[A-Za-z_][\w]*$", line) or re.search(r"\w*$", line)
        return m.group(0)

    def _newline(self):
        c = self.textCursor()
        line = c.block().text()
        before = line[:c.positionInBlock()]
        after = line[c.positionInBlock():]
        indent = re.match(r"[ \t]*", line).group(0)
        stripped = before.rstrip()
        extra = ""
        if self.language.colon_blocks and stripped.endswith(":"):
            extra = "    "
        elif stripped.endswith(("{", "[", "(")):
            extra = "    "
        c.beginEditBlock()
        if extra and after.strip()[:1] in ("}", "]", ")") and not self.language.colon_blocks:
            c.insertText("\n" + indent + extra + "\n" + indent)
            c.movePosition(QTextCursor.MoveOperation.Up)
            c.movePosition(QTextCursor.MoveOperation.EndOfLine)
        else:
            c.insertText("\n" + indent + extra)
        c.endEditBlock()
        self.setTextCursor(c)

    def _indent(self, direction):
        c = self.textCursor()
        if not c.hasSelection():
            if direction > 0:
                c.insertText(" " * (4 - c.positionInBlock() % 4))
            else:
                self._dedent_lines(c.blockNumber(), c.blockNumber())
            return
        start = self.document().findBlock(c.selectionStart()).blockNumber()
        end = self.document().findBlock(c.selectionEnd() - 1 if c.selectionEnd() > c.selectionStart() else c.selectionEnd()).blockNumber()
        if direction > 0:
            cur = QTextCursor(self.document())
            cur.beginEditBlock()
            for n in range(start, end + 1):
                b = self.document().findBlockByNumber(n)
                cur.setPosition(b.position())
                cur.insertText("    ")
            cur.endEditBlock()
        else:
            self._dedent_lines(start, end)

    def _dedent_lines(self, start, end):
        cur = QTextCursor(self.document())
        cur.beginEditBlock()
        for n in range(start, end + 1):
            b = self.document().findBlockByNumber(n)
            spaces = len(b.text()) - len(b.text().lstrip(" "))
            k = min(4, spaces)
            if k:
                cur.setPosition(b.position())
                cur.setPosition(b.position() + k, QTextCursor.MoveMode.KeepAnchor)
                cur.removeSelectedText()
        cur.endEditBlock()

    def _smart_backspace(self):
        c = self.textCursor()
        if c.hasSelection():
            return False
        before = c.block().text()[:c.positionInBlock()]
        after = c.block().text()[c.positionInBlock():]
        if before and after and before[-1] + after[0] in ("()", "[]", "{}", '""', "''"):
            c.deleteChar()
            c.deletePreviousChar()
            return True
        if before and not before.strip(" ") and len(before) % 4 == 0 and len(before) >= 4:
            for _ in range(4):
                c.deletePreviousChar()
            return True
        return False

    def _auto_pair(self, ch):
        pairs = {"(": ")", "[": "]", "{": "}", '"': '"', "'": "'"}
        c = self.textCursor()
        after = c.block().text()[c.positionInBlock():]
        before = c.block().text()[:c.positionInBlock()]
        if ch in "\"'":
            if after[:1] == ch:
                return False
            if before[-1:].isalnum() or before.count(ch) % 2 == 1:
                return False
        if after[:1] and after[:1].isalnum():
            return False
        if c.hasSelection():
            sel = c.selectedText()
            c.insertText(ch + sel + pairs[ch])
            return True
        c.insertText(ch + pairs[ch])
        c.movePosition(QTextCursor.MoveOperation.Left)
        self.setTextCursor(c)
        return True

    def _skip_closing(self, ch):
        c = self.textCursor()
        after = c.block().text()[c.positionInBlock():]
        if after[:1] == ch:
            c.movePosition(QTextCursor.MoveOperation.Right)
            self.setTextCursor(c)
            return True
        return False

    def _dedent_brace(self):
        c = self.textCursor()
        line = c.block().text()
        if line.strip() == "}" and line.startswith("    "):
            # выравниваем по открывающей скобке
            depth, b = 0, c.block().previous()
            target = None
            while b.isValid():
                t = b.text()
                depth += t.count("}") - t.count("{")
                if depth < 0:
                    target = re.match(r"[ \t]*", t).group(0)
                    break
                b = b.previous()
            if target is not None:
                c.select(QTextCursor.SelectionType.LineUnderCursor)
                c.insertText(target + "}")
                self.setTextCursor(c)

    def toggle_comment(self):
        mark = self.language.line_comment or "#"
        c = self.textCursor()
        start = self.document().findBlock(c.selectionStart()).blockNumber()
        end = self.document().findBlock(max(c.selectionStart(), c.selectionEnd() - (1 if c.hasSelection() else 0))).blockNumber()
        blocks = [self.document().findBlockByNumber(n) for n in range(start, end + 1)]
        all_commented = all(b.text().strip().startswith(mark) for b in blocks if b.text().strip())
        cur = QTextCursor(self.document())
        cur.beginEditBlock()
        for b in blocks:
            t = b.text()
            if not t.strip():
                continue
            ind = len(t) - len(t.lstrip())
            cur.setPosition(b.position() + ind)
            if all_commented:
                n = len(mark) + (1 if t[ind + len(mark):ind + len(mark) + 1] == " " else 0)
                cur.setPosition(b.position() + ind + n, QTextCursor.MoveMode.KeepAnchor)
                cur.removeSelectedText()
            else:
                cur.insertText(mark + " ")
        cur.endEditBlock()

    def duplicate_line(self):
        c = self.textCursor()
        text = c.block().text()
        c.movePosition(QTextCursor.MoveOperation.EndOfBlock)
        c.insertText("\n" + text)
        self.setTextCursor(c)

    def go_to_line(self, line, col=1):
        block = self.document().findBlockByNumber(max(0, line - 1))
        c = QTextCursor(block)
        c.movePosition(QTextCursor.MoveOperation.Right, n=max(0, min(col - 1, len(block.text()))))
        self.setTextCursor(c)
        self.centerCursor()
        self.setFocus()

    # ------------------------------------------------------------ подсказки
    def _line_col(self):
        c = self.textCursor()
        return c.blockNumber() + 1, c.positionInBlock()

    def request_completion(self, force=False):
        self._req += 1
        srv = self._lsp_server()
        if srv:
            line, col = self._line_col()
            srv.submit("complete", req=self._req, editor=id(self), code=self.toPlainText(),
                       line=line, col=col, path=self.path)
        elif self.lang == "python" and self.services.jedi.available:
            line, col = self._line_col()
            self.services.jedi.submit("complete", req=self._req, editor=id(self),
                                      code=self.toPlainText(), line=line, col=col, path=self.path)
        elif self.lang == "csharp":
            self.show_completions(CSHARP.complete(self.toPlainText(), self.textCursor().position()))
        else:
            prefix = self.word_before_cursor()
            if prefix or force:
                self.show_completions(word_items(self.toPlainText(), prefix))

    def _lsp_server(self):
        """Языковой сервер этого языка (см. languages.py), если он готов; иначе — запасные
        подсказки: справочник Unity для C#, jedi для Python, слова из файла для остальных."""
        svc = self.services.service_for(self.language)
        return svc.ready_for(self.path) if svc is not None else None

    def on_worker_result(self, res):
        kind, data = res["kind"], res["data"]
        if kind == "complete" and res["req"] == self._req:
            self.show_completions(data or [])
        elif kind == "signatures" and res["req"] == self._sig_req:
            self.show_calltip(data or [])
        elif kind == "hover" and data:
            html_ = data if isinstance(data, str) else hover_html(data)
            if html_ and self._hover_pos is not None:
                QToolTip.showText(self.mapToGlobal(self._hover_pos), self._tip_html(html_), self)
        elif kind == "references":
            self.busy_changed.emit("")
            self.references_found.emit(getattr(self, "_last_symbol", ""), data or [])
        elif kind == "rename":
            self.busy_changed.emit("")
            self.rename_ready.emit(getattr(self, "_new_name", ""), data or {"error": "анализатор не ответил"})
        elif kind == "definition":
            self.busy_changed.emit("")
            if data:
                self.goto_requested.emit(data["path"], data["line"])
            else:
                self.services.status("Определение не найдено")

    def show_completions(self, items):
        popup = self.completer.popup()
        prefix = self.word_before_cursor()
        if not prefix.startswith("_"):
            items = [it for it in items if not it.name.startswith("_")] + \
                    [it for it in items if it.name.startswith("_") and prefix]
        if not items or (len(items) == 1 and items[0].name == prefix):
            popup.hide()
            return
        self.model.clear()
        for it in items[:300]:
            row = QStandardItem(kind_icon(it.kind), it.name)
            row.setData(it.detail, ROLE_DETAIL)
            row.setData(it.doc, ROLE_DOC)
            self.model.appendRow(row)
        self.completer.setCompletionPrefix(prefix)
        if self.completer.completionCount() == 0:
            popup.hide()
            return
        popup.setCurrentIndex(self.completer.completionModel().index(0, 0))
        self.show_completions_geometry()

    def show_completions_geometry(self):
        prefix = self.word_before_cursor()
        cr = self.cursorRect()
        cr.translate(self.viewportMargins().left() - self.fontMetrics().horizontalAdvance(prefix), 0)
        cr.setWidth(int(self.hint_pt() * 40))
        self.completer.complete(cr)

    def _on_highlighted(self, name):
        # описание выбранной подсказки сбоку от списка
        popup = self.completer.popup()
        idx = popup.currentIndex()
        doc = idx.data(ROLE_DOC) if idx.isValid() else ""
        detail = idx.data(ROLE_DETAIL) if idx.isValid() else ""
        if doc or (detail and len(detail) > 40):
            self.doc_tip.setText("<b>%s</b><br><span style='color:%s'>%s</span>" % (
                detail or name, C["tip_doc"], doc or ""))
            self.doc_tip.adjustSize()
            g = popup.geometry()
            self.doc_tip.move(popup.mapToGlobal(QPoint(g.width() + 2, 0)))
            self.doc_tip.show()
        else:
            self.doc_tip.hide()

    def insert_completion(self, name):
        c = self.textCursor()
        prefix = self.word_before_cursor()
        c.movePosition(QTextCursor.MoveOperation.Left, QTextCursor.MoveMode.KeepAnchor, len(prefix))
        c.insertText(name)
        self.setTextCursor(c)
        self.doc_tip.hide()

    def request_signatures(self, force=False):
        srv = self._lsp_server()
        if srv:
            self._sig_req += 1
            line, col = self._line_col()
            srv.submit("signatures", req=self._sig_req, editor=id(self), code=self.toPlainText(),
                       line=line, col=col, path=self.path)
        elif self.lang == "python" and self.services.jedi.available:
            self._sig_req += 1
            line, col = self._line_col()
            self.services.jedi.submit("signatures", req=self._sig_req, editor=id(self),
                                      code=self.toPlainText(), line=line, col=col, path=self.path)
        elif self.lang == "csharp":
            self.show_calltip(CSHARP.signatures(self.toPlainText(), self.textCursor().position()))

    def show_calltip(self, sigs):
        if not sigs:
            self.calltip.hide()
            return
        lines = [signature_html(n, params, idx) for n, params, idx in sigs[:4]]
        if len(sigs) > 1:
            lines.append("<span style='color:%s'>вариантов: %d</span>" % (C["hint"], len(sigs)))
        self.calltip.setText("<br>".join(lines))
        self.calltip.adjustSize()
        cr = self.cursorRect()
        pos = self.viewport().mapToGlobal(QPoint(cr.left(), cr.top()))
        self.calltip.move(pos.x() - 10, pos.y() - self.calltip.height() - 4)
        self.calltip.show()

    def go_to_definition(self):
        srv = self._lsp_server()
        if srv or (self.lang == "python" and self.services.jedi.available):
            word = self._word_at(self.textCursor())
            # переход в класс из сборки (декомпиляция) может занять несколько секунд — показываем, что идёт работа
            self.busy_changed.emit("Переход к определению «%s»…" % word if word else "Переход к определению…")
        if srv:
            line, col = self._line_col()
            srv.submit("definition", req=0, editor=id(self), code=self.toPlainText(),
                       line=line, col=col, path=self.path)
        elif self.lang == "python" and self.services.jedi.available:
            line, col = self._line_col()
            self.services.jedi.submit("definition", req=0, editor=id(self),
                                      code=self.toPlainText(), line=line, col=col, path=self.path)
        else:
            word = self._word_at(self.textCursor())
            m = re.search(r"\b(?:class|void|[A-Z]\w*|int|float|bool|string)\s+%s\b" % re.escape(word),
                          self.toPlainText()) if word else None
            if m:
                line = self.toPlainText().count("\n", 0, m.start()) + 1
                self.go_to_line(line)
            else:
                self.services.status("Определение «%s» не найдено в файле" % word)

    # ------------------------------------------------------------ ссылки и переименование
    def _symbol_request(self, kind, busy_text, extra=None):
        """Запрос к анализатору о слове под курсором. False — анализатора для этого файла нет."""
        line, col = self._line_col()
        srv = self._lsp_server()
        if srv:
            srv.submit(kind, req=0, editor=id(self), code=self.toPlainText(), line=line, col=col,
                       path=self.path, extra=extra)
        elif self.lang == "python" and self.services.jedi.available:
            self.services.jedi.submit(kind, req=0, editor=id(self), code=self.toPlainText(),
                                      line=line, col=col, path=self.path, extra=extra or {})
        else:
            self.services.status("Для этого файла нет анализатора кода (решение ещё загружается?)")
            return False
        self.busy_changed.emit(busy_text)
        return True

    def symbol_under_cursor(self):
        return self._word_at(self.textCursor())

    def find_references(self):
        word = self.symbol_under_cursor()
        if word:
            self._last_symbol = word
            self._symbol_request("references", "Поиск ссылок на «%s»…" % word)

    def rename_symbol(self, new_name):
        self._new_name = new_name
        self._symbol_request("rename", "Переименование в «%s»…" % new_name, {"new_name": new_name})

    def apply_edits(self, edits):
        """Правки LSP (стр, стлб, стр2, стлб2, текст; с 0, столбцы в UTF-16 — как позиции Qt) одним шагом отмены."""
        doc = self.document()

        def pos(line, col):
            block = doc.findBlockByNumber(line)
            if not block.isValid():
                return doc.characterCount() - 1
            return block.position() + min(col, block.length() - 1)
        c = QTextCursor(doc)
        c.beginEditBlock()
        for a, b, l2, c2, text in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
            c.setPosition(pos(a, b))
            c.setPosition(pos(l2, c2), QTextCursor.MoveMode.KeepAnchor)
            c.insertText(text)
        c.endEditBlock()

    # ------------------------------------------------------------ наведение мыши
    def keyReleaseEvent(self, e):
        if e.key() == Qt.Key.Key_Control:
            self._update_link(None, False)
        super().keyReleaseEvent(e)

    def _update_link(self, pos, ctrl):
        """Подчеркнуть слово под мышью, пока зажат Ctrl (pos — в координатах viewport)."""
        link = None
        if ctrl and pos is not None and self.viewport().rect().contains(pos):
            cur = self.cursorForPosition(pos)
            # cursorForPosition берёт ближайшую позицию — проверим, что мышь действительно над текстом
            if self.cursorRect(cur).adjusted(-12, 0, 12, 0).contains(pos):
                cur.select(QTextCursor.SelectionType.WordUnderCursor)
                if re.fullmatch(r"[A-Za-z_]\w*", cur.selectedText()):
                    link = cur
        old = self._link
        same = (old is None and link is None) or (old is not None and link is not None and
                                                    old.selectionStart() == link.selectionStart() and
                                                    old.selectionEnd() == link.selectionEnd())
        self.viewport().setCursor(Qt.CursorShape.PointingHandCursor if link is not None
                                  else Qt.CursorShape.IBeamCursor)
        if not same:
            self._link = link
            self.update_selections()

    def mousePressEvent(self, e):
        # боковые кнопки мыши — назад / вперёд по переходам, как в Visual Studio
        if e.button() in (Qt.MouseButton.BackButton, Qt.MouseButton.ForwardButton):
            self.services.navigate(-1 if e.button() == Qt.MouseButton.BackButton else 1)
            return
        # Ctrl+клик — перейти к определению, как в Visual Studio
        if e.button() == Qt.MouseButton.LeftButton and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.setTextCursor(self.cursorForPosition(e.position().toPoint()))
            self._update_link(None, False)
            self.go_to_definition()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        self._update_link(e.position().toPoint(), bool(e.modifiers() & Qt.KeyboardModifier.ControlModifier))
        super().mouseMoveEvent(e)
        self._hover_pos = e.position().toPoint()
        self._hover_timer.start()

    def leaveEvent(self, e):
        self._update_link(None, False)
        self._hover_timer.stop()
        super().leaveEvent(e)

    def _word_at(self, cursor):
        cursor.select(QTextCursor.SelectionType.WordUnderCursor)
        return cursor.selectedText()

    def _do_hover(self):
        if self._hover_pos is None or not self.underMouse():
            return
        vp = self.viewport().mapFrom(self, self._hover_pos)
        cur = self.cursorForPosition(vp)
        # ошибка под мышью
        for line_no, col, msg, sev in self.diagnostics:
            if cur.blockNumber() == line_no - 1:
                QToolTip.showText(self.mapToGlobal(self._hover_pos), self._tip_html(
                                  "<span style='color:%s'>●</span> %s" % (
                                      C["error"] if sev == "error" else C["warning"], msg)), self)
                return
        word = self._word_at(QTextCursor(cur))
        if not word or not re.match(r"\w", word):
            return
        srv = self._lsp_server()
        if srv:
            srv.submit("hover", req=0, editor=id(self), code=self.toPlainText(),
                       line=cur.blockNumber() + 1, col=cur.positionInBlock(), path=self.path)
        elif self.lang == "python" and self.services.jedi.available:
            self.services.jedi.submit("hover", req=0, editor=id(self), code=self.toPlainText(),
                                      line=cur.blockNumber() + 1, col=cur.positionInBlock(),
                                      path=self.path)
        elif self.lang == "csharp":
            wc = QTextCursor(cur)
            wc.select(QTextCursor.SelectionType.WordUnderCursor)
            html_ = CSHARP.hover(self.toPlainText(), wc.selectionStart(), word)
            if html_:
                QToolTip.showText(self.mapToGlobal(self._hover_pos), self._tip_html(html_), self)

    def focusOutEvent(self, e):
        self._update_link(None, False)
        self.calltip.hide()
        self.doc_tip.hide()
        super().focusOutEvent(e)

    def hideEvent(self, e):
        self.calltip.hide()
        self.doc_tip.hide()
        super().hideEvent(e)

    def wheelEvent(self, e):
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.services.zoom(1 if e.angleDelta().y() > 0 else -1)
            return
        super().wheelEvent(e)


# ====================================================================== проверки
def python_warnings(tree, text):
    """Лёгкие предупреждения: неиспользуемые импорты."""
    out = []
    imported = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                imported[(a.asname or a.name).split(".")[0]] = node.lineno
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name != "*":
                    imported[a.asname or a.name] = node.lineno
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    used |= {n.value.id for n in ast.walk(tree) if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)}
    all_names = set(re.findall(r"__all__\s*=\s*\[([^\]]*)\]", text))
    for name, line in imported.items():
        if name not in used and name not in "".join(all_names):
            out.append((line, 1, "Импорт «%s» не используется" % name, "warning"))
    return out


def check_csharp(text):
    """Простая проверка C#: парность скобок и незакрытые строки."""
    out = []
    stack = []
    pairs = {")": "(", "]": "[", "}": "{"}
    i, n, line, col = 0, len(text), 1, 1
    in_block = False
    while i < n:
        ch = text[i]
        if in_block:
            if text.startswith("*/", i):
                in_block = False
                i += 2
                col += 2
                continue
        elif text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            col += j - i
            i = j
            continue
        elif text.startswith("/*", i):
            in_block = True
        elif ch in "\"'":
            start_col, j = col, i + 1
            verbatim = i > 0 and text[i - 1] == "@"
            while j < n and text[j] != ch and (verbatim or text[j] != "\n"):
                j += 2 if (text[j] == "\\" and not verbatim) else 1
            if j >= n or text[j] == "\n":
                out.append((line, start_col, "Строка не закрыта кавычкой", "error"))
            col += j - i + 1
            i = j + 1
            continue
        elif ch in "([{":
            stack.append((ch, line, col))
        elif ch in ")]}":
            if stack and stack[-1][0] == pairs[ch]:
                stack.pop()
            else:
                out.append((line, col, "Лишняя закрывающая скобка «%s»" % ch, "error"))
        if ch == "\n":
            line += 1
            col = 1
        else:
            col += 1
        i += 1
    for ch, l, c in stack[-3:]:
        out.append((l, c, "Скобка «%s» не закрыта" % ch, "error"))
    # простое предупреждение: забытая точка с запятой
    for no, ln in enumerate(text.splitlines(), 1):
        s = ln.strip()
        if re.match(r"^(?:[\w.<>\[\]]+\s+)?[\w.\[\]]+\s*(?:[+\-*/]?=)\s*[^;{}]*[\w)\]\"']$", s) and \
                not s.startswith(("if", "for", "while", "else", "//", "[", "#", "case", "using", "namespace")) \
                and "=>" not in s and not s.endswith(","):
            out.append((no, len(ln) - len(ln.lstrip()) + 1, "Возможно, пропущена «;» в конце строки", "warning"))
    return out
