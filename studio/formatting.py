"""Форматирование документа и выделения (Ctrl+K, Ctrl+D / Ctrl+K, Ctrl+F), как в Visual Studio.

Кто форматирует — решает `Formatter` по очереди:
1. анализатор языка, если он объявил форматирование (Roslyn для C#: учитывает .editorconfig проекта);
2. для Python — внешнее средство: ruff или black (официальные пакеты Arch `ruff`, `python-black`);
   pyright форматировать не умеет.
Правки ложатся одним шагом отмены. Если пока ждали ответ, текст изменился, — результат не применяется.
"""
import os
import shutil

from PyQt6.QtCore import QObject, QProcess, QTimer

from . import lsp
from .i18n import tr

TIMEOUT_MS = 15000


class PythonTool:
    """Внешний форматер Python: команда и аргументы для текста из stdin."""
    name = ""

    def __init__(self, exe):
        self.exe = exe

    @classmethod
    def find(cls):
        exe = shutil.which(cls.name)
        return cls(exe) if exe else None

    def args(self, path, selection):
        raise NotImplementedError


class Ruff(PythonTool):
    name = "ruff"

    def args(self, path, selection):
        out = ["format", "--stdin-filename", path]
        if selection:
            out += ["--range", "%d-%d" % (selection[0] + 1, selection[1] + 1)]
        return out + ["-"]


class Black(PythonTool):
    name = "black"

    def args(self, path, selection):
        out = ["-q", "--stdin-filename", path]
        if selection:
            out.append("--line-ranges=%d-%d" % (selection[0] + 1, selection[1] + 1))
        return out + ["-"]


PYTHON_TOOLS = {"ruff": Ruff, "black": Black}


def find_python_tool(preferred="auto"):
    order = [preferred] if preferred in PYTHON_TOOLS else ["ruff", "black"]
    for name in order:
        tool = PYTHON_TOOLS[name].find()
        if tool is not None:
            return tool
    return None


class Formatter(QObject):
    """Форматирует редактор окна. Из окна берёт: service_for, options, status."""

    def __init__(self, window):
        super().__init__(window)
        self.win = window
        self._proc = None

    def format(self, ed, selection_only=False):
        if ed is None or ed.isReadOnly():
            return
        selection = None
        if selection_only:
            c = ed.textCursor()
            if not c.hasSelection():
                self.win.status(tr("formatting.select_lines_first"))
                return
            doc = ed.document()
            end = c.selectionEnd() - 1 if c.selectionEnd() > c.selectionStart() else c.selectionEnd()
            selection = (doc.findBlock(c.selectionStart()).blockNumber(), doc.findBlock(end).blockNumber())
        text, revision = ed.toPlainText(), ed.document().revision()
        svc = self.win.service_for(ed.language)
        srv = svc.ready_for(ed.path) if svc is not None else None
        provider = "documentRangeFormattingProvider" if selection else "documentFormattingProvider"
        if srv is not None and srv.supports(provider):
            self.win.busy(tr("formatting.formatting"))
            srv.format(ed.path, text, ed.indent, lambda edits: self._apply(ed, revision, edits), selection)
            return
        if ed.lang == "python":
            tool = find_python_tool(self.win.options["formatting/python_tool"])
            if tool is None:
                self.win.status(tr("formatting.no_python_tool"), 12000)
                return
            self._run_tool(ed, tool, text, revision, selection)
            return
        if srv is None and svc is not None and svc.server_for(ed.path, start=False) is not None:
            self.win.status(tr("formatting.analyzer_loading"))
            return
        self.win.status(tr("formatting.not_supported") % ed.language.name)

    def _run_tool(self, ed, tool, text, revision, selection):
        if self._proc is not None:
            self._proc.kill()
        proc = self._proc = QProcess(self)
        proc.setWorkingDirectory(os.path.dirname(ed.path))
        timer = QTimer(proc, singleShot=True, interval=TIMEOUT_MS, timeout=proc.kill)

        finished = []

        def done(code, _status):
            if finished:                # и finished, и errorOccurred — обработать один раз
                return
            finished.append(code)
            timer.stop()
            if proc is self._proc:
                self._proc = None
            out = bytes(proc.readAllStandardOutput()).decode("utf-8", "replace")
            err = bytes(proc.readAllStandardError()).decode("utf-8", "replace").strip()
            proc.deleteLater()
            if code != 0:
                self.win.busy("")
                self.win.status(tr("formatting.tool_failed") % (tool.name, err.splitlines()[-1] if err else code), 15000)
                return
            self._apply(ed, revision, lsp.line_diff_edits(text, out), tool.name)

        proc.finished.connect(done)
        proc.errorOccurred.connect(lambda _e: proc.state() == QProcess.ProcessState.NotRunning and done(-1, None))
        self.win.busy(tr("formatting.formatting"))
        proc.start(tool.exe, tool.args(ed.path, selection))
        proc.write(text.encode("utf-8"))
        proc.closeWriteChannel()
        timer.start()

    def _apply(self, ed, revision, edits, tool=None):
        self.win.busy("")
        if ed not in self.win.views():                  # вкладку (или второе окно) успели закрыть
            return
        if edits is None:
            self.win.status(tr("formatting.failed"))
            return
        if ed.document().revision() != revision:
            self.win.status(tr("formatting.text_changed"))
            return
        if not edits:
            self.win.status(tr("formatting.already_formatted"))
            return
        bar = ed.verticalScrollBar()
        scroll = bar.value()
        ed.apply_edits(edits)
        bar.setValue(scroll)
        self.win.status(tr("formatting.done") + (" (%s)" % tool if tool else ""))
