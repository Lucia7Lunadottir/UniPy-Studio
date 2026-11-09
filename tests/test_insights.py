import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from types import SimpleNamespace

from PyQt6.QtCore import QPointF
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import QApplication

from studio.editor import CodeEditor
from studio.insights.codelens import bare_name, lenses
from studio.insights.python_inlay import assignments, call_sites, needs_param_hint
from studio.insights.python_semantic import analyze, from_imports
from studio.lsp_features import decode_semantic_tokens
from studio.symbols import Symbol
from studio.theme import C

app = QApplication.instance() or QApplication([])


def services():
    return SimpleNamespace(service_for=lambda language: None, jedi=SimpleNamespace(available=False),
                           csharp=SimpleNamespace(ready_for=lambda p: None, sync=lambda *a: None))


class PythonSemantic(unittest.TestCase):
    CODE = ('import os\nfrom typing import List\n\n'
            'class Ёжик(Base):\n'
            '    count = 0\n'
            '    def __init__(self, name):\n'
            '        self.name = name\n'
            '        self.go()\n'
            '    def go(self):\n'
            '        x = [i for i in range(3)]\n'
            '        print(self.name, x, Ёжик, os.path, List)\n')

    def roles(self, kinds=None):
        lines = self.CODE.split("\n")
        out = {}
        for l, c, n, r in analyze(self.CODE, kinds):
            out.setdefault((lines[l][c:c + n], l), set()).add(r)        # в строке имя может встретиться дважды
        return out

    def test_scopes_and_members(self):
        r = self.roles({("typing", "List"): "type"})
        self.assertIn("type", r[("Ёжик", 3)])                   # кириллица: столбцы в UTF-16
        self.assertIn("member", r[("count", 4)])                 # поле в теле класса
        self.assertIn("param", r[("name", 5)])
        self.assertEqual(r[("name", 6)], {"member", "param"})     # self.name = name
        self.assertIn("function", r[("go", 7)])                  # self.go() — метод класса
        self.assertIn("local", r[("x", 9)])
        self.assertIn("type", r[("range", 9)])                   # встроенные
        self.assertIn("function", r[("print", 10)])
        self.assertIn("namespace", r[("os", 10)])
        self.assertIn("type", r[("List", 10)])                   # от jedi (import_kinds)
        self.assertNotIn(("List", 10), self.roles())                # без подсказки — не красится
        self.assertNotIn(("self", 6), r)                            # self красит обычная подсветка

    def test_broken_code_and_imports(self):
        self.assertIsNone(analyze("def f(:\n"))                     # недописанный код — прошлая раскраска
        self.assertEqual(from_imports(self.CODE), [("typing", "List", 2, 19)])


class PythonInlay(unittest.TestCase):
    CODE = ('def box(width, height):\n'
            '    w = width\n'
            '    cfg = Config()\n'
            '    def inner():\n'
            '        w = 5\n'
            '    return draw(w, height, *rest)\n'
            'box(10, 20)\n')

    def test_call_sites_and_suppression(self):
        sites = {s.callee: s for s in call_sites(self.CODE, 0, 20)}
        self.assertEqual(sites["box"].args, [(0, 6, 4, "10"), (1, 6, 8, "20")])
        self.assertEqual((sites["box"].line, sites["box"].col), (7, 4))
        self.assertEqual([a[3] for a in sites["draw"].args], ["w", "height"])     # после *rest — ничего
        self.assertFalse(needs_param_hint("height", "height"))
        self.assertFalse(needs_param_hint("width", "self.width"))
        self.assertFalse(needs_param_hint("_private", "1"))
        self.assertTrue(needs_param_hint("width", "10"))
        self.assertEqual(call_sites(self.CODE, 0, 3), [])          # только видимые строки

    def test_first_assignments_per_scope(self):
        found = [(a.name, a.line0, a.end16, a.obvious) for a in assignments(self.CODE, 0, 20)]
        self.assertEqual(found, [("w", 1, 5, False), ("cfg", 2, 7, True), ("w", 4, 9, True)])


class LspTokens(unittest.TestCase):
    def test_decode_relative_and_skip_keywords(self):
        types = ["keyword", "class", "parameter", "field"]
        data = [0, 0, 6, 0, 0,   0, 7, 3, 1, 0,   2, 4, 5, 2, 0,   0, 6, 1, 3, 0]
        self.assertEqual(decode_semantic_tokens(data, types),
                         [(0, 7, 3, "type"), (2, 4, 5, "param"), (2, 10, 1, "member")])


class Lenses(unittest.TestCase):
    def test_keys_are_stable_and_overloads_differ(self):
        text = ["class Box", "{", "    void Go(int a) {}", "    void Go(string s) {}", "    int x;"]
        syms = [Symbol("Box", "class", 1, 5, children=[
            Symbol("Go(int a)", "method", 3, container="Box"), Symbol("Go(string s)", "method", 4, container="Box"),
            Symbol("x", "field", 5, container="Box")])]
        found = [(l.key, l.line, l.col) for l in lenses(syms, text)]
        self.assertEqual(found, [("/Box/class", 0, 6), ("Box/Go/method", 2, 9), ("Box/Go/method#2", 3, 9)])
        self.assertEqual(bare_name("List<T>"), "List")


class Controller(unittest.TestCase):
    def editor(self, text):
        ed = CodeEditor("/tmp/insights.py", services(), QFont("monospace", 11))
        self.addCleanup(ed.close)
        ed.setPlainText(text)
        ed.resize(700, 300)
        ed.show()
        app.processEvents()
        ed.insights.apply_settings({"semantic": True, "codelens": True, "inlay_parameters": True,
                                    "inlay_types": False})
        self.ins = {i.id: i for i in ed.insights.insights}
        return ed

    def result(self, ed, kind, data, req=None):
        ed.on_worker_result({"kind": kind, "req": ed.document().revision() if req is None else req,
                             "editor": id(ed), "data": data})
        app.processEvents()

    def test_semantic_color_and_inlay_gap_do_not_touch_text(self):
        ed = self.editor("value = compute(10, 20)\n")
        changed = []
        ed.textChanged.connect(lambda: changed.append(1))
        undo = ed.document().availableUndoSteps()
        self.result(ed, "semantic_tokens", {"tokens": [(0, 0, 5, "local")]})
        fmts = ed.document().findBlockByNumber(0).layout().formats()
        colors = {f.format.foreground().color().name() for f in fmts if f.start == 0}
        self.assertIn(QColor(C["local"]).name(), colors)
        layout = ed.document().findBlockByNumber(0).layout()
        before = layout.lineAt(0).cursorToX(16)[0]
        self.result(ed, "inlay_hints", {"hints": [(0, 16, "width:", "parameter"), (0, 20, "h:", "type")]})
        layout = ed.document().findBlockByNumber(0).layout()
        self.assertGreater(layout.lineAt(0).cursorToX(16)[0], before + 20)         # место под «width:»
        self.assertNotIn(20, [c for c, _t, _k in self.ins["inlay"].by_line[0]])   # типы выключены
        self.assertEqual(ed.toPlainText(), "value = compute(10, 20)\n")
        self.assertEqual(ed.document().availableUndoSteps(), undo)
        self.assertFalse(ed.document().isModified())
        self.assertEqual(changed, [])                               # перекраска не будит проверку ошибок и т. п.
        ed.viewport().grab()

    def test_stale_answers_are_dropped_and_disable_clears(self):
        ed = self.editor("a = 1\n")
        self.result(ed, "semantic_tokens", {"tokens": [(0, 0, 1, "local")]}, req=-5)
        self.assertEqual(self.ins["semantic"].by_line, {})           # ответ на старый текст
        self.result(ed, "semantic_tokens", {"tokens": [(0, 0, 1, "local")]})
        self.assertTrue(self.ins["semantic"].by_line)
        ed.insights.apply_settings({"semantic": False})
        self.assertEqual(self.ins["semantic"].by_line, {})

    def test_codelens_label_and_click(self):
        ed = self.editor("def go():\n    pass\n\ngo()\n")
        ed.set_symbols([Symbol("go", "function", 1, 2)])
        lens = self.ins["codelens"].lenses[0]
        self.result(ed, "reference_count", {"key": lens.key, "count": 1,
                                             "locations": [{"path": "/tmp/insights.py", "line": 4, "col": 1}]})
        ed.viewport().grab()                                        # рисование заполняет места для щелчка
        rect = self.ins["codelens"]._rects[id(ed)][0][0]
        got = []
        ed.references_found.connect(lambda name, locs: got.append((name, locs)))
        self.assertTrue(ed.insights.click(ed, QPointF(rect.center())))
        self.assertEqual(got, [("go", [{"path": "/tmp/insights.py", "line": 4, "col": 1}])])


if __name__ == "__main__":
    unittest.main()
