"""Панель «Вывод»: сообщения по источникам (сборка, запуск, отладка…), ссылки на строки."""
from collections import deque
import re
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QColor, QTextCursor, QTextCharFormat
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPlainTextEdit, QLabel, QComboBox
from .. import theme
from ..theme import C
from ..log_limits import LOG_CHARS, LOG_BLOCKS, configure_log, trim_log, clip_log
from ..i18n import bind_text, tr
from .common import mini_button


# ====================================================================== вывод
class OutputPanel(QWidget):
    link = pyqtSignal(str, int)

    def __init__(self, font):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        bar = QHBoxLayout()
        bar.setContentsMargins(6, 2, 4, 2)
        lbl = bind_text(QLabel(), "panels.show_output_from")
        lbl.setObjectName("hint")
        bar.addWidget(lbl)
        self.source = QComboBox()
        for key in ("panels.all", "common.build", "common.run", "Unity"):
            self.source.addItem(tr(key), key)
        self.source.currentIndexChanged.connect(self._rerender)
        bar.addWidget(self.source)
        bar.addStretch(1)
        bar.addWidget(mini_button("clear", "panels.clear_all", self.clear))
        lay.addLayout(bar)
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setFont(font)
        configure_log(self.view)
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.view.viewport().installEventFilter(self)
        lay.addWidget(self.view, 1)
        self.lines = deque()   # (источник, текст, цвет)
        self._line_chars = 0

    def retranslate(self):
        for i in range(self.source.count()):
            self.source.setItemText(i, tr(self.source.itemData(i)))

    def write(self, text, source="common.run", color=None):
        color = theme.role_of(color) or color      # роль, а не цвет: строка перекрасится при смене темы
        for line in clip_log(text).rstrip("\n").split("\n"):
            self.lines.append((source, line, color))
            self._line_chars += len(line) + 1
            while self.lines and (len(self.lines) > LOG_BLOCKS or self._line_chars > LOG_CHARS):
                self._line_chars -= len(self.lines.popleft()[1]) + 1
            if self.source.currentData() in ("panels.all", source):
                self._append(line, color)
        trim_log(self.view)

    def _append(self, line, color):
        cur = self.view.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.End)
        fmt = QTextCharFormat()
        if color and color != "text":       # обычный текст — цветом темы виджета
            fmt.setForeground(QColor(C.get(color, color)))
        cur.insertText(("\n" if not self.view.document().isEmpty() else "") + line, fmt)
        self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())

    def _rerender(self, *_):
        self.view.clear()
        sel = self.source.currentData()
        for src, line, color in self.lines:
            if sel in ("panels.all", src):
                self._append(line, color)

    def clear(self):
        self.lines.clear()
        self._line_chars = 0
        self.view.clear()

    def eventFilter(self, obj, ev):
        if ev.type() == ev.Type.MouseButtonDblClick:
            cur = self.view.cursorForPosition(ev.position().toPoint())
            text = cur.block().text()
            m = re.search(r'File "(.+?)", line (\d+)', text) or re.search(r"(\S+\.(?:py|cs))[:(](\d+)", text)
            if m:
                self.link.emit(m.group(1), int(m.group(2)))
                return True
        return False
