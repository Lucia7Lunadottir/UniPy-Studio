"""Заведомые ошибки: каждая помечена, какой вид она должна дать в редакторе."""
from PyQt6.QtWidgets import QPushButon, QWidget             # expect: error
import no_such_module_for_unipy                            # expect: warning


class Panel(QWidget):
    def __init__(self):
        super().__init__()
        self.title = "panel"

    def show_title(self):
        print(self.titel)                                  # expect: error
        print(undefined_name)                              # expect: error

    def call(self):
        def twice(a):
            return a * 2
        return twice(1, 2)                                 # expect: warning

    def wrong_qt_call(self):
        return QWidget(1, 2, 3)                            # expect: warning
