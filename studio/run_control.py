"""Запуск (F5 / Ctrl+F5 / Shift+F5): процесс, вывод в «Вывод», после падения — переход к строке.

Что и как запускать, решает набор инструментов (toolchains): Python — файл интерпретатором, .NET — `dotnet run`,
Unity — открыть проект в Unity. Здесь только общий процесс по RunCommand.
"""
import os

from PyQt6.QtCore import QObject, QProcess, QProcessEnvironment
from PyQt6.QtWidgets import QMessageBox

from . import toolchains
from .i18n import tr
from .theme import C

APP_NAME = "UniPy Studio"


class RunController(QObject):
    """Один запускаемый процесс. Из окна берёт: выбор «Запуск», project_context(), панели вывода и ошибок."""

    def __init__(self, window):
        super().__init__(window)
        self.win = window
        self.proc = None
        self.command = None
        self.path = None            # папка запущенного — относительные пути в выводе считаются от неё
        self.stderr = ""

    def running(self):
        return self.proc is not None and self.proc.state() != QProcess.ProcessState.NotRunning

    # --- что запускать
    def run_selected(self):
        """Ctrl+F5: выбранное в списке «Запуск» или, при «Текущий файл», то, что узнал первый набор."""
        ed = self.win.editor()
        if not self._launch(self.win.startup.currentData(), ed.path if ed else None):
            self.win.status(tr("mainwindow.open_file_python_select_boot"))

    def run_path(self, path):
        """Запустить то, что относится к файлу (обозреватель → «Запустить»). False — нечего."""
        return self._launch(None, path)

    def _launch(self, choice, path):
        ctx = self.win.project_context()
        found = toolchains.resolve_target(ctx, choice, path)
        if found is None:
            return False
        tc, target = found
        ok, hint = tc.available()
        if not ok:
            self.win.status(tr("run.tool_missing") % (tc.name, hint), 15000)
            return True
        tc.launch(ctx, target, self)
        return True

    # --- процесс
    def start(self, command):
        win = self.win
        if self.running():
            if QMessageBox.question(win, APP_NAME, tr("mainwindow.program_already_running")) \
                    != QMessageBox.StandardButton.Yes:
                return
            self.stop()
            self.proc.waitForFinished(2000)
        if win.options["documents/save_before_run"]:
            win.save_all()
        self.command = command
        self.path = command.cwd
        self.stderr = ""
        win.errors.clear_source("common.run")
        win.output.clear()
        win.output.write(tr("mainwindow.running") % command.title, "common.run", C["link"])
        win.output.write(command.command_line(), "common.run", C["dim"])
        win.show_dock(win.d_output)
        if self.proc is not None:
            self.proc.deleteLater()
        self.proc = QProcess(self)
        self.proc.setWorkingDirectory(command.cwd)
        env = QProcessEnvironment.systemEnvironment()
        for key, value in command.env.items():
            env.insert(key, value)
        self.proc.setProcessEnvironment(env)
        self.proc.readyReadStandardOutput.connect(self._read_out)
        self.proc.readyReadStandardError.connect(self._read_err)
        self.proc.finished.connect(self._finished)
        self.proc.errorOccurred.connect(self._failed)
        self.proc.start(command.program, command.args)
        win.update_run_actions()
        win.status_label.setText(tr("mainwindow.running_ellipsis"))

    def _failed(self, _err):
        self.win.output.write(tr("mainwindow.could_not_run") % self.proc.errorString(), "common.run", C["error"])

    def _read_out(self):
        self.win.output.write(bytes(self.proc.readAllStandardOutput()).decode("utf-8", "replace"), "common.run")

    def _read_err(self):
        text = bytes(self.proc.readAllStandardError()).decode("utf-8", "replace")
        self.stderr = (self.stderr + text)[-256_000:]
        self.win.output.write(text, "common.run", C["error"])

    def _finished(self, code, _status):
        win = self.win
        win.update_run_actions()
        win.output.write(tr("mainwindow.process_exited_with_code") % code, "common.run",
                         C["dim"] if code == 0 else C["warning"])
        win.status(tr("common.ready") if code == 0 else tr("mainwindow.program_ended_with_error_code") % code, 0)
        if code != 0 and self.command is not None and self.stderr:
            for path, line, col, message in self.command.problem_matcher(self.stderr):
                if os.path.isfile(path):
                    win.errors.set_group("common.run", path, [(line, col, message, "error")])
                    win.show_dock(win.d_errors)

    def stop(self):
        if self.running():
            self.proc.kill()
            self.win.output.write(tr("mainwindow.stopped_by_user"), "common.run", C["warning"])

    def shutdown(self):
        """Выход из программы: запущенный процесс не переживает окно."""
        if self.running():
            self.proc.kill()
