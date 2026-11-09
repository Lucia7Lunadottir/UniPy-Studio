"""Обозреватель решений: дерево файлов, фильтр, «только код», создание/переименование/удаление, цвета Git."""
import os
import shutil
from PyQt6.QtCore import Qt, pyqtSignal, QFileSystemWatcher, QTimer, QSize
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QTreeWidget, QTreeWidgetItem, QLineEdit, QMenu, QInputDialog, QMessageBox
from .. import theme
from ..i18n import bind_text, tr, plural
from .common import CODE_HIDDEN_DIRS, HIDDEN_DIRS, ROLE_LINE, ROLE_PATH, is_code_file, is_unity_project, mini_button


class SolutionExplorer(QWidget):
    open_file = pyqtSignal(str)
    run_file = pyqtSignal(str)
    changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.root = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        bar = QHBoxLayout()
        bar.setContentsMargins(4, 2, 4, 2)
        bar.setSpacing(1)
        self.filter = QLineEdit()
        bind_text(self.filter, "panels.search_solution_explorer_ctrl", "setPlaceholderText")
        self.filter.textChanged.connect(self._apply_filter)
        bar.addWidget(mini_button("refresh", "common.refresh", self.refresh))
        bar.addWidget(mini_button("collapse", "panels.collapse_all", self._collapse))
        bar.addWidget(mini_button("new", "panels.new_folder", lambda: self._new_folder(None)))
        self.show_all = mini_button("allfiles", "panels.show_all_files", self._toggle_all)
        self.show_all.setCheckable(True)
        bar.addWidget(self.show_all)
        bar.addStretch(1)
        lay.addLayout(bar)
        lay.addWidget(self.filter)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(14)
        self.tree.setIconSize(QSize(16, 16))
        self.tree.itemActivated.connect(self._activated)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.tree.itemExpanded.connect(lambda it: it.setIcon(0, theme.folder_icon(True)) if it.data(0, ROLE_LINE) == "dir" else None)
        lay.addWidget(self.tree, 1)
        self.watcher = QFileSystemWatcher(self)
        self._refresh_timer = QTimer(self, singleShot=True, interval=400, timeout=self.refresh)
        self.watcher.directoryChanged.connect(lambda *_: self._refresh_timer.start())
        self.files = []
        self._code_dirs = None      # папки, где есть код (режим «только код»); None — показывать всё

    def _toggle_all(self):
        self.refresh()
    def set_root(self, root):
        self.root = root
        self.refresh(first=True)

    def refresh(self, first=False):
        if not self.root:
            return
        expanded = set() if first else self._expanded_paths()
        self.tree.clear()
        self.files = []
        if self.watcher.directories():
            self.watcher.removePaths(self.watcher.directories())
        self._code_dirs = None if self.show_all.isChecked() else self._scan_code_dirs(self.root)
        name = os.path.basename(self.root.rstrip(os.sep)) or self.root
        projects = [d for d in self._list(self.root)[0]]
        self._solution_name, self._project_count = name, len(projects)
        top = QTreeWidgetItem([self._solution_title()])
        top.setIcon(0, theme.badge("▣", "#68217A"))
        f = top.font(0)
        f.setBold(True)
        top.setFont(0, f)
        top.setData(0, ROLE_PATH, self.root)
        top.setData(0, ROLE_LINE, "dir")
        self.tree.addTopLevelItem(top)
        self._fill(top, self.root, 0, expanded, first)
        top.setExpanded(True)
        self._apply_filter(self.filter.text())
        self._decorate()
        self.changed.emit()

    def _solution_title(self):
        count = self._project_count
        return tr("panels.solution") % (
            self._solution_name, count, plural(count, "panels.project_one", "panels.projects_few", "panels.projects_many"))

    def retranslate(self):
        top = self.tree.topLevelItem(0)
        if top is not None:
            top.setText(0, self._solution_title())

    @staticmethod
    def _scan_code_dirs(root, limit=200000):
        """Папки, в которых (на любой глубине) есть код: только они видны в режиме «только код»."""
        found = {root}
        seen = 0
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in HIDDEN_DIRS
                           and d not in CODE_HIDDEN_DIRS]
            seen += len(filenames)
            if seen > limit:
                break
            if any(is_code_file(f) for f in filenames):
                d = dirpath
                while d not in found and d.startswith(root):
                    found.add(d)
                    d = os.path.dirname(d)
        return found

    def _list(self, path):
        try:
            names = sorted(os.listdir(path), key=str.lower)
        except OSError:
            return [], []
        dirs, files = [], []
        code_only = self._code_dirs is not None
        for n in names:
            if n.startswith(".") or n in HIDDEN_DIRS or n.endswith(".meta"):
                continue
            p = os.path.join(path, n)
            if os.path.isdir(p):
                if not code_only or p in self._code_dirs:
                    dirs.append(n)
            elif not code_only or is_code_file(n):
                files.append(n)
        return dirs, files

    def _fill(self, parent, path, depth, expanded, first):
        if depth > 12 or len(self.files) > 8000:
            return
        self.watcher.addPath(path)
        dirs, files = self._list(path)
        for d in dirs:
            p = os.path.join(path, d)
            it = QTreeWidgetItem([d])
            it.setData(0, ROLE_PATH, p)
            it.setData(0, ROLE_LINE, "dir")
            if depth == 0 and is_unity_project(p):
                it.setIcon(0, theme.tool_icon("unity"))
                it.setText(0, d + "  (Unity)")
            elif depth == 0 and any(x.endswith(".py") for x in self._list(p)[1]):
                it.setIcon(0, theme.badge("Py", "#3572A5", "#FFD43B"))
                it.setText(0, d + "  (Python)")
            else:
                it.setIcon(0, theme.folder_icon())
            parent.addChild(it)
            self._fill(it, p, depth + 1, expanded, first)
            if p in expanded or (first and depth < 3 and d not in ("ProjectSettings", "Packages")):
                it.setExpanded(True)
        for fname in files:
            p = os.path.join(path, fname)
            it = QTreeWidgetItem([fname])
            it.setIcon(0, theme.file_icon(fname))
            it.setData(0, ROLE_PATH, p)
            it.setData(0, ROLE_LINE, "file")
            parent.addChild(it)
            self.files.append(p)

    def set_vcs_status(self, states):
        """{путь: состояние} от контроля версий (vcs) — цвет имени файла и папок, где что-то изменилось."""
        self._vcs = dict(states)
        self._decorate()

    def _decorate(self):
        from ..vcs.panel import BADGES, STATE_NAMES
        states = getattr(self, "_vcs", {})
        folders = {}
        for path, state in states.items():          # папка «изменена», если внутри что-то изменено
            parent = os.path.dirname(path)
            while self.root and parent.startswith(self.root) and parent not in folders:
                folders[parent] = state
                parent = os.path.dirname(parent)
        untracked_dirs = [p + os.sep for p, s in states.items() if s == "untracked" and os.path.isdir(p)]
        default = self.tree.palette().text().color()

        def walk(item):
            path = item.data(0, ROLE_PATH)
            state = states.get(path) or folders.get(path)
            if state is None and path and any(path.startswith(d) for d in untracked_dirs):
                state = "untracked"
            if state is not None and item.parent() is not None:
                item.setForeground(0, QColor(BADGES.get(state, ("", "#999999"))[1]))
                item.setToolTip(0, "%s — %s" % (path, tr(STATE_NAMES[state]) if state in STATE_NAMES else state))
            elif item.parent() is not None:
                item.setForeground(0, default)
                item.setToolTip(0, path or "")
            for i in range(item.childCount()):
                walk(item.child(i))
        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))

    def _expanded_paths(self):
        out = set()

        def walk(it):
            if it.isExpanded():
                out.add(it.data(0, ROLE_PATH))
            for i in range(it.childCount()):
                walk(it.child(i))
        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))
        return out

    def _collapse(self):
        self.tree.collapseAll()
        if self.tree.topLevelItemCount():
            self.tree.topLevelItem(0).setExpanded(True)

    def _apply_filter(self, text):
        text = text.strip().lower()

        def walk(it):
            visible_child = False
            for i in range(it.childCount()):
                visible_child |= walk(it.child(i))
            match = not text or text in it.text(0).lower()
            show = match or visible_child
            it.setHidden(not show)
            if text and visible_child:
                it.setExpanded(True)
            return show
        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))

    def _activated(self, item, _col):
        if item.data(0, ROLE_LINE) == "file":
            self.open_file.emit(item.data(0, ROLE_PATH))

    def select_path(self, path):
        def walk(it):
            if it.data(0, ROLE_PATH) == path:
                self.tree.setCurrentItem(it)
                return True
            return any(walk(it.child(i)) for i in range(it.childCount()))
        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))

    def _menu(self, pos):
        item = self.tree.itemAt(pos)
        path = item.data(0, ROLE_PATH) if item else self.root
        is_dir = os.path.isdir(path) if path else True
        folder = path if is_dir else os.path.dirname(path)
        m = QMenu(self)
        if not is_dir:
            m.addAction(tr("panels.open"), lambda: self.open_file.emit(path))
            if path.endswith(".py"):
                m.addAction(tr("panels.run"), lambda: self.run_file.emit(path))
            m.addSeparator()
        m.addAction(tr("common.new_file"), lambda: self.window().new_file_dialog(folder))
        m.addAction(tr("panels.new_folder_2"), lambda: self._new_folder(folder))
        m.addSeparator()
        if path != self.root:
            m.addAction(tr("common.rename_2"), lambda: self._rename(path))
            m.addAction(tr("panels.delete"), lambda: self._delete(path))
        m.addAction(tr("common.copy_path"), lambda: self.window().copy_text(path))
        m.addAction(tr("common.open_folder_file_manager"), lambda: self.window().open_external(folder))
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def _new_folder(self, folder):
        folder = folder or self.root
        name, ok = QInputDialog.getText(self, tr("panels.new_folder"), tr("panels.name_folder"))
        if ok and name.strip():
            os.makedirs(os.path.join(folder, name.strip()), exist_ok=True)
            self.refresh()

    def _rename(self, path):
        old = os.path.basename(path)
        name, ok = QInputDialog.getText(self, tr("common.rename"), tr("panels.new_name"), text=old)
        if ok and name.strip() and name != old:
            new = os.path.join(os.path.dirname(path), name.strip())
            if os.path.exists(new):
                QMessageBox.warning(self, tr("common.rename"), tr("panels.name_exists"))
                return
            os.rename(path, new)
            self.window().on_path_renamed(path, new)
            self.refresh()

    def _delete(self, path):
        what = tr("panels.folder_word") if os.path.isdir(path) else tr("panels.file")
        if QMessageBox.question(self, tr("panels.delete"), tr("panels.delete_cannot_undone") % (
                what, os.path.basename(path))) != QMessageBox.StandardButton.Yes:
            return
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
        self.window().on_path_deleted(path)
        self.refresh()
