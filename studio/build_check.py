"""Проверка одного файла (Ctrl+F7) и проверка синтаксиса текста — общая с build.PythonCheck.

Полная сборка решения (Ctrl+Shift+B) — в build.py."""
import os

from .editor import check_csharp
from .i18n import tr


def check_text(path, text):
    """Ошибки одного файла: [(строка, столбец, сообщение, "error"/"warning")]."""
    if path.endswith(".py"):
        try:
            compile(text, path, "exec")
        except SyntaxError as e:
            return [(e.lineno or 1, e.offset or 1, tr("common.syntax_error") % e.msg, "error")]
        return []
    if path.endswith(".cs"):
        return check_csharp(text)
    return []


class FileCheck:
    """Ctrl+F7: перепроверить текущий файл анализатором и показать «Список ошибок»."""

    def __init__(self, window):
        self.win = window

    def check_current(self):
        win = self.win
        ed = win.editor()
        if ed:
            ed.run_diagnostics()
            e = sum(1 for d in ed.diagnostics if d[3] == "error")
            win.status(tr("mainwindow.errors") % (os.path.basename(ed.path), e))
            win.show_dock(win.d_errors)
