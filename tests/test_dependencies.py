import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from PyQt6.QtWidgets import QApplication

from studio.dependencies.base import MISMATCH, MISSING, OK
from studio.dependencies.python import PythonDependencies, normalize, parse_line

app = QApplication.instance() or QApplication([])

PYPROJECT = '''[project]
name = "demo"
dependencies = [
    "jedi>=0.10",
    "surely-not-installed-pkg-xyz>=1",
]

[project.optional-dependencies]
dev = ["jedi>=999"]

[dependency-groups]
test = ["pytest"]
'''


def spin(seconds, until):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
        if until():
            return True
    return False


class Parse(unittest.TestCase):
    def test_lines(self):
        r = parse_line("requests[socks] >= 2.0 ; python_version >= '3'", "r.txt", 3)
        self.assertEqual((r.name, r.spec, r.line), ("requests", ">=2.0", 3))
        self.assertIsNone(parse_line("pywin32; sys_platform == 'win32'", "r.txt", 1))     # не для Linux
        for skipped in ("# comment", "-r base.txt", "-e .", "git+https://x/y.git", "", "   "):
            self.assertIsNone(parse_line(skipped, "r.txt", 1), skipped)
        self.assertEqual(parse_line("PyQt6  # GUI", "r.txt", 1).name, "PyQt6")
        self.assertEqual(normalize("PyQt_6.x"), "pyqt-6-x")                      # PEP 503
        self.assertEqual(normalize("PyQt6"), normalize("pyqt6"))

    def test_pyproject_groups_lines_and_requirements(self):
        root = Path(tempfile.mkdtemp())
        (root / "pyproject.toml").write_text(PYPROJECT)
        (root / "requirements-dev.txt").write_text("# dev\njedi>=999\nblack\n")
        reqs = PythonDependencies(str(root), sys.executable).requirements()
        rows = [(r.name, r.spec, r.group, os.path.basename(r.source), r.line) for r in reqs]
        self.assertIn(("jedi", ">=0.10", "", "pyproject.toml", 4), rows)
        self.assertIn(("jedi", ">=999", "dev", "pyproject.toml", 9), rows)
        self.assertIn(("pytest", "", "test", "pyproject.toml", 12), rows)
        self.assertIn(("black", "", "dev", "requirements-dev.txt", 3), rows)
        self.assertEqual(sum(1 for r in reqs if r.name == "jedi" and r.group == "dev"), 1)    # дубль — одной строкой


class Check(unittest.TestCase):
    def test_states_and_system_commands(self):
        root = Path(tempfile.mkdtemp())
        (root / "pyproject.toml").write_text(PYPROJECT)
        provider = PythonDependencies(str(root), sys.executable)
        reqs = {(r.name, r.group): r for r in provider.check(provider.requirements())}
        self.assertEqual(reqs[("jedi", "")].state, OK)
        self.assertEqual(reqs[("jedi", "dev")].state, MISMATCH)
        self.assertEqual(reqs[("surely-not-installed-pkg-xyz", "")].state, MISSING)
        if provider.is_system():                    # Arch: системный Python ставит пакеты только pacman
            self.assertIsNone(reqs[("surely-not-installed-pkg-xyz", "")].system_package)
            missing = [reqs[("surely-not-installed-pkg-xyz", "")]]
            self.assertEqual(provider.install_commands(missing), [])        # нет в репозитории — только .venv
            self.assertIsNotNone(provider.create_environment_command())

    def test_virtualenv_uses_pip(self):
        root = Path(tempfile.mkdtemp())
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(root / ".venv")], check=True, timeout=120)
        venv_python = str(root / ".venv" / "bin" / "python")
        (root / "requirements.txt").write_text("jedi>=0.10\n")
        provider = PythonDependencies(str(root), venv_python)
        self.assertFalse(provider.is_system())
        reqs = provider.check(provider.requirements())
        self.assertEqual(reqs[0].state, MISSING)                          # в пустом venv ничего нет
        self.assertEqual(provider.install_commands(reqs), [('"%s" -m pip install "jedi>=0.10"' % venv_python, "pip")])
        self.assertIsNone(provider.create_environment_command())          # .venv уже есть


class Window(unittest.TestCase):
    def test_panel_shows_checked_requirements(self):
        from studio.mainwindow import MainWindow
        win = MainWindow(screenshot=True)
        self.addCleanup(lambda: (win.jedi.stop(), win.csharp_index.stop(), win.close()))
        root = Path(tempfile.mkdtemp())
        (root / "requirements.txt").write_text("jedi>=0.10\nsurely-not-installed-pkg-xyz\n")
        (root / "main.py").write_text("print(1)\n")                    # Python-проект без .py не бывает
        win.set_root(str(root))
        win.dependencies.check()
        self.assertTrue(spin(30, lambda: win.dependencies.requirements))
        tree = win.dependencies.panel.tree
        self.assertEqual(tree.topLevelItemCount(), 2)
        self.assertIn("jedi", tree.topLevelItem(0).text(0))
        self.assertTrue(win.dependencies.panel.install_button.isEnabled())     # есть недостающий пакет


if __name__ == "__main__":
    unittest.main()
