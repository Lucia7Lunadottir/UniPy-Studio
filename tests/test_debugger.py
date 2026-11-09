import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import sys
import tempfile
import time
import unittest
from pathlib import Path

from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication

from studio import toolchains
from studio.i18n import tr
from studio.debug.breakpoints import BreakpointStore, BreakpointAnchors
from studio.debug.session import DebugConfig, DebugSession, STOPPED, ENDED
from studio.toolchains import RunTarget, Toolchain

app = QApplication.instance() or QApplication([])
FAKE = str(Path(__file__).with_name("fake_dap_adapter.py"))


def spin(seconds, until):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)
        if until():
            return True
    return False


def settings():
    return QSettings(os.path.join(tempfile.mkdtemp(), "d.ini"), QSettings.Format.IniFormat)


class Store(unittest.TestCase):
    def test_toggle_persist_and_move(self):
        qs = settings()
        store = BreakpointStore(qs)
        store.load("/project")
        self.assertTrue(store.toggle("/project/a.py", 3))
        store.toggle("/project/a.py", 7)
        store.set_condition("/project/a.py", 7, "hp < 0")
        self.assertFalse(store.toggle("/project/a.py", 3))
        again = BreakpointStore(qs)
        again.load("/project")
        self.assertEqual([(b.line, b.condition) for b in again.for_path("/project/a.py")], [(7, "hp < 0")])
        again.load("/other")
        self.assertEqual(again.paths(), [])                       # у каждого решения свои точки
        store.move_lines("/project/a.py", {7: 9})
        self.assertEqual([b.line for b in store.for_path("/project/a.py")], [9])

    def test_anchors_follow_edits(self):
        from studio.editor import CodeEditor
        from types import SimpleNamespace
        path = Path(tempfile.mkdtemp()) / "a.py"
        path.write_text("a = 1\nb = 2\nc = 3\n")
        services = SimpleNamespace(service_for=lambda language: None, jedi=SimpleNamespace(available=False),
                                   csharp=SimpleNamespace(ready_for=lambda p: None, sync=lambda *a: None))
        ed = CodeEditor(str(path), services, QFont("monospace", 11))
        ed.load()
        self.addCleanup(ed.close)
        store = BreakpointStore()
        ed.breakpoints = store
        BreakpointAnchors(ed, store)
        ed.toggle_breakpoint(3)
        ed.textCursor().insertText("# new line\n# another\n")       # курсор в начале — всё уезжает на 2 строки
        spin(1, lambda: store.for_path(str(path))[0].line == 5)
        self.assertEqual([b.line for b in store.for_path(str(path))], [5])


class Session(unittest.TestCase):
    def test_protocol_order_stop_step_variables_and_end(self):
        work = Path(tempfile.mkdtemp())
        program, log = work / "game.py", work / "commands.log"
        program.write_text("\n".join("x%d = %d" % (i, i) for i in range(10)))
        store = BreakpointStore()
        store.toggle(str(program), 4)
        store.toggle(str(program), 99)                             # строка без кода — не привяжется
        config = DebugConfig(sys.executable, [FAKE, str(log)], "launch", {"program": str(program)}, "fake")
        session = DebugSession(config, store)
        stops, outputs, ended = [], [], []
        session.stopped.connect(lambda t, reason, frames: stops.append((reason, [f.line for f in frames])))
        session.output.connect(lambda cat, text: outputs.append((cat, text)))
        session.ended.connect(ended.append)
        session.start()
        self.assertTrue(spin(10, lambda: stops))
        self.assertEqual(stops[0], ("breakpoint", [4, 1]))
        self.assertEqual(session.state, STOPPED)
        self.assertIn(("stdout", "program started\n"), outputs)
        self.assertTrue(store.get(str(program), 4).verified)
        self.assertFalse(store.get(str(program), 99).verified)
        commands = log.read_text().split()
        self.assertEqual(commands[:3], ["initialize", "launch", "setBreakpoints"])
        self.assertLess(commands.index("setExceptionBreakpoints"), commands.index("configurationDone"))

        scopes, variables, children, evaluated = [], [], [], []
        session.scopes(session.frames[0].id, scopes.extend)
        self.assertTrue(spin(5, lambda: scopes))
        session.variables(scopes[0][1], variables.extend)
        self.assertTrue(spin(5, lambda: variables))
        self.assertEqual([(v.name, v.value, v.type) for v in variables], [("hp", "42", "int"), ("player", "Player()", "Player")])
        session.variables(variables[1].reference, children.extend)
        self.assertTrue(spin(5, lambda: children))
        self.assertEqual(children[0].value, "'Hero'")
        session.evaluate("hp", session.frames[0].id, "hover", lambda v, m: evaluated.append(v and v.value))
        session.evaluate("nope", session.frames[0].id, "hover", lambda v, m: evaluated.append(m))
        self.assertTrue(spin(5, lambda: len(evaluated) == 2))
        self.assertEqual(evaluated, ["42", "name not defined"])

        session.step_over()
        self.assertTrue(spin(5, lambda: len(stops) == 2))
        self.assertEqual(stops[1], ("step", [5, 1]))
        session.continue_()
        self.assertTrue(spin(10, lambda: ended))
        self.assertEqual((session.state, ended), (ENDED, [3]))


class FakeDebugToolchain(Toolchain):
    """Набор, который отлаживает поддельным адаптером: проверка, что окно не знает языков и в отладке."""
    id = "fake-debug"
    name = "FakeDebug"
    file_extensions = (".fake",)

    def applies(self, ctx):
        return any(p.endswith(".fake") for p in ctx.files)

    def target_for_file(self, ctx, path):
        return RunTarget(path, os.path.basename(path), path) if path.endswith(".fake") else None

    def run_targets(self, ctx):
        return []

    def debug_available(self, ctx):
        return True, ""

    def debug_config(self, ctx, target):
        return DebugConfig(sys.executable, [FAKE], "launch", {"program": target.path}, "fake", title="fake")


class WindowDebugging(unittest.TestCase):
    def test_f5_breakpoint_arrow_panels_hover_and_finish(self):
        from studio.mainwindow import MainWindow
        tc = toolchains.register(FakeDebugToolchain())
        win = MainWindow(screenshot=True)
        try:
            root = Path(tempfile.mkdtemp())
            program = root / "game.fake"
            program.write_text("\n".join("line %d" % i for i in range(1, 11)))
            win.set_root(str(root))
            ed = win.open_path(str(program))
            ed.go_to_line(3)
            win.a_breakpoint.trigger()                                 # F9
            self.assertEqual([b.line for b in win.debugger.store.for_path(str(program))], [3])
            self.assertFalse(win.a_step_over.isVisible())
            win.a_run.trigger()                                        # F5
            self.assertTrue(spin(10, lambda: ed.execution_line == 3))
            self.assertTrue(win.a_step_over.isEnabled() and win.a_step_over.isVisible())
            self.assertEqual(win.a_run.text(), tr("debug.continue"))   # F5 во время остановки — «Продолжить»
            self.assertTrue(spin(5, lambda: win.debugger.variables.topLevelItemCount() == 1
                                 and win.debugger.variables.topLevelItem(0).childCount() == 2))
            local = win.debugger.variables.topLevelItem(0).child(0)
            self.assertEqual((local.text(0), local.text(1)), ("hp", "42"))
            self.assertEqual(win.debugger.call_stack.topLevelItemCount(), 2)
            shown = []
            self.assertTrue(win.debugger.hover("hp", shown.append))
            self.assertTrue(spin(5, lambda: shown))
            self.assertIn("42", shown[0])
            win.a_step_over.trigger()                                  # F10
            self.assertTrue(spin(5, lambda: ed.execution_line == 4))
            win.a_run.trigger()                                        # F5 = «Продолжить»
            self.assertTrue(spin(10, lambda: not win.debugger.active()))
            self.assertIsNone(ed.execution_line)
            self.assertFalse(win.a_step_over.isVisible())
            self.assertIn("program started", win.output.view.toPlainText())
        finally:
            toolchains.unregister(tc)
            win.jedi.stop()
            win.csharp_index.stop()
            for svc in win.language_services:
                svc.stop()
            win.close()


if __name__ == "__main__":
    unittest.main()
