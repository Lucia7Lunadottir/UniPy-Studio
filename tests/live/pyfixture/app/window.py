"""Чистый код на PyQt6: ни ошибок, ни предупреждений быть не должно."""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QApplication, QLabel, QMainWindow, QPushButton, QVBoxLayout, QWidget


class CounterMixin:
    """Примесь: её атрибуты задаёт класс, в который её подмешивают."""

    def bump(self):
        self.count += 1
        self.label.setText(str(self.count))


class Window(CounterMixin, QMainWindow):
    changed = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self.count = 0
        self.label = QLabel("0", alignment=Qt.AlignmentFlag.AlignCenter)
        button = QPushButton("+1", clicked=self.bump)
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.addWidget(self.label)
        layout.addWidget(button)
        self.setCentralWidget(box)
        self.changed.connect(self.on_changed)

    def on_changed(self, value):
        self.statusBar().showMessage(str(value))


def main():
    app = QApplication([])
    window = Window()
    window.show()
    return app.exec()
