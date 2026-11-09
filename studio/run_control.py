"""Запуск Python-файла (F5 / Ctrl+F5 / Shift+F5): процесс, вывод, ошибка с переходом к строке."""
import os
import re
import sys

from PyQt6.QtCore import QObject, QProcess, QProcessEnvironment
from PyQt6.QtWidgets import QMessageBox

from .i18n import tr
from .theme import C

APP_NAME = "UniPy Studio"


class RunController(QObject):
    """Один запускаемый процесс. Из окна берёт: выбор «Запуск» и интерпретатора, панели вывода и ошибок,
    панель Unity (C# запускается в Unity), сохранение и параметры."""

    def __init__(self, window):
        super().__init__(window)
        self.win = window
        self.proc = None
        self.path = None            # что запущено — относительные пути в выводе считаются от него
        self.stderr = ""

    def running(self):
        return self.proc is not None and self.proc.state() != QProcess.ProcessState.NotRunning

    def run_selected(self):
        win = self.win
        target = win.startup.currentData()
        if target:
            self.run_file(target)
            return
        ed = win.editor()
        if ed and ed.lang == "python":
            self.run_file(ed.path)
        elif ed and ed.lang == "csharp":
            win.output.write(tr("mainwindow.c_scripts_run_unity_opening"), "Unity", C["warning"])
            win.show_dock(win.d_output)
            win.unity.open_in_unity()
        else:
            win.status(tr("mainwindow.open_file_python_select_boot"))

    def run_file(self, path):
        win = self.win
        if self.running():
            if QMessageBox.question(win, APP_NAME, tr("mainwindow.program_already_running")) \
                    != QMessageBox.StandardButton.Yes:
                return
            self.stop()
            self.proc.waitForFinished(2000)
        if win.options["documents/save_before_run"]:
            win.save_all()
        self.path = path
        self.stderr = ""
        win.errors.clear_source("common.run")
        interp = win.interp.currentData() or sys.executable
        win.output.clear()
        win.output.write(tr("mainwindow.running") % os.path.relpath(path, win.root or os.path.dirname(path)),
                         "common.run", C["link"])
        win.output.write("%s %s" % (interp, path), "common.run", C["dim"])
        win.show_dock(win.d_output)
        if self.proc is not None:
            self.proc.deleteLater()
        self.proc = QProcess(self)
        self.proc.setWorkingDirectory(os.path.dirname(path))
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        env.insert("PYTHONIOENCODING", "utf-8")
        self.proc.setProcessEnvironment(env)
        self.proc.readyReadStandardOutput.connect(self._read_out)
        self.proc.readyReadStandardError.connect(self._read_err)
        self.proc.finished.connect(self._finished)
        self.proc.errorOccurred.connect(self._failed)
        self.proc.start(interp, ["-u", path])
        win.a_stop.setEnabled(True)
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
        win.a_stop.setEnabled(False)
        win.output.write(tr("mainwindow.process_exited_with_code") % code, "common.run",
                         C["dim"] if code == 0 else C["warning"])
        win.status(tr("common.ready") if code == 0 else tr("mainwindow.program_ended_with_error_code") % code, 0)
        if self.stderr and "Traceback" in self.stderr:
            frames = re.findall(r'File "(.+?)", line (\d+)', self.stderr)
            last = self.stderr.strip().splitlines()[-1]
            if frames:
                own = [f for f in frames if win.root and os.path.abspath(f[0]).startswith(win.root)] or frames
                path, line = own[-1]
                win.errors.set_group("common.run", os.path.abspath(path), [(int(line), 1, last, "error")])
                win.show_dock(win.d_errors)

    def stop(self):
        if self.running():
            self.proc.kill()
            self.win.output.write(tr("mainwindow.stopped_by_user"), "common.run", C["warning"])

    def shutdown(self):
        """Выход из программы: запущенный процесс не переживает окно."""
        if self.running():
            self.proc.kill()
