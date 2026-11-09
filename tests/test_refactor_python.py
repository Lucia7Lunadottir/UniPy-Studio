import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication, QMessageBox

from studio.refactor_python import MoveError, can_move, move_symbol_plan, snake_case, update_importers

app = QApplication.instance() or QApplication([])

SOURCE = '''"""Окна."""
import os
import sys
from PyQt6.QtWidgets import QMainWindow, QDialog, QLabel


# Диалог настроек
class SettingsDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.label = QLabel(os.getcwd())


class MainWindow(QMainWindow):
    pass


def helper(x):
    return x * 2


if __name__ == "__main__":
    sys.exit(0)
'''


class Plan(unittest.TestCase):
    def folder(self, package=False):
        d = Path(tempfile.mkdtemp())
        if package:
            (d / "__init__.py").write_text("")
        (d / "windows.py").write_text(SOURCE)
        return d

    def test_class_moves_with_only_needed_imports(self):
        d = self.folder()
        plan = move_symbol_plan(SOURCE, 9, str(d / "windows.py"))
        self.assertEqual(plan["file_name"], "settings_dialog.py")
        self.assertEqual(plan["new_text"].split("\n\n\n")[0],
                         "import os\nfrom PyQt6.QtWidgets import QDialog, QLabel")
        self.assertIn("# Диалог настроек\nclass SettingsDialog(QDialog):", plan["new_text"])
        self.assertNotIn("SettingsDialog", plan["rest_text"])                  # в старом не используется
        self.assertIn("class MainWindow", plan["rest_text"])
        self.assertNotIn("\n\n\n\n", plan["rest_text"])
        compile(plan["new_text"], "settings_dialog.py", "exec")
        compile(plan["rest_text"], "windows.py", "exec")
        self.assertFalse(plan["circular"])

    def test_function_and_still_used_name_in_package(self):
        d = self.folder(package=True)
        src = SOURCE.replace("    pass\n", "    value = helper(1)\n")
        plan = move_symbol_plan(src, 18, str(d / "windows.py"))
        self.assertEqual(plan["file_name"], "helper.py")
        self.assertIn("from .helper import helper\n", plan["rest_text"])        # пакет — относительный импорт

    def test_errors(self):
        d = self.folder()
        with self.assertRaises(MoveError):
            move_symbol_plan("def f(:\n", 1, str(d / "x.py"))
        with self.assertRaises(MoveError):
            move_symbol_plan("import os\n\n\nclass Only:\n    pass\n", 4, str(d / "only.py"))   # уже один
        self.assertFalse(can_move("import os\n\n\nclass Only:\n    pass\n", 4))
        self.assertFalse(can_move(SOURCE, 2))                                   # курсор не в определении
        (d / "settings_dialog.py").write_text("")
        with self.assertRaises(MoveError):
            move_symbol_plan(SOURCE, 9, str(d / "windows.py"))
        self.assertEqual((snake_case("HTTPServer"), snake_case("UIManager2")), ("http_server", "ui_manager2"))

    def test_importers_absolute_relative_and_other_folder(self):
        d = self.folder(package=True)
        plan = move_symbol_plan(SOURCE, 9, str(d / "windows.py"))
        other = Path(tempfile.mkdtemp())
        files = {
            str(d / "app.py"): "from .windows import MainWindow, SettingsDialog as SD\n",
            str(d / "tool.py"): "from windows import SettingsDialog\n",
            str(other / "x.py"): "from windows import SettingsDialog\n",          # чужой модуль с тем же именем
        }
        out = update_importers(plan, files)
        self.assertEqual(out[str(d / "app.py")],
                         "from .windows import MainWindow\nfrom .settings_dialog import SettingsDialog as SD\n")
        self.assertEqual(out[str(d / "tool.py")], "from settings_dialog import SettingsDialog\n")
        self.assertNotIn(str(other / "x.py"), out)


class WindowMove(unittest.TestCase):
    def test_command_moves_updates_importer_and_undoes(self):
        from studio.mainwindow import MainWindow
        win = MainWindow(screenshot=True)
        self.addCleanup(lambda: (win.jedi.stop(), win.csharp_index.stop(),
                                 [s.stop() for s in win.language_services], win.close()))
        d = Path(tempfile.mkdtemp())
        (d / "windows.py").write_text(SOURCE.replace("TITLE", "T"))
        (d / "app.py").write_text("from windows import SettingsDialog\nSettingsDialog()\n")
        win.set_root(str(d))
        ed = win.open_path(str(d / "windows.py"))
        ed.go_to_line(9)
        self.assertTrue(any(a["source"] == "own" for a in ed._own_actions()))      # лампочка предлагает перенос
        with patch.object(QMessageBox, "information"):
            win.a_move_type.trigger()                                            # Ctrl+R, Ctrl+F
        self.assertTrue((d / "settings_dialog.py").exists())
        self.assertEqual(win.editor().path, str(d / "settings_dialog.py"))
        self.assertNotIn("class SettingsDialog", (d / "windows.py").read_text())
        self.assertEqual((d / "app.py").read_text(), "from settings_dialog import SettingsDialog\nSettingsDialog()\n")
        ed.undo()                                                                # одна правка — одно Ctrl+Z
        self.assertIn("class SettingsDialog", ed.toPlainText())

    def test_circular_import_is_reported(self):
        from studio.mainwindow import MainWindow
        win = MainWindow(screenshot=True)
        self.addCleanup(lambda: (win.jedi.stop(), win.csharp_index.stop(),
                                 [s.stop() for s in win.language_services], win.close()))
        d = Path(tempfile.mkdtemp())
        (d / "game.py").write_text("SPEED = 3\n\n\nclass Player:\n    speed = SPEED\n\n\np = Player()\n")
        win.set_root(str(d))
        ed = win.open_path(str(d / "game.py"))
        ed.go_to_line(4)
        with patch.object(QMessageBox, "information") as told:
            win.a_move_type.trigger()
        self.assertIn("game", told.call_args[0][2])
        self.assertIn("player", told.call_args[0][2])


if __name__ == "__main__":
    unittest.main()
