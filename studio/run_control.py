"""Запуск (F5 / Ctrl+F5 / Shift+F5): процесс, вывод в «Вывод», после падения — переход к строке.

Что и как запускать, решает набор инструментов (toolchains): Python — файл интерпретатором, .NET — `dotnet run`,
Unity — открыть проект в Unity. Здесь только общий процесс по RunCommand.
"""
import os

from PyQt6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer
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
        self.launch = None          # OutputLaunch или TerminalLaunch — где идёт программа
        self.command = None
        self.path = None            # папка запущенного — относительные пути в выводе считаются от неё

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
    def running(self):
        return self.launch is not None and self.launch.running()

    def start(self, command):
        """Запустить RunCommand: во встроенном терминале (ввод с клавиатуры) или в окно «Вывод» — по параметрам."""
        win = self.win
        if self.running():
            if QMessageBox.question(win, APP_NAME, tr("mainwindow.program_already_running")) \
                    != QMessageBox.StandardButton.Yes:
                return
            self.launch.stop()
        if win.options["documents/save_before_run"]:
            win.save_all()
        self.command = command
        self.path = command.cwd
        win.errors.clear_source("common.run")
        kind = TerminalLaunch if win.options["run/in_terminal"] and win.terminal.available else OutputLaunch
        self.launch = kind(win, command, self._finished)
        self.launch.start()
        win.update_run_actions()
        win.status_label.setText(tr("mainwindow.running_ellipsis"))

    def _finished(self, code, error_text):
        win = self.win
        win.update_run_actions()
        win.status(tr("common.ready") if code == 0 else tr("mainwindow.program_ended_with_error_code") % code, 0)
        if code != 0 and self.command is not None and error_text:
            for path, line, col, message in self.command.problem_matcher(error_text):
                if os.path.isfile(path):
                    win.errors.set_group("common.run", path, [(line, col, message, "error")])
                    win.show_dock(win.d_errors)

    def stop(self):
        if self.running():
            self.launch.stop()

    def shutdown(self):
        """Выход из программы: запущенный процесс не переживает окно."""
        if self.running():
            self.launch.kill()


class OutputLaunch(QObject):
    """Вывод — в «Вывод» (быстро, без ввода с клавиатуры). done(код, текст stderr)."""

    def __init__(self, win, command, done):
        super().__init__(win)
        self.win, self.command, self.done = win, command, done
        self.proc = None
        self.stderr = ""

    def start(self):
        win, command = self.win, self.command
        win.output.clear()
        win.output.write(tr("mainwindow.running") % command.title, "common.run", C["link"])
        win.output.write(command.command_line(), "common.run", C["dim"])
        win.show_dock(win.d_output)
        self.proc = QProcess(self)
        self.proc.setWorkingDirectory(command.cwd)
        env = QProcessEnvironment.systemEnvironment()
        for key, value in command.env.items():
            env.insert(key, value)
        self.proc.setProcessEnvironment(env)
        self.proc.readyReadStandardOutput.connect(
            lambda: win.output.write(bytes(self.proc.readAllStandardOutput()).decode("utf-8", "replace"), "common.run"))
        self.proc.readyReadStandardError.connect(self._read_err)
        self.proc.finished.connect(self._finished)
        self.proc.errorOccurred.connect(lambda _e: win.output.write(
            tr("mainwindow.could_not_run") % self.proc.errorString(), "common.run", C["error"]))
        self.proc.start(command.program, command.args)

    def _read_err(self):
        text = bytes(self.proc.readAllStandardError()).decode("utf-8", "replace")
        self.stderr = (self.stderr + text)[-256_000:]
        self.win.output.write(text, "common.run", C["error"])

    def _finished(self, code, _status):
        self.win.output.write(tr("mainwindow.process_exited_with_code") % code, "common.run",
                              C["dim"] if code == 0 else C["warning"])
        self.done(code, self.stderr)

    def running(self):
        return self.proc is not None and self.proc.state() != QProcess.ProcessState.NotRunning

    def stop(self):
        if self.running():
            self.proc.kill()
            self.win.output.write(tr("mainwindow.stopped_by_user"), "common.run", C["warning"])

    kill = stop


class TerminalLaunch(QObject):
    """Программа во вкладке встроенного терминала (настоящий PTY): input(), Console.ReadLine(), цвета.
    Ошибки ищутся во всём выводе (stdout и stderr в терминале смешаны)."""

    def __init__(self, win, command, done):
        super().__init__(win)
        self.win, self.command, self.done = win, command, done
        self.session = None

    def start(self):
        win, command = self.win, self.command
        win.show_dock(win.d_terminal)
        self.session = win.terminal.run([command.program, *command.args], command.cwd, command.env, command.title)
        self.session.finished.connect(lambda code, text: self.done(code, text))

    def running(self):
        return self.session is not None and self.session.running()

    def stop(self):
        """Shift+F5 — как Ctrl+C, а если программа не послушалась — закрыть сеанс."""
        if self.running():
            self.session.process.interrupt()
            QTimer.singleShot(1500, lambda: self.running() and self.session.process.terminate())

    def kill(self):
        if self.running():
            self.session.process.terminate()
