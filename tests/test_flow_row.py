import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest

from PyQt6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget

from studio.panels.common import FlowRow

app = QApplication.instance() or QApplication([])


class Flow(unittest.TestCase):
    def make(self):
        host = QWidget()
        lay = QVBoxLayout(host)
        row = FlowRow()
        buttons = [QPushButton("Кнопка номер %d" % n) for n in range(4)]
        for b in buttons[:-1]:
            row.addWidget(b)
        row.add(buttons[-1], fill=True)
        lay.addLayout(row)
        below = QLabel("ниже")
        lay.addWidget(below)
        self.addCleanup(host.deleteLater)
        return host, buttons, below

    def test_wraps_instead_of_widening(self):
        host, buttons, below = self.make()
        widest = max(b.sizeHint().width() for b in buttons)
        self.assertLess(host.minimumSizeHint().width(), widest + 40)    # не сумма всех кнопок
        host.resize(widest + 30, 400)
        host.show()
        app.processEvents()
        tops = {b.geometry().top() for b in buttons}
        self.assertEqual(len(tops), 4)                                  # каждая кнопка — своей строкой
        self.assertGreaterEqual(below.geometry().top(), max(b.geometry().bottom() for b in buttons))

    def test_one_line_when_wide_and_fill_takes_rest(self):
        host, buttons, _below = self.make()
        host.resize(sum(b.sizeHint().width() for b in buttons) + 200, 200)
        host.show()
        app.processEvents()
        self.assertEqual(len({b.geometry().top() for b in buttons}), 1)
        self.assertGreater(buttons[-1].width(), buttons[-1].sizeHint().width())   # растянулась до края


class Window(unittest.TestCase):
    def test_docks_do_not_block_narrowing(self):
        from studio.mainwindow import MainWindow
        win = MainWindow(screenshot=True)
        self.addCleanup(lambda: (win.jedi.stop(), win.csharp_index.stop(), win.close()))
        for dock in win.findChildren(type(win.d_git)):
            self.assertLess(dock.minimumSizeHint().width(), 260, dock.windowTitle())
        self.assertLess(win.minimumSizeHint().width(), 900)
        win.resize(760, 600)                    # как restoreGeometry до show(): узкое окно восстанавливается узким
        self.assertEqual(win.width(), 760)


if __name__ == "__main__":
    unittest.main()
