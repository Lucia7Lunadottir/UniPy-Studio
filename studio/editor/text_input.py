"""Набор текста как в Visual Studio: отступы, парные скобки, умный Backspace, комментарии, дублирование строки."""
import re
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor, QTextCursor


class TypingMixin:
    """Клавиатура и правки при наборе."""

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
        if self.lang in ("csharp", "python") and not self.isReadOnly():
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

    def focusNextPrevChild(self, next):
        # Tab belongs to the editor, including while the completion popup is open.
        return False
