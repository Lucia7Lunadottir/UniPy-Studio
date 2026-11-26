"""Наследник из другого файла: self.window.… — не ошибка, хотя у QWidget есть метод window()."""
from app.pages import BasePage


class SettingsPage(BasePage):
    def open(self):
        self.window.statusBar().showMessage("settings")
        self.window.setWindowTitle("Settings")
