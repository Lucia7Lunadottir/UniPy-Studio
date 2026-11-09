import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import importlib.util
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from PyQt6.QtWidgets import QApplication

from studio.testing.base import ERROR, FAILED, MARK, NOT_RUN, PASSED, SKIPPED, aggregate
from studio.testing.python import HELPER
from studio.i18n import tr

app = QApplication.instance() or QApplication([])

TESTS = '''import pytest


class TestCalc:
    def test_add(self):
        assert 1 + 1 == 2

    def test_bad(self):
        value = 2
        assert value * 2 == 5

    @pytest.mark.skip(reason="later")
    def test_later(self):
        pass


@pytest.fixture
def broken():
    raise RuntimeError("fixture broke")


def test_uses_broken(broken):
    pass


def test_prints():
    print("hello from test")
'''


def spin(seconds, until):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
        if until():
            return True
    return False


class Aggregate(unittest.TestCase):
    def test_group_status(self):
        self.assertEqual(aggregate([PASSED, FAILED]), FAILED)
        self.assertEqual(aggregate([PASSED, ERROR]), FAILED)
        self.assertEqual(aggregate([PASSED, SKIPPED]), PASSED)
        self.assertEqual(aggregate([SKIPPED]), SKIPPED)
        self.assertEqual(aggregate([PASSED, NOT_RUN]), NOT_RUN)


class UnittestFallback(unittest.TestCase):
    def test_helper_without_pytest(self):
        d = Path(tempfile.mkdtemp())
        (d / "test_u.py").write_text("import unittest\nclass T(unittest.TestCase):\n"
                                     "    def test_ok(self):\n        pass\n"
                                     "    def test_bad(self):\n        self.assertEqual(1, 2)\n")
        code = "import sys, runpy; sys.modules['pytest'] = None; sys.argv = ['h'] + sys.argv[1:]; " \
               "runpy.run_path(%r, run_name='__main__')" % HELPER
        out = subprocess.run([sys.executable, "-c", code, "run", str(d)], capture_output=True, text=True,
                             timeout=60).stdout
        events = [json.loads(l[len(MARK):]) for l in out.splitlines() if l.startswith(MARK)]
        self.assertEqual(events[0], {"event": "framework", "name": "unittest", "python": sys.version.split()[0]})
        results = {e["id"].split(".")[-1]: e for e in events if e["event"] == "result"}
        self.assertEqual((results["test_ok"]["outcome"], results["test_bad"]["outcome"]), ("passed", "failed"))
        self.assertEqual(results["test_bad"]["line"], 6)
        self.assertEqual(events[-1], {"event": "done", "code": 1})


@unittest.skipUnless(importlib.util.find_spec("pytest"), "нужен pytest в интерпретаторе")
class Explorer(unittest.TestCase):
    def setUp(self):
        from studio.mainwindow import MainWindow
        self.root = Path(tempfile.mkdtemp())
        (self.root / "test_calc.py").write_text(TESTS)
        self.win = MainWindow(screenshot=True)
        self.addCleanup(lambda: (self.win.jedi.stop(), self.win.csharp_index.stop(), self.win.close()))
        self.win.set_root(str(self.root))
        self.tests = self.win.tests

    def node(self, suffix):
        return next(n for i, n in self.tests.nodes.items() if i.endswith(suffix))

    def test_discover_run_failed_and_cursor(self):
        self.tests.discover()
        self.assertTrue(spin(30, lambda: self.tests.nodes and not self.tests.running()))
        self.assertEqual(sorted(n.name for n in self.tests.nodes.values() if n.kind == "test"),
                         ["test_add", "test_bad", "test_later", "test_prints", "test_uses_broken"])
        self.assertEqual(self.node("::TestCalc").kind, "group")
        self.assertEqual(self.node("test_bad").line, 8)

        self.tests.run([])                                              # Ctrl+R, A
        self.assertTrue(spin(30, lambda: not self.tests.running() and self.node("test_add").status == PASSED))
        self.assertEqual(self.node("test_bad").status, FAILED)
        self.assertEqual(self.node("test_later").status, SKIPPED)
        self.assertEqual(self.node("test_uses_broken").status, ERROR)     # упала фикстура, а не тест
        self.assertIn("hello from test", self.node("test_prints").output)
        bad = self.node("test_bad")
        self.assertEqual((os.path.basename(bad.fail_path), bad.fail_line), ("test_calc.py", 10))
        errors = self.win.errors.groups.get(("testing.errors", str(self.root / "test_calc.py")), [])
        self.assertIn(10, [e[0] for e in errors])
        expected = tr("testing.summary").rsplit("%.1f", 1)[0] % (5, 2, 2, 1, 0)    # без времени — оно разное
        self.assertTrue(self.tests.panel.summary.text().startswith(expected), self.tests.panel.summary.text())

        (self.root / "test_calc.py").write_text(TESTS.replace("== 5", "== 4"))
        self.tests.run_failed()                                         # Ctrl+R, F — только упавшие
        self.assertTrue(spin(30, lambda: not self.tests.running() and self.node("test_bad").status == PASSED))
        self.assertEqual(self.node("test_add").status, PASSED)          # результаты прошлых прогонов не теряются

        ed = self.win.open_path(str(self.root / "test_calc.py"))
        ed.go_to_line(6)
        self.assertEqual(self.tests.at_cursor().name, "test_add")

    def test_broken_test_file_is_shown_as_error(self):
        (self.root / "test_broken.py").write_text("def test_x(:\n    pass\n")
        self.tests.discover()
        self.assertTrue(spin(30, lambda: self.tests.nodes and not self.tests.running()))
        self.tests.run([])
        self.assertTrue(spin(30, lambda: not self.tests.running() and any(
            i.endswith("test_broken.py") for i in self.tests.nodes)))
        self.assertEqual(self.node("test_broken.py").status, ERROR)
        self.assertIn("SyntaxError", self.node("test_broken.py").details)

    @unittest.skipUnless(importlib.util.find_spec("debugpy"), "нужен python-debugpy")
    def test_debug_test_stops_at_breakpoint(self):
        self.tests.discover()
        self.assertTrue(spin(30, lambda: self.tests.nodes and not self.tests.running()))
        ed = self.win.open_path(str(self.root / "test_calc.py"))
        ed.go_to_line(10)
        self.win.a_breakpoint.trigger()
        self.tests.debug([self.node("test_bad").id])
        self.assertTrue(spin(40, lambda: ed.execution_line == 10))
        shown = []
        self.win.debugger.hover("value", shown.append)
        self.assertTrue(spin(10, lambda: shown))
        self.assertIn("2", shown[0])
        self.win.debugger.stop()
        self.assertTrue(spin(20, lambda: not self.win.debugger.active()))


if __name__ == "__main__":
    unittest.main()
