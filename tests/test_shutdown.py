import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from pathlib import Path

from PyQt6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])


class Shutdown(unittest.TestCase):
    def test_closed_window_never_restarts_analyzers(self):
        """Регрессия: запоздалый таймер проверки ошибок поднимал pyright после закрытия окна —
        процесс переживал окно, а при выходе Python падал (QProcess в деструкторе звал удалённый обработчик)."""
        from studio.mainwindow import MainWindow
        win = MainWindow(screenshot=True)
        root = Path(tempfile.mkdtemp())
        (root / "pyproject.toml").write_text("[project]\nname='x'\n")
        (root / "a.py").write_text("x = 1\n")
        win.set_root(str(root))
        ed = win.open_path(str(root / "a.py"))
        win.close()                                         # и в режиме скриншотов фоновое гасится
        self.assertTrue(all(getattr(svc, "closed", False) for svc in win.language_services))
        ed.run_diagnostics()                                # как сработавший потом таймер
        self.assertEqual([s for svc in win.language_services for s in svc.servers.values()], [])
        self.assertIsNone(win.python_ls.server_for(str(root / "a.py")))
        win.jedi.stop()
        win.csharp_index.stop()


if __name__ == "__main__":
    unittest.main()
