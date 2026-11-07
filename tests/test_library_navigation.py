import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFont, QTextCursor
from PyQt6.QtTest import QSignalSpy, QTest
from PyQt6.QtCore import Qt
from studio.intellisense import CSharpProvider, CSharpIndexWorker, package_sources, package_documents
from studio.editor import CodeEditor
from studio.lsp import RoslynServer

app = QApplication.instance() or QApplication([])

class LibraryNavigationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root/'ProjectSettings').mkdir()
        (self.root/'ProjectSettings/ProjectVersion.txt').write_text('test')
        (self.root/'Assets').mkdir()
        package = self.root/'Library/PackageCache/com.pg.menu@hash'
        package.mkdir(parents=True)
        self.pause = package/'Pause.cs'
        self.pause.write_text('''namespace PG.MenuManagement {
public partial class Pause {
 public CanvasGroup panel => _panel;
 private CanvasGroup _panel;
}
}''')
        package = self.root/'Packages/com.pg.pgtween/Runtime'
        package.mkdir(parents=True)
        self.tween = package/'PGTween.cs'
        self.tween.write_text('''namespace PG.Tween {
public static class PGTween {
 public static async Awaitable OnAlphaTween(this CanvasGroup canvasGroup, float to, float time) { }
}
}''')
        self.path = str(self.root/'Assets/Test.cs')
        self.provider = CSharpProvider()
        self.addCleanup(package_sources.cache_clear)
        self.addCleanup(package_documents.cache_clear)

    def test_async_extension_through_package_property(self):
        text = 'using PG.MenuManagement;\nclass Test { Pause _pause; void Run() { _pause.panel.OnAl'
        items = self.provider.complete(text, len(text), self.path)
        match = next(i for i in items if i.name == 'OnAlphaTween')
        self.assertIn('CanvasGroup.OnAlphaTween', match.detail)
        self.assertEqual(match.data['additionalTextEdits'][0]['newText'], 'using PG.Tween;\n')
        # Same completion on a bare dot, when the namespace is already imported.
        text = 'using PG.Tween;\n' + text[:-4]
        match = next(i for i in self.provider.complete(text,len(text),self.path) if i.name == 'OnAlphaTween')
        self.assertIsNone(match.data)

    def test_package_type_and_expression_property_definition(self):
        text = 'using PG.MenuManagement;\nclass Test { Pause _pause; void Run() { _pause.panel; } }'
        for word,line in [('Pause _pause',2),('panel;',3)]:
            self.assertEqual(self.provider.definition(text,text.index(word)+2,self.path),
                             {'path':str(self.pause),'line':line})

    def test_extension_definition(self):
        text='using PG.MenuManagement;\nusing PG.Tween;\nclass Test { Pause _pause; void Run() { _pause.panel.OnAlphaTween(1,1); } }'
        self.assertEqual(self.provider.definition(text,text.index('OnAlphaTween')+3,self.path),
                         {'path':str(self.tween),'line':3})

    def test_editor_navigation_without_ready_server(self):
        worker=CSharpIndexWorker()
        services=SimpleNamespace(service_for=lambda lang:None, csharp_index=worker,
                                 jedi=SimpleNamespace(available=False), status=lambda *a:None)
        ed=CodeEditor(self.path,services,QFont('monospace',11))
        worker.finished.connect(ed.on_worker_result)
        try:
            ed.setPlainText('using PG.MenuManagement;\nclass Test { Pause _pause; void Run() { _pause.panel')
            ed.moveCursor(QTextCursor.MoveOperation.End)
            spy=QSignalSpy(ed.goto_requested)
            ed.resize(1000,250)
            ed.show(); ed.setFocus(); app.processEvents()
            click=ed.textCursor()
            click.setPosition(ed.toPlainText().index('panel')+2)
            QTest.mouseClick(ed.viewport(),Qt.MouseButton.LeftButton,Qt.KeyboardModifier.ControlModifier,
                             ed.cursorRect(click).center())
            if not spy: spy.wait(5000)
            self.assertTrue(spy)
            self.assertEqual(list(spy[0]),[str(self.pause),3])
        finally:
            worker.stop(); ed.close()

    def test_actual_roslyn_navigation_configuration_names(self):
        server=RoslynServer(str(self.root),'unused')
        for name in ('navigation.dotnet_navigate_to_decompiled_sources',
                     'navigation.dotnet_navigate_to_source_link_and_embedded_sources'):
            self.assertIs(server.configuration(name),True)
