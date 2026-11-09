import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest

from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import QApplication, QTabBar

from studio.editor.split_view import SplitView

app = QApplication.instance() or QApplication([])


class Tabs(unittest.TestCase):
    def setUp(self):
        from studio.mainwindow import MainWindow
        self.win = MainWindow(screenshot=True)
        self.addCleanup(lambda: (self.win.jedi.stop(), self.win.csharp_index.stop(), self.win.close()))
        self.root = tempfile.mkdtemp()
        self.win.set_root(self.root)
        self.paths = []
        for name in ("a.py", "b.py", "c.py", "d.py"):
            p = os.path.join(self.root, name)
            with open(p, "w") as f:
                f.write("def f():\n    x = 1\n    return x\n\n\nprint(f())\n")
            self.paths.append(p)
            self.win.open_path(p)

    def names(self):
        return [os.path.basename(e.path) for e in self.win.editors()]

    def test_pinned_tabs_stay_left_and_survive_close_others(self):
        docs = self.win.documents
        docs.set_pinned(2, True)                                        # c.py
        self.assertEqual(self.names(), ["c.py", "a.py", "b.py", "d.py"])
        self.assertIsNotNone(self.win.tabs.tabBar().tabButton(0, QTabBar.ButtonPosition.LeftSide))   # булавка
        self.win.tabs.tabBar().moveTab(0, 3)                            # перетащили мышью вправо — вернулась
        self.assertEqual(self.names()[0], "c.py")
        self.win.settings.setValue("solution/pinned_files", [])
        self.win.screenshot_mode = False                                # запоминание открытых файлов включено
        self.addCleanup(setattr, self.win, "screenshot_mode", True)
        self.win._remember_open()
        self.assertEqual(self.win.settings.value("solution/pinned_files"), [self.paths[2]])
        self.win._close_tabs(keep=self.win.editors()[1])                # «Закрыть остальные» у a.py
        self.assertEqual(self.names(), ["c.py", "a.py"])
        self.win.close_unpinned_tabs()
        self.assertEqual(self.names(), ["c.py"])
        self.win.toggle_pin(0)
        self.assertIsNone(self.win.tabs.tabBar().tabButton(0, QTabBar.ButtonPosition.LeftSide))

    def test_split_window_shares_document_and_routes_requests(self):
        ed = self.win.open_path(self.paths[0])
        self.win.a_split.trigger()                                      # Окно → Разделить
        page = self.win.documents.current_page()
        view = page.view
        self.assertIsInstance(view, SplitView)
        self.assertIs(view.document(), ed.document())
        view.setFocus()
        page.active = view
        self.assertIs(self.win.editor(), view)
        self.assertEqual(self.win.editors().count(ed), 1)               # файл открыт один раз
        self.assertIn(view, self.win.views())

        got = []
        view.on_worker_result = got.append                              # ответ анализатора — второму окну
        self.win._worker_result({"kind": "hover", "req": 0, "editor": id(view), "data": None})
        self.assertEqual(len(got), 1)

        cur = view.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.End)
        cur.insertText("# from view\n")
        self.assertTrue(ed.document().isModified())
        self.win.save_current()                                         # Ctrl+S во втором окне сохраняет файл
        with open(self.paths[0]) as f:
            self.assertTrue(f.read().endswith("# from view\n"))
        self.assertFalse(ed.document().isModified())

        ed.folding.recompute()
        view.setTextCursor(QTextCursor(view.document().findBlockByNumber(1)))
        self.win._fold("toggle")                                        # Ctrl+M, Ctrl+M во втором окне
        self.assertTrue(ed.folding.is_collapsed(0))
        self.assertFalse(view.document().findBlockByNumber(1).isVisible())
        view.grab()
        ed.grab()

        self.win.a_split.trigger()                                      # ещё раз — разделение убрано
        self.assertIsNone(page.view)
        self.assertEqual(ed.split_views, [])
        self.win.a_split.trigger()
        self.assertTrue(self.win.close_tab(self.win.tabs.currentIndex()))   # закрытие разделённой вкладки
        app.processEvents()
        self.assertNotIn("a.py", self.names())


if __name__ == "__main__":
    unittest.main()
