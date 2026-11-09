import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from types import SimpleNamespace

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QFont, QGuiApplication, QTextCursor
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from studio.editor import CodeEditor

app = QApplication.instance() or QApplication([])
ALT, SHIFT, CTRL = (Qt.KeyboardModifier.AltModifier, Qt.KeyboardModifier.ShiftModifier,
                    Qt.KeyboardModifier.ControlModifier)


def services():
    return SimpleNamespace(service_for=lambda language: None, jedi=SimpleNamespace(available=False),
                           csharp=SimpleNamespace(ready_for=lambda p: None, sync=lambda *a: None))


class MultiCursor(unittest.TestCase):
    def make(self, text):
        ed = CodeEditor("/tmp/multi.txt", services(), QFont("monospace", 11))
        self.addCleanup(ed.close)
        ed.setPlainText(text)
        ed.resize(600, 300)
        ed.show()
        QTest.qWaitForWindowExposed(ed)
        return ed

    def put(self, ed, line, col):
        c = QTextCursor(ed.document().findBlockByNumber(line))
        c.setPosition(c.position() + col)
        ed.setTextCursor(c)

    def test_keyboard_box_typing_is_one_undo_step(self):
        ed = self.make("alpha\nbeta\ngamma\n")
        self.put(ed, 0, 0)
        QTest.keyClick(ed, Qt.Key.Key_Down, SHIFT | ALT)               # Shift+Alt+↓ ×2 — столбец на трёх строках
        QTest.keyClick(ed, Qt.Key.Key_Down, SHIFT | ALT)
        self.assertEqual(len(ed.all_carets()), 3)
        QTest.keyClicks(ed, "> ")
        self.assertEqual(ed.toPlainText(), "> alpha\n> beta\n> gamma\n")
        QTest.keyClick(ed, Qt.Key.Key_Backspace)
        self.assertEqual(ed.toPlainText(), ">alpha\n>beta\n>gamma\n")
        ed.undo()                                                       # Ctrl+Z: последнее удаление — разом
        self.assertEqual(ed.toPlainText(), "> alpha\n> beta\n> gamma\n")
        self.assertFalse(ed.has_multiple_carets())                      # после отмены — один курсор

    def test_box_selection_replaces_on_each_line(self):
        ed = self.make("let a = 1;\nlet b = 2;\nlet c = 3;\n")
        self.put(ed, 0, 0)
        for _ in range(3):
            QTest.keyClick(ed, Qt.Key.Key_Right, SHIFT | ALT)           # ширина — 3 символа («let»)
        QTest.keyClick(ed, Qt.Key.Key_Down, SHIFT | ALT)
        QTest.keyClick(ed, Qt.Key.Key_Down, SHIFT | ALT)
        self.assertEqual(sorted(c.selectedText() for c in ed.all_carets()), ["let"] * 3)
        QTest.keyClicks(ed, "var")
        self.assertEqual(ed.toPlainText(), "var a = 1;\nvar b = 2;\nvar c = 3;\n")
        ed.copy()
        self.assertEqual(QGuiApplication.clipboard().text(), "\n\n")    # выделений не осталось — пустые строки
        QTest.keyClick(ed, Qt.Key.Key_Escape)
        self.assertFalse(ed.has_multiple_carets())

    def test_alt_drag_with_mouse(self):
        ed = self.make("one\ntwo\nthree\nfour\n")
        vp = ed.viewport()
        top = ed.cursorRect(QTextCursor(ed.document().findBlockByNumber(0))).center()
        bottom = ed.cursorRect(QTextCursor(ed.document().findBlockByNumber(2))).center()
        QTest.mousePress(vp, Qt.MouseButton.LeftButton, ALT, QPoint(top.x(), top.y()))
        QTest.mouseMove(vp, QPoint(bottom.x(), bottom.y()))
        QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, ALT, QPoint(bottom.x(), bottom.y()))
        self.assertEqual(sorted(c.blockNumber() for c in ed.all_carets()), [0, 1, 2])
        QTest.keyClicks(ed, "#")
        self.assertEqual(ed.toPlainText(), "#one\n#two\n#three\nfour\n")
        QTest.mouseClick(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(top.x(), top.y()))
        self.assertFalse(ed.has_multiple_carets())                      # обычный щелчок — снова один курсор

    def test_ctrl_alt_click_and_occurrences(self):
        ed = self.make("x = foo\ny = foo\nz = foo\n")
        self.put(ed, 0, 5)
        ed.add_next_occurrence()                                        # первое нажатие — выделить слово
        self.assertEqual(ed.textCursor().selectedText(), "foo")
        ed.add_next_occurrence()
        ed.add_next_occurrence()
        self.assertEqual(len(ed.all_carets()), 3)
        ed.add_next_occurrence()                                        # больше нет — не добавляет повтор
        self.assertEqual(len(ed.all_carets()), 3)
        QTest.keyClicks(ed, "bar")
        self.assertEqual(ed.toPlainText(), "x = bar\ny = bar\nz = bar\n")
        ed.clear_carets()

        self.put(ed, 0, 0)
        ed.select_all_occurrences()                                     # «x» — одно вхождение
        self.assertEqual(len(ed.all_carets()), 1)
        vp = ed.viewport()
        p = ed.cursorRect(QTextCursor(ed.document().findBlockByNumber(2))).center()
        QTest.mouseClick(vp, Qt.MouseButton.LeftButton, CTRL | ALT, QPoint(p.x(), p.y()))
        self.assertEqual(len(ed.all_carets()), 2)
        QGuiApplication.clipboard().setText("A\nB")                     # две строки на два курсора — по одной
        ed.paste()
        self.assertEqual(ed.toPlainText().splitlines()[0][:1], "A")
        self.assertEqual(ed.toPlainText().splitlines()[2][:1], "B")
        ed.viewport().grab()                                            # курсоры рисуются


if __name__ == "__main__":
    unittest.main()
