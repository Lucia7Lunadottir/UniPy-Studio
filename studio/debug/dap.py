"""Клиент Debug Adapter Protocol — общий язык редактора и отладчиков (debugpy, netcoredbg и др.),
тот же, что у VS Code и Rider. Адаптер — отдельный процесс, обмен через stdin/stdout в рамке
Content-Length (framing.py). Здесь только транспорт: запросы, ответы, события; смысл — в session.py."""
import os

from PyQt6.QtCore import QObject, QProcess, QProcessEnvironment, pyqtSignal

from ..framing import frame, split_frames


class DapConnection(QObject):
    event_received = pyqtSignal(str, object)        # имя события, body (не «event»: это метод QObject)
    reverse_request = pyqtSignal(int, str, object)  # seq, команда, arguments — адаптер просит клиента
    stderr = pyqtSignal(str)
    exited = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.proc = QProcess(self)
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.readyReadStandardError.connect(
            lambda: self.stderr.emit(bytes(self.proc.readAllStandardError()).decode("utf-8", "replace")))
        self.proc.finished.connect(lambda code, _status: self.exited.emit(code))
        self.proc.errorOccurred.connect(self._error)
        self._buf = b""
        self._seq = 1
        self._callbacks = {}

    def start(self, program, args, cwd, env=None):
        environment = QProcessEnvironment.systemEnvironment()
        for key, value in (env or {}).items():
            environment.insert(key, value)
        self.proc.setProcessEnvironment(environment)
        self.proc.setWorkingDirectory(cwd if cwd and os.path.isdir(cwd) else os.path.expanduser("~"))
        self.proc.start(program, list(args))
        return self.proc.waitForStarted(5000)

    def running(self):
        return self.proc.state() != QProcess.ProcessState.NotRunning

    def request(self, command, arguments=None, callback=None):
        """callback(success, body, message) — когда придёт ответ."""
        seq = self._next()
        if callback is not None:
            self._callbacks[seq] = callback
        self._send({"seq": seq, "type": "request", "command": command, "arguments": arguments or {}})
        return seq

    def respond(self, request_seq, command, success=True, body=None, message=None):
        msg = {"seq": self._next(), "type": "response", "request_seq": request_seq, "command": command,
               "success": success, "body": body or {}}
        if message:
            msg["message"] = message
        self._send(msg)

    def _next(self):
        seq = self._seq
        self._seq += 1
        return seq

    def _send(self, msg):
        if self.running():
            self.proc.write(frame(msg))

    def _read(self):
        self._buf += bytes(self.proc.readAllStandardOutput())
        messages, self._buf = split_frames(self._buf)
        for msg in messages:
            self.dispatch(msg)

    def dispatch(self, msg):
        kind = msg.get("type")
        if kind == "response":
            cb = self._callbacks.pop(msg.get("request_seq"), None)
            if cb is not None:
                cb(bool(msg.get("success")), msg.get("body") or {}, msg.get("message") or "")
        elif kind == "event":
            self.event_received.emit(msg.get("event", ""), msg.get("body") or {})
        elif kind == "request":
            self.reverse_request.emit(msg.get("seq", 0), msg.get("command", ""), msg.get("arguments") or {})

    def _error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.stderr.emit(self.proc.errorString())
            self.exited.emit(-1)

    def stop(self):
        """Адаптер получил disconnect — дать ему закончить; не закончил — убить."""
        if self.running() and not self.proc.waitForFinished(1500):
            self.proc.kill()
            self.proc.waitForFinished(500)
        for cb in list(self._callbacks.values()):
            cb(False, {}, "disconnected")
        self._callbacks.clear()
        self._buf = b""
