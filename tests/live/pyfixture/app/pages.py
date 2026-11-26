"""Базовая страница: self.window задаётся здесь, а пользуются им наследники в других файлах."""
from PyQt6.QtWidgets import QWidget


class BasePage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
