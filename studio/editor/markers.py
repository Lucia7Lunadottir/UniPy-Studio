"""Подсветки поверх текста: текущая строка, парные скобки, одинаковые слова, найденное, бледный неиспользуемый
код, строка отладчика; точки останова, полоски Git, символы файла."""
import re
from PyQt6.QtGui import QColor, QTextFormat, QTextCursor, QTextCharFormat
from PyQt6.QtWidgets import QTextEdit
from ..theme import C


class MarkersMixin:
    """Что подсвечено в тексте и на поле."""

    def toggle_breakpoint(self, line=None):
        if self.breakpoints is None:
            return
        self.breakpoints.toggle(self.path, line or self.textCursor().blockNumber() + 1)
        self.gutter.update()

    def set_faded(self, ranges):
        """Неиспользуемые using/import и переменные — бледным (пометка Unnecessary от анализатора), как в VS."""
        if ranges != self.faded:
            self.faded = ranges
            self.update_selections()
            self.refresh_views()

    def _faded_selections(self):
        doc = self.document()
        dim = QColor(C["text"])
        dim.setAlpha(110)                           # бледнее, но читается (VS — около 60% непрозрачности)

        def position(line, col):
            block = doc.findBlockByNumber(line)
            if not block.isValid():
                return None
            return block.position() + min(col, block.length() - 1)
        out = []
        for l1, c1, l2, c2, kind in self.faded[:2000]:
            a, b = position(l1, c1), position(l2, c2)
            if a is None or b is None or b <= a:
                continue
            sel = QTextEdit.ExtraSelection()
            if kind == "unnecessary":
                sel.format.setForeground(dim)
            else:
                sel.format.setFontStrikeOut(True)
            sel.cursor = QTextCursor(doc)
            sel.cursor.setPosition(a)
            sel.cursor.setPosition(b, QTextCursor.MoveMode.KeepAnchor)
            out.append(sel)
        return out

    def set_vcs_markers(self, markers):
        """Полоски изменений на поле (vcs.linediff): зелёная — добавлено, синяя — изменено, красный треугольник — удалено."""
        if markers != self.vcs_markers:
            self.vcs_markers = markers
            self.gutter.update()
            self.refresh_views()

    def set_execution_line(self, line, top=True):
        """Строка, где стоит отладчик (None — убрать стрелку и подсветку)."""
        self.execution_line, self.execution_is_top = line, top
        if line is not None:
            self.folding.ensure_visible(line - 1)
        self.update_selections()
        self.gutter.update()
        self.refresh_views()

    def set_navigation_bar(self, on):
        """Панель «тип / член» над кодом — для языков, где есть символы."""
        self.navbar.setVisible(bool(on) and self.lang in ("python", "csharp"))
        self._update_gutter_width()

    def set_symbols(self, items):
        self.symbols = items or []
        if not self.navbar.isHidden():
            self.navbar.set_symbols(self.symbols)
        self.refresh_views()

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
        sels.extend(self._faded_selections())
        if self.execution_line is not None:
            block = self.document().findBlockByNumber(self.execution_line - 1)
            if block.isValid():
                here = QTextEdit.ExtraSelection()
                back = QColor("#FFE000" if self.execution_is_top else "#7BC27B")
                back.setAlpha(70)
                here.format.setBackground(back)
                here.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
                here.cursor = QTextCursor(block)
                sels.append(here)
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
