"""Панель «Терминал»: вкладки с оболочками и запущенными программами (F5 / Ctrl+F5 запускают сюда —
так работает ввод с клавиатуры: input(), Console.ReadLine()). Закрытие вкладки завершает процесс."""
import os
import re

import time

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QTabWidget, QToolButton, QVBoxLayout, QWidget

from .. import theme
from ..i18n import tr
from .pty_process import PtyProcess
from .widget import TerminalView

_ANSI = re.compile(rb"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][0-9A-Za-z]|\x1b[=>]")
CAPTURE_LIMIT = 256_000
SLICE_BYTES = 8192          # pyte разбирает ~150 КБ/с: вывод подаётся кусками,
SLICE_MS = 12               # не дольше ~12 мс за такт — окно не замирает даже от `cat` большого файла
BACKLOG_PAUSE = 1 << 20     # накопилось больше 1 МБ — перестать читать из PTY, программа подождёт
BACKLOG_RESUME = 256 << 10


def plain_text(data):
    """Вывод без управляющих последовательностей — для поиска traceback / стека исключения."""
    return _ANSI.sub(b"", data).replace(b"\r\n", b"\n").decode("utf-8", "replace")


class TerminalSession(QWidget):
    """Терминал + процесс. finished(код, весь вывод без раскраски) — когда процесс завершился."""
    finished = pyqtSignal(object, str)

    def __init__(self, font, title, parent=None):
        super().__init__(parent)
        self.title = title
        self.view = TerminalView(font, self)
        self.process = PtyProcess(self)
        self._captured = b""
        self._pending = bytearray()
        self._exit = None                   # процесс завершился, но вывод ещё разбирается
        self._paused = False
        self._pump = QTimer(self, interval=0, timeout=self._drain)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.view)
        self.view.send.connect(self.process.write)
        self.view.resized.connect(self.process.resize)
        self.process.data.connect(self._data)
        self.process.finished.connect(self._finished)

    def start(self, argv, cwd, env=None):
        self.view._fit()
        return self.process.start(argv, cwd, env, self.view.term.cols, self.view.term.rows)

    def _data(self, data):
        self._captured = (self._captured + data)[-CAPTURE_LIMIT:]
        self._pending += data
        if len(self._pending) > BACKLOG_PAUSE and not self._paused:
            self._paused = True
            self.process.set_reading(False)
        if not self._pump.isActive():
            self._pump.start()

    def _drain(self):
        deadline = time.monotonic() + SLICE_MS / 1000
        while self._pending and time.monotonic() < deadline:
            chunk = bytes(self._pending[:SLICE_BYTES])
            del self._pending[:SLICE_BYTES]
            self.view.feed(chunk)
        if self._paused and len(self._pending) < BACKLOG_RESUME:
            self._paused = False
            self.process.set_reading(True)
        if not self._pending:
            self._pump.stop()
            if self._exit is not None:
                self._report_exit()

    def drain_all(self):
        """Разобрать всё накопленное сразу (тесты, закрытие)."""
        while self._pending:
            self._drain()

    def _finished(self, code):
        self._exit = ("exit", code)
        if not self._pending:
            self._report_exit()

    def _report_exit(self):
        code = self._exit[1]
        self._exit = None
        note = tr("terminal.exited") % ("?" if code is None else code)
        self.view.feed(("\r\n\x1b[2m%s\x1b[0m\r\n" % note).encode("utf-8"))
        self.finished.emit(code, plain_text(self._captured))

    def running(self):
        return self.process.running()


class TerminalPanel(QWidget):
    message = pyqtSignal(str)

    def __init__(self, font):
        super().__init__()
        self.font = font
        self.cwd = os.path.expanduser("~")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.tabCloseRequested.connect(self.close_session)
        self.new_button = QToolButton()
        self.new_button.setText("+")
        self.new_button.clicked.connect(lambda: self.new_shell())
        self.tabs.setCornerWidget(self.new_button, Qt.Corner.TopRightCorner)
        lay.addWidget(self.tabs)
        self.retranslate()

    def retranslate(self):
        self.new_button.setToolTip(tr("terminal.new"))

    def set_cwd(self, path):
        """Папка для новых терминалов (корень решения)."""
        if path and os.path.isdir(path):
            self.cwd = path

    def set_code_font(self, font):
        self.font = font
        for s in self.sessions():
            s.view.set_code_font(font)

    def sessions(self):
        return [self.tabs.widget(i) for i in range(self.tabs.count())]

    def current(self):
        return self.tabs.currentWidget()

    def _add(self, session, icon):
        index = self.tabs.addTab(session, theme.tool_icon(icon), session.title)
        self.tabs.setCurrentIndex(index)
        session.view.title_changed.connect(lambda t, s=session: t and self._retitle(s, t))
        session.view.setFocus()
        return session

    def _retitle(self, session, title):
        i = self.tabs.indexOf(session)
        if i >= 0:
            self.tabs.setTabText(i, title[:40])

    def new_shell(self, cwd=None):
        shell = os.environ.get("SHELL") or "/bin/bash"
        session = TerminalSession(self.font, os.path.basename(shell))
        self._add(session, "terminal")
        session.start([shell, "-i"], cwd or self.cwd)
        session.finished.connect(lambda *_: None)
        return session

    def ensure_shell(self):
        """Текущий терминал-оболочку или новый, если открытых нет."""
        for s in self.sessions():
            if s.running() and not getattr(s, "program", False):
                self.tabs.setCurrentWidget(s)
                return s
        return self.new_shell()

    def run(self, argv, cwd, env=None, title=""):
        """Программа в своей вкладке (▶ имя). Прошлая вкладка с той же программой закрывается, если уже завершилась."""
        for s in self.sessions():
            if getattr(s, "program", False) and s.title == "▶ " + title and not s.running():
                self.tabs.removeTab(self.tabs.indexOf(s))
                s.deleteLater()
        session = TerminalSession(self.font, "▶ " + title)
        session.program = True
        self._add(session, "run")
        session.start(argv, cwd, env)
        return session

    def type_text(self, text):
        """Вписать команду в терминал без Enter — пусть человек посмотрит и нажмёт сам."""
        session = self.ensure_shell()
        session.view.paste(text)
        session.view.setFocus()

    def close_session(self, index):
        session = self.tabs.widget(index)
        if session is None:
            return
        session.process.terminate()
        self.tabs.removeTab(index)
        session.deleteLater()

    def stop(self):
        for s in self.sessions():
            s.process.terminate()

    def showEvent(self, e):
        super().showEvent(e)
        if not self.tabs.count():
            self.new_shell()                # открыли панель — сразу оболочка, как в VS
