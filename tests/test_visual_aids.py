import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest

from PyQt6.QtWidgets import QApplication

from studio.editor import guide_levels, indent_columns

app = QApplication.instance() or QApplication([])


class Guides(unittest.TestCase):
    def test_indent_columns(self):
        self.assertEqual(indent_columns("    x", 4), 4)
        self.assertEqual(indent_columns("\tx", 4), 4)
        self.assertEqual(indent_columns("  \tx", 4), 4)                # таб — до следующей позиции табуляции
        self.assertEqual(indent_columns("\t\tx", 8), 16)
        self.assertIsNone(indent_columns("   ", 4))                      # пустая строка

    def test_blank_lines_take_smaller_neighbour(self):
        indents = [0, 4, 8, None, 12, 4, None, None, 0]
        levels = guide_levels(indents, 4)
        self.assertEqual(levels[3], [0, 4])                              # между 8 и 12 — как у 8
        self.assertEqual(levels[4], [0, 4, 8])
        self.assertEqual(levels[6], [])                                  # между 4 и 0 — линий нет
        self.assertEqual(levels[0], [])


class Window(unittest.TestCase):
    def test_option_reaches_open_editors(self):
        import tempfile
        from studio.mainwindow import MainWindow
        win = MainWindow(screenshot=True)
        self.addCleanup(lambda: (win.jedi.stop(), win.csharp_index.stop(), win.close()))
        root = tempfile.mkdtemp()
        path = os.path.join(root, "a.txt")
        with open(path, "w") as f:
            f.write("  a\n")
        ed = win.open_path(path)
        self.assertFalse(ed.show_whitespace)
        win.a_whitespace.trigger()                                       # Ctrl+R, Ctrl+W
        self.assertTrue(win.options["editor/show_whitespace"])
        self.assertTrue(ed.show_whitespace)
        win.options.set("editor/indent_guides", False)
        self.assertFalse(ed.indent_guides)
        ed.viewport().grab()                                             # рисование не падает с выключенными линиями


if __name__ == "__main__":
    unittest.main()
