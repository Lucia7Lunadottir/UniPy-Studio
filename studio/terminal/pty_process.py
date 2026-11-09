"""Процесс в псевдотерминале (PTY): для программы это настоящий терминал — цвета, ввод с клавиатуры,
Ctrl+C, размер окна, полноэкранные программы (vim, htop).

Запуск через `setsid -c` (util-linux): новый сеанс с этим терминалом в роли управляющего —
иначе Ctrl+C не доходит до программы, а оболочка пишет «no job control»."""
import errno
import fcntl
import os
import signal
import struct
import subprocess
import termios

from PyQt6.QtCore import QObject, QSocketNotifier, QTimer, pyqtSignal

READ_CHUNK = 65536


class PtyProcess(QObject):
    data = pyqtSignal(bytes)
    finished = pyqtSignal(object)           # код выхода (None — неизвестен)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.proc = None
        self.master = None
        self._notifier = None
        self._done = False
        self._timer = QTimer(self, interval=250, timeout=self._poll)

    def start(self, argv, cwd, env=None, cols=80, rows=24):
        master, slave = os.openpty()
        self._resize_fd(master, cols, rows)
        environment = dict(os.environ)
        environment.update({"TERM": "xterm-256color", "COLORTERM": "truecolor", "COLUMNS": str(cols),
                            "LINES": str(rows)})
        environment.update(env or {})
        try:
            self.proc = subprocess.Popen(["setsid", "-c", *argv], stdin=slave, stdout=slave, stderr=slave,
                                         cwd=cwd if cwd and os.path.isdir(cwd) else None, env=environment,
                                         close_fds=True)
        except OSError as e:
            os.close(master)
            os.close(slave)
            self.data.emit(("%s\r\n" % e).encode("utf-8"))
            self._done = True
            self.finished.emit(None)
            return False
        os.close(slave)
        os.set_blocking(master, False)
        self.master = master
        self._notifier = QSocketNotifier(master, QSocketNotifier.Type.Read, self)
        self._notifier.activated.connect(self._read)
        self._timer.start()
        return True

    @property
    def pid(self):
        return self.proc.pid if self.proc else None

    def running(self):
        return self.proc is not None and not self._done

    def _read(self, *_):
        while self.master is not None:
            try:
                chunk = os.read(self.master, READ_CHUNK)
            except BlockingIOError:
                return
            except OSError as e:            # EIO — все концы терминала закрыты: программа завершилась
                if e.errno in (errno.EIO, errno.EBADF):
                    self._close()
                return
            if not chunk:
                self._close()
                return
            self.data.emit(chunk)

    def write(self, payload):
        if self.master is None:
            return
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        try:
            os.write(self.master, payload)
        except OSError:
            pass

    @staticmethod
    def _resize_fd(fd, cols, rows):
        try:
            fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        except OSError:
            pass

    def resize(self, cols, rows):
        """Размер окна терминала — программа получает SIGWINCH и перерисовывается."""
        if self.master is not None:
            self._resize_fd(self.master, cols, rows)

    def _poll(self):
        if self.proc is not None and self.proc.poll() is not None:
            QTimer.singleShot(100, self._close)          # дочитать последний вывод

    def _close(self):
        if self._done:
            return
        if self.master is not None:
            self._read_rest()
        self._done = True
        self._timer.stop()
        if self._notifier is not None:
            self._notifier.setEnabled(False)
            self._notifier.deleteLater()
            self._notifier = None
        if self.master is not None:
            os.close(self.master)
            self.master = None
        code = None
        if self.proc is not None:
            try:
                code = self.proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                code = None
        self.finished.emit(code)

    def _read_rest(self):
        while True:
            try:
                chunk = os.read(self.master, READ_CHUNK)
            except OSError:
                return
            if not chunk:
                return
            self.data.emit(chunk)

    def set_reading(self, on):
        """Притормозить чтение (программа сама подождёт на записи) — пока виджет не разобрал накопленное."""
        if self._notifier is not None:
            self._notifier.setEnabled(on)
            if on:
                QTimer.singleShot(0, self._read)

    def interrupt(self):
        """Как Ctrl+C в терминале — для кнопки «Остановить»."""
        self.write(b"\x03")

    def terminate(self):
        """Закрыть: SIGHUP сеансу (как закрытие окна терминала), через секунду — SIGKILL."""
        if self.proc is None or self._done:
            return
        try:
            os.killpg(self.proc.pid, signal.SIGHUP)
        except (ProcessLookupError, PermissionError):
            pass
        QTimer.singleShot(1000, self._kill)

    def _kill(self):
        if self.proc is not None and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        self._close()
