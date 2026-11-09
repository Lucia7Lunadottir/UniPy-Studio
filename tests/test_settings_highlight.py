import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import unittest
from PyQt6.QtWidgets import QApplication, QDialog
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QTextDocument
from studio.highlighter import Highlighter
from studio.mainwindow import MainWindow
from studio.theme import C

app=QApplication.instance() or QApplication([])
class SettingsHighlight(unittest.TestCase):
    def test_screenshot_attribute_string_color(self):
        source='[Tooltip("Предмет застрял дальше этого от нужной точки (плюс его размер) - отпускается")]'
        document=QTextDocument(); highlighter=Highlighter(document,'csharp')
        document.setPlainText(source); highlighter.rehighlight()
        formats=document.firstBlock().layout().formats()
        def color(pos):
            return next(r.format.foreground().color().name() for r in formats if r.start<=pos<r.start+r.length)
        self.assertEqual(color(source.index('Tooltip')), C['type'].lower())
        self.assertEqual(color(source.index('Предмет')), C['string'].lower())
        self.assertNotEqual(color(source.index('Tooltip')),color(source.index('Предмет')))
        highlighter.rebuild()
        self.assertEqual(color(source.index('Предмет')),C['string'].lower())

    def test_settings_visible_opens_and_persists(self):
        win=MainWindow(screenshot=True)
        try:
            # настройки теста — в песочнице tests/conftest.py, не в ~/.config пользователя
            self.assertFalse(win.settings.fileName().startswith(os.path.expanduser('~/.config')))
            self.assertTrue(any(win.a_settings in action.menu().actions()
                                for action in win.menuBar().actions() if action.menu()))
            self.assertEqual(win.a_settings.shortcut().toString(),'Ctrl+,')
            outcome=[]
            def accept():
                dialog=app.activeModalWidget()
                outcome.append(isinstance(dialog,QDialog))
                if dialog:
                    fields={key:field for key,(_o,field,_r) in dialog._editors.items()}
                    fields['editor/font_size'].setValue(15); fields['editor/hint_size'].setValue(13)
                    fields['editor/word_wrap'].setChecked(True)
                    dialog.accept()
            QTimer.singleShot(50,accept)
            win.a_settings.trigger()
            self.assertEqual(outcome,[True])
            self.assertEqual(win.font_size,15)
            self.assertEqual(win.hint_size,13)
            self.assertTrue(win.settings.value('editor/word_wrap',False,type=bool))
        finally:
            win.jedi.stop(); win.csharp_index.stop()
            for svc in win.language_services:svc.stop()
            win.close()
