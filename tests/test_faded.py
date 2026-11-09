import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication

from studio.editor import CodeEditor
from studio.lsp import LanguageServer, RoslynServer

app = QApplication.instance() or QApplication([])


def diag(line, c1, c2, tags, code="", message=""):
    return {"range": {"start": {"line": line, "character": c1}, "end": {"line": line, "character": c2}},
            "severity": 4, "tags": tags, "code": code, "message": message}


class Faded(unittest.TestCase):
    def test_server_collects_tags_from_all_groups(self):
        srv = LanguageServer("/tmp")
        got = []
        srv.faded.connect(lambda path, ranges: got.append(ranges))
        srv._raw_diag["file:///tmp/a.py"] = {"g1": [diag(0, 7, 9, [1])], "g2": [diag(3, 0, 4, [2]), diag(5, 0, 1, [])]}
        srv._emit_faded("/tmp/a.py", "file:///tmp/a.py")
        self.assertEqual(got[0], [(0, 7, 0, 9, "unnecessary"), (3, 0, 3, 4, "deprecated")])

    def test_unity_messages_are_not_faded(self):
        root = Path(tempfile.mkdtemp())
        (root / "ProjectSettings").mkdir()
        (root / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 6000.6.4f1\n")
        srv = RoslynServer(str(root), "roslyn")
        self.assertFalse(srv.keep_faded(diag(9, 4, 9, [1], "IDE0051", "Private member 'Player.Awake' is unused")))
        self.assertTrue(srv.keep_faded(diag(9, 4, 9, [1], "IDE0051", "Private member 'Player.Helper' is unused")))
        self.assertTrue(srv.keep_faded(diag(0, 0, 18, [1], "IDE0005", "Using directive is unnecessary.")))

    def test_editor_draws_faded_and_strikeout(self):
        services = SimpleNamespace(service_for=lambda language: None, jedi=SimpleNamespace(available=False),
                                   csharp=SimpleNamespace(ready_for=lambda p: None, sync=lambda *a: None))
        ed = CodeEditor("/tmp/f.py", services, QFont("monospace", 11))
        self.addCleanup(ed.close)
        ed.setPlainText("import os\nold()\n")
        ed.set_faded([(0, 7, 0, 9, "unnecessary"), (1, 0, 1, 3, "deprecated")])
        sels = [s for s in ed.extraSelections() if s.cursor.hasSelection()]
        self.assertEqual([s.cursor.selectedText() for s in sels], ["os", "old"])
        self.assertLess(sels[0].format.foreground().color().alpha(), 255)
        self.assertTrue(sels[1].format.fontStrikeOut())


if __name__ == "__main__":
    unittest.main()
