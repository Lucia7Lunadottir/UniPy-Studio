import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication

from studio import languages, symbols
from studio.editor import CodeEditor
from studio.folding import fold_ranges, FoldRange, MEMBER, TYPE, REGION, IMPORTS, COMMENT, NAMESPACE
from studio.goto_all import parse_query

app = QApplication.instance() or QApplication([])

UNITY = '''using System;
using UnityEngine;

namespace Game
{
    /// <summary>
    /// Игрок
    /// </summary>
    public class Player : MonoBehaviour
    {
        #region Fields
        int hp;
        string s = "{ не скобка";
        #endregion

        void Update()
        {
            if (hp > 0) {
                hp--;
            }
        }
    }
}
'''


def services():
    return SimpleNamespace(service_for=lambda language: None, jedi=SimpleNamespace(available=False),
                           csharp=SimpleNamespace(ready_for=lambda p: None, sync=lambda *a: None))


def spin(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()


class Ranges(unittest.TestCase):
    def test_csharp_unity_style(self):
        found = {(r.start, r.end, r.kind) for r in fold_ranges(UNITY, languages.for_path("P.cs"))}
        self.assertEqual(found, {(0, 1, IMPORTS), (3, 22, NAMESPACE), (5, 7, COMMENT), (8, 21, TYPE),
                                 (10, 13, REGION), (15, 20, MEMBER), (17, 19, "block")})

    def test_python_ast_and_broken_file(self):
        src = "import os\nimport sys\n\nclass A:\n    def f(self):\n        if x:\n            return 1\n"
        found = {(r.start, r.end, r.kind) for r in fold_ranges(src, languages.for_path("a.py"))}
        self.assertIn((3, 6, TYPE), found)
        self.assertIn((4, 6, MEMBER), found)
        self.assertIn((0, 1, IMPORTS), found)
        broken = "def f(:\n    x = 1\n    y = 2\n"
        self.assertEqual(fold_ranges(broken, languages.for_path("a.py")), [FoldRange(0, 2, MEMBER)])

    def test_xml_json_markdown(self):
        self.assertEqual(len(fold_ranges("<a>\n<b/>\n</a>", languages.for_path("a.xml"))), 1)
        self.assertEqual(len(fold_ranges('{\n "a": [\n 1\n ]\n}', languages.for_path("a.json"))), 2)
        md = "# A\ntext\n## B\nmore\n# C\n"
        self.assertEqual([(r.start, r.end) for r in fold_ranges(md, languages.for_path("a.md"))], [(0, 3), (2, 3)])


class EditorFolding(unittest.TestCase):
    def editor(self, text, name="P.cs"):
        path = Path(tempfile.mkdtemp()) / name
        path.write_text(text)
        ed = CodeEditor(str(path), services(), QFont("monospace", 11))
        ed.resize(600, 400)
        ed.load()
        ed.show()
        app.processEvents()
        self.addCleanup(ed.close)
        return ed

    def visible(self, ed):
        return [ed.document().findBlockByNumber(i).isVisible() for i in range(ed.blockCount())]

    def test_collapse_keep_on_edit_and_expand_on_goto(self):
        ed = self.editor(UNITY)
        ed.folding.collapse_to_definitions()
        self.assertEqual(ed.folding.collapsed_lines(), [0, 5, 10, 15])
        self.assertFalse(self.visible(ed)[16])
        ed.textCursor().insertText("// новая строка\n")        # курсор в начале — всё сдвигается на строку
        spin(0.4)
        self.assertEqual(ed.folding.collapsed_lines(), [1, 6, 11, 16])
        ed.go_to_line(18)                                       # внутрь свёрнутого Update()
        self.assertNotIn(16, ed.folding.collapsed_lines())
        self.assertTrue(self.visible(ed)[17])
        ed.folding.toggle_all()
        self.assertTrue(all(self.visible(ed)))

    def test_folding_keeps_scroll_position(self):
        """Регрессия: сворачивание прокручивало вид к курсору — наверх файла."""
        body = "".join("    void M%d()\n    {\n        x();\n        y();\n    }\n\n" % i for i in range(60))
        ed = self.editor("public class P\n{\n" + body + "}\n")
        bar = ed.verticalScrollBar()
        bar.setValue(150)                       # колёсиком вниз; курсор остался на 1-й строке
        app.processEvents()
        first = ed.firstVisibleBlock().blockNumber()
        r = ed.folding.innermost(first + 3)
        ed.folding.toggle(r.start)
        app.processEvents()
        self.assertEqual(ed.firstVisibleBlock().blockNumber(), first)
        ed.folding.toggle(r.start)
        app.processEvents()
        self.assertEqual(ed.firstVisibleBlock().blockNumber(), first)
        ed.folding.collapse_to_definitions()   # первая строка экрана уходит в блок — держимся за заголовок
        app.processEvents()
        self.assertLessEqual(abs(ed.firstVisibleBlock().blockNumber() - first), 5)
        self.assertGreater(bar.value(), 0)

    def test_box_click_and_disable(self):
        ed = self.editor(UNITY)
        ed.folding.toggle(15)
        box = ed.folding.collapsed_box(ed.document().findBlockByNumber(15))
        self.assertIsNotNone(box)
        self.assertEqual(ed.folding.box_at(box.center()), 15)
        self.assertIn("if (hp > 0)", ed.folding.hidden_text(15))
        ed.folding.set_enabled(False)
        self.assertTrue(all(self.visible(ed)))
        self.assertEqual(ed.folding.ranges, [])


class NavigationBarTests(unittest.TestCase):
    def test_follows_cursor_and_jumps(self):
        from studio.panels import outline
        path = Path(tempfile.mkdtemp()) / "P.cs"
        path.write_text(UNITY)
        ed = CodeEditor(str(path), services(), QFont("monospace", 11))
        ed.resize(700, 400)
        ed.load()
        ed.set_navigation_bar(True)
        ed.show()
        self.addCleanup(ed.close)
        ed.set_symbols(symbols.from_outline(outline(UNITY, "csharp"), ed.folding.ranges, str(path)))
        ed.go_to_line(19)                       # внутри Update()
        ed.navbar.follow_cursor(force=True)
        self.assertEqual(ed.navbar.types.currentText(), "Player")
        self.assertTrue(ed.navbar.members.currentText().startswith("Update"))
        self.assertGreater(ed.viewportMargins().top(), 0)
        ed.navbar._member_chosen(0)             # первый член — поле hp
        self.assertEqual(ed.textCursor().blockNumber() + 1, ed.navbar._member_list[0].start)
        ed.set_navigation_bar(False)
        self.assertEqual(ed.viewportMargins().top(), 0)


class Symbols(unittest.TestCase):
    def test_document_symbols_hierarchy_and_flat(self):
        tree = symbols.from_document_symbols([{
            "name": "Player", "kind": 5, "range": {"start": {"line": 8}, "end": {"line": 21}},
            "children": [{"name": "Update", "kind": 6, "detail": "()", "range": {"start": {"line": 15}, "end": {"line": 20}}},
                         {"name": "hp", "kind": 8, "range": {"start": {"line": 11}, "end": {"line": 11}}}]}])
        self.assertEqual([c.name for c in tree[0].children], ["hp", "Update()"])
        t, m = symbols.at_line(tree, 18)
        self.assertEqual((t.name, m.name), ("Player", "Update()"))
        flat = symbols.from_document_symbols([
            {"name": "A", "kind": 5, "location": {"uri": "file:///a.py", "range": {"start": {"line": 0}, "end": {"line": 9}}}},
            {"name": "f", "kind": 6, "location": {"uri": "file:///a.py", "range": {"start": {"line": 2}, "end": {"line": 4}}}}])
        self.assertEqual([(s.name, [c.name for c in s.children]) for s in flat], [("A", ["f"])])

    def test_outline_fallback_gets_block_ends(self):
        from studio.panels import outline
        folds = fold_ranges(UNITY, languages.for_path("P.cs"))
        tree = symbols.from_outline(outline(UNITY, "csharp"), folds)
        player = next(s for _d, s in symbols.walk(tree) if s.name == "Player")
        self.assertEqual((player.start, player.end), (9, 22))
        update = next(s for _d, s in symbols.walk(tree) if s.name.startswith("Update"))
        self.assertEqual(update.end, 21)

    def test_matching_and_query(self):
        self.assertTrue(symbols.matches("PC", "PlayerController"))
        self.assertTrue(symbols.matches("contr", "PlayerController"))
        self.assertFalse(symbols.matches("xyz", "PlayerController"))
        self.assertLess(symbols.score("Play", "Player"), symbols.score("Play", "SpacePlayer"))
        self.assertEqual(parse_query("t Player"), ("types", "Player"))
        self.assertEqual(parse_query(":42"), ("line", "42"))
        self.assertEqual(parse_query("Update"), (None, "Update"))


@unittest.skipUnless(shutil.which("black") or shutil.which("ruff"), "нужен black или ruff")
class PythonFormatting(unittest.TestCase):
    def test_format_document_one_undo_step(self):
        from studio.formatting import Formatter
        path = Path(tempfile.mkdtemp()) / "a.py"
        path.write_text("x=[1,2 ,3]\n")
        ed = CodeEditor(str(path), services(), QFont("monospace", 11))
        ed.load()
        self.addCleanup(ed.close)
        messages = []
        window = SimpleNamespace(service_for=lambda lang: None, options={"formatting/python_tool": "auto"},
                                 status=lambda text, *a: messages.append(text), busy=lambda *a: None,
                                 editors=lambda: [ed])
        fmt = Formatter.__new__(Formatter)
        from PyQt6.QtCore import QObject
        QObject.__init__(fmt)
        fmt.win, fmt._proc = window, None
        fmt.format(ed)
        deadline = time.monotonic() + 15
        while ed.toPlainText() == "x=[1,2 ,3]\n" and time.monotonic() < deadline:
            spin(0.05)
        self.assertEqual(ed.toPlainText(), "x = [1, 2, 3]\n")
        ed.undo()
        self.assertEqual(ed.toPlainText(), "x=[1,2 ,3]\n")


if __name__ == "__main__":
    unittest.main()
