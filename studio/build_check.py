"""«Сборка → Проверить решение» (Ctrl+Shift+B) и «Проверить текущий файл» (Ctrl+F7).

Пока это проверка синтаксиса: compile() для Python и свой разбор для C# (editor.check_csharp).
Настоящая сборка (dotnet build) — отдельный шаг плана."""
import os

from .editor import check_csharp
from .i18n import tr
from .theme import C


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


class SolutionCheck:
    def __init__(self, window):
        self.win = window

    def _text(self, path):
        for ed in self.win.editors():
            if ed.path == path:
                return ed.toPlainText(), None       # несохранённые правки тоже проверяются
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                return f.read(), None
        except OSError as e:
            return None, [(1, 1, str(e), "error")]

    def check_file(self, path):
        text, error = self._text(path)
        return error if error is not None else check_text(path, text)

    def check_solution(self):
        win = self.win
        if not win.root:
            return
        win.errors.clear_source("common.build")
        win.output.clear()
        win.output.write(tr("mainwindow.checking_solution") % os.path.basename(win.root), "common.build", C["link"])
        per_project = {}
        for p in win.explorer.files:
            if not p.endswith((".py", ".cs")):
                continue
            proj = os.path.relpath(p, win.root).split(os.sep)[0]
            diags = self.check_file(p)
            stat = per_project.setdefault(proj, [0, 0, 0])
            stat[0] += 1
            stat[1] += sum(1 for d in diags if d[3] == "error")
            stat[2] += sum(1 for d in diags if d[3] != "error")
            if diags:
                win.errors.set_group("common.build", p, diags)
                for line, col, msg, sev in diags:
                    win.output.write("%s(%d,%d): %s: %s" % (
                        os.path.relpath(p, win.root), line, col,
                        tr("mainwindow.error") if sev == "error" else tr("mainwindow.warning"), msg),
                        "common.build", C["error"] if sev == "error" else C["warning"])
        ok = bad = 0
        for proj, (n, e, w) in sorted(per_project.items()):
            win.output.write(tr("mainwindow.files_checked_errors_warnings") % (proj, n, e, w), "common.build")
            if e:
                bad += 1
            else:
                ok += 1
        win.output.write(tr("mainwindow.check_passed_failed") % (ok, bad), "common.build",
                         C["ok"] if not bad else C["error"])
        win.show_dock(win.d_output)
        win.status(tr("mainwindow.check_complete_passed_failed") % (ok, bad))

    def check_current(self):
        win = self.win
        ed = win.editor()
        if ed:
            ed.run_diagnostics()
            e = sum(1 for d in ed.diagnostics if d[3] == "error")
            win.status(tr("mainwindow.errors") % (os.path.basename(ed.path), e))
            win.show_dock(win.d_errors)
