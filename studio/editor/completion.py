"""Подсказки (IntelliSense): список с фильтрами по видам, справка к выбранному, вставка (с using/import),
подсказка параметров. Источники — анализатор (LSP), jedi, свой разбор C#."""
import re
from PyQt6.QtCore import Qt, QEvent, QRect, QSize, QPoint
from PyQt6.QtGui import QColor, QFont, QTextCursor, QStandardItem, QFontMetrics
from PyQt6.QtWidgets import QWidget, QLabel, QStyledItemDelegate, QToolTip, QStyle, QApplication, QListView, QToolButton, QHBoxLayout
from ..lsp import markdown_to_html as lsp_markdown_to_html
from ..intellisense import CSharpProvider, signature_html, hover_html, word_items, KIND_GROUPS, arrange, present_groups
from .. import theme
from ..theme import C, kind_icon
from ..i18n import tr


CSHARP = CSharpProvider()


DEFAULT_HINT_PT = 11    # размер подсказок (список, параметры, справка); свой масштаб, отдельно от кода


ROLE_DETAIL = Qt.ItemDataRole.UserRole + 1


ROLE_DOC = Qt.ItemDataRole.UserRole + 2


ROLE_ITEM = Qt.ItemDataRole.UserRole + 3     # сам элемент подсказки (Item)


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


class CompletionMixin:
    """Список подсказок и подсказка параметров."""

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
        if self.insights is not None and self.insights.handles(kind):     # семантика, подсказки в строке, CodeLens
            self.insights.on_result(res)
            return
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

    @staticmethod
    def _text_before_cursor(cursor):
        start = QTextCursor(cursor)
        start.movePosition(QTextCursor.MoveOperation.StartOfBlock, QTextCursor.MoveMode.KeepAnchor)
        return start.selectedText()

    def _request_index(self, base=None):
        self.services.csharp_index.submit(req=self._req, editor=id(self), code=self.toPlainText(),
                                          pos=self._source_offset(), path=self.path, base=base)
