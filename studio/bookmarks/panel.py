"""Окно «Закладки»: флажок (включена), имя (F2 или двойной щелчок по имени — переименовать), файл, строка.
Ничего не открывает само — только сигналы."""
import os

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import QAbstractItemView, QMenu, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from ..i18n import tr
from ..panels.common import FlowRow, mini_button

ROLE = Qt.ItemDataRole.UserRole


class BookmarksPanel(QWidget):
    goto = pyqtSignal(str, int)
    step = pyqtSignal(bool)             # True — следующая, False — предыдущая

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        bar = FlowRow(margins=(4, 2, 4, 2), spacing=2)
        bar.addWidget(mini_button("back", "bookmarks.previous", lambda: self.step.emit(False)))
        bar.addWidget(mini_button("forward", "bookmarks.next", lambda: self.step.emit(True)))
        self.delete_button = mini_button("clear", "bookmarks.delete", self._delete_selected)
        bar.addWidget(self.delete_button)
        lay.addLayout(bar)
        self.tree = QTreeWidget()
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        # правится только имя (столбец 0) — по F2 или из меню; двойной щелчок ведёт к коду, как в VS
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        rename = QShortcut(QKeySequence("F2"), self.tree, self._rename_current)
        rename.setContext(Qt.ShortcutContext.WidgetShortcut)
        self.tree.itemActivated.connect(self._activated)
        self.tree.itemChanged.connect(self._item_changed)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        lay.addWidget(self.tree, 1)
        store.changed.connect(lambda _path: self.refresh())
        self.retranslate()
        self.refresh()

    def retranslate(self):
        self.tree.setHeaderLabels([tr("bookmarks.name"), tr("common.file"), tr("panels.line")])

    def refresh(self):
        self.tree.blockSignals(True)
        self.tree.clear()
        for path, mark in self.store.all():
            item = QTreeWidgetItem([mark.name or tr("bookmarks.default_name") % mark.line,
                                    os.path.basename(path), str(mark.line)])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEditable)
            item.setCheckState(0, Qt.CheckState.Checked if mark.enabled else Qt.CheckState.Unchecked)
            item.setToolTip(1, path)
            item.setData(0, ROLE, (path, mark.line))
            self.tree.addTopLevelItem(item)
        for col in (1, 2):
            self.tree.resizeColumnToContents(col)
        self.delete_button.setEnabled(bool(self.store.paths()))
        self.tree.blockSignals(False)

    def _activated(self, item, column):
        path, line = item.data(0, ROLE)
        self.goto.emit(path, line)

    def _item_changed(self, item, column):
        path, line = item.data(0, ROLE)
        mark = self.store.get(path, line)
        if mark is None:
            return
        if column == 0:
            enabled = item.checkState(0) == Qt.CheckState.Checked
            if enabled != mark.enabled:
                self.store.set_enabled(path, line, enabled)
            name = item.text(0)
            if name != (mark.name or tr("bookmarks.default_name") % mark.line):
                self.store.set_name(path, line, name)

    def _rename_current(self):
        item = self.tree.currentItem()
        if item is not None:
            self.tree.editItem(item, 0)

    def _selected(self):
        return [it.data(0, ROLE) for it in self.tree.selectedItems()]

    def _delete_selected(self):
        chosen = self._selected()
        if not chosen:              # ничего не выбрано — кнопка удаляет все, как «Удалить все закладки» в VS
            self.store.clear()
            return
        for path, line in chosen:
            self.store.remove(path, line)

    def _menu(self, pos):
        item = self.tree.itemAt(pos)
        menu = QMenu(self)
        if item is not None:
            path, line = item.data(0, ROLE)
            menu.addAction(tr("debug.go_to_code"), lambda: self.goto.emit(path, line))
            menu.addAction(tr("bookmarks.rename"), lambda: self.tree.editItem(item, 0))
            menu.addAction(tr("bookmarks.delete"), lambda: [self.store.remove(p, l) for p, l in self._selected()])
            menu.addSeparator()
        menu.addAction(tr("bookmarks.clear_all"), self.store.clear).setEnabled(bool(self.store.paths()))
        menu.exec(self.tree.viewport().mapToGlobal(pos))
        menu.deleteLater()
