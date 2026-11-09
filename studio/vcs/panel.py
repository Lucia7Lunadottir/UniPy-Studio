"""Панель «Изменения Git», как в Visual Studio: ветка, получение/отправка, сообщение коммита,
«Индексированные изменения» и «Изменения» с кнопками +/−. Ничего не делает сама — только сигналы."""
import os

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QToolButton, QPlainTextEdit, QPushButton,
                             QTreeWidget, QTreeWidgetItem, QCheckBox, QMenu, QAbstractItemView, QMessageBox)

from .. import theme
from ..i18n import tr
from .base import ADDED, CONFLICT, DELETED, MODIFIED, RENAMED, UNTRACKED

ROLE = Qt.ItemDataRole.UserRole
# буква и цвет состояния, как в VS Code / Visual Studio
BADGES = {MODIFIED: ("M", "#E2C08D"), ADDED: ("A", "#73C991"), DELETED: ("D", "#E4676B"),
          RENAMED: ("R", "#73C991"), UNTRACKED: ("U", "#73C991"), CONFLICT: ("!", "#E4676B")}
STATE_NAMES = {MODIFIED: "vcs.state_modified", ADDED: "vcs.state_added", DELETED: "vcs.state_deleted",
               RENAMED: "vcs.state_renamed", UNTRACKED: "vcs.state_untracked", CONFLICT: "vcs.state_conflict"}


def badge_color(state):
    return QColor(BADGES.get(state, ("", "#999999"))[1])


class GitChangesPanel(QWidget):
    stage = pyqtSignal(object)              # [пути]
    unstage = pyqtSignal(object)
    discard = pyqtSignal(object)
    commit = pyqtSignal(str, bool, bool)    # сообщение, amend, «всё» (индексировать всё перед коммитом)
    open_diff = pyqtSignal(str)
    open_file = pyqtSignal(str)
    remote = pyqtSignal(str)                # fetch / pull / push
    refresh_requested = pyqtSignal()
    switch_branch = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.root = None
        self.status = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        top = QHBoxLayout()
        self.branch = QToolButton()
        self.branch.clicked.connect(self.switch_branch)
        top.addWidget(self.branch, 1)
        self.buttons = {}
        for action in ("fetch", "pull", "push"):
            b = QToolButton()
            b.clicked.connect(lambda _c=False, a=action: self.remote.emit(a))
            self.buttons[action] = b
            top.addWidget(b)
        self.refresh_button = QToolButton()
        self.refresh_button.setIcon(theme.tool_icon("refresh"))
        self.refresh_button.setProperty("tool_icon", "refresh")
        self.refresh_button.clicked.connect(self.refresh_requested)
        top.addWidget(self.refresh_button)
        lay.addLayout(top)

        self.message = QPlainTextEdit()
        self.message.setMaximumHeight(80)
        lay.addWidget(self.message)
        row = QHBoxLayout()
        self.commit_button = QPushButton()
        self.commit_button.clicked.connect(self._commit)
        self.amend = QCheckBox()
        row.addWidget(self.commit_button, 1)
        row.addWidget(self.amend)
        lay.addLayout(row)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.tree.itemDoubleClicked.connect(self._double)
        lay.addWidget(self.tree, 1)
        self.note = QLabel()
        self.note.setWordWrap(True)
        theme.style(self.note, "color:@dim@;")
        lay.addWidget(self.note)
        self.retranslate()
        self.show_repository(None, "", {})

    def retranslate(self):
        for action, key in (("fetch", "vcs.fetch"), ("pull", "vcs.pull"), ("push", "vcs.push")):
            self.buttons[action].setText(tr(key))
        self.refresh_button.setToolTip(tr("vcs.refresh"))
        self.message.setPlaceholderText(tr("vcs.message_placeholder"))
        self.amend.setText(tr("vcs.amend"))
        self.branch.setToolTip(tr("vcs.switch_branch"))
        self.show_repository(self.root, getattr(self, "_branch", ""), self.status)

    # --- данные
    def show_repository(self, root, branch, status):
        self.root, self._branch, self.status = root, branch, dict(status)
        enabled = root is not None
        for w in (self.message, self.commit_button, self.amend, self.tree, *self.buttons.values(), self.branch):
            w.setEnabled(enabled)
        self.branch.setText(("⎇ " + branch) if enabled else tr("vcs.no_repository"))
        selected = {it.data(0, ROLE) for it in self.tree.selectedItems()}
        self.tree.clear()
        if not enabled:
            self.note.setText(tr("vcs.no_repository_hint"))
            return
        staged = sorted(p for p, s in status.items() if s.staged and s.staged != CONFLICT)
        changed = sorted(p for p, s in status.items() if s.unstaged or s.staged == CONFLICT)
        for title, paths, kind in (("vcs.staged", staged, "staged"), ("vcs.changes", changed, "unstaged")):
            group = QTreeWidgetItem(self.tree, [tr(title) % len(paths)])
            group.setData(0, ROLE, None)
            f = group.font(0)
            f.setBold(True)
            group.setFont(0, f)
            for p in paths:
                st = status[p]
                state = st.staged if kind == "staged" else (st.unstaged or st.staged)
                letter, color = BADGES.get(state, ("?", "#999999"))
                rel = os.path.relpath(p, root)
                item = QTreeWidgetItem(group, ["%s   %s" % (os.path.basename(p), letter)])
                item.setToolTip(0, "%s — %s" % (rel, tr(STATE_NAMES[state]) if state in STATE_NAMES else state))
                item.setForeground(0, QColor(color))
                item.setIcon(0, theme.file_icon(p))
                item.setData(0, ROLE, (kind, p))
                if (kind, p) in selected:
                    item.setSelected(True)
            group.setExpanded(True)
        self.note.setText("" if staged or changed else tr("vcs.clean"))
        self.commit_button.setText(tr("vcs.commit_staged") if staged else tr("vcs.commit_all"))

    def _selection(self, kind=None):
        out = []
        for item in self.tree.selectedItems():
            data = item.data(0, ROLE)
            if data and (kind is None or data[0] == kind):
                out.append(data[1])
        return out

    # --- действия
    def _commit(self):
        text = self.message.toPlainText().strip()
        if not text and not self.amend.isChecked():
            self.message.setFocus()
            self.note.setText(tr("vcs.enter_message"))
            return
        has_staged = any(s.staged and s.staged != CONFLICT for s in self.status.values())
        self.commit.emit(text, self.amend.isChecked(), not has_staged)

    def commit_done(self):
        self.message.clear()
        self.amend.setChecked(False)

    def _double(self, item, _col):
        data = item.data(0, ROLE)
        if data:
            st = self.status.get(data[1])
            if st is not None and (st.unstaged == UNTRACKED or (st.staged == ADDED and not st.unstaged)):
                self.open_file.emit(data[1])            # новый файл — сравнивать не с чем
            else:
                self.open_diff.emit(data[1])

    def _menu(self, pos):
        unstaged, staged = self._selection("unstaged"), self._selection("staged")
        if not unstaged and not staged:
            return
        menu = QMenu(self)
        paths = unstaged or staged
        menu.addAction(tr("vcs.compare"), lambda: self.open_diff.emit(paths[0]))
        menu.addAction(tr("vcs.open_file"), lambda: self.open_file.emit(paths[0]))
        menu.addSeparator()
        if unstaged:
            menu.addAction(tr("vcs.stage"), lambda: self.stage.emit(unstaged))
            menu.addAction(tr("vcs.discard"), lambda: self._discard(unstaged))
        if staged:
            menu.addAction(tr("vcs.unstage"), lambda: self.unstage.emit(staged))
        menu.exec(self.tree.viewport().mapToGlobal(pos))
        menu.deleteLater()

    def _discard(self, paths):
        tracked = [p for p in paths if self.status.get(p) and self.status[p].unstaged != UNTRACKED]
        if not tracked:
            return
        names = "\n".join(os.path.basename(p) for p in tracked[:15])
        if QMessageBox.question(self, tr("vcs.discard"), tr("vcs.discard_confirm") % names,
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                                QMessageBox.StandardButton.Cancel) == QMessageBox.StandardButton.Yes:
            self.discard.emit(tracked)
