"""Сеанс отладки: порядок разговора с адаптером по DAP, остановки, шаги, стек, переменные.

Общий для всех языков. Что запускать — DebugConfig от набора инструментов (toolchains): программа
адаптера и запрос launch/attach. Порядок по спецификации DAP:
  initialize → (ответ: возможности) → launch/attach → событие initialized →
  setBreakpoints (по файлу) → setExceptionBreakpoints → configurationDone → … stopped / continued … → terminated.
"""
import os

from PyQt6.QtCore import QObject, pyqtSignal

from .dap import DapConnection

IDLE, STARTING, RUNNING, STOPPED, ENDED = "idle", "starting", "running", "stopped", "ended"


class DebugConfig:
    """Как отлаживать цель: команда адаптера и запрос к нему. request — "launch" или "attach";
    arguments — тело этого запроса (у каждого адаптера своё: program, cwd, justMyCode…)."""

    def __init__(self, adapter, adapter_args, request, arguments, adapter_id="", cwd=None, env=None, title=""):
        self.adapter, self.adapter_args = adapter, list(adapter_args)
        self.request, self.arguments = request, dict(arguments)
        self.adapter_id = adapter_id
        self.cwd = cwd
        self.env = dict(env or {})
        self.title = title


class StackFrame:
    __slots__ = ("id", "name", "path", "line", "column")

    def __init__(self, raw):
        self.id = raw.get("id")
        self.name = raw.get("name", "")
        source = raw.get("source") or {}
        self.path = source.get("path")
        self.line = raw.get("line", 0)
        self.column = raw.get("column", 1)


class Variable:
    __slots__ = ("name", "value", "type", "reference")

    def __init__(self, raw):
        self.name = raw.get("name", "")
        self.value = raw.get("value", raw.get("result", ""))
        self.type = raw.get("type", "")
        self.reference = raw.get("variablesReference", 0)     # > 0 — есть дети (поля, элементы)


class DebugSession(QObject):
    state_changed = pyqtSignal(str)
    stopped = pyqtSignal(object, str, object)   # thread id, причина, [StackFrame]
    output = pyqtSignal(str, str)               # категория (stdout/stderr/console), текст
    breakpoints_bound = pyqtSignal(str, object)  # путь, [(строка, verified, message)]
    ended = pyqtSignal(object)                  # код выхода программы или None

    def __init__(self, config, breakpoints, exception_filters=None, run_in_terminal=None, parent=None):
        super().__init__(parent)
        self.config = config
        # run_in_terminal(argv, cwd, env, title) → pid или None — запустить программу во встроенном терминале
        self.run_in_terminal = run_in_terminal
        self.store = breakpoints
        self.exception_filters = exception_filters      # None — какие адаптер считает включёнными по умолчанию
        self.state = IDLE
        self.capabilities = {}
        self.thread_id = None
        self.frames = []
        self.exit_code = None
        self._configured = False
        self.conn = DapConnection(self)
        self.conn.event_received.connect(self._event)
        self.conn.reverse_request.connect(self._reverse_request)
        self.conn.stderr.connect(lambda text: self.output.emit("adapter", text))
        self.conn.exited.connect(self._adapter_exited)
        self.store.changed.connect(self._breakpoints_changed)

    # --- жизненный цикл
    def start(self):
        self._set_state(STARTING)
        if not self.conn.start(self.config.adapter, self.config.adapter_args, self.config.cwd, self.config.env):
            self.output.emit("stderr", "%s %s\n" % (self.config.adapter, " ".join(self.config.adapter_args)))
            self._finish(None)
            return
        self.conn.request("initialize", {
            "clientID": "unipy-studio", "clientName": "UniPy Studio", "adapterID": self.config.adapter_id,
            "pathFormat": "path", "linesStartAt1": True, "columnsStartAt1": True,
            "supportsVariableType": True, "supportsVariablePaging": False,
            "supportsRunInTerminalRequest": self.run_in_terminal is not None,
            "supportsProgressReporting": False, "locale": os.environ.get("LANG", "en").split(".")[0]},
            self._initialized)

    def _initialized(self, ok, body, message):
        if not ok:
            self.output.emit("stderr", message + "\n")
            self.terminate()
            return
        self.capabilities = body or {}
        self.conn.request(self.config.request, self.config.arguments, self._launched)

    def _launched(self, ok, body, message):
        if not ok:
            self.output.emit("stderr", (message or "launch failed") + "\n")
            self.terminate()

    def _configure(self):
        """Событие initialized: адаптер готов принять точки останова."""
        for path in self.store.paths():
            self._send_breakpoints(path)
        filters = self.exception_filters
        if filters is None:
            filters = [f["filter"] for f in self.capabilities.get("exceptionBreakpointFilters", []) if f.get("default")]
        if self.capabilities.get("exceptionBreakpointFilters") is not None:
            self.conn.request("setExceptionBreakpoints", {"filters": filters})
        if self.capabilities.get("supportsConfigurationDoneRequest"):
            self.conn.request("configurationDone")
        self._configured = True
        if self.state == STARTING:
            self._set_state(RUNNING)

    # --- точки останова
    def _send_breakpoints(self, path):
        items = [b for b in self.store.for_path(path) if b.enabled]
        args = {"source": {"path": path, "name": os.path.basename(path)},
                "breakpoints": [dict({"line": b.line}, **({"condition": b.condition} if b.condition else {}))
                                for b in items],
                "sourceModified": False}

        def done(ok, body, _message, sent=tuple(b.line for b in items)):
            if not ok:
                return
            bound = []
            for line, raw in zip(sent, body.get("breakpoints", [])):
                self.store.set_verified(path, line, bool(raw.get("verified")), raw.get("message", ""))
                bound.append((line, bool(raw.get("verified")), raw.get("message", "")))
            self.breakpoints_bound.emit(path, bound)
        self.conn.request("setBreakpoints", args, done)

    def _breakpoints_changed(self, path):
        if self._configured and self.state in (RUNNING, STOPPED):
            for p in ([path] if path else self.store.paths()):
                self._send_breakpoints(p)

    # --- события
    def _event(self, name, body):
        if name == "initialized":
            self._configure()
        elif name == "stopped":
            self.thread_id = body.get("threadId", self.thread_id)
            reason = body.get("reason", "")
            if self.thread_id is None:
                self.conn.request("threads", {}, lambda ok, b, _m: ok and b.get("threads") and self._stack(
                    b["threads"][0]["id"], reason))
            else:
                self._stack(self.thread_id, reason)
        elif name == "continued":
            self.frames = []
            self._set_state(RUNNING)
        elif name == "output":
            category = body.get("category", "console")
            if category != "telemetry":
                self.output.emit(category, body.get("output", ""))
        elif name == "exited":
            self.exit_code = body.get("exitCode")
        elif name == "terminated":
            self.terminate()
        elif name == "breakpoint":
            raw = body.get("breakpoint", {})
            path = (raw.get("source") or {}).get("path")
            if path and raw.get("line"):
                self.store.set_verified(path, raw["line"], bool(raw.get("verified")), raw.get("message", ""))
                self.breakpoints_bound.emit(path, [(raw["line"], bool(raw.get("verified")), raw.get("message", ""))])

    def _stack(self, thread_id, reason):
        self.thread_id = thread_id

        def done(ok, body, _message):
            self.frames = [StackFrame(f) for f in body.get("stackFrames", [])] if ok else []
            self._set_state(STOPPED)
            self.stopped.emit(thread_id, reason, self.frames)
        self.conn.request("stackTrace", {"threadId": thread_id, "startFrame": 0, "levels": 200}, done)

    def _reverse_request(self, seq, command, arguments):
        """runInTerminal — запустить программу во встроенном терминале (ввод с клавиатуры при отладке);
        startDebugging и прочее не поддерживаем — отвечаем честно."""
        if command == "runInTerminal" and self.run_in_terminal is not None:
            env = {k: v for k, v in (arguments.get("env") or {}).items() if v is not None}
            pid = self.run_in_terminal(arguments.get("args") or [], arguments.get("cwd"), env,
                                       arguments.get("title") or self.config.title)
            if pid:
                self.conn.respond(seq, command, body={"processId": pid})
                return
        self.conn.respond(seq, command, success=False, message="not supported by UniPy Studio")

    # --- управление (как кнопки панели отладки VS)
    def _thread_args(self):
        return {"threadId": self.thread_id} if self.thread_id is not None else {}

    def _resume(self, command):
        if self.state != STOPPED:
            return
        self.frames = []
        self._set_state(RUNNING)
        self.conn.request(command, self._thread_args())

    def continue_(self):
        self._resume("continue")

    def step_over(self):
        self._resume("next")

    def step_into(self):
        self._resume("stepIn")

    def step_out(self):
        self._resume("stepOut")

    def pause(self):
        if self.state == RUNNING:
            self.conn.request("pause", {"threadId": self.thread_id or 0})

    def terminate(self):
        """Остановить отладку: программа завершается, адаптер закрывается."""
        if self.state in (ENDED, IDLE):
            return
        if self.conn.running():
            if self.capabilities.get("supportsTerminateRequest") and self.state != STARTING:
                self.conn.request("terminate", {}, lambda *_: self._disconnect())
            else:
                self._disconnect()
        else:
            self._finish(self.exit_code)

    def _disconnect(self):
        self.conn.request("disconnect", {"terminateDebuggee": True, "restart": False},
                          lambda *_: self._finish(self.exit_code))

    def _adapter_exited(self, _code):
        self._finish(self.exit_code)

    def _finish(self, code):
        if self.state == ENDED:
            return
        self._set_state(ENDED)
        self.frames = []
        try:
            self.store.changed.disconnect(self._breakpoints_changed)
        except TypeError:
            pass
        self.conn.stop()
        self.ended.emit(code)

    # --- данные для панелей
    def scopes(self, frame_id, callback):
        """callback([(имя области, reference, дорогая)])."""
        self.conn.request("scopes", {"frameId": frame_id}, lambda ok, body, _m: callback(
            [(s.get("name", ""), s.get("variablesReference", 0), bool(s.get("expensive")))
             for s in body.get("scopes", [])] if ok else []))

    def variables(self, reference, callback):
        self.conn.request("variables", {"variablesReference": reference}, lambda ok, body, _m: callback(
            [Variable(v) for v in body.get("variables", [])] if ok else []))

    def evaluate(self, expression, frame_id, context, callback):
        """context: "hover" (подсказка над словом), "watch", "repl". callback(Variable | None, сообщение)."""
        args = {"expression": expression, "context": context}
        if frame_id is not None:
            args["frameId"] = frame_id
        self.conn.request("evaluate", args, lambda ok, body, message: callback(
            Variable(dict(body, name=expression)) if ok else None, message))

    def _set_state(self, state):
        if state != self.state:
            self.state = state
            self.state_changed.emit(state)
