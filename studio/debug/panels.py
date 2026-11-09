"""Панели отладки, как в Visual Studio: «Стек вызовов», «Локальные», «Точки останова».
Панели не знают об окне: показывают то, что им дали, и сообщают сигналами, что выбрали."""
import os

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QTreeWidget, QTreeWidgetItem, QHeaderView, QMenu, QInputDialog, QAbstractItemView)

from ..i18n import tr
from ..theme import C

ROLE = Qt.ItemDataRole.UserRole
_PLACEHOLDER = "…"


class CallStackPanel(QTreeWidget):
    frame_chosen = pyqtSignal(object)           # StackFrame

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRootIsDecorated(False)
        self.setHeaderLabels([tr("debug.function"), tr("debug.location")])
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.itemActivated.connect(lambda item, _c: self.frame_chosen.emit(item.data(0, ROLE)))
        self.itemClicked.connect(lambda item, _c: self.frame_chosen.emit(item.data(0, ROLE)))

    def retranslate(self):
        self.setHeaderLabels([tr("debug.function"), tr("debug.location")])

    def show_frames(self, frames, current=None):
        self.clear()
        for i, f in enumerate(frames):
            where = "%s:%d" % (os.path.basename(f.path), f.line) if f.path else tr("debug.no_source")
            item = QTreeWidgetItem(self, [("➤ " if i == 0 else "   ") + f.name, where])
            item.setData(0, ROLE, f)
            item.setToolTip(1, f.path or "")
            if not f.path:
                for col in (0, 1):
                    item.setForeground(col, QColor(C["dim"]))      # кадр без исходника (библиотека)
            if f is current:
                self.setCurrentItem(item)


class VariablesPanel(QTreeWidget):
    """Области кадра (Локальные, Глобальные…) и переменные. Дети загружаются при раскрытии узла."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.session = None
        self.setHeaderLabels([tr("debug.name"), tr("debug.value"), tr("debug.type")])
        self.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.setColumnWidth(0, 200)
        self.setUniformRowHeights(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.itemExpanded.connect(self._expand)
        self._generation = 0
        self._expanded = set()                  # пути раскрытых узлов — остаются раскрытыми после шага

    def retranslate(self):
        self.setHeaderLabels([tr("debug.name"), tr("debug.value"), tr("debug.type")])

    def show_frame(self, session, frame):
        self._remember_expanded()
        self._generation += 1
        generation = self._generation
        self.clear()
        self.session = session
        if session is None or frame is None:
            return

        def got(scopes):
            if generation != self._generation:
                return
            for i, (name, reference, expensive) in enumerate(scopes):
                item = QTreeWidgetItem(self, [name, "", ""])
                item.setData(0, ROLE, reference)
                f = item.font(0)
                f.setBold(True)
                item.setFont(0, f)
                if reference:
                    QTreeWidgetItem(item, [_PLACEHOLDER])
                    if (i == 0 and not expensive) or (name,) in self._expanded:
                        item.setExpanded(True)          # «Локальные» раскрыты сразу, как в VS
        session.scopes(frame.id, got)

    def _path(self, item):
        names = []
        while item is not None:
            names.insert(0, item.text(0))
            item = item.parent()
        return tuple(names)

    def _remember_expanded(self):
        self._expanded = set()
        stack = [self.topLevelItem(i) for i in range(self.topLevelItemCount())]
        while stack:
            item = stack.pop()
            if item.isExpanded():
                self._expanded.add(self._path(item))
                stack.extend(item.child(i) for i in range(item.childCount()))

    def _expand(self, item):
        if item.childCount() != 1 or item.child(0).text(0) != _PLACEHOLDER or self.session is None:
            return
        reference = item.data(0, ROLE)
        generation = self._generation

        def got(variables):
            if generation != self._generation:
                return
            item.takeChildren()
            for v in variables:
                child = QTreeWidgetItem(item, [v.name, v.value, v.type])
                child.setToolTip(1, v.value)
                child.setData(0, ROLE, v.reference)
                if v.reference:
                    QTreeWidgetItem(child, [_PLACEHOLDER])
                    if self._path(child) in self._expanded:
                        child.setExpanded(True)
        self.session.variables(reference, got)


class BreakpointsPanel(QTreeWidget):
    goto = pyqtSignal(str, int)

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self.setRootIsDecorated(False)
        self.setHeaderLabels([tr("debug.breakpoint"), tr("debug.condition")])
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.itemActivated.connect(lambda item, _c: self.goto.emit(*item.data(0, ROLE)))
        self.itemChanged.connect(self._checked)
        store.changed.connect(lambda _p: self.refresh())
        self.refresh()

    def retranslate(self):
        self.setHeaderLabels([tr("debug.breakpoint"), tr("debug.condition")])
        self.refresh()

    def refresh(self):
        self.blockSignals(True)
        self.clear()
        root = self.store.root
        for path, bp in self.store.all():
            name = os.path.relpath(path, root) if root and path.startswith(root) else path
            item = QTreeWidgetItem(self, ["%s, %s %d" % (name, tr("debug.line"), bp.line), bp.condition])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Checked if bp.enabled else Qt.CheckState.Unchecked)
            item.setData(0, ROLE, (path, bp.line))
            if bp.verified is False:
                item.setToolTip(0, bp.message or tr("debug.not_bound"))
                item.setForeground(0, self.palette().placeholderText())
        self.blockSignals(False)

    def _checked(self, item, column):
        if column == 0:
            path, line = item.data(0, ROLE)
            self.store.set_enabled(path, line, item.checkState(0) == Qt.CheckState.Checked)

    def _menu(self, pos):
        item = self.itemAt(pos)
        menu = QMenu(self)
        if item is not None:
            path, line = item.data(0, ROLE)
            menu.addAction(tr("debug.go_to_code"), lambda: self.goto.emit(path, line))
            menu.addAction(tr("debug.edit_condition"), lambda: self.edit_condition(path, line))
            menu.addAction(tr("debug.delete_breakpoint"), lambda: self.store.remove(path, line))
            menu.addSeparator()
        menu.addAction(tr("debug.delete_all_breakpoints"), self.store.clear).setEnabled(bool(self.store.paths()))
        menu.exec(self.viewport().mapToGlobal(pos))
        menu.deleteLater()

    def edit_condition(self, path, line):
        bp = self.store.get(path, line)
        if bp is None:
            return
        text, ok = QInputDialog.getText(self, tr("debug.edit_condition"), tr("debug.condition_prompt"), text=bp.condition)
        if ok:
            self.store.set_condition(path, line, text)
