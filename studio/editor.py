"""Редактор кода: номера строк, подсветка, подсказки, подсказки параметров, ошибки."""
import ast
import os
import re
import traceback

from PyQt6.QtCore import Qt, QEvent, QRect, QSize, QTimer, pyqtSignal, QPoint, QRectF, QModelIndex
from PyQt6.QtGui import (QColor, QCursor, QFont, QPainter, QTextFormat, QTextCursor, QStandardItemModel,
                         QStandardItem, QKeySequence, QTextCharFormat, QPalette, QFontMetrics)
from PyQt6.QtWidgets import (QPlainTextEdit, QWidget, QTextEdit, QCompleter, QLabel,
                             QStyledItemDelegate, QToolTip, QStyle, QApplication, QMessageBox,
                             QListView, QToolButton, QHBoxLayout, QMenu)

from .highlighter import Highlighter, PygmentsHighlighter
from . import languages, refactor
from .documents import DiskState, atomic_write
from .indentation import IndentStyle, apply_save_rules
from .lsp import markdown_to_html as lsp_markdown_to_html, line_diff_edits
from .intellisense import (CSharpProvider, signature_html, hover_html, word_items,
                           KIND_GROUPS, arrange, present_groups)
from . import theme
from .theme import C, kind_icon
from .i18n import tr


CSHARP = CSharpProvider()
DEFAULT_HINT_PT = 11    # размер подсказок (список, параметры, справка); свой масштаб, отдельно от кода
ROLE_DETAIL = Qt.ItemDataRole.UserRole + 1
ROLE_DOC = Qt.ItemDataRole.UserRole + 2
ROLE_ITEM = Qt.ItemDataRole.UserRole + 3     # сам элемент подсказки (Item)


ENCODING_NAME = {"utf-8": "UTF-8", "utf-8-sig": "editor.utf_8_with_bom", "utf-16": "UTF-16",
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

    def mousePressEvent(self, e):
        ed = self.editor
        if e.button() == Qt.MouseButton.LeftButton and ed._bulb_line is not None \
                and e.position().x() >= self.width() - 18:
            block = ed.cursorForPosition(QPoint(1, int(e.position().y()))).block()
            if block.blockNumber() == ed._bulb_line:
                ed.show_bulb_menu()
                return
        super().mousePressEvent(e)


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


class CompletionPopup(QListView):
    """Список подсказок с полосой кнопок внизу (место под неё оставлено полями области просмотра)."""
    FOOT = 26

    def __init__(self):
        super().__init__()
        # то же, что QCompleter делает с собственным списком
        self.setEditTriggers(QListView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QListView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QListView.SelectionMode.SingleSelection)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.footer = None

    def attach_footer(self, footer):
        self.footer = footer
        footer.setParent(self)
        self.setViewportMargins(0, 0, 0, self.FOOT)
        footer.show()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.footer is not None:
            fw = self.frameWidth()
            self.footer.setGeometry(fw, self.height() - self.FOOT - fw, self.width() - 2 * fw, self.FOOT)


class CompletionFooter(QWidget):
    """Кнопки внизу списка подсказок, как в Visual Studio: фильтр по видам (классы, методы, свойства…)
    и переключатель сортировки «по релевантности / по алфавиту»."""

    def __init__(self, editor):
        super().__init__()
        self.setObjectName("completionFooter")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(2)
        self.buttons = {}
        for gid, _title, _kinds, icon_kind in KIND_GROUPS:
            b = self._button()
            b.setIcon(kind_icon(icon_kind))
            b.clicked.connect(lambda _checked=False, g=gid: editor.toggle_completion_group(g))
            lay.addWidget(b)
            self.buttons[gid] = b
        lay.addStretch(1)
        self.sort = self._button()
        self.sort.setText("A→Z")
        self.sort.clicked.connect(lambda _checked=False: editor.toggle_completion_sort())
        lay.addWidget(self.sort)
        self.restyle()

    @staticmethod
    def _button():
        b = QToolButton()
        b.setCheckable(True)
        b.setAutoRaise(True)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)       # фокус остаётся в редакторе
        b.setIconSize(QSize(16, 16))
        return b

    def restyle(self):
        theme.style(self, "#completionFooter { background:@menu@; border-top:1px solid @tooltip_border@; }"
                          " QToolButton { border:none; border-radius:3px; padding:1px 3px; color:@text@; font-size:8pt; }"
                          " QToolButton:hover, QToolButton:checked { background:@completion_sel@; }")

    def update_state(self, present, enabled, alpha):
        titles = {gid: title for gid, title, _k, _i in KIND_GROUPS}
        many = len(present) > 1                         # одна группа — фильтровать нечего
        for gid, b in self.buttons.items():
            b.setVisible(many and gid in present)
            b.setChecked(gid in enabled)
            b.setToolTip(tr("editor.show_only") + tr(titles[gid]))
        self.sort.setChecked(alpha)
        self.sort.setToolTip(tr("editor.sort_alphabetically_off_by_relevance"))


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
    saved = pyqtSignal(str)                      # путь; файл записан на диск

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
        self.disk = DiskState(path)    # что было на диске при открытии/сохранении — чтобы заметить чужие изменения
        self.missing_on_disk = False   # файл удалили вне редактора; сохранение создаст его заново
        self.indent = IndentStyle()    # пробелы/табы и размер; окно ставит итог .editorconfig/файла/параметров
        self._link = None              # слово под мышью при зажатом Ctrl — подчёркнуто, как ссылка в VS
        self.find_text = ""
        self._completion_items = {}
        self._completion_items_full = []    # всё, что дал анализатор; список на экране — его фильтр
        self._completion_anchor = None      # где начинается набираемое слово
        self._completion_base = ""          # начало слова, для которого собран список
        self._completion_exact = False      # анализатор отдал всё: при наборе фильтруем сами, без запроса
        self._enabled_groups = set()        # включённые кнопки-фильтры по видам
        self._bulb_line = None              # строка, где показана лампочка (быстрые действия)
        self._bulb_actions = []
        self._bulb_req = 0
        self._bulb_open_after = False       # Ctrl+. нажали раньше, чем пришёл ответ сервера
        self._bulb_timer = QTimer(self, singleShot=True, interval=600, timeout=self._refresh_bulb)
        self._req = 0
        self._notice_req = 0
        self._notice_timer = QTimer(self, singleShot=True, interval=350, timeout=self._pending_notice)
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
        self._footer = None
        try:        # кнопки внизу списка; если что-то пойдёт не так — список подсказок работает и без них
            popup_view = CompletionPopup()
            self.completer.setPopup(popup_view)
            self._footer = CompletionFooter(self)
            popup_view.attach_footer(self._footer)
        except Exception:       # noqa: BLE001
            traceback.print_exc()
            self._footer = None
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
        self.completer.activated[QModelIndex].connect(self._activated)
        self.completer.highlighted[str].connect(self._on_highlighted)
        self.calltip = CallTip(self, self.hint_pt())
        self.doc_tip = CallTip(self, self.hint_pt())

        self._diag_timer = QTimer(self, singleShot=True, interval=500, timeout=self.run_diagnostics)
        self._hover_timer = QTimer(self, singleShot=True, interval=550, timeout=self._do_hover)
        self._hover_pos = None
        self.textChanged.connect(self._diag_timer.start)
        self.textChanged.connect(self._bulb_timer.start)
        self.setMouseTracking(True)
        self._on_cursor()

    # ------------------------------------------------------------ файл
    def load(self):
        with open(self.path, "rb") as f:
            raw = f.read()
        self.disk.remember(raw)
        self.encoding, text = decode_source(raw)
        self.newline = "\r\n" if "\r\n" in text else "\n"
        self.setPlainText(text.replace("\r\n", "\n").replace("\r", "\n"))
        self.document().setModified(False)
        self.run_diagnostics()

    def reload_from(self, raw):
        """Новая версия файла с диска (изменили вне редактора). Меняются только отличающиеся строки,
        одним шагом отмены: курсор и прокрутка остаются на месте, Ctrl+Z вернёт прежний текст."""
        self.disk.remember(raw)
        self.missing_on_disk = False
        self.encoding, text = decode_source(raw)
        self.newline = "\r\n" if "\r\n" in text else "\n"
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        bar = self.verticalScrollBar()
        scroll = bar.value()
        edits = line_diff_edits(self.toPlainText(), text)
        if edits:
            self.apply_edits(edits)
        bar.setValue(scroll)
        self.document().setModified(False)
        self.run_diagnostics()

    def set_text_as_edit(self, text):
        """Подменить весь текст как обычную правку (Ctrl+Z вернёт прежний) — для восстановленных копий."""
        edits = line_diff_edits(self.toPlainText(), text)
        if edits:
            self.apply_edits(edits)

    def save(self):
        """Сохраняет в той же кодировке и с теми же переводами строк, что были у файла (как Visual Studio).
        Запись атомарная; если файл успели изменить вне редактора — сначала спрашивает.
        Правила .editorconfig (пробелы в конце строк, перевод строки в конце) применяются и к тексту
        во вкладке — одной правкой, Ctrl+Z её вернёт."""
        text = self.toPlainText()
        ruled = apply_save_rules(text, self.indent)
        if ruled != text:
            self.set_text_as_edit(ruled)
            text = self.toPlainText()
        if self.newline != "\n":
            text = text.replace("\n", self.newline)
        try:
            data = text.encode(self.encoding)
        except UnicodeEncodeError as e:
            bad = text[e.start:e.end]
            r = QMessageBox.question(
                self, tr("editor.encoding"), tr("editor.symbol_cannot_stored_encoding_save") % (bad, tr(ENCODING_NAME.get(self.encoding, self.encoding))),
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel)
            if r != QMessageBox.StandardButton.Save:
                return False
            self.encoding = "utf-8-sig"
            data = text.encode(self.encoding)
        on_disk = self.disk.check()
        if on_disk is not None and on_disk is not DiskState.MISSING:
            r = QMessageBox.question(self, tr("documents.changed_outside_title"),
                                     tr("documents.overwrite_changed") % os.path.basename(self.path),
                                     QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
                                     QMessageBox.StandardButton.Cancel)
            if r != QMessageBox.StandardButton.Save:
                return False
        try:
            atomic_write(self.path, data)
        except OSError as e:
            QMessageBox.warning(self, tr("documents.save_failed_title"),
                                tr("documents.save_failed") % (self.path, e.strerror or e))
            return False
        self.disk.remember(data)
        self.missing_on_disk = False
        self.document().setModified(False)
        self.saved.emit(self.path)
        self.run_diagnostics()
        return True

    def encoding_label(self):
        return tr(ENCODING_NAME.get(self.encoding, self.encoding.upper())) + ("  CRLF" if self.newline == "\r\n" else "")

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
                if num == self._bulb_line:
                    self._draw_bulb(p, w - 15, top + h / 2 - 7)
                if num in err_lines:
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QColor(C["error"] if err_lines[num] == "error" else C["warning"]))
                    p.drawEllipse(QRectF(3, top + h / 2 - 3.5, 7, 7))
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            num += 1
        p.end()

    @staticmethod
    def _draw_bulb(p, x, y):
        """Лампочка быстрых действий (жёлтая колба и серый цоколь)."""
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#F5C518"))
        p.drawEllipse(QRectF(x + 1.5, y, 9, 9))
        p.setBrush(QColor("#9A9A9A"))
        p.drawRoundedRect(QRectF(x + 3.5, y + 9, 5, 2.5), 1, 1)
        p.restore()

    # ------------------------------------------------------------ лампочка (быстрые действия)
    def _own_actions(self):
        """Рефакторинги самой программы (работают и без анализатора)."""
        if self.lang != "csharp" or self.isReadOnly():
            return []
        text = self.toPlainText()
        pos = len(text.encode("utf-16-le")[:self.textCursor().position() * 2].decode("utf-16-le", "ignore"))
        try:
            types = refactor.parse(text)[0]
        except Exception:       # noqa: BLE001 — разбор не должен мешать набору текста
            return []
        if len(types) > 1 and any(t.begin <= pos <= t.end for t in types):
            return [{"title": tr("common.move_type_own_file"), "source": "own",
                     "run": self.services.move_type_to_file}]
        return []

    def _refresh_bulb(self):
        if not self.isVisible() or self.completer.popup().isVisible():
            return
        self._bulb_req += 1
        req = self._bulb_req
        line, col = self._line_col()
        line -= 1
        own = self._own_actions()
        self._bulb_actions = own
        self._bulb_line = line if own else None
        self.gutter.update()
        srv = self._lsp_server()
        if srv is not None and hasattr(srv, "code_actions"):
            srv.code_actions(self.path, line, col,
                             lambda items, req=req, line=line, own=own: self._bulb_result(req, line, own, items))
        elif self._bulb_open_after:
            self._bulb_open_after = False
            self._open_bulb_menu()

    def _bulb_result(self, req, line, own, items):
        if req != self._bulb_req or line != self.textCursor().blockNumber():
            return                  # пока сервер думал, курсор уже ушёл
        self._bulb_actions = [{"title": it["title"], "source": "lsp", "item": it} for it in items] + own
        self._bulb_line = line if self._bulb_actions else None
        self.gutter.update()
        if self._bulb_open_after:
            self._bulb_open_after = False
            self._open_bulb_menu()

    def show_bulb_menu(self):
        """Ctrl+. или клик по лампочке: список быстрых действий."""
        if self._bulb_line == self.textCursor().blockNumber() and self._bulb_actions:
            self._open_bulb_menu()
            return
        self._bulb_open_after = True        # данных для этой строки ещё нет — спросим и откроем
        self._refresh_bulb()

    def _open_bulb_menu(self):
        if not self._bulb_actions:
            self.services.status(tr("editor.no_quick_actions_here"))
            return
        menu = QMenu(self)
        for a in self._bulb_actions:
            menu.addAction(a["title"]).triggered.connect(lambda _c=False, a=a: self._run_bulb_action(a))
        menu.exec(self.viewport().mapToGlobal(self.cursorRect().bottomLeft()))
        menu.deleteLater()

    def _run_bulb_action(self, action):
        if action["source"] == "own":
            action["run"]()
            return
        srv = self._lsp_server()
        if srv is None:
            return
        title = action["title"]
        srv.resolve_code_action(action["item"], lambda edit, title=title: self.services.apply_code_action(edit, title))

    # ------------------------------------------------------------ выделения
    def _on_cursor(self):
        c = self.textCursor()
        if self._bulb_line is not None and self._bulb_line != c.blockNumber():
            self._bulb_line = None
            self.gutter.update()
        self._bulb_timer.start()
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
                    diags.append((e.lineno or 1, e.offset or 1, tr("common.syntax_error") % e.msg, "error"))
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
                diags.append((e.lineno, e.colno, tr("editor.json_error") % e.msg, "error"))
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
        self._update_tab_stops(font)
        if hasattr(self, "gutter"):
            self._update_gutter_width()

    def _update_tab_stops(self, font):
        width = getattr(self, "indent", None).tab_width if hasattr(self, "indent") else 4
        self.setTabStopDistance(QFontMetrics(font).horizontalAdvance(" ") * width)

    def set_indent_style(self, style):
        """Стиль отступа файла (indentation.resolve). Ширина таба на экране — из него же."""
        self.indent = style
        self._update_tab_stops(self.font())

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
            self._update_tab_stops(self.font())
        super().changeEvent(e)

    def apply_theme(self):
        """Тема сменилась: фон и текст, подсветка кода, подчёркивания ошибок, номера строк."""
        self._apply_palette()
        self.highlighter.rebuild()
        self.update_selections()
        self.gutter.update()
        self.viewport().update()
        if self._footer is not None:
            self._footer.restyle()

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
        if e.type() == QEvent.Type.Hide and obj is self.completer.popup():
            self.doc_tip.hide()
        if e.type() == QEvent.Type.KeyPress and obj is self.completer.popup():
            if self._completion_key(e):
                return True
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
        if popup.isVisible() and self._completion_key(e):
            return
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        if ctrl and mods & Qt.KeyboardModifier.ShiftModifier and key == Qt.Key.Key_Space:
            self.request_signatures(force=True)
            return
        if ctrl and key == Qt.Key.Key_Space:
            self.request_completion(force=True)
            return
        if ctrl and key == Qt.Key.Key_Period:           # быстрые действия, как Ctrl+. в Visual Studio
            self.show_bulb_menu()
            return
        if key == Qt.Key.Key_Escape:
            self._req += 1
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
            self.doc_tip.hide()
            self._req += 1              # запоздавший ответ уже не нужен
            self._notice_timer.stop()
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
            extra = self.indent.unit()
        elif stripped.endswith(("{", "[", "(")):
            extra = self.indent.unit()
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
                c.insertText(self.indent.tab_insertion(c.block().text()[:c.positionInBlock()]))
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
                cur.insertText(self.indent.unit())
            cur.endEditBlock()
        else:
            self._dedent_lines(start, end)

    def _dedent_lines(self, start, end):
        cur = QTextCursor(self.document())
        cur.beginEditBlock()
        for n in range(start, end + 1):
            b = self.document().findBlockByNumber(n)
            leading = b.text()[:len(b.text()) - len(b.text().lstrip(" \t"))]
            k = self.indent.dedent(leading)
            if leading and k:
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
        width = self.indent.backspace_width(before)
        if width > 1:
            c.beginEditBlock()
            for _ in range(width):
                c.deletePreviousChar()
            c.endEditBlock()
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
        if line.strip() == "}" and line[:1] in (" ", "\t"):
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

    def contextMenuEvent(self, e):
        menu = self.createStandardContextMenu()
        if self.lang == "csharp" and not self.isReadOnly():
            menu.addSeparator()
            menu.addAction(self.services.a_move_type)
        menu.exec(e.globalPos())
        menu.deleteLater()

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

    def _word_anchor(self):
        c = self.textCursor()
        return c.blockNumber(), c.positionInBlock() - len(self.word_before_cursor())

    def _refine_completion(self):
        """Список уже открыт и анализатор отдал всё: при наборе слова достаточно отфильтровать его на месте.
        Так не нужен новый запрос на каждую букву — подсказки не отстают от набора."""
        if not (self._completion_exact and self._completion_items_full and self.completer.popup().isVisible()):
            return False
        word = self.word_before_cursor()
        # Стёрли букву: список был собран для более длинного начала и мог не включать то, что подходит
        # к короткому, — нужен новый запрос. Фильтровать на месте можно только при удлинении начала.
        if not word or self._word_anchor() != self._completion_anchor \
                or not word.lower().startswith(self._completion_base):
            return False
        self._completion_location = (self.document().revision(), self.textCursor().position())
        self._render_completions()
        return True

    def _pending_notice(self):
        # «Поиск подсказок…» — только если ответа нет заметно долго (раньше мигало на каждую букву)
        if self._notice_req == self._req and not self.completer.popup().isVisible():
            self._completion_notice(tr("editor.searching_completions"))

    def request_completion(self, force=False):
        if not force and self._refine_completion():
            return
        self._req += 1
        self._notice_req = self._req
        self._notice_timer.start()
        self._completion_location = (self.document().revision(), self.textCursor().position())
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
            self._request_index()
        else:
            prefix = self.word_before_cursor()
            if prefix or force:
                self.show_completions(word_items(self.toPlainText(), prefix))

    def _source_offset(self):
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start, QTextCursor.MoveMode.KeepAnchor)
        return len(cursor.selectedText())

    def _lsp_server(self):
        """Языковой сервер этого языка (см. languages.py), если он готов; иначе — запасные
        подсказки: справочник Unity для C#, jedi для Python, слова из файла для остальных."""
        svc = self.services.service_for(self.language)
        return svc.ready_for(self.path) if svc is not None else None

    def on_worker_result(self, res):
        kind, data = res["kind"], res["data"]
        if kind == "complete" and res["req"] == self._req:
            if self._completion_location != (self.document().revision(), self.textCursor().position()):
                return
            if self.lang == "csharp" and not res.get("indexed"):
                self._request_index(data)
                return
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
            self.rename_ready.emit(getattr(self, "_new_name", ""), data or {"error": tr("editor.analyzer_no_response")})
        elif kind == "definition":
            if not data and self.lang == "csharp" and not res.get("indexed"):
                self._request_source_definition()
                return
            self.busy_changed.emit("")
            if data:
                self.goto_requested.emit(data["path"], data["line"])
            else:
                self.services.status(tr("editor.definition_not_found"))

    def _completion_notice(self, message):
        self.doc_tip.setText("<b>IntelliSense</b><br>" + message)
        self.doc_tip.adjustSize()
        self.doc_tip.move(self.viewport().mapToGlobal(self.cursorRect().bottomLeft()))
        self.doc_tip.show()

    def show_completions(self, items):
        self._notice_timer.stop()
        exact = getattr(items, "incomplete", True) is False
        prefix = self.word_before_cursor()
        if not prefix.startswith("_"):
            items = [it for it in items if not it.name.startswith("_")] + \
                    [it for it in items if it.name.startswith("_") and prefix]
        self._completion_items_full = items
        self._completion_exact = exact
        anchor = self._word_anchor()
        if anchor != self._completion_anchor:
            self._enabled_groups = set()                # новое слово — фильтры по видам сбрасываются
        self._completion_anchor = anchor
        self._completion_base = prefix.lower()          # для какого начала слова собран список
        self._render_completions()

    def _render_completions(self):
        """Что показать сейчас: набранное начало + кнопки-фильтры + сортировка."""
        popup = self.completer.popup()
        prefix = self.word_before_cursor()
        alpha = bool(getattr(self.services, "completion_alpha", False))
        low = prefix.lower()
        full = self._completion_items_full
        matching = [it for it in full if it.name.lower().startswith(low)] if low else full
        present = present_groups(matching)
        self._enabled_groups &= set(present)            # такого вида больше нет — фильтр снимается
        shown = arrange(matching, "", self._enabled_groups, alpha)
        self._completion_items = {it.name: it for it in shown}
        if not shown:
            popup.hide()
            self._completion_notice(tr("editor.no_completions_retry"))
            return
        if self._footer is not None:
            self._footer.update_state(present, self._enabled_groups, alpha)
        self.model.clear()
        for it in shown:
            row = QStandardItem(kind_icon(it.kind), it.name)
            row.setData(tr(it.detail) if it.detail else it.detail, ROLE_DETAIL)
            row.setData(tr(it.doc) if it.doc else it.doc, ROLE_DOC)
            row.setData(it, ROLE_ITEM)
            self.model.appendRow(row)
        self.completer.setCompletionPrefix(prefix)
        if self.completer.completionCount() == 0:
            popup.hide()
            self._completion_notice(tr("editor.no_completions_retry"))
            return
        self.show_completions_geometry()
        popup.setCurrentIndex(self.completer.completionModel().index(0, 0))

    def toggle_completion_group(self, group):
        self._enabled_groups.symmetric_difference_update({group})
        self._render_completions()

    def toggle_completion_sort(self):
        setter = getattr(self.services, "set_completion_alpha", None)
        if setter:
            setter(not getattr(self.services, "completion_alpha", False))
        self._render_completions()

    def _activated(self, index):
        """Выбрана строка списка: вставляем именно её (одинаковые имена из разных библиотек различаются)."""
        self.insert_completion(index.data(Qt.ItemDataRole.DisplayRole), index.data(ROLE_ITEM))

    def show_completions_geometry(self):
        prefix = self.word_before_cursor()
        cr = self.cursorRect()
        cr.translate(self.viewportMargins().left() - self.fontMetrics().horizontalAdvance(prefix), 0)
        cr.setWidth(int(self.hint_pt() * 40))
        self.completer.complete(cr)
        popup = self.completer.popup()
        foot = popup.FOOT if getattr(popup, "footer", None) is not None else 0
        if foot:        # QCompleter считает высоту без полосы кнопок — добавляем её
            g = popup.geometry()
            cursor_top = self.viewport().mapToGlobal(self.cursorRect().topLeft()).y()
            top = g.y() - foot if g.bottom() <= cursor_top + 2 else g.y()    # список над строкой растёт вверх
            popup.setGeometry(g.x(), top, g.width(), g.height() + foot)

    def _on_highlighted(self, name):
        # описание выбранной подсказки сбоку от списка
        popup = self.completer.popup()
        idx = popup.currentIndex()
        doc = idx.data(ROLE_DOC) if idx.isValid() else ""
        detail = idx.data(ROLE_DETAIL) if idx.isValid() else ""
        if doc or (detail and len(detail) > 40):
            self._show_doc_tip("<b>%s</b><br><span style='color:%s'>%s</span>" % (
                detail or name, C["tip_doc"], doc or ""))
        else:
            self.doc_tip.hide()
        # у языкового сервера — полное имя и документация (class UnityEngine.UI.Image …)
        item = idx.data(ROLE_ITEM) if idx.isValid() else None
        srv = self._lsp_server()
        if srv is not None and item is not None and item.data:
            self._resolve_req = getattr(self, "_resolve_req", 0) + 1
            req = self._resolve_req
            srv.resolve_completion(item.data, lambda r, req=req: self._show_resolved(r, req))

    def _show_doc_tip(self, html_):
        popup = self.completer.popup()
        self.doc_tip.setText(html_)
        self.doc_tip.adjustSize()
        g = popup.geometry()
        self.doc_tip.move(popup.mapToGlobal(QPoint(g.width() + 2, 0)))
        self.doc_tip.show()

    def _show_resolved(self, raw, req):
        if req != getattr(self, "_resolve_req", 0) or not self.completer.popup().isVisible():
            return          # пока сервер отвечал, выбрали другую подсказку или закрыли список
        doc = raw.get("documentation") or ""
        if isinstance(doc, dict):
            doc = doc.get("value", "")
        html_ = lsp_markdown_to_html(doc) if doc else ""
        source = (raw.get("labelDetails") or {}).get("description")
        if source and "using " not in html_:
            html_ += "<br><span style='color:%s'>using %s;</span>" % (C["hint"], source)
        if html_:
            self._show_doc_tip(html_)

    def insert_completion(self, name, item=None):
        c = self.textCursor()
        prefix = self.word_before_cursor()
        before = self._text_before_cursor(c)
        after = c.block().text()[len(before):]
        suffix = re.match(r"\w*", after).group()
        position = c.position()
        c.setPosition(position - len(prefix.encode("utf-16-le")) // 2)
        c.setPosition(position + len(suffix.encode("utf-16-le")) // 2,
                      QTextCursor.MoveMode.KeepAnchor)
        if item is None:
            item = self._completion_items.get(name)
        insertion = name
        move_back = 0
        if item and item.kind in ("method", "function") and not after[len(suffix):].lstrip().startswith("("):
            insertion += "()"
            move_back = 1
        elif item and item.kind == "event" and self.lang == "csharp":
            signature = item.detail
            if "(" in signature:
                if re.search(r"\bvoid\s+" + re.escape(prefix) + r"$", before):
                    signature = re.sub(r"^void\s+", "", signature)
                indent = re.match(r"[ \t]*", before).group()
                insertion = signature + "\n" + indent + "{\n" + indent + self.indent.unit() + "\n" + indent + "}"
                move_back = len(indent) + 2
        c.beginEditBlock()
        c.insertText(insertion)
        if move_back:
            c.movePosition(QTextCursor.MoveOperation.Left, n=move_back)
        c.endEditBlock()
        self.setTextCursor(c)
        self.doc_tip.hide()
        self._remember_choice(item)
        self._add_completion_imports(item)
        if item and item.kind in ("method", "function"):
            self.request_signatures(True)


    def _remember_choice(self, item):
        """Выбрали одно из одинаковых имён (Debug из UnityEngine или из System.Diagnostics) —
        в следующий раз оно будет первым."""
        if item is None or not isinstance(item.data, dict):
            return
        if sum(1 for it in self._completion_items_full if it.name == item.name) < 2:
            return
        remember = getattr(self.services, "remember_completion", None)
        if remember:
            remember(item.name, (item.data.get("labelDetails") or {}).get("description") or "")

    def _add_completion_imports(self, item):
        """Подсказка из неподключённого пространства имён: дописать «using …;» (C#) или
        импорт (Python) — правки берутся у сервера; одно Ctrl+Z отменяет и слово, и импорт."""
        if item is None or not item.data:
            return
        revision = self.document().revision()

        def apply(raw):
            edits = raw.get("additionalTextEdits") or []
            if not edits or self.document().revision() != revision:
                return      # ничего не нужно, или текст уже успели изменить — не портим его
            self.apply_edits([(e["range"]["start"]["line"], e["range"]["start"]["character"],
                               e["range"]["end"]["line"], e["range"]["end"]["character"], e.get("newText", ""))
                              for e in edits], join_undo=True)
        if item.data.get("additionalTextEdits"):
            apply(item.data)
            return
        srv = self._lsp_server()
        if srv is not None:
            srv.resolve_completion(item.data, apply)

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
            lines.append(tr("editor.overloads") % (C["hint"], len(sigs)))
        self.calltip.setText("<br>".join(lines))
        self.calltip.adjustSize()
        cr = self.cursorRect()
        pos = self.viewport().mapToGlobal(QPoint(cr.left(), cr.top()))
        self.calltip.move(pos.x() - 10, pos.y() - self.calltip.height() - 4)
        self.calltip.show()

    def _request_source_definition(self):
        self.busy_changed.emit(tr("editor.searching_definition_sources"))
        self.services.csharp_index.submit(kind="definition", req=0, editor=id(self),
            code=self.toPlainText(), pos=self._source_offset(), path=self.path)

    def go_to_definition(self):
        srv = self._lsp_server()
        if srv or (self.lang == "python" and self.services.jedi.available):
            word = self._word_at(self.textCursor())
            # переход в класс из сборки (декомпиляция) может занять несколько секунд — показываем, что идёт работа
            self.busy_changed.emit(tr("editor.going_definition") % word if word else tr("editor.going_to_definition"))
        if srv:
            line, col = self._line_col()
            srv.submit("definition", req=0, editor=id(self), code=self.toPlainText(),
                       line=line, col=col, path=self.path)
        elif self.lang == "python" and self.services.jedi.available:
            line, col = self._line_col()
            self.services.jedi.submit("definition", req=0, editor=id(self),
                                      code=self.toPlainText(), line=line, col=col, path=self.path)
        elif self.lang == "csharp":
            self._request_source_definition()
        else:
            word = self._word_at(self.textCursor())
            m = re.search(r"\b(?:class|void|[A-Z]\w*|int|float|bool|string)\s+%s\b" % re.escape(word),
                          self.toPlainText()) if word else None
            if m:
                line = self.toPlainText().count("\n", 0, m.start()) + 1
                self.go_to_line(line)
            else:
                self.services.status(tr("editor.definition_not_found_file") % word)

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
            self.services.status(tr("editor.no_code_analyzer_file_solution"))
            return False
        self.busy_changed.emit(busy_text)
        return True

    def symbol_under_cursor(self):
        return self._word_at(self.textCursor())

    def find_references(self):
        word = self.symbol_under_cursor()
        if word:
            self._last_symbol = word
            self._symbol_request("references", tr("editor.finding_references") % word)

    def rename_symbol(self, new_name):
        self._new_name = new_name
        self._symbol_request("rename", tr("editor.renaming") % new_name, {"new_name": new_name})

    def apply_edits(self, edits, join_undo=False):
        """Правки LSP (стр, стлб, стр2, стлб2, текст; с 0, столбцы в UTF-16 — как позиции Qt) одним шагом отмены."""
        doc = self.document()

        def pos(line, col):
            block = doc.findBlockByNumber(line)
            if not block.isValid():
                return doc.characterCount() - 1
            return block.position() + min(col, block.length() - 1)
        c = QTextCursor(doc)
        if join_undo:
            c.joinPreviousEditBlock()   # тот же шаг отмены, что и предыдущая правка
        else:
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

    def _completion_key(self, event):
        key = event.key()
        if key in (Qt.Key.Key_Escape, Qt.Key.Key_Backtab):
            self._req += 1
            self.completer.popup().hide()
            self.doc_tip.hide()
            return True
        if key in (Qt.Key.Key_Tab, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            index = self.completer.popup().currentIndex()
            if index.isValid():
                self.insert_completion(index.data(), index.data(ROLE_ITEM))
                self.completer.popup().hide()
                event.accept()
                return True
        return False

    def focusNextPrevChild(self, next):
        # Tab belongs to the editor, including while the completion popup is open.
        return False

    @staticmethod
    def _text_before_cursor(cursor):
        start = QTextCursor(cursor)
        start.movePosition(QTextCursor.MoveOperation.StartOfBlock, QTextCursor.MoveMode.KeepAnchor)
        return start.selectedText()

    def _request_index(self, base=None):
        self.services.csharp_index.submit(req=self._req, editor=id(self), code=self.toPlainText(),
                                          pos=self._source_offset(), path=self.path, base=base)






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
            out.append((line, 1, tr("editor.import_not_used") % name, "warning"))
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
                out.append((line, start_col, tr("editor.unclosed_string"), "error"))
            col += j - i + 1
            i = j + 1
            continue
        elif ch in "([{":
            stack.append((ch, line, col))
        elif ch in ")]}":
            if stack and stack[-1][0] == pairs[ch]:
                stack.pop()
            else:
                out.append((line, col, tr("editor.unexpected_closing_bracket") % ch, "error"))
        if ch == "\n":
            line += 1
            col = 1
        else:
            col += 1
        i += 1
    for ch, l, c in stack[-3:]:
        out.append((l, c, tr("editor.unclosed_bracket") % ch, "error"))
    # простое предупреждение: забытая точка с запятой
    for no, ln in enumerate(text.splitlines(), 1):
        s = ln.strip()
        if re.match(r"^(?:[\w.<>\[\]]+\s+)?[\w.\[\]]+\s*(?:[+\-*/]?=)\s*[^;{}]*[\w)\]\"']$", s) and \
                not s.startswith(("if", "for", "while", "else", "//", "[", "#", "case", "using", "namespace")) \
                and "=>" not in s and not s.endswith(","):
            out.append((no, len(ln) - len(ln.lstrip()) + 1, tr("editor.maybe_you_missed_at_end"), "warning"))
    return out
