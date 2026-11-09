"""Журнал редактора Unity (Editor.log): разбор на записи и слежение за дописанным.

Где журнал: Unity 6.6 пишет его в папку проекта — <проект>/Logs/Editor.log (в ~/.config/unity3d/Editor.log
остаётся только строка «Logs moved to project-relative Editor.log»); старые версии — в ~/.config/unity3d.
Файл бывает в десятки мегабайт, поэтому читается только хвост, а дальше — то, что дописано.
Без виджетов: панель — в unity_console.py.
"""
import os
import re

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

TAIL_BYTES = 512 * 1024
POLL_MS = 700

ERROR, WARNING, LOG, INFO = "error", "warning", "log", "info"

_COMPILE = re.compile(r"^(?P<path>(?:Assets|Packages|Library)/.+?\.cs)\((?P<line>\d+),(?P<col>\d+)\):\s*"
                      r"(?P<sev>error|warning)\s+(?P<code>\w+):\s*(?P<msg>.*)$")
_DEBUG_CALL = re.compile(r"^UnityEngine\.Debug:(?P<call>Log\w*)\b")
_AT = re.compile(r"\(at (?:\./)?(?P<path>(?:Assets|Packages)/[^:()]+?):(?P<line>\d+)\)")
_AT_PACKAGE = re.compile(r"\(at (?:\./)?(?P<path>Library/PackageCache/[^:()]+?):(?P<line>\d+)\)")
_EXCEPTION = re.compile(r"^[A-Za-z_][\w.]*(?:Exception|Error)\b[^:]*:")
_INTERNAL = ("UnityEngine.Debug:ExtractStackTraceNoAlloc", "UnityEngine.StackTraceUtility:",
             "UnityEngine.DebugLogHandler:", "UnityEngine.Logger:")
COMPILE_START = re.compile(r"\[ScriptCompilation\] Requested script compilation|^- Starting script compilation")


class LogEntry:
    """Запись консоли: kind (error/warning/log/info), message, trace (строки), path/line — куда вести по щелчку
    (относительно проекта), compile — это ошибка компиляции (CS0103 и т.п.)."""
    __slots__ = ("kind", "message", "trace", "path", "line", "col", "compile", "code")

    def __init__(self, kind, message, trace=(), path=None, line=0, col=1, compile=False, code=""):
        self.kind, self.message, self.trace = kind, message, list(trace)
        self.path, self.line, self.col, self.compile, self.code = path, line, col, compile, code

    def key(self):
        """Для «Свернуть одинаковые» (Collapse в Unity): текст и место."""
        return self.kind, self.message, self.path, self.line

    def __repr__(self):
        return "LogEntry(%s, %r, %s:%s)" % (self.kind, self.message[:40], self.path, self.line)


def _location(lines):
    """Первое место в коде проекта (Assets/ или Packages/) в стеке вызовов; иначе — в пакете (Library/PackageCache)."""
    for pattern in (_AT, _AT_PACKAGE):
        for text in lines:
            m = pattern.search(text)
            if m:
                return m.group("path"), int(m.group("line"))
    return None, 0


def classify_block(lines):
    """Блок строк между пустыми строками → записи (ошибки компиляции — каждая отдельно)."""
    out = []
    rest = []
    for text in lines:
        m = _COMPILE.match(text.strip())
        if m:
            kind = ERROR if m.group("sev") == "error" else WARNING
            out.append(LogEntry(kind, "%s: %s" % (m.group("code"), m.group("msg")), (), m.group("path"),
                                int(m.group("line")), int(m.group("col")), compile=True, code=m.group("code")))
        else:
            rest.append(text)
    if not rest:
        return out
    call_at = next((i for i, t in enumerate(rest) if _DEBUG_CALL.match(t)), None)
    if call_at is not None:
        call = _DEBUG_CALL.match(rest[call_at]).group("call")
        kind = ERROR if call.startswith(("LogError", "LogException", "LogAssertion")) else \
            WARNING if call.startswith("LogWarning") else LOG
        first_trace = next((i for i, t in enumerate(rest) if t.startswith(_INTERNAL)), call_at)
        # Unity не всегда отделяет свои служебные строки пустой строкой: сообщение — строка прямо перед стеком,
        # всё, что выше, — отдельная служебная запись
        if first_trace > 1:
            out.append(LogEntry(INFO, "\n".join(rest[:first_trace - 1])))
        message = rest[first_trace - 1].strip() if first_trace else rest[call_at]
        trace = rest[call_at:]
        path, line = _location(trace)
        out.append(LogEntry(kind, message, trace, path, line))
    elif _EXCEPTION.match(rest[0]) and any("(at " in t or ":" in t and "(" in t for t in rest[1:]):
        path, line = _location(rest[1:])
        out.append(LogEntry(ERROR, rest[0], rest[1:], path, line))
    else:
        out.append(LogEntry(INFO, "\n".join(rest)))
    return out


class UnityLogParser:
    """Кусками дописанного текста → записи. Незаконченный последний блок ждёт продолжения.
    `compile_started` — Unity начал компиляцию скриптов: старые ошибки компиляции больше не верны."""

    def __init__(self):
        self._pending = []          # строки незаконченного блока
        self._partial = ""          # незаконченная строка

    def feed(self, text):
        """→ список событий: LogEntry или строка "compile_started"."""
        text = self._partial + text
        lines = text.split("\n")
        self._partial = lines.pop()             # после последнего \n — ещё не вся строка
        events = []
        for raw in lines:
            line = raw.rstrip("\r")
            if COMPILE_START.search(line):
                events.extend(self._close())
                events.append("compile_started")
                continue
            if not line.strip():
                events.extend(self._close())
            else:
                self._pending.append(line)
        return events

    def flush(self):
        """Конец файла надолго: отдать и незаконченный блок (Unity дописывает блок целиком, но не всегда с пустой строкой)."""
        if self._partial:
            self._pending.append(self._partial)
            self._partial = ""
        return self._close()

    def _close(self):
        if not self._pending:
            return []
        block, self._pending = self._pending, []
        return classify_block(block)


def log_path_for(project):
    """Editor.log для Unity-проекта: в папке проекта (Unity 6.6+), иначе общий."""
    own = os.path.join(project, "Logs", "Editor.log") if project else None
    if own and os.path.isfile(own):
        return own
    common = os.path.expanduser("~/.config/unity3d/Editor.log")
    return own if own and not os.path.isfile(common) else common


class UnityLogWatcher(QObject):
    """Следит за Editor.log: при старте — хвост файла, дальше — дописанное. Новый файл (Unity перезапущен,
    журнал переименован в Editor-prev.log) — читается с начала и сообщается `reset`."""
    events = pyqtSignal(object)         # [LogEntry | "compile_started"]
    reset = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.path = None
        self._offset = 0
        self._inode = None
        self._parser = UnityLogParser()
        self._idle = 0
        self._timer = QTimer(self, interval=POLL_MS, timeout=self.poll)

    def watch(self, path):
        self.path = path
        self._parser = UnityLogParser()
        self._offset, self._inode = 0, None
        if not path:
            self._timer.stop()
            return
        try:
            st = os.stat(path)
            self._inode = st.st_ino
            self._offset = max(0, st.st_size - TAIL_BYTES)
        except OSError:
            pass
        self.poll(first=True)
        self._timer.start()

    def stop(self):
        self._timer.stop()

    def poll(self, first=False):
        if not self.path:
            return
        try:
            st = os.stat(self.path)
        except OSError:
            return
        if self._inode is not None and (st.st_ino != self._inode or st.st_size < self._offset):
            self._parser = UnityLogParser()             # новый журнал — новая сессия Unity
            self._offset, self._inode = 0, st.st_ino
            self.reset.emit()
        self._inode = st.st_ino
        if st.st_size == self._offset:
            self._idle += 1
            if self._idle == 2:                         # тишина ~1.5 с — отдать незаконченный блок
                tail = self._parser.flush()
                if tail:
                    self.events.emit(tail)
            return
        self._idle = 0
        try:
            with open(self.path, "rb") as f:
                f.seek(self._offset)
                data = f.read(8 * 1024 * 1024)
        except OSError:
            return
        start = self._offset
        self._offset += len(data)
        if first and start > 0:                         # начали с середины файла — до первой целой строки
            cut = data.find(b"\n\n")
            data = data[cut + 2:] if cut >= 0 else b""
        found = self._parser.feed(data.decode("utf-8", "replace"))
        if found:
            self.events.emit(found)
