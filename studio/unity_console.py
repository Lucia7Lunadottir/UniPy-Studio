"""Панель «Консоль Unity»: сообщения редактора Unity из Editor.log, как окно Console в самом Unity.

Ошибки / предупреждения / сообщения с счётчиками, «Свернуть одинаковые» (Collapse), поиск, «Очистить»;
внизу — полный текст и стек вызовов со ссылками на строки. Двойной щелчок — открыть место в коде.
Ошибки компиляции Unity (CS0103 и т.п.) панель отдаёт окну (`compile_changed`) — для «Списка ошибок».
"""
import html
import os
import re

from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QToolButton, QLineEdit, QListWidget,
                             QListWidgetItem, QSplitter, QTextBrowser, QLabel)

from . import theme
from .panels.common import FlowRow
from .i18n import tr
from .theme import C
from .unity_log import ERROR, WARNING, LOG, INFO, UnityLogWatcher, log_path_for

MAX_ENTRIES = 5000
ROLE = Qt.ItemDataRole.UserRole
_LINK = re.compile(r"\(at (?:\./)?((?:Assets|Packages|Library/PackageCache)/[^:()]+?):(\d+)\)")


class UnityConsole(QWidget):
    open_file = pyqtSignal(str, int)        # абсолютный путь, строка
    compile_changed = pyqtSignal(object)    # {абсолютный путь: [(строка, столбец, текст, вид, "Unity")]}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.project = None
        self.entries = []                   # все записи (с начала сеанса или после «Очистить»)
        self.compile = {}
        self.watcher = UnityLogWatcher(self)
        self.watcher.events.connect(self._events)
        self.watcher.reset.connect(self._new_session)
        self._render_timer = QTimer(self, singleShot=True, interval=150, timeout=self.refresh)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        bar = FlowRow(margins=(6, 2, 6, 2))         # узкое окно — кнопки переносятся
        self.clear_button = QToolButton()
        self.clear_button.clicked.connect(self.clear)
        self.collapse = QToolButton(checkable=True)
        self.collapse.toggled.connect(lambda *_: self.refresh())
        bar.addWidget(self.clear_button)
        bar.addWidget(self.collapse)
        self.search = QLineEdit()
        self.search.setMinimumWidth(120)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda *_: self._render_timer.start())
        bar.add(self.search, fill=True)
        self.kind_buttons = {}
        for kind in (ERROR, WARNING, LOG, INFO):
            b = QToolButton(checkable=True, checked=kind != INFO)
            b.toggled.connect(lambda *_: self.refresh())
            self.kind_buttons[kind] = b
            bar.addWidget(b)
        lay.addLayout(bar)
        self.where = QLabel()
        self.where.setContentsMargins(8, 0, 8, 2)
        self.where.setWordWrap(True)
        theme.style(self.where, "color:@dim@;")
        lay.addWidget(self.where)
        split = QSplitter(Qt.Orientation.Vertical)
        self.list = QListWidget()
        self.list.setUniformItemSizes(True)
        self.list.currentItemChanged.connect(lambda *_: self._show_details())
        self.list.itemDoubleClicked.connect(self._open_item)
        self.details = QTextBrowser()
        self.details.setOpenLinks(False)
        self.details.anchorClicked.connect(self._open_link)
        # свои минимумы поменьше стандартных 70 px: иначе вкладка не даёт уменьшить нижнюю область дока
        self.list.setMinimumHeight(40)
        self.details.setMinimumHeight(24)
        split.addWidget(self.list)
        split.addWidget(self.details)
        split.setSizes([300, 110])
        lay.addWidget(split, 1)
        self.retranslate()

    # --- проект и журнал
    def set_project(self, project):
        """Unity-проект решения (или None). Журнал выбирается сам (log_path_for)."""
        self.project = project
        self.entries.clear()
        self._set_compile({})
        path = log_path_for(project) if project else None
        self.watcher.watch(path)
        self._update_where()
        self.refresh()

    def _update_where(self):
        if not self.project:
            self.where.setText(tr("unity_console.no_project"))
        else:
            self.where.setText(tr("unity_console.reading") % (self.watcher.path or ""))

    def _new_session(self):
        """Unity перезапущен — новый журнал."""
        self.entries.clear()
        self._set_compile({})
        self.refresh()

    def _events(self, events):
        compile_now = dict(self.compile)
        changed = False
        for ev in events:
            if ev == "compile_started":
                compile_now = {}
                changed = True
                continue
            self.entries.append(ev)
            if ev.compile and ev.path and self.project:
                path = os.path.join(self.project, ev.path)
                compile_now.setdefault(path, []).append((ev.line, ev.col, ev.message, ev.kind, "Unity"))
                changed = True
        del self.entries[:-MAX_ENTRIES]
        if changed:
            self._set_compile(compile_now)
        self._render_timer.start()

    def _set_compile(self, compile_now):
        old = set(self.compile)
        self.compile = compile_now
        self.compile_changed.emit({p: compile_now.get(p, []) for p in old | set(compile_now)})

    def clear(self):
        """Как «Clear» в Unity: убрать сообщения. Ошибки компиляции остаются, пока Unity их не исправит."""
        self.entries = [e for e in self.entries if e.compile and e.kind == ERROR]
        self.refresh()

    # --- показ
    def counts(self):
        out = {k: 0 for k in (ERROR, WARNING, LOG, INFO)}
        for e in self.entries:
            out[e.kind] += 1
        return out

    def retranslate(self):
        self.clear_button.setText(tr("unity_console.clear"))
        self.collapse.setText(tr("unity_console.collapse"))
        self.search.setPlaceholderText(tr("unity_console.search"))
        self._update_where()
        self._update_counts()

    def _update_counts(self):
        counts = self.counts()
        names = {ERROR: "unity_console.errors", WARNING: "unity_console.warnings", LOG: "unity_console.messages",
                 INFO: "unity_console.service"}
        for kind, b in self.kind_buttons.items():
            b.setText(tr(names[kind]) % counts[kind])

    def visible_entries(self):
        needle = self.search.text().strip().casefold()
        shown = [e for e in self.entries if self.kind_buttons[e.kind].isChecked()
                 and (not needle or needle in e.message.casefold())]
        if not self.collapse.isChecked():
            return [(e, 1) for e in shown]
        grouped = {}
        for e in shown:
            if e.key() in grouped:
                grouped[e.key()][1] += 1
            else:
                grouped[e.key()] = [e, 1]
        return [tuple(v) for v in grouped.values()]

    def refresh(self):
        self._update_counts()
        bar = self.list.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 2
        current = self.list.currentItem().data(ROLE) if self.list.currentItem() else None
        self.list.setUpdatesEnabled(False)
        self.list.clear()
        colors = {ERROR: C["error"], WARNING: C["warning"], LOG: C["text"], INFO: C["dim"]}
        icons = {ERROR: "✖", WARNING: "⚠", LOG: "ⓘ", INFO: "·"}
        for entry, count in self.visible_entries():
            first = entry.message.split("\n", 1)[0]
            text = "%s  %s" % (icons[entry.kind], first)
            if entry.path:
                text += "    — %s:%d" % (os.path.basename(entry.path), entry.line)
            if count > 1:
                text += "    ×%d" % count
            item = QListWidgetItem(text)
            item.setForeground(QColor(colors[entry.kind]))
            item.setData(ROLE, entry)
            self.list.addItem(item)
            if entry is current:
                self.list.setCurrentItem(item)
        self.list.setUpdatesEnabled(True)
        if at_bottom:
            self.list.scrollToBottom()

    def _show_details(self):
        item = self.list.currentItem()
        entry = item.data(ROLE) if item else None
        if entry is None:
            self.details.clear()
            return
        parts = ["<pre style='white-space:pre-wrap; margin:0'>%s</pre>" % html.escape(entry.message)]
        if entry.compile and entry.path:
            parts.append("<a href='open:%s:%d'>%s(%d,%d)</a>" % (html.escape(entry.path), entry.line,
                                                                 html.escape(entry.path), entry.line, entry.col))
        if entry.trace:
            lines = []
            for t in entry.trace:
                esc = html.escape(t)
                m = _LINK.search(t)
                if m:
                    target = "open:%s:%s" % (m.group(1), m.group(2))
                    esc = esc.replace(html.escape(m.group(0)), "(at <a href='%s'>%s:%s</a>)" % (
                        html.escape(target), html.escape(m.group(1)), m.group(2)))
                lines.append(esc)
            parts.append("<pre style='margin-top:6px; color:%s'>%s</pre>" % (C["dim"], "\n".join(lines)))
        self.details.setHtml("".join(parts))

    def _open_item(self, item):
        entry = item.data(ROLE)
        if entry is not None and entry.path:
            self._open(entry.path, entry.line)

    def _open_link(self, url):
        text = url.toString()
        if text.startswith("open:"):
            rel, _, line = text[5:].rpartition(":")
            self._open(rel, int(line or 1))

    def _open(self, rel, line):
        if not self.project:
            return
        path = rel if os.path.isabs(rel) else os.path.join(self.project, rel)
        if os.path.isfile(path):
            self.open_file.emit(path, line)
