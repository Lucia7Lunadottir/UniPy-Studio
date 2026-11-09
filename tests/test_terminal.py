import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import importlib.util
import sys
import tempfile
import time
import unittest
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication

from studio.terminal.panel import TerminalSession, plain_text
from studio.terminal.widget import key_bytes

app = QApplication.instance() or QApplication([])
M = Qt.KeyboardModifier


def spin(seconds, until):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
        if until():
            return True
    return False


class Keys(unittest.TestCase):
    def test_xterm_sequences(self):
        none = M.NoModifier
        self.assertEqual(key_bytes(Qt.Key.Key_Up, none, ""), b"\x1b[A")
        self.assertEqual(key_bytes(Qt.Key.Key_Up, none, "", app_cursor=True), b"\x1bOA")       # vim, less
        self.assertEqual(key_bytes(Qt.Key.Key_Right, M.ControlModifier, ""), b"\x1b[1;5C")    # Ctrl+→ — по словам
        self.assertEqual(key_bytes(Qt.Key.Key_C, M.ControlModifier, "\x03"), b"\x03")
        self.assertEqual(key_bytes(Qt.Key.Key_Return, none, "\r"), b"\r")
        self.assertEqual(key_bytes(Qt.Key.Key_Backspace, none, "\x08"), b"\x7f")
        self.assertEqual(key_bytes(Qt.Key.Key_F5, none, ""), b"\x1b[15~")
        self.assertEqual(key_bytes(Qt.Key.Key_B, M.AltModifier, "b"), b"\x1bb")
        self.assertEqual(key_bytes(Qt.Key.Key_A, none, "ж"), "ж".encode())
        self.assertIsNone(key_bytes(Qt.Key.Key_Shift, M.ShiftModifier, ""))

    def test_plain_text_strips_colours(self):
        self.assertEqual(plain_text(b"\x1b[31mred\x1b[0m\r\n\x1b]0;title\x07ok"), "red\nok")


class Pty(unittest.TestCase):
    def session(self, argv):
        s = TerminalSession(QFont("monospace", 10), "t")
        s.resize(700, 300)
        s.show()
        app.processEvents()
        s.start(argv, tempfile.gettempdir())
        self.addCleanup(lambda: (s.process.terminate(), s.deleteLater()))
        return s

    def test_real_tty_size_and_input(self):
        code = ("import os, sys; print('tty', sys.stdin.isatty(), os.get_terminal_size().columns > 0); "
                "name = input('name? '); print('hi', name)")
        s = self.session([sys.executable, "-c", code])
        done = []
        s.finished.connect(lambda c, text: done.append((c, text)))
        self.assertTrue(spin(10, lambda: b"name? " in s._captured))
        self.assertIn(b"tty True True", s._captured)
        s.process.write("Ann\r")                                 # как будто напечатали и нажали Enter
        self.assertTrue(spin(10, lambda: done))
        self.assertEqual(done[0][0], 0)
        self.assertIn("hi Ann", done[0][1])
        s.drain_all()
        rows = [s.view.term.text(i) for i in range(s.view.term.total_lines())]
        self.assertIn("hi Ann", rows)

    def test_ctrl_c_interrupts(self):
        s = self.session([sys.executable, "-c", "import time; print('ready', flush=True); time.sleep(60)"])
        done = []
        s.finished.connect(lambda c, text: done.append(c))
        self.assertTrue(spin(10, lambda: b"ready" in s._captured))
        s.view.send.emit(b"\x03")                                # Ctrl+C из виджета
        self.assertTrue(spin(10, lambda: done))
        self.assertNotEqual(done[0], 0)


class WindowRun(unittest.TestCase):
    def window(self):
        from studio.mainwindow import MainWindow
        win = MainWindow(screenshot=True)
        self.addCleanup(lambda: (win.jedi.stop(), win.csharp_index.stop(),
                                 [svc.stop() for svc in win.language_services], win.terminal.stop(), win.close()))
        return win

    def test_f5_python_input_and_traceback_line(self):
        win = self.window()
        root = Path(tempfile.mkdtemp())
        main = root / "main.py"
        main.write_text("name = input('name? ')\nprint('hello', name)\nraise ValueError(name)\n")
        win.set_root(str(root))
        win.open_path(str(main))
        win.a_run2.trigger()                                     # Ctrl+F5 — без отладки
        self.assertTrue(spin(10, lambda: win.terminal.current() is not None
                             and b"name? " in win.terminal.current()._captured))
        win.terminal.current().process.write("Bob\r")
        self.assertTrue(spin(10, lambda: win.errors.counts()[0] == 1))
        self.assertIn(b"hello Bob", win.terminal.current()._captured)
        row = [win.errors.table.item(0, i).text() for i in (1, 3, 4)]
        self.assertEqual(row, ["ValueError: Bob", "main.py", "3"])

    @unittest.skipUnless(importlib.util.find_spec("debugpy"), "нужен python-debugpy")
    def test_debugging_with_keyboard_input(self):
        win = self.window()
        root = Path(tempfile.mkdtemp())
        main = root / "main.py"
        main.write_text("name = input('name? ')\ngreeting = 'hi ' + name\nprint(greeting)\n")
        win.set_root(str(root))
        ed = win.open_path(str(main))
        ed.go_to_line(3)
        win.a_breakpoint.trigger()
        win.a_run.trigger()                                      # F5 — отладка; программа в терминале (runInTerminal)
        self.assertTrue(spin(30, lambda: win.terminal.current() is not None
                             and b"name? " in win.terminal.current()._captured))
        win.terminal.current().process.write("Zoe\r")
        self.assertTrue(spin(20, lambda: ed.execution_line == 3))
        shown = []
        win.debugger.hover("greeting", shown.append)
        self.assertTrue(spin(10, lambda: shown))
        self.assertIn("hi Zoe", shown[0])
        win.a_run.trigger()
        self.assertTrue(spin(20, lambda: not win.debugger.active()))


if __name__ == "__main__":
    unittest.main()
