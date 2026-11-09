import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import json
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication, QMessageBox

from studio import documents
from studio.documents import DiskState, RecoveryStore, atomic_write
from studio.editor import CodeEditor
from studio.options import Options, OptionsDialog

app = QApplication.instance() or QApplication([])


def type_text(ed, text):
    """Как будто текст набрали: setPlainText сбрасывает признак «изменён»."""
    ed.setPlainText(text)
    ed.document().setModified(True)


def services():
    return SimpleNamespace(service_for=lambda language: None, jedi=SimpleNamespace(available=False),
                           csharp=SimpleNamespace(ready_for=lambda p: None, sync=lambda *a: None))


class AtomicWrite(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def test_replaces_content_keeps_mode_and_leaves_no_temp(self):
        path = self.dir / "Player.cs"
        path.write_bytes(b"old")
        os.chmod(path, 0o640)
        atomic_write(str(path), b"new")
        self.assertEqual(path.read_bytes(), b"new")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["Player.cs"])

    def test_symlink_stays_a_link(self):
        real = self.dir / "real.py"
        real.write_bytes(b"a")
        link = self.dir / "link.py"
        link.symlink_to(real)
        atomic_write(str(link), b"b")
        self.assertTrue(link.is_symlink())
        self.assertEqual(real.read_bytes(), b"b")

    def test_failure_keeps_old_file(self):
        path = self.dir / "a.py"
        path.write_bytes(b"keep")
        with patch("os.replace", side_effect=OSError(28, "No space left on device")):
            with self.assertRaises(OSError):
                atomic_write(str(path), b"lost")
        self.assertEqual(path.read_bytes(), b"keep")
        self.assertEqual([p.name for p in self.dir.iterdir()], ["a.py"])


class DiskStateTests(unittest.TestCase):
    def test_same_bytes_rewritten_is_not_a_change(self):
        path = Path(tempfile.mkdtemp()) / "x.cs"
        path.write_bytes(b"class A {}")
        state = DiskState(str(path))
        state.remember(b"class A {}")
        os.utime(path, ns=(1, 1))
        self.assertIsNone(state.check())
        path.write_bytes(b"class B {}")
        self.assertEqual(state.check(), b"class B {}")
        path.unlink()
        self.assertIs(state.check(), DiskState.MISSING)


class EditorFileSafety(unittest.TestCase):
    def editor(self, text=b"line1\nline2\nline3\n"):
        path = Path(tempfile.mkdtemp()) / "Mover.cs"
        path.write_bytes(text)
        ed = CodeEditor(str(path), services(), QFont("monospace", 11))
        ed.load()
        self.addCleanup(ed.close)
        return ed, path

    def test_reload_keeps_undo_and_clears_modified(self):
        ed, path = self.editor()
        path.write_bytes(b"line1\nCHANGED\nline3\n")
        ed.reload_from(path.read_bytes())
        self.assertEqual(ed.toPlainText(), "line1\nCHANGED\nline3\n")
        self.assertFalse(ed.document().isModified())
        ed.undo()
        self.assertEqual(ed.toPlainText(), "line1\nline2\nline3\n")

    def test_save_asks_before_overwriting_outside_change(self):
        ed, path = self.editor()
        type_text(ed, "mine\n")
        path.write_bytes(b"theirs\n")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel) as asked:
            self.assertFalse(ed.save())
        asked.assert_called_once()
        self.assertEqual(path.read_bytes(), b"theirs\n")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Save):
            self.assertTrue(ed.save())
        self.assertEqual(path.read_bytes(), b"mine\n")

    def test_save_error_is_reported_not_raised(self):
        ed, path = self.editor()
        type_text(ed, "x")
        with patch("studio.editor.atomic_write", side_effect=PermissionError(13, "Permission denied")), \
                patch.object(QMessageBox, "warning") as warned:
            self.assertFalse(ed.save())
        warned.assert_called_once()
        self.assertTrue(ed.document().isModified())

    def test_external_change_reloads_clean_and_asks_when_modified(self):
        ed, path = self.editor()
        handler = documents.ExternalChanges(None, lambda p: ed if p == ed.path else None, lambda: True)
        path.write_bytes(b"from unity\n")
        handler.on_changed(ed.path)
        self.assertEqual(ed.toPlainText(), "from unity\n")      # правок не было — загружено молча
        type_text(ed, "my edit\n")
        path.write_bytes(b"from git\n")
        with patch.object(documents.QGuiApplication, "applicationState",
                          return_value=documents.Qt.ApplicationState.ApplicationActive), \
                patch.object(documents.ExternalChanges, "_ask") as ask:
            handler.on_changed(ed.path)
        ask.assert_called_once()
        self.assertEqual(ed.toPlainText(), "my edit\n")

    def test_deleted_file_marks_tab_modified(self):
        ed, path = self.editor()
        handler = documents.ExternalChanges(None, lambda p: ed, lambda: True)
        path.unlink()
        handler.on_changed(ed.path)
        self.assertTrue(ed.missing_on_disk)
        self.assertTrue(ed.document().isModified())
        self.assertTrue(ed.save())                  # файл создаётся заново
        self.assertTrue(path.exists())


class Recovery(unittest.TestCase):
    def test_snapshot_orphans_and_live_owner(self):
        folder = tempfile.mkdtemp()
        root = tempfile.mkdtemp()
        path = Path(root) / "Game.cs"
        path.write_bytes(b"a")
        ed = CodeEditor(str(path), services(), QFont("monospace", 11))
        ed.load()
        self.addCleanup(ed.close)
        store = RecoveryStore(folder)
        type_text(ed, "unsaved")
        store.snapshot([ed])
        self.assertEqual(len(os.listdir(folder)), 1)
        self.assertEqual(store.orphans(root, 7), [])            # владелец (этот процесс) жив — не предлагать
        draft_file = os.path.join(folder, os.listdir(folder)[0])
        with open(draft_file, encoding="utf-8") as f:
            data = json.load(f)
        data["owner"] = "999999999:1"                            # «упавший» процесс
        with open(draft_file, "w", encoding="utf-8") as f:
            json.dump(data, f)
        found = store.orphans(root, 7)
        self.assertEqual([d.text for d in found], ["unsaved"])
        self.assertFalse(found[0].disk_changed())
        self.assertEqual(store.orphans(tempfile.mkdtemp(), 7), [])   # чужой проект — не предлагать
        data["saved_at"] = 0
        with open(draft_file, "w", encoding="utf-8") as f:
            json.dump(data, f)
        self.assertEqual(store.orphans(root, 7), [])             # старше срока — удалена
        self.assertEqual(os.listdir(folder), [])

    def test_saved_editor_drops_its_copy(self):
        folder = tempfile.mkdtemp()
        path = Path(tempfile.mkdtemp()) / "a.py"
        path.write_bytes(b"x = 1\n")
        ed = CodeEditor(str(path), services(), QFont("monospace", 11))
        ed.load()
        self.addCleanup(ed.close)
        store = RecoveryStore(folder)
        ed.saved.connect(store.discard)
        type_text(ed, "x = 2\n")
        store.snapshot([ed])
        self.assertTrue(os.listdir(folder))
        self.assertTrue(ed.save())
        self.assertEqual(os.listdir(folder), [])


class OptionsTests(unittest.TestCase):
    def settings(self):
        path = os.path.join(tempfile.mkdtemp(), "o.ini")
        return QSettings(path, QSettings.Format.IniFormat)

    def test_values_cast_clamp_and_signal(self):
        qs = self.settings()
        qs.setValue("editor/font_size", "99")
        qs.setValue("editor/hint_size", "12.0")
        qs.setValue("recovery/enabled", "false")
        opts = Options(qs)
        self.assertEqual(opts["editor/font_size"], 32)
        self.assertEqual(opts["editor/hint_size"], 12.0)
        self.assertFalse(opts["recovery/enabled"])
        changed = []
        opts.changed.connect(changed.append)
        self.assertTrue(opts.set("analyzers/restart_mb", 2048))
        self.assertFalse(opts.set("analyzers/restart_mb", 2048))
        self.assertEqual(changed, ["analyzers/restart_mb"])
        self.assertEqual(Options(qs)["analyzers/restart_mb"], 2048)

    def test_dialog_apply_reset_and_search(self):
        opts = Options(self.settings())
        dialog = OptionsDialog(opts)
        self.addCleanup(dialog.deleteLater)
        option, field, _row = dialog._editors["documents/reload_unmodified"]
        option.set_editor_value(field, False)
        dialog._touched()
        self.assertTrue(dialog.apply_button.isEnabled())
        dialog.apply()
        self.assertFalse(opts["documents/reload_unmodified"])
        dialog.search.setText("zzzz-nothing")
        self.assertTrue(all(item.isHidden() for item, _p, _i in dialog._page_items))
        dialog.search.setText("")
        self.assertFalse(any(item.isHidden() for item, _p, _i in dialog._page_items))
        dialog.tree.setCurrentItem(next(it for it, p, _i in dialog._page_items if "options.documents" in p.path))
        dialog._reset_page()
        dialog.apply()
        self.assertTrue(opts["documents/reload_unmodified"])


if __name__ == "__main__":
    unittest.main()
