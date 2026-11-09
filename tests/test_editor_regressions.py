import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFont, QTextCursor, QTextDocument
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from studio.editor import CodeEditor
from studio.i18n import tr
from studio.intellisense import Item, CSharpProvider, package_sources
from studio.highlighter import Highlighter
from studio.search import pattern, SearchWorker

app = QApplication.instance() or QApplication([])

class Regressions(unittest.TestCase):
    def editor(self, text):
        services = SimpleNamespace(service_for=lambda language: None, jedi=SimpleNamespace(available=False),
                                   csharp=SimpleNamespace(ready_for=lambda p: None, sync=lambda *a: None))
        ed = CodeEditor('/tmp/test.cs', services, QFont('monospace', 11))
        ed.setPlainText(text)
        ed.moveCursor(QTextCursor.MoveOperation.End)
        ed.show()
        ed.setFocus()
        app.processEvents()
        self.addCleanup(ed.close)
        return ed

    def test_accept_tab_and_enter(self):
        for key in (Qt.Key.Key_Tab, Qt.Key.Key_Return):
            ed = self.editor('transform.Tran')
            ed.show_completions([Item('Translate', 'method')])
            app.processEvents()
            QTest.keyClick(ed, key)
            self.assertEqual(ed.toPlainText(), 'transform.Translate()')
            self.assertEqual(ed.textCursor().position(), len('transform.Translate('))
            ed.undo()
            self.assertEqual(ed.toPlainText(), 'transform.Tran')
            ed.close()

    def test_popup_key_and_unity_method_body(self):
        ed = self.editor('private void Upd')
        ed.show_completions([Item('Update', 'event', 'void Update()')])
        app.processEvents()
        QTest.keyClick(ed.completer.popup(), Qt.Key.Key_Return)
        self.assertEqual(ed.toPlainText(), 'private void Update()\n{\n    \n}')

    def test_background_index(self):
        from studio.intellisense import CSharpIndexWorker
        from PyQt6.QtTest import QSignalSpy
        worker = CSharpIndexWorker()
        spy = QSignalSpy(worker.finished)
        try:
            worker.submit(req=7, editor=1, code='transform.Tran', pos=len('transform.Tran'),
                          path='/tmp/Test.cs', base=None)
            if not spy:
                spy.wait(3000)
            self.assertTrue(spy)
            self.assertEqual(spy[0][0]['req'], 7)
            self.assertIn('Translate', [it.name for it in spy[0][0]['data']])
        finally:
            worker.stop()

    def test_existing_parenthesis_and_empty_results(self):
        ed = self.editor('Tran()')
        c = ed.textCursor(); c.setPosition(4); ed.setTextCursor(c)
        ed.show_completions([Item('Translate', 'method')])
        ed.insert_completion('Translate')
        self.assertEqual(ed.toPlainText(), 'Translate()')
        ed.show_completions([])
        self.assertIn(tr('editor.no_completions_retry'), ed.doc_tip.text())
        self.assertTrue(ed.doc_tip.isVisible())

    def test_package_filename_and_extension_at_dot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'ProjectSettings').mkdir()
            (root/'ProjectSettings/ProjectVersion.txt').write_text('test')
            (root/'Packages/pkg').mkdir(parents=True)
            (root/'Assets').mkdir()
            (root/'Packages/pkg/Misc.cs').write_text('''namespace Example {
public class Widget {
 public void Run() {}
}
public static class Extensions {
 public static void Stretch(this Transform value) {}
}
}''')
            provider = CSharpProvider()
            source = 'using Example;\nWidget w;\nw.'
            items = provider.complete(source, len(source), str(root/'Assets/Test.cs'))
            self.assertIn('Run', [it.name for it in items])
            source = 'using Example;\ntransform.'
            items = provider.complete(source, len(source), str(root/'Assets/Test.cs'))
            self.assertIn('Stretch', [it.name for it in items])
            source = 'using Example;\nWid'
            self.assertIn('Widget', [it.name for it in provider.complete(source, len(source), str(root/'Assets/Test.cs'))])
        package_sources.cache_clear()

    def test_multiline_search_and_buffers(self):
        rx = pattern('one\ntwo')
        self.assertEqual(rx.sub(lambda m: 'three\nfour', 'ONE\ntwo'), 'three\nfour')
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp)/'a.cs')
            Path(path).write_text('disk')
            worker = SearchWorker(tmp, {path: 'one\ntwo'}, rx, ['*.cs'])
            found = []
            worker.found.connect(found.append)
            worker.run()
            self.assertEqual(found[0][0][0][2], [(0, 7)])
        self.assertEqual(len(list(pattern('one', words=True).finditer('someone one'))), 1)

    def test_multiline_strings(self):
        doc = QTextDocument()
        highlighter = Highlighter(doc, 'csharp')
        doc.setPlainText('var s = @"first ""quoted""\nsecond";\nvar r = """raw\ntext""";')
        highlighter.rehighlight()
        self.assertEqual(doc.findBlockByNumber(0).userState(), highlighter.ST_VERBATIM)
        self.assertEqual(doc.findBlockByNumber(1).userState(), 0)
        self.assertEqual(doc.findBlockByNumber(2).userState(), 103)
        self.assertEqual(doc.findBlockByNumber(3).userState(), 0)


class WindowIntegration(unittest.TestCase):
    def test_project_replace_undo_and_stale_preview(self):
        from studio.mainwindow import MainWindow
        from studio.search import ProjectSearch
        win = MainWindow(screenshot=True)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                win.root = tmp
                paths = [Path(tmp)/'a.txt', Path(tmp)/'b.txt']
                for p in paths:
                    p.write_text('old\nvalue')
                dialog = ProjectSearch(win)
                dialog.query.setPlainText('old\nvalue')
                dialog.replacement.setPlainText('new\nvalue')
                dialog.start()
                self.assertTrue(dialog.worker.wait(5000))
                app.processEvents()
                self.assertEqual(len(dialog.results), 2)
                changed = win.open_path(str(paths[1]))
                changed.setPlainText('changed after preview')
                dialog.replace_all()
                self.assertEqual(win.open_path(str(paths[0])).toPlainText(), 'new\nvalue')
                self.assertEqual(paths[0].read_text(), 'old\nvalue')
                win.editor().undo()
                self.assertEqual(win.editor().toPlainText(), 'old\nvalue')
                self.assertEqual(changed.toPlainText(), 'changed after preview')
                dialog.close()
        finally:
            win.jedi.stop()
            win.csharp_index.stop()
            for svc in win.language_services:
                svc.stop()
            win.close()

if __name__ == '__main__':
    unittest.main()
