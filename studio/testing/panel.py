"""Панель «Обозреватель тестов», как в Visual Studio. Ничего не запускает сама — только сигналы;
показывает дерево TestNode и подробности выбранного теста."""
import html
import os
import re

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QLabel, QLineEdit, QSplitter, QTextBrowser, QToolButton, QTreeWidget,
                             QTreeWidgetItem, QVBoxLayout, QWidget, QAbstractItemView, QMenu)

from .. import theme
from ..panels.common import FlowRow
from ..i18n import tr
from ..theme import C
from .base import ERROR, FAILED, NOT_RUN, PASSED, RUNNING, SKIPPED

ROLE = Qt.ItemDataRole.UserRole
GLYPHS = {PASSED: ("✔", "#57AB5A"), FAILED: ("✖", "#E5534B"), ERROR: ("✖", "#E5534B"),
          SKIPPED: ("↷", "#8B949E"), RUNNING: ("●", "#4C9BE8"), NOT_RUN: ("○", "#8B949E")}
_LINK = re.compile(r'File "([^"]+)", line (\d+)|(/[^\s:"]+\.py):(\d+)')


class TestExplorer(QWidget):
    run_all = pyqtSignal()
    run_failed = pyqtSignal()
    run_ids = pyqtSignal(object)            # [id]
    debug_ids = pyqtSignal(object)
    refresh = pyqtSignal()
    open_location = pyqtSignal(str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.nodes = {}
        self._items = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        bar = FlowRow()                             # узкая панель — кнопки переносятся
        self.buttons = {}
        for key, signal in (("run_all", self.run_all), ("run_failed", self.run_failed),
                            ("run_selected", None), ("debug_selected", None), ("refresh", self.refresh)):
            b = QToolButton()
            if signal is not None:
                b.clicked.connect(signal)
            self.buttons[key] = b
            bar.addWidget(b)
        self.buttons["run_selected"].clicked.connect(lambda: self.run_ids.emit(self.selected_ids()))
        self.buttons["debug_selected"].clicked.connect(lambda: self.debug_ids.emit(self.selected_ids()))
        lay.addLayout(bar)
        self.filter = QLineEdit()                   # своей строкой: рядом с кнопками ему не хватало места
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self._apply_filter)
        lay.addWidget(self.filter)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        theme.style(self.summary, "color:@dim@;")
        lay.addWidget(self.summary)
        split = QSplitter(Qt.Orientation.Vertical)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.itemDoubleClicked.connect(self._double)
        self.tree.currentItemChanged.connect(lambda *_: self._show_details())
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.details = QTextBrowser()
        self.details.setOpenLinks(False)
        self.details.anchorClicked.connect(self._link)
        split.addWidget(self.tree)
        split.addWidget(self.details)
        split.setSizes([320, 140])
        lay.addWidget(split, 1)
        self.retranslate()
        self.set_message(tr("testing.not_discovered"))

    def retranslate(self):
        for key, text in (("run_all", "testing.run_all"), ("run_failed", "testing.run_failed"),
                          ("run_selected", "testing.run_selected"), ("debug_selected", "testing.debug_selected"),
                          ("refresh", "testing.refresh")):
            self.buttons[key].setText(tr(text))
        self.filter.setPlaceholderText(tr("testing.filter"))

    def set_running(self, running):
        for key in ("run_all", "run_failed", "run_selected", "debug_selected", "refresh"):
            self.buttons[key].setEnabled(not running)

    def set_message(self, text):
        self.summary.setText(text)

    # --- дерево
    def set_nodes(self, nodes):
        """Новое дерево после поиска тестов. Раскрытые узлы и выбор сохраняются."""
        expanded = {i for i, it in self._items.items() if it.isExpanded()}
        current = self.tree.currentItem().data(0, ROLE) if self.tree.currentItem() else None
        self.nodes = nodes
        self._items = {}
        self.tree.clear()
        for node in nodes.values():
            if node.parent is None or node.parent not in nodes:
                self._add(None, node)
        for node_id, item in self._items.items():
            item.setExpanded(node_id in expanded or not expanded)     # впервые — всё раскрыто, как в VS
            if node_id == current:
                self.tree.setCurrentItem(item)
        self.update_statuses()
        self._apply_filter(self.filter.text())

    def _add(self, parent_item, node):
        item = QTreeWidgetItem([node.name])
        item.setData(0, ROLE, node.id)
        (parent_item.addChild if parent_item else self.tree.addTopLevelItem)(item)
        self._items[node.id] = item
        for child in node.children:
            self._add(item, child)

    def status_of(self, node):
        from .base import aggregate
        if node.kind == "test":
            return node.status
        return aggregate(t.status for t in node.tests())

    def update_statuses(self, only=None):
        targets = self.nodes.values() if only is None else only
        for node in targets:
            item = self._items.get(node.id)
            if item is None:
                continue
            glyph, color = GLYPHS[self.status_of(node)]
            text = "%s  %s" % (glyph, node.name)
            if node.kind == "test" and node.status not in (NOT_RUN, RUNNING):
                text += "   %s" % self._duration(node.duration)
            item.setText(0, text)
            item.setForeground(0, QColor(color) if self.status_of(node) != NOT_RUN else QColor(C["text"]))
        self._update_summary()
        if self.tree.currentItem() is not None:
            self._show_details()

    @staticmethod
    def _duration(seconds):
        if seconds < 0.0005:
            return tr("testing.under_ms")
        return tr("testing.ms") % round(seconds * 1000) if seconds < 1 else tr("testing.seconds") % seconds

    def _update_summary(self):
        tests = [n for n in self.nodes.values() if n.kind == "test"]
        if not tests:
            return
        count = {s: sum(1 for t in tests if t.status == s) for s in (PASSED, FAILED, ERROR, SKIPPED, NOT_RUN, RUNNING)}
        total = sum(t.duration for t in tests)
        self.set_message(tr("testing.summary") % (len(tests), count[PASSED], count[FAILED] + count[ERROR],
                                                  count[SKIPPED], count[NOT_RUN] + count[RUNNING], total))

    def selected_ids(self):
        ids = [it.data(0, ROLE) for it in self.tree.selectedItems()]
        return ids or ([self.tree.currentItem().data(0, ROLE)] if self.tree.currentItem() else [])

    def _apply_filter(self, text):
        needle = text.strip().casefold()

        def visit(item):
            child_hit = any([visit(item.child(i)) for i in range(item.childCount())])
            hit = not needle or needle in item.data(0, ROLE).casefold() or child_hit
            item.setHidden(not hit)
            return hit
        for i in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(i))

    # --- подробности
    def _show_details(self):
        item = self.tree.currentItem()
        node = self.nodes.get(item.data(0, ROLE)) if item else None
        if node is None:
            self.details.clear()
            return
        parts = ["<b>%s</b>" % html.escape(node.id)]
        if node.kind == "test" and node.status not in (NOT_RUN, RUNNING):
            parts.append("%s — %s" % (tr(STATUS_NAMES[node.status]), self._duration(node.duration)))
        pre = "<pre style='white-space:pre-wrap; margin:6px 0 0 0;%s'>%s</pre>"
        if node.message:
            parts.append(pre % ("color:%s" % C["error"], html.escape(node.message.strip())))
        if node.details:
            parts.append(pre % ("", self._linkify(node.details.strip())))
        if node.output.strip():
            parts.append("<div style='margin-top:8px'><b>%s</b></div>" % tr("testing.output") +
                         pre % ("color:%s" % C["dim"], html.escape(node.output.strip())))
        self.details.setHtml("".join("<div>%s</div>" % p if not p.startswith("<") else p for p in parts))

    @staticmethod
    def _linkify(text):
        out, last = [], 0
        for m in _LINK.finditer(text):
            path, line = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
            out.append(html.escape(text[last:m.start()]))
            out.append("<a href='open:%s:%s'>%s</a>" % (html.escape(path), line, html.escape(m.group(0))))
            last = m.end()
        out.append(html.escape(text[last:]))
        return "".join(out)

    def _link(self, url):
        target = url.toString()
        if target.startswith("open:"):
            path, _, line = target[5:].rpartition(":")
            if os.path.isfile(path):
                self.open_location.emit(path, int(line))

    def _double(self, item, _col):
        node = self.nodes.get(item.data(0, ROLE))
        if node is None:
            return
        if node.status in (FAILED, ERROR) and node.fail_path and os.path.isfile(node.fail_path):
            self.open_location.emit(node.fail_path, node.fail_line or 1)          # упавший — к месту падения
        elif node.path and os.path.isfile(node.path):
            self.open_location.emit(node.path, node.line or 1)

    def _menu(self, pos):
        ids = self.selected_ids()
        if not ids:
            return
        menu = QMenu(self)
        menu.addAction(tr("testing.run_selected"), lambda: self.run_ids.emit(ids))
        menu.addAction(tr("testing.debug_selected"), lambda: self.debug_ids.emit(ids))
        menu.exec(self.tree.viewport().mapToGlobal(pos))
        menu.deleteLater()


STATUS_NAMES = {PASSED: "testing.passed", FAILED: "testing.failed", ERROR: "testing.error",
                SKIPPED: "testing.skipped", NOT_RUN: "testing.not_run", RUNNING: "testing.running"}
