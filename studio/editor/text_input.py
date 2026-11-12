"""Набор текста как в Visual Studio: отступы, парные скобки, умный Backspace, комментарии, дублирование строки."""
import re
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor, QKeySequence, QTextCursor
from . import comments, smart_indent

_SMART_CHARS = "([{)]}\"'"   # скобки и кавычки: пара, перескок через закрывающую, «{» под if

SLASH_KEY_SCANCODE = 61     # клавиша «/» (xkb); в русской раскладке она даёт «.», и Ctrl+/ приходил бы как Ctrl+.


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
        if ctrl and (key == Qt.Key.Key_Slash or e.nativeScanCode() == SLASH_KEY_SCANCODE):
            self.toggle_comment()
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
        if e.matches(QKeySequence.StandardKey.Undo):    # Qt отменил бы сам, мимо undo() (выделение на место)
            self.undo()
            return
        if e.matches(QKeySequence.StandardKey.Redo):
            self.redo()
            return
        if ctrl and key == Qt.Key.Key_D:
            self.duplicate_line()
            return
        if key == Qt.Key.Key_F12 and not mods & Qt.KeyboardModifier.ShiftModifier:
            self.go_to_definition()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not ctrl:
            self._at_caret(self.newline_at)
            return
        if key == Qt.Key.Key_Tab:
            self._indent(+1)
            return
        if key == Qt.Key.Key_Backtab:
            self._indent(-1)
            return
        if key == Qt.Key.Key_Backspace and self._at_caret(self._smart_backspace_at):
            return
        if text and text in _SMART_CHARS and self._at_caret(self._smart_char_at, text):
            pass
        else:
            super().keyPressEvent(e)
            if text == "}" and self.language.braces:
                self._at_caret(self._dedent_brace_at)

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

    def _previous_code_line(self, block):
        """Ближайшая строка выше block, где что-то написано ("" — таких нет)."""
        b = block.previous()
        while b.isValid() and not b.text().strip():
            b = b.previous()
        return b.text() if b.isValid() else ""

    def _at_caret(self, action, *args):
        """action(курсор, …) для основного курсора; курсор потом ставится в редактор."""
        c = self.textCursor()
        result = action(c, *args)
        self.setTextCursor(c)
        return result

    # Правки «в курсоре c» — одни и те же для одного курсора и для каждого из нескольких (multi_cursor).
    def newline_at(self, c):
        """Enter: отступ по правилам языка (smart_indent); между «{» и «}» — блок из трёх строк, как в VS."""
        if c.hasSelection():
            c.removeSelectedText()
        line = c.block().text()
        before = line[:c.positionInBlock()]
        after = line[c.positionInBlock():]
        rules = smart_indent.rules_for(self.language)
        unit = self.indent.unit()
        indent = rules.newline_indent(before, self._previous_code_line(c.block()), unit)
        c.beginEditBlock()
        moved = after.lstrip(" \t")
        if len(moved) != len(after):        # пробелы перед переносимым текстом не тянем на новую строку
            c.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, len(after) - len(moved))
            c.removeSelectedText()
        own = smart_indent.leading(before)
        pair = before.rstrip()[-1:] + moved[:1]
        if rules.expands_pairs and pair in ("{}", "[]", "()") and indent != own:
            c.insertText("\n" + indent + "\n" + own)
            c.movePosition(QTextCursor.MoveOperation.Up)
            c.movePosition(QTextCursor.MoveOperation.EndOfLine)
        else:
            c.insertText("\n" + indent)
        c.endEditBlock()

    def type_char_at(self, c, ch):
        """Набор символа в курсоре c со всеми удобствами (для нескольких курсоров)."""
        if not (ch in _SMART_CHARS and self._smart_char_at(c, ch)):
            c.insertText(ch)
            if ch == "}" and self.language.braces:
                self._dedent_brace_at(c)

    def backspace_at(self, c):
        if c.hasSelection():
            c.removeSelectedText()
        elif not self._smart_backspace_at(c):
            c.deletePreviousChar()

    def _smart_char_at(self, c, ch):
        """Скобка/кавычка: True — уже обработана (пара или перескок), False — вставить как обычный символ."""
        if ch == "{":
            self._place_open_brace_at(c)
        if ch in "([{\"'" and self._auto_pair_at(c, ch):
            return True
        return ch in ")]}\"'" and self._skip_closing_at(c, ch)

    def _place_open_brace_at(self, c):
        """«{» в пустой строке под `if (…)` встаёт вровень с if, а не на уровень тела."""
        if c.hasSelection():
            return
        before = c.block().text()[:c.positionInBlock()]
        target = smart_indent.rules_for(self.language).open_brace_indent(before, self._previous_code_line(c.block()))
        if target is None or target == before:
            return
        c.movePosition(QTextCursor.MoveOperation.StartOfBlock, QTextCursor.MoveMode.KeepAnchor)
        c.insertText(target)

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
            def indent(cur):
                for n in range(start, end + 1):
                    cur.setPosition(self.document().findBlockByNumber(n).position())
                    cur.insertText(self.indent.unit())
            self.edit_lines(indent)
        else:
            self._dedent_lines(start, end)

    def _dedent_lines(self, start, end):
        def dedent(cur):
            for n in range(start, end + 1):
                b = self.document().findBlockByNumber(n)
                leading = b.text()[:len(b.text()) - len(b.text().lstrip(" \t"))]
                k = self.indent.dedent(leading)
                if leading and k:
                    cur.setPosition(b.position())
                    cur.setPosition(b.position() + k, QTextCursor.MoveMode.KeepAnchor)
                    cur.removeSelectedText()
        self.edit_lines(dedent)

    def edit_lines(self, action):
        """action(курсор) правит строки одним шагом отмены; Ctrl+Z потом возвращает курсор и выделение
        на место, как в VS. Курсор правки — копия курсора редактора: Qt запоминает, где стоял курсор
        в начале шага, и ставит туда курсор после отмены (с курсором в начале файла улетал бы в начало)."""
        doc = self.document()
        main = self.textCursor()
        before = (main.anchor(), main.position())   # числа: сам курсор уехал бы вместе с правкой
        key = (doc.availableUndoSteps(), doc.characterCount())   # каким документ станет после отмены
        cur = self.textCursor()
        cur.beginEditBlock()
        action(cur)
        cur.endEditBlock()
        if (doc.availableUndoSteps(), doc.characterCount()) != key:
            self._selection_before_edit = (key, *before)

    def undo(self):
        super().undo()
        saved = getattr(self, "_selection_before_edit", None)
        doc = self.document()
        if saved and saved[0] == (doc.availableUndoSteps(), doc.characterCount()):
            c = self.textCursor()
            c.setPosition(saved[1])
            c.setPosition(saved[2], QTextCursor.MoveMode.KeepAnchor)
            self.setTextCursor(c)

    def _smart_backspace_at(self, c):
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

    def _auto_pair_at(self, c, ch):
        pairs = {"(": ")", "[": "]", "{": "}", '"': '"', "'": "'"}
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
        return True

    def _skip_closing_at(self, c, ch):
        after = c.block().text()[c.positionInBlock():]
        if after[:1] == ch:
            c.movePosition(QTextCursor.MoveOperation.Right)
            return True
        return False

    def _dedent_brace_at(self, c):
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

    def toggle_comment(self):
        self._comment_lines(comments.TOGGLE)

    def comment_selection(self):
        """Ctrl+K, Ctrl+C — закомментировать строки выделения (уже закомментированные — ещё раз, как в VS)."""
        self._comment_lines(comments.COMMENT)

    def uncomment_selection(self):
        """Ctrl+K, Ctrl+U — снять по одному комментарию со строк выделения."""
        self._comment_lines(comments.UNCOMMENT)

    def _comment_lines(self, mode):
        style = comments.style_for(self.language)
        if style is None or self.isReadOnly():
            return
        doc = self.document()
        numbers = set()         # строки всех курсоров (несколько курсоров — каждая строка один раз)
        for c in self.all_carets():
            start = doc.findBlock(c.selectionStart()).blockNumber()
            end = doc.findBlock(max(c.selectionStart(), c.selectionEnd() - (1 if c.hasSelection() else 0))).blockNumber()
            numbers.update(range(start, end + 1))
        blocks = [doc.findBlockByNumber(n) for n in sorted(numbers)]
        edits = style.edits([b.text() for b in blocks], mode)
        if not edits:
            return
        def apply(cur):
            for edit in edits:
                pos = blocks[edit.line].position() + edit.col
                cur.setPosition(pos)
                cur.setPosition(pos + edit.remove, QTextCursor.MoveMode.KeepAnchor)
                cur.insertText(edit.insert)
        self.edit_lines(apply)

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
