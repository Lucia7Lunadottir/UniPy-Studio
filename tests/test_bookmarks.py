import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import time
import unittest

from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import QApplication

from studio.bookmarks.store import BookmarkStore
from studio.debug.breakpoints import BreakpointStore

app = QApplication.instance() or QApplication([])


def spin(seconds, until):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
        if until():
            return True
    return False


class Store(unittest.TestCase):
    def test_step_wraps_across_files_and_skips_disabled(self):
        s = BookmarkStore()
        s.toggle("/a.py", 10)
        s.toggle("/a.py", 30)
        s.toggle("/b.py", 5)
        s.set_enabled("/a.py", 30, False)
        self.assertEqual(s.step("/a.py", 10), ("/b.py", 5))             # 30 выключена — мимо
        self.assertEqual(s.step("/b.py", 5), ("/a.py", 10))             # по кругу
        self.assertEqual(s.step("/a.py", 10, forward=False), ("/b.py", 5))
        self.assertEqual(s.step("/a.py", 1), ("/a.py", 10))
        self.assertIsNone(BookmarkStore().step("/a.py", 1))

    def test_saved_per_solution_next_to_breakpoints(self):
        ini = os.path.join(tempfile.mkdtemp(), "s.ini")
        qs = QSettings(ini, QSettings.Format.IniFormat)
        s = BookmarkStore(qs)
        s.load("/proj")
        s.toggle("/proj/a.py", 3)
        s.set_name("/proj/a.py", 3, "  вход  ")
        BreakpointStore(qs).load("/proj")
        again = BookmarkStore(qs)
        again.load("/proj")
        mark = again.get("/proj/a.py", 3)
        self.assertEqual((mark.name, mark.enabled), ("вход", True))
        again.load("/other")
        self.assertEqual(again.paths(), [])                             # у другого решения — свои


class Window(unittest.TestCase):
    def setUp(self):
        from studio.mainwindow import MainWindow
        self.win = MainWindow(screenshot=True)
        self.addCleanup(lambda: (self.win.jedi.stop(), self.win.csharp_index.stop(), self.win.close()))
        self.root = tempfile.mkdtemp()
        self.win.set_root(self.root)
        self.a = os.path.join(self.root, "a.txt")
        self.b = os.path.join(self.root, "b.txt")
        for p in (self.a, self.b):
            with open(p, "w") as f:
                f.write("".join("line %d\n" % i for i in range(1, 21)))

    def test_toggle_navigate_and_follow_edits(self):
        ed = self.win.open_path(self.a, 4)
        self.win.a_bookmark.trigger()                                   # Ctrl+K, Ctrl+K
        self.assertEqual([m.line for m in self.win.bookmarks.store.for_path(self.a)], [4])
        ed_b = self.win.open_path(self.b, 7)
        self.win.a_bookmark.trigger()
        self.win.a_bookmark_next.trigger()                              # Ctrl+K, Ctrl+N: из b — по кругу в a:4
        self.assertIs(self.win.editor(), ed)
        self.assertEqual(ed.textCursor().blockNumber() + 1, 4)
        self.win.a_bookmark_prev.trigger()                              # назад — b:7
        self.assertIs(self.win.editor(), ed_b)

        cur = QTextCursor(ed.document())                                # две строки сверху — закладка едет
        cur.insertText("new 1\nnew 2\n")
        self.assertTrue(spin(3, lambda: [m.line for m in self.win.bookmarks.store.for_path(self.a)] == [6]))
        ed.gutter.grab()                                                # рисуется на поле

        panel = self.win.bookmarks.panel
        self.assertEqual(panel.tree.topLevelItemCount(), 2)
        item = panel.tree.topLevelItem(0)
        item.setText(0, "важное место")                                 # переименование в окне «Закладки»
        self.assertEqual(self.win.bookmarks.store.get(self.a, 6).name, "важное место")

        self.win.a_bookmark_clear.trigger()                             # Ctrl+K, Ctrl+L
        self.assertEqual(self.win.bookmarks.store.paths(), [])
        self.assertEqual(panel.tree.topLevelItemCount(), 0)


if __name__ == "__main__":
    unittest.main()
