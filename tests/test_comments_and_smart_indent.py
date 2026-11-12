import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QTextCursor
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from studio import languages
from studio.editor import CodeEditor
from studio.editor import comments, smart_indent

app = QApplication.instance() or QApplication([])


def apply(lines, edits):
    lines = list(lines)
    for e in edits:
        t = lines[e.line]
        lines[e.line] = t[:e.col] + e.insert + t[e.col + e.remove:]
    return lines


class CommentStyles(unittest.TestCase):
    def test_line_comment_at_leftmost_indent_and_back(self):
        style = comments.LineCommentStyle("//")
        src = ["    if (a)", "", "        b();"]
        done = apply(src, style.edits(src, comments.COMMENT))
        self.assertEqual(done, ["    // if (a)", "", "    //     b();"])
        self.assertEqual(apply(done, style.edits(done, comments.UNCOMMENT)), src)
        self.assertEqual(apply(done, style.edits(done, comments.TOGGLE)), src)

    def test_comment_again_and_uncomment_one_level(self):
        style = comments.LineCommentStyle("#")
        twice = apply(["# x"], style.edits(["# x"], comments.COMMENT))
        self.assertEqual(twice, ["# # x"])
        self.assertEqual(apply(twice, style.edits(twice, comments.UNCOMMENT)), ["# x"])
        self.assertEqual(style.edits(["x = 1"], comments.UNCOMMENT), [])

    def test_block_comment(self):
        style = comments.BlockCommentStyle("<!--", "-->")
        src = ["  <a>", "  </a>"]
        done = apply(src, style.edits(src, comments.COMMENT))
        self.assertEqual(done, ["  <!-- <a>", "  </a> -->"])
        self.assertEqual(apply(done, style.edits(done, comments.TOGGLE)), src)
        one = apply(["<b/>"], style.edits(["<b/>"], comments.COMMENT))
        self.assertEqual(one, ["<!-- <b/> -->"])
        self.assertEqual(apply(one, style.edits(one, comments.UNCOMMENT)), ["<b/>"])

    def test_style_by_language(self):
        self.assertIsInstance(comments.style_for(languages.BY_ID["csharp"]), comments.LineCommentStyle)
        self.assertEqual(comments.style_for(languages.BY_ID["css"]).opening, "/*")
        self.assertIsNone(comments.style_for(languages.PLAIN))


class Rules(unittest.TestCase):
    U = "    "

    def test_brace_rules(self):
        r = smart_indent.BraceRules()
        self.assertEqual(r.newline_indent("    if (a > 0)", "", self.U), "        ")
        self.assertEqual(r.newline_indent("    } else", "", self.U), "        ")
        self.assertEqual(r.newline_indent("        b();", "    if (a)", self.U), "    ")
        self.assertEqual(r.newline_indent("        b();", "    x();", self.U), "        ")
        self.assertEqual(r.newline_indent("    Foo(a); // if (x)", "", self.U), "    ")
        self.assertEqual(r.open_brace_indent("        ", "    if (a)"), "    ")
        self.assertIsNone(r.open_brace_indent("        ", "    x();"))

    def test_colon_rules(self):
        r = smart_indent.ColonRules()
        self.assertEqual(r.newline_indent("    def f():  # note", "", self.U), "        ")
        self.assertEqual(r.newline_indent("        return x", "", self.U), "    ")
        self.assertEqual(r.newline_indent("        passive = 1", "", self.U), "        ")


class EditorHelpers:
    def editor(self, text, path='/tmp/test.cs'):
        services = SimpleNamespace(service_for=lambda language: None, jedi=SimpleNamespace(available=False),
                                   csharp=SimpleNamespace(ready_for=lambda p: None, sync=lambda *a: None))
        ed = CodeEditor(path, services, QFont('monospace', 11))
        ed.request_completion = lambda *a, **k: None
        ed.request_signatures = lambda *a, **k: None
        ed.setPlainText(text)
        ed.moveCursor(QTextCursor.MoveOperation.End)
        ed.show()
        ed.setFocus()
        self.addCleanup(ed.close)
        return ed

    def type(self, ed, *parts):
        for part in parts:
            if part == "\n":
                QTest.keyClick(ed, Qt.Key.Key_Return)
            else:
                QTest.keyClicks(ed, part)


class Typing(EditorHelpers, unittest.TestCase):
    def test_braces_expand_and_embedded_statement(self):
        ed = self.editor('class A')
        self.type(ed, "\n", "{", "\n", "void F()", "\n", "{", "\n", "if (a)", "\n", "b();", "\n", "c();")
        self.assertEqual(ed.toPlainText(),
                         'class A\n{\n    void F()\n    {\n        if (a)\n            b();\n        c();\n    }\n}')

    def test_brace_under_if_aligns_with_if(self):
        ed = self.editor('    if (a)')
        self.type(ed, "\n", "{", "\n", "x();")
        self.assertEqual(ed.toPlainText(), '    if (a)\n    {\n        x();\n    }')

    def test_enter_moves_text_without_leading_spaces(self):
        ed = self.editor('    Foo(a,   b);')
        c = ed.textCursor(); c.setPosition(len('    Foo(a,')); ed.setTextCursor(c)
        self.type(ed, "\n")
        self.assertEqual(ed.toPlainText(), '    Foo(a,\n    b);')

    def test_python_dedent_after_return(self):
        ed = self.editor('def f():', '/tmp/test.py')
        self.type(ed, "\n", "return 1", "\n", "x")
        self.assertEqual(ed.toPlainText(), 'def f():\n    return 1\nx')

    def test_comment_commands_and_single_undo(self):
        ed = self.editor('void F()\n{\n    x();\n}')
        ed.selectAll()
        ed.comment_selection()
        self.assertEqual(ed.toPlainText(), '// void F()\n// {\n//     x();\n// }')
        ed.uncomment_selection()
        self.assertEqual(ed.toPlainText(), 'void F()\n{\n    x();\n}')
        ed.undo()
        self.assertEqual(ed.toPlainText(), '// void F()\n// {\n//     x();\n// }')

    def test_ctrl_slash_on_russian_layout_key(self):
        ed = self.editor('x();')
        # в русской раскладке клавиша «/» приходит как «.» — узнаём её по коду клавиши
        from PyQt6.QtGui import QKeyEvent
        from PyQt6.QtCore import QEvent
        e = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Period, Qt.KeyboardModifier.ControlModifier,
                      61, 0, 0, ".")
        ed.keyPressEvent(e)
        self.assertEqual(ed.toPlainText(), '// x();')



class UndoKeepsSelection(EditorHelpers, unittest.TestCase):
    TEXT = '    a();\n    b();\n    c();\n    d();'

    def selected(self):
        ed = self.editor(self.TEXT)
        c = ed.textCursor(); c.setPosition(13); c.setPosition(29, QTextCursor.MoveMode.KeepAnchor)
        ed.setTextCursor(c)
        return ed

    def assert_restored(self, ed):
        self.assertEqual(ed.toPlainText(), self.TEXT)
        self.assertEqual((ed.textCursor().anchor(), ed.textCursor().position()), (13, 29))

    def test_ctrl_z_after_comment_and_tab(self):
        for act in (lambda ed: ed.comment_selection(), lambda ed: QTest.keyClick(ed, Qt.Key.Key_Tab),
                    lambda ed: QTest.keyClick(ed, Qt.Key.Key_Backtab)):
            ed = self.selected()
            act(ed)
            QTest.keyClick(ed, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
            self.assert_restored(ed)

    def test_lsp_edits_undo_keeps_caret_off_file_start(self):
        ed = self.editor(self.TEXT)
        c = ed.textCursor(); c.setPosition(20); ed.setTextCursor(c)
        ed.apply_edits([(0, 4, 0, 5, 'A')])
        ed.undo()
        self.assertEqual(ed.textCursor().position(), 20)


class MultipleCarets(EditorHelpers, unittest.TestCase):
    def carets_at_line_ends(self, text, path='/tmp/test.cs'):
        """Курсор в конце каждой строки (основной — на последней)."""
        ed = self.editor(text, path)
        doc = ed.document()
        ends = []
        for n in range(doc.blockCount()):
            c = QTextCursor(doc.findBlockByNumber(n))
            c.movePosition(QTextCursor.MoveOperation.EndOfBlock)
            ends.append(c)
        ed._set_carets(ends[-1], ends[:-1])
        return ed

    def test_braces_and_enter_in_every_caret(self):
        ed = self.carets_at_line_ends('    void A()\n    void B()')
        self.type(ed, "\n", "{", "\n", "x();")
        self.assertEqual(ed.toPlainText(),
                         '    void A()\n    {\n        x();\n    }\n    void B()\n    {\n        x();\n    }')
        ed.undo()       # всё набранное в нескольких курсорах — отменяется по шагу на клавишу, не по курсору
        self.assertNotIn('x();', ed.toPlainText())

    def test_embedded_statement_and_backspace_pair(self):
        ed = self.carets_at_line_ends('    if (a)\n    if (b)')
        self.type(ed, "\n", "(")
        self.assertEqual(ed.toPlainText(), '    if (a)\n        ()\n    if (b)\n        ()')
        QTest.keyClick(ed, Qt.Key.Key_Backspace)
        self.assertEqual(ed.toPlainText(), '    if (a)\n        \n    if (b)\n        ')

    def test_comment_lines_of_all_carets(self):
        ed = self.carets_at_line_ends('a();\nb();\nc();')
        ed.comment_selection()
        self.assertEqual(ed.toPlainText(), '// a();\n// b();\n// c();')
        QTest.keyClick(ed, Qt.Key.Key_Slash, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(ed.toPlainText(), 'a();\nb();\nc();')
        self.assertEqual(len(ed.all_carets()), 3)       # Ctrl+/ не сбрасывает курсоры


if __name__ == '__main__':
    unittest.main()
