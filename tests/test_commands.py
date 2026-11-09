import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest

from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import QApplication, QMainWindow

from studio.commands import CommandRegistry, KeyboardPage
from studio.options import Options, OptionsDialog

app = QApplication.instance() or QApplication([])


def settings():
    return QSettings(os.path.join(tempfile.mkdtemp(), "c.ini"), QSettings.Format.IniFormat)


class Registry(unittest.TestCase):
    def setUp(self):
        self.window = QMainWindow()
        self.addCleanup(self.window.deleteLater)
        self.qs = settings()
        self.reg = CommandRegistry(self.window, self.qs)
        self.calls = []
        self.save = self.reg.add("File.Save", "mainwindow.save", lambda: self.calls.append("save"), "Ctrl+S")
        self.redo = self.reg.add("Edit.Redo", "mainwindow.redo", lambda: None, ("Ctrl+Y", "Ctrl+Shift+Z"))
        self.plain = self.reg.add("Help.About", "mainwindow.about", lambda: None)

    def test_defaults_override_and_persist(self):
        self.assertEqual([k.toString() for k in self.redo.shortcuts()], ["Ctrl+Y", "Ctrl+Shift+Z"])
        self.assertEqual(self.plain.shortcuts(), [])
        self.save.trigger()
        self.assertEqual(self.calls, ["save"])
        self.reg.set_shortcuts("File.Save", ["Ctrl+Alt+S"])
        self.assertEqual(self.save.shortcut().toString(), "Ctrl+Alt+S")
        self.assertFalse(self.reg.is_default("File.Save"))
        again = CommandRegistry(QMainWindow(), self.qs)          # перезапуск программы
        a = again.add("File.Save", "mainwindow.save", lambda: None, "Ctrl+S")
        self.assertEqual(a.shortcut().toString(), "Ctrl+Alt+S")
        self.reg.set_shortcuts("Edit.Redo", [])                   # без клавиш — тоже запоминается
        self.assertEqual(CommandRegistry(QMainWindow(), self.qs).add(
            "Edit.Redo", "mainwindow.redo", lambda: None, "Ctrl+Y").shortcuts(), [])
        self.reg.reset("File.Save")
        self.assertIsNone(self.qs.value("keys/File.Save"))
        self.assertEqual(self.save.shortcut().toString(), "Ctrl+S")

    def test_duplicate_id_rejected_and_owners(self):
        with self.assertRaises(ValueError):
            self.reg.add("File.Save", "mainwindow.save", lambda: None)
        self.assertEqual(self.reg.owners("Ctrl+Shift+Z"), ["Edit.Redo"])
        self.assertEqual(self.reg.owners(QKeySequence("Ctrl+S")), ["File.Save"])

    def test_keyboard_page_moves_shortcut_and_applies_on_ok(self):
        dialog = OptionsDialog(Options(settings()), extra_pages=(KeyboardPage(self.reg),))
        self.addCleanup(dialog.deleteLater)
        view = next(v for _it, v, _i in dialog.views if v.page.path[-1] == "commands.keyboard")
        view.tree.setCurrentItem(view.items["Help.About"])
        view.edit.setKeySequence(QKeySequence("Ctrl+S"))
        self.assertIn(tr_label("mainwindow.save"), view.conflict.text())
        view._assign(replace=True)
        self.assertEqual(view.keys_of("Help.About"), ["Ctrl+S"])
        self.assertEqual(view.keys_of("File.Save"), [])          # перешло к выбранной команде
        self.assertEqual(self.save.shortcut().toString(), "Ctrl+S")   # до «Применить» ничего не меняется
        self.assertTrue(dialog.apply_button.isEnabled())
        dialog.apply()
        self.assertEqual(self.plain.shortcut().toString(), "Ctrl+S")
        self.assertEqual(self.save.shortcuts(), [])
        dialog.search.setText("Ctrl+Shift+Z")
        self.assertFalse(view.items["Edit.Redo"].isHidden())
        self.assertTrue(view.items["File.Save"].isHidden())
        view.reset_defaults()
        dialog.apply()
        self.assertEqual(self.save.shortcut().toString(), "Ctrl+S")
        self.assertEqual(self.plain.shortcuts(), [])


def tr_label(key):
    from studio.i18n import tr
    return tr(key).replace("&", "")


class WindowCommands(unittest.TestCase):
    def test_window_commands_have_unique_default_shortcuts(self):
        from studio.mainwindow import MainWindow
        win = MainWindow(screenshot=True)
        try:
            seen = {}
            for cmd in win.commands:
                for key in cmd.defaults:
                    self.assertNotIn(key, seen, "%s и %s" % (cmd.id, seen.get(key)))
                    seen[key] = cmd.id
            self.assertIn("Tools.Options", win.commands)
            self.assertIs(win.commands.action("File.SaveAll"), win.a_save_all)
        finally:
            win.jedi.stop()
            win.csharp_index.stop()
            for svc in win.language_services:
                svc.stop()
            win.close()


if __name__ == "__main__":
    unittest.main()
