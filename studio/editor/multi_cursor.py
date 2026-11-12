"""Несколько курсоров и прямоугольное выделение, как в Visual Studio.

Alt + мышь или Shift+Alt+стрелки — прямоугольник (набор и удаление — на каждой строке сразу);
Ctrl+Alt + щелчок — добавить или убрать курсор; Shift+Alt+. — выделить следующее такое же слово,
Shift+Alt+; — все такие же; Esc или обычный щелчок — снова один курсор.

Основной курсор — обычный textCursor(); дополнительные — self.carets (QTextCursor сами едут при правках).
Правка во всех курсорах — один шаг отмены. Прямоугольник задаётся X-координатами (в координатах документа),
поэтому табы и шрифт учитываются так же, как на экране.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QGuiApplication, QPainter, QTextCursor, QTextDocument
from PyQt6.QtWidgets import QTextEdit

from ..theme import C
from .text_input import SLASH_KEY_SCANCODE

_ALT = Qt.KeyboardModifier.AltModifier
_SHIFT = Qt.KeyboardModifier.ShiftModifier
_CTRL = Qt.KeyboardModifier.ControlModifier
_ARROWS = (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down)
_MOVES = {Qt.Key.Key_Left: (QTextCursor.MoveOperation.Left, QTextCursor.MoveOperation.WordLeft),
          Qt.Key.Key_Right: (QTextCursor.MoveOperation.Right, QTextCursor.MoveOperation.WordRight),
          Qt.Key.Key_Up: (QTextCursor.MoveOperation.Up, QTextCursor.MoveOperation.Up),
          Qt.Key.Key_Down: (QTextCursor.MoveOperation.Down, QTextCursor.MoveOperation.Down),
          Qt.Key.Key_Home: (QTextCursor.MoveOperation.StartOfBlock, QTextCursor.MoveOperation.Start),
          Qt.Key.Key_End: (QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveOperation.End)}


class MultiCursorMixin:
    carets = ()                 # дополнительные курсоры (список появляется при первом добавлении)
    _box = None                 # (строка-якорь, x якоря, строка-голова, x головы) — прямоугольник
    _box_drag = False

    # --- состояние
    def has_multiple_carets(self):
        return bool(self.carets)

    def clear_carets(self):
        if self.carets or self._box is not None:
            self.carets = []
            self._box = None
            self.update_selections()
            self.viewport().update()

    def all_carets(self):
        return [self.textCursor()] + list(self.carets)

    def _set_carets(self, main, extras):
        self.carets = [c for c in extras if not _same(c, main)]
        unique = []
        for c in self.carets:
            if not any(_same(c, u) for u in unique):
                unique.append(c)
        self.carets = unique
        self.setTextCursor(main)
        self.update_selections()
        self.viewport().update()

    # --- прямоугольник
    def _doc_x(self, view_x):
        return view_x - self.contentOffset().x()

    def _position_at_x(self, block, doc_x):
        layout = block.layout()
        if layout is None or layout.lineCount() == 0:
            return block.position()
        line = layout.lineAt(0)
        return block.position() + line.xToCursor(doc_x)

    def _caret_x(self, cursor):
        block = cursor.block()
        layout = block.layout()
        if layout is None or layout.lineCount() == 0:
            return 0.0
        line = layout.lineForTextPosition(cursor.positionInBlock())
        return line.cursorToX(cursor.positionInBlock())[0] if line.isValid() else 0.0

    def _apply_box(self):
        anchor_line, anchor_x, head_line, head_x = self._box
        doc = self.document()
        step = 1 if head_line >= anchor_line else -1
        cursors = []
        for n in range(anchor_line, head_line + step, step):
            block = doc.findBlockByNumber(n)
            if not block.isValid() or not block.isVisible():
                continue
            c = QTextCursor(doc)
            c.setPosition(self._position_at_x(block, anchor_x))
            c.setPosition(self._position_at_x(block, head_x), QTextCursor.MoveMode.KeepAnchor)
            cursors.append(c)
        if not cursors:
            return
        self._set_carets(cursors[-1], cursors[:-1])    # основной — на строке «головы», как в VS

    def _start_box_here(self):
        c = self.textCursor()
        x = self._caret_x(c)
        self._box = (c.blockNumber(), x, c.blockNumber(), x)

    def _move_box(self, key):
        anchor_line, anchor_x, head_line, head_x = self._box
        space = self.fontMetrics().horizontalAdvance(" ")
        if key == Qt.Key.Key_Left:
            head_x = max(0.0, head_x - space)
        elif key == Qt.Key.Key_Right:
            head_x += space
        else:
            block = self.document().findBlockByNumber(head_line)
            block = block.previous() if key == Qt.Key.Key_Up else block.next()
            while block.isValid() and not block.isVisible():
                block = block.previous() if key == Qt.Key.Key_Up else block.next()
            if block.isValid():
                head_line = block.blockNumber()
        self._box = (anchor_line, anchor_x, head_line, head_x)
        self._apply_box()

    # --- правка во всех курсорах
    def _edit_all(self, action):
        """action(курсор, номер по порядку в тексте) для каждого курсора — одним шагом отмены.
        textCursor() отдаёт копию, поэтому основной курсор берётся один раз и потом ставится обратно."""
        main = self.textCursor()
        ordered = sorted([main] + list(self.carets), key=lambda c: c.selectionStart())
        main.beginEditBlock()
        for n, c in enumerate(ordered):
            action(c, n)
        main.endEditBlock()
        self._box = None
        self._set_carets(main, self.carets)

    def _insert_all(self, text):
        self._edit_all(lambda c, _n: c.insertText(text))

    def _delete_all(self, forward):
        def delete(c, _n):
            if c.hasSelection():
                c.removeSelectedText()
            elif forward:
                c.deleteChar()
            else:
                c.deletePreviousChar()
        self._edit_all(delete)

    def _move_all(self, key, mods):
        ops = _MOVES[key]
        op = ops[1] if mods & _CTRL else ops[0]
        mode = QTextCursor.MoveMode.KeepAnchor if mods & _SHIFT else QTextCursor.MoveMode.MoveAnchor
        main = self.textCursor()
        for c in [main] + list(self.carets):
            c.movePosition(op, mode)
        self._box = None
        self._set_carets(main, self.carets)

    # --- клавиатура
    def keyPressEvent(self, e):
        key, mods, text = e.key(), e.modifiers(), e.text()
        if key in _ARROWS and mods & _ALT and mods & _SHIFT:        # Shift+Alt+стрелки — прямоугольник
            if self._box is None:
                self.carets = []
                self._start_box_here()
            self._move_box(key)
            return
        if not self.carets:
            if self._box is not None and key not in (Qt.Key.Key_Shift, Qt.Key.Key_Alt):
                self._box = None                    # прямоугольник в одну строку — дальше обычный курсор
            super().keyPressEvent(e)
            return
        popup = self.completer.popup()
        if popup.isVisible():
            popup.hide()
        if key == Qt.Key.Key_Escape:
            self.clear_carets()
            return
        if key in _MOVES and not mods & _ALT:
            self._move_all(key, mods)
            return
        if key == Qt.Key.Key_Backspace:            # пары скобок и отступ — как у одного курсора (text_input)
            self._edit_all(lambda c, _n: self.backspace_at(c))
            return
        if key == Qt.Key.Key_Delete:
            self._delete_all(True)
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):    # отступ и раскрытие «{}» в каждом курсоре
            self._edit_all(lambda c, _n: self.newline_at(c))
            return
        if key == Qt.Key.Key_Tab:
            self._insert_all("\t" if self.indent.use_tabs else " " * self.indent.size)
            return
        if text and text.isprintable() and not mods & (_CTRL | _ALT):
            self._edit_all(lambda c, _n: self.type_char_at(c, text))
            return
        if mods & _CTRL and (key == Qt.Key.Key_Slash or e.nativeScanCode() == SLASH_KEY_SCANCODE):
            self.toggle_comment()                   # строки всех курсоров; курсоры остаются
            return
        if key in (Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Control, Qt.Key.Key_Meta):
            return
        self.clear_carets()                         # остальные команды — для одного курсора
        super().keyPressEvent(e)

    # --- мышь
    def mousePressEvent(self, e):
        mods = e.modifiers()
        if e.button() == Qt.MouseButton.LeftButton and mods & _ALT:
            pos = e.position().toPoint()
            clicked = self.cursorForPosition(pos)
            if mods & _CTRL:                        # Ctrl+Alt+щелчок — добавить или убрать курсор
                existing = [c for c in self.all_carets() if c.position() == clicked.position()]
                if existing and len(self.all_carets()) > 1:
                    rest = [c for c in self.all_carets() if c.position() != clicked.position()]
                    self._set_carets(rest[0], rest[1:])
                else:
                    self._set_carets(self.textCursor(), list(self.carets) + [clicked])
                return
            x = self._doc_x(e.position().x())       # Alt+мышь — прямоугольное выделение
            line = clicked.blockNumber()
            self._box = (line, x, line, x)
            self._box_drag = True
            self.carets = []
            self._apply_box()
            return
        if e.button() == Qt.MouseButton.LeftButton:
            self.clear_carets()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._box_drag and e.buttons() & Qt.MouseButton.LeftButton:
            anchor_line, anchor_x, _h, _hx = self._box
            head = self.cursorForPosition(e.position().toPoint()).blockNumber()
            self._box = (anchor_line, anchor_x, head, max(0.0, self._doc_x(e.position().x())))
            self._apply_box()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._box_drag:
            self._box_drag = False
            return
        super().mouseReleaseEvent(e)

    # --- буфер обмена (команды окна зовут copy/cut/paste редактора)
    def copy(self):
        if not self.carets:
            return super().copy()
        QGuiApplication.clipboard().setText("\n".join(c.selectedText() for c in self._ordered()))

    def cut(self):
        if not self.carets:
            return super().cut()
        self.copy()
        self._edit_all(lambda c, _n: c.removeSelectedText())

    def paste(self):
        if not self.carets:
            return super().paste()
        text = QGuiApplication.clipboard().text().replace("\r\n", "\n")
        parts = text.split("\n")
        if len(parts) == len(self.all_carets()):    # строк столько же, сколько курсоров — по одной в каждый
            self._edit_all(lambda c, n: c.insertText(parts[n]))
        else:
            self._insert_all(text)

    def undo(self):
        self.clear_carets()
        super().undo()

    def redo(self):
        self.clear_carets()
        super().redo()

    def _ordered(self):
        return sorted(self.all_carets(), key=lambda c: c.selectionStart())

    # --- одинаковые слова (Shift+Alt+. / Shift+Alt+;)
    def _occurrence_text(self):
        main = self.textCursor()
        if not main.hasSelection():
            main.select(QTextCursor.SelectionType.WordUnderCursor)
            if main.hasSelection():
                self.setTextCursor(main)            # первое нажатие только выделяет слово, как в VS
            return None
        return main.selectedText()

    def add_next_occurrence(self):
        text = self._occurrence_text()
        if not text:
            return
        doc = self.document()
        flags = QTextDocument.FindFlag.FindCaseSensitively
        taken = self.all_carets()
        start = max(c.selectionEnd() for c in taken)
        found = doc.find(text, start, flags)
        if found.isNull():
            found = doc.find(text, 0, flags)        # по кругу с начала файла
        if found.isNull() or any(_same(found, c) for c in taken):
            return                                  # все совпадения уже выбраны
        self._set_carets(found, taken)
        self.ensureCursorVisible()

    def select_all_occurrences(self):
        text = self._occurrence_text() or self.textCursor().selectedText()
        if not text:
            return
        doc = self.document()
        flags = QTextDocument.FindFlag.FindCaseSensitively
        main = self.textCursor()
        found, c = [], doc.find(text, 0, flags)
        while not c.isNull() and len(found) < 2000:
            found.append(c)
            c = doc.find(text, c, flags)
        self._set_carets(main, [f for f in found if not _same(f, main)])

    # --- вид
    def multi_caret_selections(self):
        """Выделения дополнительных курсоров (основное рисует сам QPlainTextEdit) — для update_selections."""
        out = []
        back = QColor(C["select"])
        for c in self.carets:
            if c.hasSelection():
                s = QTextEdit.ExtraSelection()
                s.format.setBackground(back)
                s.cursor = c
                out.append(s)
        return out

    def paintEvent(self, e):
        super().paintEvent(e)
        if not self.carets:
            return
        p = QPainter(self.viewport())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(C["text"]))
        width = self.cursorWidth()
        for c in self.carets:
            r = self.cursorRect(c)
            if r.intersects(e.rect()):
                p.drawRect(r.x(), r.y(), width, r.height())
        p.end()


def _same(a, b):
    return a.position() == b.position() and a.anchor() == b.anchor()

