"""Обозреватель решений, как в Visual Studio: решение → проекты → «Зависимости» (платформы, пакеты, проекты,
сборки, анализаторы) и файлы проекта. Что за проекты — читает studio/solution (без Qt).
Кнопка «Показать все файлы» — просто папка решения (так же, если проектов нет). Фильтр, «только код»,
создание/переименование/удаление, цвета Git — в обоих видах."""
import os
import shutil
from PyQt6.QtCore import Qt, pyqtSignal, QFileSystemWatcher, QTimer, QSize
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QTreeWidget, QTreeWidgetItem, QLineEdit, QMenu, QInputDialog, QMessageBox
from .. import theme
from .. import solution as solution_mod
from ..i18n import bind_text, tr, plural
from .common import (CODE_HIDDEN_DIRS, HIDDEN_DIRS, ROLE_LINE, ROLE_PATH, FlowRow, is_code_file, is_unity_project,
                     mini_button)

# ROLE_LINE — вид узла: "dir", "file", "solution", "project", "deps", "dep_group", "dep"
PROJECT_BADGES = {"unity": None, "csharp": ("C#", "#68217A"), "python": ("Py", "#3572A5", "#FFD43B")}
DEPENDENCY_GROUPS = {solution_mod.FRAMEWORK: "explorer.frameworks", solution_mod.PACKAGE: "explorer.packages",
                     solution_mod.PROJECT: "explorer.projects", solution_mod.ASSEMBLY: "explorer.assemblies",
                     solution_mod.ANALYZER: "explorer.analyzers"}


class SolutionExplorer(QWidget):
    open_file = pyqtSignal(str)
    run_file = pyqtSignal(str)
    changed = pyqtSignal()
    build_project = pyqtSignal(str)         # путь файла проекта
    set_startup = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.root = None
        self.solution = None                # solution.Solution или None — проектов нет, показывается папка
        self.can_start = lambda path: False     # окно подставляет: можно ли назначить проект запускаемым
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        bar = FlowRow(margins=(4, 2, 4, 2), spacing=1)
        self.filter = QLineEdit()
        bind_text(self.filter, "panels.search_solution_explorer_ctrl", "setPlaceholderText")
        self.filter.textChanged.connect(self._apply_filter)
        bar.addWidget(mini_button("refresh", "common.refresh", self.refresh))
        bar.addWidget(mini_button("collapse", "panels.collapse_all", self._collapse))
        bar.addWidget(mini_button("new", "panels.new_folder", lambda: self._new_folder(None)))
        self.show_all = mini_button("allfiles", "panels.show_all_files", self._toggle_all)
        self.show_all.setCheckable(True)
        bar.addWidget(self.show_all)
        lay.addLayout(bar)
        lay.addWidget(self.filter)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(14)
        self.tree.setIconSize(QSize(16, 16))
        self.tree.itemActivated.connect(self._activated)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.tree.itemExpanded.connect(self._expanded)
        lay.addWidget(self.tree, 1)
        self.watcher = QFileSystemWatcher(self)
        self._refresh_timer = QTimer(self, singleShot=True, interval=400, timeout=self.refresh)
        self.watcher.directoryChanged.connect(self._fs_changed)
        self.watcher.fileChanged.connect(self._fs_changed)      # .sln/.csproj: Unity пересоздаёт их
        self.files = []
        self._code_dirs = None      # папки, где есть код (режим «только код»); None — показывать всё

    def _fs_changed(self, _path):
        self._refresh_timer.start()

    @staticmethod
    def _expanded(item):
        if item.data(0, ROLE_LINE) == "dir":
            item.setIcon(0, theme.folder_icon(True))

    def _toggle_all(self):
        self.refresh()

    def set_root(self, root):
        self.root = root
        self.refresh(first=True)

    # ------------------------------------------------------------ построение
    def refresh(self, first=False):
        if not self.root:
            return
        expanded = set() if first else self._expanded_paths()
        self.tree.clear()
        self.files = []
        for paths in (self.watcher.directories(), self.watcher.files()):
            if paths:
                self.watcher.removePaths(paths)
        self._code_dirs = None if self.show_all.isChecked() else self._scan_code_dirs(self.root)
        self.solution = solution_mod.load_solution(self.root)
        top = QTreeWidgetItem()
        top.setIcon(0, theme.badge("▣", "#68217A"))
        f = top.font(0)
        f.setBold(True)
        top.setFont(0, f)
        top.setData(0, ROLE_PATH, self.root)
        top.setData(0, ROLE_LINE, "solution")
        self.tree.addTopLevelItem(top)
        if self.solution is not None and not self.show_all.isChecked():
            self._solution_name, self._project_count = self.solution.name, len(self.solution.projects)
            self._fill_projects(top, expanded, first)
        else:
            name = os.path.basename(self.root.rstrip(os.sep)) or self.root
            self._solution_name = self.solution.name if self.solution is not None else name
            self._project_count = len(self.solution.projects) if self.solution is not None else len(self._list(self.root)[0])
            self._fill(top, self.root, 0, expanded, first)
        self.files = list(dict.fromkeys(self.files))      # один файл в двух проектах — один раз для Ctrl+P
        top.setText(0, self._solution_title())
        top.setExpanded(True)
        self._apply_filter(self.filter.text())
        self._decorate()
        self.changed.emit()

    def _solution_title(self):
        count = self._project_count
        return tr("panels.solution") % (
            self._solution_name, count, plural(count, "panels.project_one", "panels.projects_few", "panels.projects_many"))

    def retranslate(self):
        if self.tree.topLevelItemCount():
            self.refresh()

    def _fill_projects(self, top, expanded, first):
        sln = self.solution
        if os.path.isfile(sln.path):
            self.watcher.addPath(sln.path)
        self.watcher.addPath(self.root)                 # появился/пропал проект
        for project in sln.projects:
            it = QTreeWidgetItem([project.name])
            it.setData(0, ROLE_PATH, project.path)
            it.setData(0, ROLE_LINE, "project")
            badge = PROJECT_BADGES.get(project.kind)
            it.setIcon(0, theme.tool_icon("unity") if project.kind == "unity" else theme.badge(*badge))
            it.setToolTip(0, project.error or project.path)
            if project.error:
                it.setForeground(0, QColor(theme.C["error"]))
            top.addChild(it)
            if os.path.isfile(project.path):
                self.watcher.addPath(project.path)
            self._fill_dependencies(it, project, expanded)
            if project.files is not None:
                self._fill_listed(it, project, expanded, first)
            else:
                self._fill(it, project.folder, 1, expanded, first, skip=self._other_projects(project))
            if project.path in expanded or (first and len(sln.projects) <= 3):
                it.setExpanded(True)

    def _other_projects(self, project):
        """Папки других проектов внутри папки этого (решение в корне с Python-проектом рядом) — не дублировать."""
        return {p.folder for p in self.solution.projects if p is not project and p.folder != project.folder
                and p.folder.startswith(project.folder.rstrip(os.sep) + os.sep)}

    def _fill_dependencies(self, parent, project, expanded):
        if not project.dependencies:
            return
        key = "deps::" + project.path
        deps = QTreeWidgetItem([tr("explorer.dependencies")])
        deps.setIcon(0, theme.badge("⧉", "#5A5A5A"))
        deps.setData(0, ROLE_PATH, key)
        deps.setData(0, ROLE_LINE, "deps")
        parent.addChild(deps)
        for kind in solution_mod.DEPENDENCY_KINDS:
            items = project.dependencies_of(kind)
            if not items:
                continue
            group = QTreeWidgetItem(["%s (%d)" % (tr(DEPENDENCY_GROUPS[kind]), len(items))])
            group.setIcon(0, theme.folder_icon())
            group.setData(0, ROLE_PATH, "%s::%s" % (key, kind))
            group.setData(0, ROLE_LINE, "dep_group")
            deps.addChild(group)
            for d in sorted(items, key=lambda x: x.name.lower()):
                it = QTreeWidgetItem([d.label()])
                it.setIcon(0, theme.badge(*_dependency_badge(kind)))
                it.setData(0, ROLE_PATH, d.path or "")
                it.setData(0, ROLE_LINE, "dep")
                it.setToolTip(0, d.path or d.label())
                if kind == solution_mod.PROJECT and d.path and self.solution.project_by_path(d.path) is None:
                    it.setForeground(0, QColor(theme.C["warning"]))     # ссылка на проект вне решения
                group.addChild(it)
            group.setExpanded("%s::%s" % (key, kind) in expanded)
        deps.setExpanded(key in expanded)

    def _fill_listed(self, parent, project, expanded, first):
        """Проект с явным списком файлов (.csproj Unity): папки строятся из путей файлов."""
        folders = {}
        base = project.folder.rstrip(os.sep)

        def folder_item(rel_dir):
            if not rel_dir:
                return parent
            if rel_dir in folders:
                return folders[rel_dir]
            up = folder_item(os.path.dirname(rel_dir))
            it = QTreeWidgetItem([os.path.basename(rel_dir)])
            it.setIcon(0, theme.folder_icon())
            path = os.path.join(base, rel_dir)
            it.setData(0, ROLE_PATH, path)
            it.setData(0, ROLE_LINE, "dir")
            up.addChild(it)
            folders[rel_dir] = it
            return it
        code_only = self._code_dirs is not None
        for path in project.files:
            name = os.path.basename(path)
            if code_only and not is_code_file(name):
                continue
            rel = os.path.relpath(path, base)
            parent_item = folder_item("" if rel.startswith("..") else os.path.dirname(rel))
            it = QTreeWidgetItem([name])
            it.setIcon(0, theme.file_icon(name))
            it.setData(0, ROLE_PATH, path)
            it.setData(0, ROLE_LINE, "file")
            if not os.path.exists(path):
                it.setForeground(0, QColor(theme.C["warning"]))         # в проекте есть, на диске нет
            parent_item.addChild(it)
            self.files.append(path)
        for rel_dir, it in folders.items():
            if it.data(0, ROLE_PATH) in expanded or (first and rel_dir.count(os.sep) < 1):
                it.setExpanded(True)
        for directory in {os.path.dirname(p) for p in project.files}:
            if os.path.isdir(directory):
                self.watcher.addPath(directory)

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

    def _fill(self, parent, path, depth, expanded, first, skip=()):
        if depth > 12 or len(self.files) > 8000:
            return
        self.watcher.addPath(path)
        dirs, files = self._list(path)
        for d in dirs:
            p = os.path.join(path, d)
            if p in skip:
                continue
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

    # ------------------------------------------------------------ Git
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
            kind = item.data(0, ROLE_LINE)
            if kind in ("dir", "file"):
                path = item.data(0, ROLE_PATH)
                state = states.get(path) or folders.get(path)
                if state is None and path and any(path.startswith(d) for d in untracked_dirs):
                    state = "untracked"
                if state is not None:
                    item.setForeground(0, QColor(BADGES.get(state, ("", "#999999"))[1]))
                    item.setToolTip(0, "%s — %s" % (path, tr(STATE_NAMES[state]) if state in STATE_NAMES else state))
                elif item.foreground(0).color() != QColor(theme.C["warning"]):
                    item.setForeground(0, default)
                    item.setToolTip(0, path or "")
            if kind in ("deps", "dep_group", "dep"):
                return                              # зависимости — не файлы решения
            for i in range(item.childCount()):
                walk(item.child(i))
        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))

    # ------------------------------------------------------------ вид
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
        kind, path = item.data(0, ROLE_LINE), item.data(0, ROLE_PATH)
        if kind == "file" and os.path.isfile(path):
            self.open_file.emit(path)
        elif kind == "dep" and path and self.solution is not None and self.solution.project_by_path(path):
            self.select_path(self.solution.project_by_path(path).path)     # ссылка на проект — к нему

    def select_path(self, path):
        def walk(it):
            if it.data(0, ROLE_PATH) == path and it.data(0, ROLE_LINE) != "dep":
                self.tree.setCurrentItem(it)
                return True
            return any(walk(it.child(i)) for i in range(it.childCount()))
        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))

    def project_of(self, path):
        return self.solution.project_of_file(path) if self.solution is not None else None

    # ------------------------------------------------------------ меню
    def _menu(self, pos):
        item = self.tree.itemAt(pos)
        kind = item.data(0, ROLE_LINE) if item else "solution"
        path = item.data(0, ROLE_PATH) if item else self.root
        m = QMenu(self)
        if kind == "project":
            self._project_menu(m, path)
        elif kind in ("deps", "dep_group", "dep"):
            self._dependency_menu(m, item, path)
        else:
            self._file_menu(m, path)
        if not m.isEmpty():
            m.exec(self.tree.viewport().mapToGlobal(pos))
        m.deleteLater()

    def _project_menu(self, m, path):
        project = self.solution.project_by_path(path) if self.solution is not None else None
        if project is None:
            return
        m.addAction(tr("explorer.build_project"), lambda: self.build_project.emit(project.path))
        start = m.addAction(tr("explorer.set_startup"), lambda: self.set_startup.emit(project.path))
        start.setEnabled(self.can_start(project.path))
        m.addSeparator()
        if os.path.isfile(project.path):
            m.addAction(tr("explorer.edit_project_file"), lambda: self.open_file.emit(project.path))
        m.addAction(tr("common.new_file"), lambda: self.window().new_file_dialog(project.folder))
        m.addAction(tr("panels.new_folder_2"), lambda: self._new_folder(project.folder))
        m.addSeparator()
        m.addAction(tr("common.copy_path"), lambda: self.window().copy_text(project.path))
        m.addAction(tr("common.open_folder_file_manager"), lambda: self.window().open_external(project.folder))

    def _dependency_menu(self, m, item, path):
        if item.data(0, ROLE_LINE) == "dep" and path:
            target = self.solution.project_by_path(path) if self.solution is not None else None
            if target is not None:
                m.addAction(tr("explorer.go_to_project"), lambda: self.select_path(target.path))
            m.addAction(tr("common.copy_path"), lambda: self.window().copy_text(path))
        project_path = (path or "").split("::")[1] if (path or "").startswith("deps::") else None
        if project_path and project_path.endswith((".toml", ".txt", ".py", ".cfg")):
            m.addAction(tr("explorer.check_dependencies"), lambda: self.window().show_dependencies())

    def _file_menu(self, m, path):
        is_dir = os.path.isdir(path) if path else True
        folder = path if is_dir else os.path.dirname(path)
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


def _dependency_badge(kind):
    return {solution_mod.FRAMEWORK: ("F", "#5C2D91"), solution_mod.PACKAGE: ("N", "#004880"),
            solution_mod.PROJECT: ("P", "#68217A"), solution_mod.ASSEMBLY: ("A", "#5A5A5A"),
            solution_mod.ANALYZER: ("Az", "#8A6D00")}[kind]
