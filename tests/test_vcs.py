import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from PyQt6.QtWidgets import QApplication

from studio import vcs
from studio.i18n import tr
from studio.vcs.base import ADDED, CONFLICT, DELETED, MODIFIED, RENAMED, UNTRACKED
from studio.vcs.git import GitRepository, parse_status_v2
from studio.vcs.linediff import line_markers
from studio.vcs.diff_view import align

app = QApplication.instance() or QApplication([])


def spin(seconds, until):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
        if until():
            return True
    return False


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t"})


def make_repo():
    root = Path(tempfile.mkdtemp()) / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "t")
    git(root, "config", "user.email", "t@t")
    (root / "Player.cs").write_bytes("// Игрок\nclass Player {}\n".encode("cp1251"))
    (root / "a.py").write_text("a = 1\nb = 2\nc = 3\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    return root


class Pure(unittest.TestCase):
    def test_status_v2(self):
        data = ("# branch.head main\0"
                "1 .M N... 100644 100644 100644 a b src/a.py\0"
                "1 A. N... 000000 100644 100644 0 b new.py\0"
                "2 R. N... 100644 100644 100644 a b R100 renamed.py\0old.py\0"
                "u UU N... 100644 100644 100644 100644 a b c conflict.cs\0"
                "? notes/\0")
        st = parse_status_v2(data, "/r")
        self.assertEqual(st["/r/src/a.py"].unstaged, MODIFIED)
        self.assertEqual(st["/r/new.py"].staged, ADDED)
        self.assertEqual((st["/r/renamed.py"].staged, st["/r/renamed.py"].original), (RENAMED, "/r/old.py"))
        self.assertEqual(st["/r/conflict.cs"].main(), CONFLICT)
        self.assertEqual(st["/r/notes"].unstaged, UNTRACKED)

    def test_line_markers_and_alignment(self):
        base = "a\nb\nc\nd\n"
        self.assertEqual(line_markers(base, "a\nB\nc\nd\n"), {2: MODIFIED})
        self.assertEqual(line_markers(base, "a\nb\nnew\nc\nd\n"), {3: ADDED})
        self.assertEqual(line_markers(base, "a\nd\n"), {1: DELETED})          # удалено после строки 1
        self.assertEqual(line_markers(None, "x\ny"), {1: ADDED, 2: ADDED})
        left, right = align("a\nb\nc", "a\nB\nc\nd")
        self.assertEqual(len(left), len(right))
        self.assertEqual([k for _n, _t, k in right], ["equal", "replace", "equal", "insert"])
        self.assertEqual(left[-1], (None, "", "filler"))


class LiveGit(unittest.TestCase):
    def test_repository_round_trip(self):
        root = make_repo()
        repo = vcs.detect(str(root / "a.py"))
        self.assertIsInstance(repo, GitRepository)
        self.assertEqual((repo.root, repo.branch()), (str(root), "main"))
        self.assertEqual(repo.status(), {})
        self.assertEqual(repo.base_text(str(root / "Player.cs")), "// Игрок\nclass Player {}\n")    # cp1251
        self.assertIsNone(repo.base_text(str(root / "nope.py")))
        (root / "a.py").write_text("a = 1\nb = 20\nc = 3\n")
        (root / "new.py").write_text("x\n")
        st = repo.status()
        self.assertEqual((st[str(root / "a.py")].unstaged, st[str(root / "new.py")].unstaged), (MODIFIED, UNTRACKED))
        repo.stage([str(root / "new.py")])
        self.assertEqual(repo.status()[str(root / "new.py")].staged, ADDED)
        repo.unstage([str(root / "new.py")])
        self.assertEqual(repo.status()[str(root / "new.py")].unstaged, UNTRACKED)
        repo.discard([str(root / "a.py")])
        self.assertEqual((root / "a.py").read_text(), "a = 1\nb = 2\nc = 3\n")
        repo.stage([str(root / "new.py")])
        repo.commit("Add new.py\n\nWith body")
        self.assertEqual(repo.status(), {})
        git(root, "branch", "feature")
        self.assertEqual(repo.branches(), [("feature", False), ("main", True)])
        repo.checkout("feature")
        self.assertEqual(repo.branch(), "feature")
        self.assertEqual(repo.remote_command("push"), ("git", ["push"], str(root)))     # нет удалённых — как есть
        self.assertIsNone(vcs.detect(tempfile.mkdtemp()))


class WindowGit(unittest.TestCase):
    def test_explorer_markers_panel_and_commit(self):
        from studio.mainwindow import MainWindow
        root = make_repo()
        win = MainWindow(screenshot=True)
        try:
            win.set_root(str(root))
            self.assertTrue(spin(5, lambda: win.vcs.branch == "main"))
            self.assertEqual(win.branch_label.text(), "⎇ main")
            ed = win.open_path(str(root / "a.py"))
            self.assertTrue(spin(5, lambda: str(root / "a.py") in win.vcs._base))
            ed.setPlainText("a = 1\nb = 22\nc = 3\nd = 4\n")
            self.assertTrue(spin(3, lambda: ed.vcs_markers == {2: MODIFIED, 4: ADDED}))
            self.assertTrue(ed.save())
            self.assertTrue(spin(5, lambda: str(root / "a.py") in win.vcs.status))
            item = self._explorer_item(win, str(root / "a.py"))
            self.assertEqual(item.foreground(0).color().name().upper(), "#E2C08D")       # изменён — жёлтый
            panel = win.vcs.panel
            self.assertEqual(panel.tree.topLevelItem(1).childCount(), 1)                  # «Изменения (1)»
            self.assertEqual(panel.commit_button.text(), tr("vcs.commit_all"))          # ничего не индексировано
            panel.message.setPlainText("Change b")
            panel._commit()                                                               # «Зафиксировать все»
            self.assertTrue(spin(5, lambda: win.vcs.status == {}))
            self.assertTrue(spin(5, lambda: ed.vcs_markers == {}))                        # коммит — полоски исчезли
            log = subprocess.run(["git", "log", "--format=%s", "-1"], cwd=root, capture_output=True, text=True).stdout
            self.assertEqual(log.strip(), "Change b")
        finally:
            win.jedi.stop()
            win.csharp_index.stop()
            for svc in win.language_services:
                svc.stop()
            win.close()

    @staticmethod
    def _explorer_item(win, path):
        stack = [win.explorer.tree.topLevelItem(i) for i in range(win.explorer.tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            if item.data(0, 256) == path:
                return item
            stack.extend(item.child(i) for i in range(item.childCount()))
        raise AssertionError(path)


if __name__ == "__main__":
    unittest.main()
