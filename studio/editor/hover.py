"""Мышь: Ctrl+наведение (ссылка), Ctrl+клик (F12), справка при наведении, значение при отладке, «…» свёрнутых блоков,
боковые кнопки (назад/вперёд), масштаб колесом."""
import html
import re
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter, QTextCursor
from PyQt6.QtWidgets import QToolTip
from ..theme import C
from .completion import CSHARP


class HoverMixin:
    """Мышь и всплывающая справка."""

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
        line = self.folding.box_at(e.position()) if e.button() == Qt.MouseButton.LeftButton else None
        if line is not None:                    # щелчок по «…» — развернуть
            self.folding.set_collapsed(line, False)
            return
        # Ctrl+клик — перейти к определению, как в Visual Studio
        if e.button() == Qt.MouseButton.LeftButton and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.setTextCursor(self.cursorForPosition(e.position().toPoint()))
            self._update_link(None, False)
            self.go_to_definition()
            return
        super().mousePressEvent(e)

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.folding.collapsed_lines():
            p = QPainter(self.viewport())
            self.folding.paint_boxes(p, {"border": C["gutter_num"], "back": C["editor"], "text": C["dim"]})
            p.end()

    def mouseDoubleClickEvent(self, e):
        line = self.folding.box_at(e.position()) if e.button() == Qt.MouseButton.LeftButton else None
        if line is not None:                    # двойной щелчок по «…» — развернуть
            self.folding.set_collapsed(line, False)
            return
        super().mouseDoubleClickEvent(e)

    def mouseMoveEvent(self, e):
        self._update_link(e.position().toPoint(), bool(e.modifiers() & Qt.KeyboardModifier.ControlModifier))
        line = self.folding.box_at(e.position()) if self.folding.collapsed_lines() else None
        if line is not None:                    # над «…» — показать свёрнутый текст, как VS
            QToolTip.showText(e.globalPosition().toPoint(), self.folding.hidden_text(line), self)
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
        if self.debug_hover is not None and self._debug_tip(cur):
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

    def _debug_tip(self, cur):
        """Во время остановки — значение выражения под мышью (DataTip VS): a, self.hp, player.transform.position."""
        block = cur.block().text()
        col = cur.positionInBlock()
        m = re.search(r"[A-Za-z_][\w.]*$", block[:col] + re.match(r"\w*", block[col:]).group())
        if not m or m.group().endswith("."):
            return False
        expression = m.group()
        pos = self._hover_pos

        def show(text):
            if text and self.underMouse() and self._hover_pos == pos:
                QToolTip.showText(self.mapToGlobal(pos), self._tip_html(
                    "<b>%s</b> = %s" % (html.escape(expression), html.escape(text))), self)
        return self.debug_hover(expression, show)

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
