import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QTextCursor
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from studio import indentation
from studio.editor import CodeEditor
from studio.indentation import IndentStyle

app = QApplication.instance() or QApplication([])


class Detect(unittest.TestCase):
    def test_tabs_spaces_and_size(self):
        self.assertTrue(indentation.detect("class A\n{\n\tvoid M()\n\t{\n\t\tx();\n\t}\n}\n").use_tabs)
        two = indentation.detect("def f():\n  if x:\n    y()\n  return 1\n")
        self.assertEqual((two.use_tabs, two.size), (False, 2))
        self.assertIsNone(indentation.detect("a\nb\n"))
        doc = indentation.detect("/**\n * doc\n */\nclass A {\n    int x;\n}\n")   # « * » в комментарии — не отступ 1
        self.assertEqual(doc.size, 4)


class EditorConfig(unittest.TestCase):
    def setUp(self):
        # своя папка-родитель: .editorconfig «выше root» пишется в неё, а не в общий /tmp
        self.root = Path(tempfile.mkdtemp()) / "project"
        (self.root / "Assets" / "Scripts").mkdir(parents=True)

    def test_sections_root_and_precedence(self):
        (self.root.parent / ".editorconfig").write_text("[*]\nindent_style = tab\n")     # выше root — не читается
        (self.root / ".editorconfig").write_text(
            "root = true\n[*]\nindent_style = space\nindent_size = 4\n"
            "[*.{cs,shader}]\nindent_size = 2\ntrim_trailing_whitespace = true\n"
            "[Assets/**.py]\nindent_style = tab\n")
        (self.root / "Assets" / ".editorconfig").write_text("[Scripts/*.cs]\ninsert_final_newline = true\n")
        cs = self.root / "Assets" / "Scripts" / "Player.cs"
        props = indentation.editorconfig_for(str(cs))
        self.assertEqual(props["indent_style"], "space")
        self.assertEqual(props["indent_size"], "2")
        self.assertEqual(props["insert_final_newline"], "true")
        style = indentation.resolve(str(cs), "\tx", IndentStyle())
        self.assertEqual((style.use_tabs, style.size, style.source, style.trim_trailing, style.final_newline),
                         (False, 2, "editorconfig", True, True))
        py = indentation.resolve(str(self.root / "Assets" / "Scripts" / "tool.py"), "", IndentStyle())
        self.assertTrue(py.use_tabs)
        other = indentation.resolve(str(self.root / "README.md"), "", IndentStyle(size=3))
        self.assertEqual((other.size, other.use_tabs), (4, False))

    def test_glob_patterns(self):
        m = indentation.section_matches
        self.assertTrue(m("*.cs", "Assets/A.cs"))
        self.assertTrue(m("{*.cs,*.py}", "x/y.py"))
        self.assertTrue(m("file{1..3}.txt", "file2.txt"))
        self.assertFalse(m("file{1..3}.txt", "file4.txt"))
        self.assertTrue(m("src/**/*.cs", "src/a/b/C.cs"))
        self.assertFalse(m("src/*.cs", "src/a/C.cs"))
        self.assertTrue(m("[!a]*.cs", "B.cs"))

    def test_without_editorconfig_falls_back(self):
        style = indentation.resolve(str(self.root / "x.cs"), "\tfoo()", IndentStyle(), use_editorconfig=False)
        self.assertEqual((style.use_tabs, style.source), (True, "file"))
        style = indentation.resolve(str(self.root / "x.cs"), "\tfoo()", IndentStyle(size=3), False, adapt=False)
        self.assertEqual((style.size, style.source), (3, "options"))

    def test_save_rules(self):
        s = IndentStyle(trim_trailing=True, final_newline=True)
        self.assertEqual(indentation.apply_save_rules("a  \nb\t", s), "a\nb\n")
        self.assertEqual(indentation.apply_save_rules("a\n\n", IndentStyle(final_newline=False)), "a")


class EditorIndent(unittest.TestCase):
    def editor(self, text, style, path="/tmp/t.cs"):
        services = SimpleNamespace(service_for=lambda language: None, jedi=SimpleNamespace(available=False),
                                   csharp=SimpleNamespace(ready_for=lambda p: None, sync=lambda *a: None))
        ed = CodeEditor(path, services, QFont("monospace", 11))
        ed.set_indent_style(style)
        ed.setPlainText(text)
        ed.moveCursor(QTextCursor.MoveOperation.End)
        ed.show()
        app.processEvents()
        self.addCleanup(ed.close)
        return ed

    def test_tabs_style_enter_tab_and_backtab(self):
        ed = self.editor("void M()\n{", IndentStyle(use_tabs=True))
        QTest.keyClick(ed, Qt.Key.Key_Return)
        self.assertEqual(ed.toPlainText(), "void M()\n{\n\t")
        QTest.keyClick(ed, Qt.Key.Key_Tab)
        self.assertEqual(ed.toPlainText().split("\n")[-1], "\t\t")
        QTest.keyClick(ed, Qt.Key.Key_Backtab)
        self.assertEqual(ed.toPlainText().split("\n")[-1], "\t")

    def test_two_space_style_tab_and_backspace(self):
        ed = self.editor("x", IndentStyle(size=2))
        ed.moveCursor(QTextCursor.MoveOperation.StartOfLine)
        QTest.keyClick(ed, Qt.Key.Key_Tab)
        QTest.keyClick(ed, Qt.Key.Key_Tab)
        self.assertEqual(ed.toPlainText(), "    x")
        QTest.keyClick(ed, Qt.Key.Key_Backspace)
        self.assertEqual(ed.toPlainText(), "  x")

    def test_save_trims_and_keeps_undo(self):
        path = Path(tempfile.mkdtemp()) / "a.py"
        path.write_text("x = 1   \n")
        ed = self.editor("", IndentStyle(trim_trailing=True, final_newline=True), str(path))
        ed.load()
        ed.set_indent_style(IndentStyle(trim_trailing=True, final_newline=True))
        ed.document().setModified(True)
        self.assertTrue(ed.save())
        self.assertEqual(path.read_text(), "x = 1\n")
        self.assertEqual(ed.toPlainText(), "x = 1\n")


if __name__ == "__main__":
    unittest.main()
