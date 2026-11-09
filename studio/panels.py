"""Панели: обозреватель решений, структура, вывод, ошибки, терминал, Unity, конструктор PyQt."""
from collections import deque
import ast
import html
import os
import re
import shutil

from PyQt6.QtCore import Qt, pyqtSignal, QProcess, QFileSystemWatcher, QTimer, QSize
from PyQt6.QtGui import QColor, QFont, QTextCursor, QBrush, QPainter, QTextCharFormat
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QTreeWidget, QTreeWidgetItem, QTreeWidgetItemIterator,
                             QPlainTextEdit, QLineEdit, QPushButton, QLabel, QToolButton,
                             QTableWidget, QTableWidgetItem, QHeaderView, QComboBox, QListWidget,
                             QListWidgetItem, QFrame, QGroupBox, QMenu, QInputDialog, QMessageBox,
                             QDialog, QDialogButtonBox, QFormLayout, QCheckBox, QRadioButton,
                             QSpinBox, QSlider, QProgressBar, QTextEdit, QGridLayout, QScrollArea,
                             QAbstractItemView, QSizePolicy)

from . import theme, unity_api, templates, languages
from .theme import C
from .log_limits import LOG_CHARS, LOG_BLOCKS, configure_log, trim_log, clip_log
from .i18n import bind_text, tr, plural

HIDDEN_DIRS = {"Library", "Temp", "Logs", "obj", "bin", "__pycache__", ".git", ".venv", "venv",
               ".idea", ".vs", ".vscode", "UserSettings", "Build", "Builds", "node_modules",
               ".mypy_cache", ".pytest_cache"}
# что обозреватель решений показывает по умолчанию — как Visual Studio: код и файлы проекта, без ресурсов
CODE_EXT = {".cs", ".asmdef", ".asmref", ".rsp", ".shader", ".hlsl", ".cginc", ".compute", ".glsl", ".shadergraph",
            ".uxml", ".uss", ".inputactions", ".py", ".pyw", ".pyi", ".ui", ".qrc", ".qss", ".rs", ".js", ".ts",
            ".html", ".css", ".json", ".xml", ".toml", ".cfg", ".ini", ".yml", ".yaml", ".md", ".txt", ".sh",
            ".sln", ".slnx", ".csproj", ".props", ".targets", ".desktop"}
CODE_NAMES = {"PKGBUILD", "Makefile", "CMakeLists.txt", "Dockerfile", "requirements.txt", ".gitignore"}
CODE_EXT |= languages.all_extensions()      # всё, что знает реестр языков, — тоже код
CODE_HIDDEN_DIRS = {"ProjectSettings", "UserSettings"}      # Unity: настройки редактора, не код
ROLE_PATH = Qt.ItemDataRole.UserRole
ROLE_LINE = Qt.ItemDataRole.UserRole + 1


def is_code_file(name):
    """Код или файл проекта: общий список, реестр языков и файлы наборов инструментов (toolchains)."""
    ext = os.path.splitext(name)[1].lower()
    if name in CODE_NAMES or ext in CODE_EXT:
        return True
    from . import toolchains
    exts, names = toolchains.known_files()
    return ext in exts or name in names


def mini_button(icon_name, tip, slot):
    b = QToolButton()
    b.setIcon(theme.tool_icon(icon_name))
    b.setProperty("tool_icon", icon_name)      # значок перерисуется при смене темы
    bind_text(b, tip, "setToolTip")
    b.setAutoRaise(True)
    b.clicked.connect(slot)
    return b


def is_unity_project(path):
    return os.path.isdir(os.path.join(path, "Assets")) and (
        os.path.isdir(os.path.join(path, "ProjectSettings")) or os.path.isdir(os.path.join(path, "Packages")))


def find_unity_projects(root, depth=2):
    out = []
    if not root or not os.path.isdir(root):
        return out
    if is_unity_project(root):
        return [root]
    try:
        for name in sorted(os.listdir(root)):
            p = os.path.join(root, name)
            if os.path.isdir(p) and name not in HIDDEN_DIRS and not name.startswith("."):
                if is_unity_project(p):
                    out.append(p)
                elif depth > 1:
                    out.extend(find_unity_projects(p, depth - 1))
    except OSError:
        pass
    return out


# ====================================================================== обозреватель решений
class BusyBar(QWidget):
    """Бегущая полоска «идёт работа» для строки состояния. Рисуется сама: стандартная
    бесконечная QProgressBar под глобальным QSS приложения не анимируется."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(110, 6)
        self._x = 0
        self._timer = QTimer(self, interval=30, timeout=self._tick)
        self.hide()

    def start(self):
        self._x = 0
        self._timer.start()
        self.show()

    def stop(self):
        self._timer.stop()
        self.hide()

    def _tick(self):
        self._x = (self._x + 3) % (self.width() + 40)
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(C["border"]))
        p.fillRect(self._x - 40, 0, 40, self.height(), QColor(C["status_fg"]))
        p.end()


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




# ====================================================================== структура файла
class OutlinePanel(QWidget):
    goto = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel(tr("panels.no_open_file"))
        self.path = None
        self.title.setObjectName("hint")
        lay.addWidget(self.title)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(14)
        self.tree.itemClicked.connect(lambda it, _: self.goto.emit(it.data(0, ROLE_LINE) or 1))
        lay.addWidget(self.tree, 1)

    def retranslate(self):
        if not self.path:
            self.title.setText(tr("panels.no_open_file"))
        items = QTreeWidgetItemIterator(self.tree)
        while (item := items.value()) is not None:
            item.setToolTip(0, tr("panels.line_2") % item.data(0, ROLE_LINE))
            items += 1

    def update_for(self, path, text, lang):
        """Запасной путь без анализатора: разбор текста (outline)."""
        from . import symbols
        self.show_symbols(path, symbols.from_outline(outline(text, lang), path=path) if path else [])

    def show_symbols(self, path, items):
        """Дерево символов (symbols.Symbol): от анализатора (documentSymbol) или из разбора текста.
        Раскрытые узлы и прокрутка сохраняются, если файл тот же."""
        same = path == self.path
        collapsed = set()
        if same:
            it = QTreeWidgetItemIterator(self.tree)
            while (item := it.value()) is not None:
                if item.childCount() and not item.isExpanded():
                    collapsed.add((item.text(0), item.data(0, ROLE_LINE)))
                it += 1
        scroll = self.tree.verticalScrollBar().value()
        self.path = path
        self.tree.clear()
        if not path:
            self.title.setText(tr("panels.no_open_file"))
            return
        self.title.setText("  " + os.path.basename(path))

        def add(parent, sym):
            it = QTreeWidgetItem([sym.name])
            it.setIcon(0, theme.kind_icon(sym.kind))
            it.setData(0, ROLE_LINE, sym.start)
            it.setToolTip(0, (sym.detail + "\n" if sym.detail else "") + tr("panels.line_2") % sym.start)
            (parent.addChild if parent is not None else self.tree.addTopLevelItem)(it)
            for ch in sym.children:
                if ch.kind not in ("variable",):        # локальные переменные в структуре не нужны
                    add(it, ch)
            it.setExpanded((sym.name, sym.start) not in collapsed)
        for sym in items:
            add(None, sym)
        if same:
            self.tree.verticalScrollBar().setValue(scroll)


def outline(text, lang):
    out = []
    if lang == "python":
        try:
            tree = ast.parse(text)

            def walk(node, depth):
                for ch in ast.iter_child_nodes(node):
                    if isinstance(ch, ast.ClassDef):
                        out.append((depth, "class", ch.name, ch.lineno))
                        walk(ch, depth + 1)
                    elif isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        args = [a.arg for a in ch.args.args if a.arg not in ("self", "cls")]
                        out.append((depth, "method" if depth else "function",
                                    "%s(%s)" % (ch.name, ", ".join(args)), ch.lineno))
            walk(tree, 0)
            return out
        except SyntaxError:
            pass
        for no, line in enumerate(text.splitlines(), 1):
            m = re.match(r"(\s*)(class|def)\s+(\w+)", line)
            if m:
                out.append((len(m.group(1)) // 4, "class" if m.group(2) == "class" else "function", m.group(3), no))
    elif lang == "csharp":
        depth = 0
        for no, line in enumerate(text.splitlines(), 1):
            s = line.strip()
            m = re.match(r"(?:\[.*?\]\s*)*(?:(?:public|private|protected|internal|static|sealed|abstract|partial)\s+)*(class|struct|interface|enum)\s+(\w+)", s)
            if m:
                out.append((depth, "class" if m.group(1) != "enum" else "enum", m.group(2), no))
            else:
                m = re.match(r"(?:(?:public|private|protected|internal|static|override|virtual|async|abstract)\s+)*"
                             r"([\w<>\[\],]+)\s+(\w+)\s*\(([^)]*)\)?\s*(?:\{.*)?$", s)
                if m and m.group(1) not in ("new", "return", "else", "if", "while", "for", "foreach", "switch", "using") and depth >= 1:
                    kind = "event" if m.group(2) in {x[0] for x in unity_api.MESSAGES} else "method"
                    out.append((depth, kind, "%s()" % m.group(2), no))
                else:
                    m = re.match(r"(?:\[.*?\]\s*)*(?:(?:public|private|protected|internal|static|readonly|const)\s+)+([\w<>\[\],]+)\s+(\w+)\s*(?:=|;|\{)", s)
                    if m and depth >= 1:
                        out.append((depth, "field", "%s : %s" % (m.group(2), m.group(1)), no))
            depth += line.count("{") - line.count("}")
            depth = max(depth, 0)
    elif lang == "xml":
        for no, line in enumerate(text.splitlines(), 1):
            m = re.search(r'<widget class="(\w+)" name="(\w+)"', line)
            if m:
                out.append((0, "class", "%s : %s" % (m.group(2), m.group(1)), no))
    elif lang == "markdown":
        for no, line in enumerate(text.splitlines(), 1):
            m = re.match(r"(#+)\s+(.*)", line)
            if m:
                out.append((len(m.group(1)) - 1, "field", m.group(2), no))
    return out


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


# ====================================================================== список ошибок
class ReferencesPanel(QWidget):
    """«Результаты поиска ссылок» (Shift+F12): по файлам, у каждой ссылки — строка кода."""
    goto = pyqtSignal(str, int, int)

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.title = QLabel()
        self.summary = None
        self.title.setContentsMargins(8, 4, 8, 4)
        lay.addWidget(self.title)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["", ""])
        self.retranslate()
        self.tree.setRootIsDecorated(True)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().resizeSection(1, 70)
        self.tree.itemActivated.connect(self._open)
        lay.addWidget(self.tree, 1)

    def retranslate(self):
        self.tree.setHeaderLabels([tr("panels.code"), tr("panels.line")])
        if self.summary is None:
            self.title.setText(tr("panels.press_shift_f12_class_method"))
        else:
            word, refs, files = self.summary
            self.title.setText(tr("panels.references") % (
                word, refs, files, plural(files, "common.file_one", "common.files_few", "common.files_few")))

    def show_results(self, word, refs, root, line_text):
        """refs — [{"path", "line", "col"}]; line_text(путь, строка) → текст строки."""
        self.tree.clear()
        files = {}
        for r in sorted(refs, key=lambda r: (r["path"], r["line"], r["col"])):
            files.setdefault(r["path"], []).append(r)
        self.summary = word, len(refs), len(files)
        self.retranslate()
        mono = theme.code_font(10)
        for path, items in files.items():
            rel = os.path.relpath(path, root) if root and path.startswith(root) else path
            top = QTreeWidgetItem([" %s  (%d)" % (rel, len(items)), ""])
            top.setIcon(0, theme.file_icon(path))
            self.tree.addTopLevelItem(top)
            for r in items:
                it = QTreeWidgetItem([line_text(path, r["line"]).strip(), str(r["line"])])
                it.setFont(0, mono)
                it.setData(0, Qt.ItemDataRole.UserRole, (path, r["line"], r["col"]))
                top.addChild(it)
            top.setExpanded(True)

    def _open(self, item, _col):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data:
            self.goto.emit(*data)


class ErrorList(QWidget):
    goto = pyqtSignal(str, int, int)

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        bar = QHBoxLayout()
        bar.setContentsMargins(6, 2, 6, 2)
        self.scope = QComboBox()
        self.scope.addItems([tr("panels.entire_solution"), tr("panels.open_documents"), tr("panels.current_document")])
        self.scope.currentIndexChanged.connect(lambda *_: self.render())
        bar.addWidget(self.scope)
        self.btn_err = QToolButton(checkable=True, checked=True)
        self.btn_warn = QToolButton(checkable=True, checked=True)
        for b in (self.btn_err, self.btn_warn):
            b.toggled.connect(lambda *_: self.render())
            bar.addWidget(b)
        bar.addStretch(1)
        lay.addLayout(bar)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["", tr("panels.description"), tr("common.project"), tr("common.file"), tr("panels.line")])
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        h.resizeSection(0, 26)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        h.resizeSection(2, 110)
        h.resizeSection(3, 170)
        h.resizeSection(4, 60)
        self.table.verticalHeader().setDefaultSectionSize(22)
        self.table.cellDoubleClicked.connect(self._open)
        lay.addWidget(self.table, 1)
        self.groups = {}      # ключ (источник, путь) → список (строка, столбец, текст, вид)
        self.root = None
        self.current = None
        self.open_paths = set()
        self.render()

    def set_group(self, source, path, diags):
        key = (source, path)
        if diags:
            self.groups[key] = list(diags)
        else:
            self.groups.pop(key, None)
        self.render()

    def clear_source(self, source):
        for key in [k for k in self.groups if k[0] == source]:
            del self.groups[key]
        self.render()

    def counts(self):
        e = sum(1 for d in self.groups.values() for x in d if x[3] == "error")
        w = sum(1 for d in self.groups.values() for x in d if x[3] != "error")
        return e, w

    def retranslate(self):
        for i, key in enumerate(("panels.entire_solution", "panels.open_documents", "panels.current_document")):
            self.scope.setItemText(i, tr(key))
        self.table.setHorizontalHeaderLabels(["", tr("panels.description"), tr("common.project"), tr("common.file"), tr("panels.line")])
        self._update_counts()

    def _update_counts(self):
        e, w = self.counts()
        self.btn_err.setText(tr("panels.errors") % e)
        self.btn_warn.setText(tr("panels.warnings") % w)

    def render(self):
        self._update_counts()
        rows = []
        scope = self.scope.currentIndex()
        for (src, path), diags in self.groups.items():
            if scope == 1 and path not in self.open_paths:
                continue
            if scope == 2 and path != self.current:
                continue
            for line, col, msg, sev, *project in diags:    # пятым может идти проект (сборка его знает)
                if sev == "error" and not self.btn_err.isChecked():
                    continue
                if sev != "error" and not self.btn_warn.isChecked():
                    continue
                rows.append((sev, msg, path, line, col, project[0] if project else None))
        rows.sort(key=lambda r: (r[0] != "error", r[2], r[3]))
        self.table.setRowCount(len(rows))
        for i, (sev, msg, path, line, col, project) in enumerate(rows):
            icon = QTableWidgetItem("✖" if sev == "error" else "⚠")
            icon.setForeground(QBrush(QColor(C["error"] if sev == "error" else C["warning"])))
            icon.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(i, 0, icon)
            self.table.setItem(i, 1, QTableWidgetItem(msg))
            proj = project or ""
            if not proj and self.root and path.startswith(self.root):
                proj = os.path.relpath(path, self.root).split(os.sep)[0]
            self.table.setItem(i, 2, QTableWidgetItem(proj))
            fi = QTableWidgetItem(os.path.basename(path))
            fi.setData(ROLE_PATH, path)
            fi.setToolTip(path)
            self.table.setItem(i, 3, fi)
            li = QTableWidgetItem(str(line))
            li.setData(ROLE_LINE, col)
            self.table.setItem(i, 4, li)

    def _open(self, row, _col):
        path = self.table.item(row, 3).data(ROLE_PATH)
        line = int(self.table.item(row, 4).text())
        col = self.table.item(row, 4).data(ROLE_LINE) or 1
        self.goto.emit(path, line, col)


# ====================================================================== терминал
class TerminalPanel(QWidget):
    def __init__(self, font):
        super().__init__()
        self.cwd = os.path.expanduser("~")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        configure_log(self.view)
        self.view.setFont(font)
        lay.addWidget(self.view, 1)
        row = QHBoxLayout()
        row.setContentsMargins(4, 2, 4, 2)
        self.prompt = QLabel()
        self.prompt.setFont(font)
        theme.style(self.prompt, "color:@ok@; background:transparent;")
        row.addWidget(self.prompt)
        self.input = QLineEdit()
        self.input.setFont(font)
        bind_text(self.input, "panels.command_example_pip_install_pyqt6", "setPlaceholderText")
        self.input.returnPressed.connect(self.run)
        self.input.installEventFilter(self)
        row.addWidget(self.input, 1)
        row.addWidget(mini_button("stop", "panels.stop_command", self.stop))
        row.addWidget(mini_button("clear", "panels.clear", self.view.clear))
        lay.addLayout(row)
        self.proc = None
        self.history, self.hpos = [], 0
        self._update_prompt()

    def set_cwd(self, path):
        if path and os.path.isdir(path):
            self.cwd = path
            self._update_prompt()

    def _update_prompt(self):
        home = os.path.expanduser("~")
        shown = self.cwd.replace(home, "~", 1) if self.cwd.startswith(home) else self.cwd
        self.prompt.setText(shown + " $")

    def eventFilter(self, obj, ev):
        if ev.type() == ev.Type.KeyPress and self.history:
            if ev.key() == Qt.Key.Key_Up:
                self.hpos = max(0, self.hpos - 1)
                self.input.setText(self.history[self.hpos])
                return True
            if ev.key() == Qt.Key.Key_Down:
                self.hpos = min(len(self.history), self.hpos + 1)
                self.input.setText(self.history[self.hpos] if self.hpos < len(self.history) else "")
                return True
        return False

    def _write(self, text, color=None):
        cur = self.view.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.End)
        fmt = QTextCharFormat()
        if color and color != C["text"]:     # обычный текст — цветом темы виджета
            fmt.setForeground(QColor(color))
        cur.insertText(clip_log(text), fmt)
        trim_log(self.view)
        self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())

    def run(self):
        cmd = self.input.text().strip()
        if not cmd:
            return
        self.history.append(cmd)
        self.history = self.history[-500:]
        self.hpos = len(self.history)
        self.input.clear()
        self._write("%s %s\n" % (self.prompt.text(), cmd), C["ok"])
        if cmd == "clear":
            self.view.clear()
            return
        m = re.match(r"cd(?:\s+(.*))?$", cmd)
        if m:
            target = os.path.expanduser((m.group(1) or "~").strip().strip('"'))
            target = os.path.normpath(os.path.join(self.cwd, target))
            if os.path.isdir(target):
                self.set_cwd(target)
            else:
                self._write(tr("panels.cd_no_such_folder") % target, C["error"])
            return
        if self.proc and self.proc.state() != QProcess.ProcessState.NotRunning:
            self._write(tr("panels.previous_command_still_being_executed"), C["warning"])
            return
        if self.proc is not None:
            self.proc.deleteLater()
        self.proc = QProcess(self)
        self.proc.setWorkingDirectory(self.cwd)
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.proc.readyReadStandardOutput.connect(
            lambda: self._write(bytes(self.proc.readAllStandardOutput()).decode("utf-8", "replace")))
        self.proc.finished.connect(lambda code, _s: self._write(
            "" if code == 0 else tr("panels.exit_code") % code, C["dim"]))
        self.proc.start("bash", ["-c", cmd])

    def stop(self):
        if self.proc and self.proc.state() != QProcess.ProcessState.NotRunning:
            self.proc.kill()
            self._write("^C\n", C["warning"])


# ====================================================================== Unity
UNITY_EDITOR_ROOTS = ["/mnt/Main/Unity/Hub/Editor", os.path.expanduser("~/Unity/Hub/Editor"),
                      "/opt/unity/Editor", "/opt/Unity/Hub/Editor"]


def find_unity_editors(extra_root=None):
    out = []
    roots = ([extra_root] if extra_root else []) + UNITY_EDITOR_ROOTS
    seen = set()
    for root in roots:
        if not root or not os.path.isdir(root) or root in seen:
            continue
        seen.add(root)
        for ver in sorted(os.listdir(root), reverse=True):
            exe = os.path.join(root, ver, "Editor", "Unity")
            if os.path.isfile(exe):
                out.append((ver, exe))
    return out


class UnityPanel(QWidget):
    create_script = pyqtSignal(str, int)     # имя, номер шаблона
    insert_text = pyqtSignal(str)
    message = pyqtSignal(str, str)           # текст, цвет

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.root = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(8, 6, 8, 8)
        scroll.setWidget(body)
        outer.addWidget(scroll)

        g = bind_text(QGroupBox(), "panels.project_unity", "setTitle")
        gl = QVBoxLayout(g)
        self.project = QComboBox()
        bind_text(self.project, "panels.project_folder_unity_from_assets", "setToolTip")
        gl.addWidget(self.project)
        self.project_version = QLabel()
        self.project_version.setObjectName("hint")
        gl.addWidget(self.project_version)
        row = QHBoxLayout()
        lbl = bind_text(QLabel(), "panels.version_unity_as_follows")
        lbl.setObjectName("hint")
        row.addWidget(lbl)
        self.editor = QComboBox()
        row.addWidget(self.editor, 1)
        gl.addLayout(row)
        row = QHBoxLayout()
        self.open_btn = bind_text(QPushButton(), "common.open_unity")
        self.open_btn.setObjectName("primary")
        self.open_btn.clicked.connect(self.open_in_unity)
        row.addWidget(self.open_btn)
        hub = QPushButton("Unity Hub")
        hub.clicked.connect(self.open_hub)
        row.addWidget(hub)
        gl.addLayout(row)
        path_row = QHBoxLayout()
        self.editors_root = QLineEdit(settings.value("unity/editors_root", UNITY_EDITOR_ROOTS[0]))
        bind_text(self.editors_root, "panels.folder_where_unity_hub_stores", "setToolTip")
        self.editors_root.editingFinished.connect(self._root_changed)
        path_row.addWidget(self.editors_root, 1)
        gl.addLayout(path_row)
        lay.addWidget(g)

        g = bind_text(QGroupBox(), "panels.new_c_script", "setTitle")
        gl = QFormLayout(g)
        self.script_name = QLineEdit()
        bind_text(self.script_name, "panels.example_enemyai", "setPlaceholderText")
        self.script_kind = QComboBox()
        self.kinds = [i for i, t in enumerate(templates.TEMPLATES) if t[1] == ".cs"]
        self.script_kind.addItems([tr(templates.TEMPLATES[i][0]) for i in self.kinds])
        gl.addRow(bind_text(QLabel(), "common.name"), self.script_name)
        gl.addRow(bind_text(QLabel(), "panels.view_label"), self.script_kind)
        b = bind_text(QPushButton(), "panels.create_script")
        b.clicked.connect(self._create)
        self.script_name.returnPressed.connect(self._create)
        gl.addRow(b)
        lay.addWidget(g)

        g = bind_text(QGroupBox(), "panels.methods_unity_double_clicking_code", "setTitle")
        gl = QVBoxLayout(g)
        self.messages = QListWidget()
        for name, sig, doc in unity_api.MESSAGES:
            it = QListWidgetItem(theme.kind_icon("event"), name)
            it.setToolTip("%s\n%s" % (sig, tr(doc)))
            it.setData(ROLE_PATH, sig)
            self.messages.addItem(it)
        self.messages.setMinimumHeight(220)
        self.messages.itemDoubleClicked.connect(self._insert_message)
        gl.addWidget(self.messages)
        self.msg_doc = QLabel(tr("panels.select_method_see_description"))
        self.msg_doc.setObjectName("hint")
        self.msg_doc.setWordWrap(True)
        self.messages.currentItemChanged.connect(
            lambda it, _p: self.msg_doc.setText(it.toolTip() if it else ""))
        gl.addWidget(self.msg_doc)
        lay.addWidget(g)
        lay.addStretch(1)
        self.reload_editors()

    def retranslate(self):
        for i, template in enumerate(self.kinds):
            self.script_kind.setItemText(i, tr(templates.TEMPLATES[template][0]))
        for i, (_, sig, doc) in enumerate(unity_api.MESSAGES):
            self.messages.item(i).setToolTip("%s\n%s" % (sig, tr(doc)))
        item = self.messages.currentItem()
        self.msg_doc.setText(item.toolTip() if item else tr("panels.select_method_see_description"))
        if self.editor.count() and not self.editor.itemData(0):
            self.editor.setItemText(0, tr("panels.not_found_please_enter_folder"))
        if self.project.count() and not self.project.itemData(0):
            self.project.setItemText(0, tr("panels.no_unity_project_solution"))
        self._match_version(select_editor=False)

    def _root_changed(self):
        self.settings.setValue("unity/editors_root", self.editors_root.text().strip())
        self.reload_editors()

    def reload_editors(self):
        self.editor.clear()
        editors = find_unity_editors(self.editors_root.text().strip())
        for ver, exe in editors:
            self.editor.addItem(ver, exe)
        if not editors:
            self.editor.addItem(tr("panels.not_found_please_enter_folder"), "")
        self._match_version()

    def set_root(self, root):
        self.root = root
        self.project.clear()
        for p in find_unity_projects(root):
            self.project.addItem(os.path.relpath(p, root) if p != root else os.path.basename(p), p)
        if not self.project.count():
            self.project.addItem(tr("panels.no_unity_project_solution"), "")
        self._match_version()

    def project_path(self):
        return self.project.currentData() or ""

    def _match_version(self, select_editor=True):
        p = self.project_path()
        ver = ""
        if p:
            try:
                with open(os.path.join(p, "ProjectSettings", "ProjectVersion.txt"), encoding="utf-8") as f:
                    m = re.search(r"m_EditorVersion:\s*(\S+)", f.read())
                    ver = m.group(1) if m else ""
            except OSError:
                pass
        self.project_version.setText(tr("panels.project_was_made_unity") % ver if ver else tr("panels.project_version_not_shown"))
        if ver and select_editor:
            i = self.editor.findText(ver)
            if i >= 0:
                self.editor.setCurrentIndex(i)

    def open_in_unity(self):
        p = self.project_path()
        if not p:
            self.message.emit(tr("panels.no_unity_project_solution_folder"), C["warning"])
            return
        exe = self.editor.currentData()
        if exe and os.path.isfile(exe):
            ok = QProcess.startDetached(exe, ["-projectPath", p])[0]
            self.message.emit((tr("panels.opening_project_at_unity") % (self.editor.currentText(), p)) if ok
                              else tr("common.could_not_run") % exe, C["text"] if ok else C["error"])
        else:
            self.open_hub(project=p)

    def open_hub(self, project=None):
        hub = shutil.which("unityhub") or shutil.which("unity-hub")
        if not hub:
            self.message.emit(tr("panels.unity_hub_was_not_found"), C["warning"])
            return
        args = ["--", "--projectPath", project] if project else []
        QProcess.startDetached(hub, args)
        self.message.emit(tr("panels.starting_unity_hub") + (tr("panels.with_project") % project if project else ""), C["text"])

    def _create(self):
        name = re.sub(r"\W", "", self.script_name.text().strip())
        if not name:
            self.message.emit(tr("panels.enter_script_name_latin_like"), C["warning"])
            return
        self.create_script.emit(name, self.kinds[self.script_kind.currentIndex()])
        self.script_name.clear()

    def _insert_message(self, item):
        sig = item.data(ROLE_PATH)
        self.insert_text.emit(sig)


# ====================================================================== конструктор PyQt
WIDGETS = [
    ("QPushButton", "panels.widget_button", "common.click_me"),
    ("QLabel", "panels.widget_label", "common.text"),
    ("QLineEdit", "panels.widget_line_edit", "panels.enter_text"),
    ("QTextEdit", "panels.multi_line_field", ""),
    ("QCheckBox", "panels.checkbox", "panels.enable"),
    ("QRadioButton", "panels.radio_button", "panels.radio_option"),
    ("QComboBox", "panels.drop_down_list", "panels.first_one_second_one_third"),
    ("QSpinBox", "panels.spin_box", "0"),
    ("QSlider", "panels.slider", "50"),
    ("QProgressBar", "panels.indicator", "40"),
    ("QListWidget", "panels.list", "panels.element_1_element_2"),
]


def build_widget(cls, text):
    if cls == "QPushButton":
        return QPushButton(text)
    if cls == "QLabel":
        return QLabel(text)
    if cls == "QLineEdit":
        w = QLineEdit()
        w.setPlaceholderText(text)
        return w
    if cls == "QTextEdit":
        w = QTextEdit()
        w.setPlainText(text)
        w.setMaximumHeight(60)
        return w
    if cls == "QCheckBox":
        return QCheckBox(text)
    if cls == "QRadioButton":
        return QRadioButton(text)
    if cls == "QComboBox":
        w = QComboBox()
        w.addItems([t for t in text.split(";") if t])
        return w
    if cls == "QSpinBox":
        w = QSpinBox()
        w.setValue(int(text) if text.strip().lstrip("-").isdigit() else 0)
        return w
    if cls == "QSlider":
        w = QSlider(Qt.Orientation.Horizontal)
        w.setValue(int(text) if text.strip().isdigit() else 50)
        return w
    if cls == "QProgressBar":
        w = QProgressBar()
        w.setValue(int(text) if text.strip().isdigit() else 0)
        return w
    if cls == "QListWidget":
        w = QListWidget()
        w.addItems([t for t in text.split(";") if t])
        w.setMaximumHeight(70)
        return w
    return QLabel(text)


PREVIEW_STYLE = """
QWidget { background: #F0F0F0; color: #202020; font-size: 9pt; }
QLineEdit, QTextEdit, QSpinBox, QComboBox, QListWidget { background: white; border: 1px solid #ADADAD; padding: 2px; }
QPushButton { background: #E1E1E1; border: 1px solid #ADADAD; padding: 4px 10px; }
QLabel#formTitle { background: #FFFFFF; border-bottom: 1px solid #D0D0D0; padding: 4px 8px; }
QProgressBar { border: 1px solid #BCBCBC; background: #E6E6E6; text-align: center; }
QProgressBar::chunk { background: #06B025; }
"""


class PyQtDesigner(QWidget):
    generated = pyqtSignal(str, str)      # имя файла, код
    new_template = pyqtSignal(int)
    preview_ui = pyqtSignal()

    def __init__(self):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(8, 6, 8, 8)
        scroll.setWidget(body)
        outer.addWidget(scroll)

        g = bind_text(QGroupBox(), "panels.new_file_pyqt", "setTitle")
        gl = QGridLayout(g)
        for n, (label, idx) in enumerate([("panels.window", 0), ("panels.dialog", 1), ("panels.widget", 2), ("panels.form_ui", 3)]):
            b = bind_text(QPushButton(), label)
            b.clicked.connect(lambda _=False, i=idx: self.new_template.emit(i))
            gl.addWidget(b, n // 2, n % 2)
        b = bind_text(QPushButton(), "panels.preview_ui_form_from_editor")
        b.clicked.connect(self.preview_ui.emit)
        gl.addWidget(b, 2, 0, 1, 2)
        lay.addWidget(g)

        g = bind_text(QGroupBox(), "panels.form_designer", "setTitle")
        gl = QVBoxLayout(g)
        form = QFormLayout()
        self.title = QLineEdit(tr("panels.my_form"))
        self.title.textChanged.connect(self.refresh_preview)
        self.cls_name = QLineEdit("MyForm")
        self.layout_kind = QComboBox()
        self.layout_kind.addItems([tr("panels.vertically"), tr("panels.horizontal"), tr("panels.two_column_grid")])
        self.layout_kind.currentIndexChanged.connect(self.refresh_preview)
        self.qt_version = QComboBox()
        self.qt_version.addItems(["PyQt6", "PyQt5"])
        form.addRow(bind_text(QLabel(), "panels.title"), self.title)
        form.addRow(bind_text(QLabel(), "panels.class"), self.cls_name)
        form.addRow(bind_text(QLabel(), "panels.layout"), self.layout_kind)
        form.addRow(bind_text(QLabel(), "panels.library"), self.qt_version)
        gl.addLayout(form)
        row = QHBoxLayout()
        self.add_kind = QComboBox()
        self.add_kind.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.add_kind.setMinimumContentsLength(12)
        for cls, ru, _ in WIDGETS:
            self.add_kind.addItem("%s (%s)" % (tr(ru), cls), cls)
        row.addWidget(self.add_kind, 1)
        add = bind_text(QPushButton(), "common.add")
        add.clicked.connect(self.add_widget)
        row.addWidget(add)
        gl.addLayout(row)
        self.items = QListWidget()
        self.items.setMinimumHeight(120)
        self.items.itemChanged.connect(self.refresh_preview)
        bind_text(self.items, "panels.double_click_change_text", "setToolTip")
        gl.addWidget(self.items)
        row = QHBoxLayout()
        for text, slot in (("↑", lambda: self.move(-1)), ("↓", lambda: self.move(1)), ("panels.delete", self.remove)):
            b = bind_text(QPushButton(), text)
            b.clicked.connect(slot)
            row.addWidget(b)
        gl.addLayout(row)
        lbl = bind_text(QLabel(), "panels.preview")
        lbl.setObjectName("hint")
        gl.addWidget(lbl)
        self.preview = QFrame()
        self.preview.setObjectName("formPreview")
        self.preview.setStyleSheet(PREVIEW_STYLE)
        self.preview.setMinimumHeight(160)
        gl.addWidget(self.preview)
        gen = bind_text(QPushButton(), "panels.create_form_code")
        gen.setObjectName("primary")
        gen.clicked.connect(self.generate)
        gl.addWidget(gen)
        lay.addWidget(g)
        lay.addStretch(1)
        self.counter = {}
        for cls, text in (("QLabel", tr("panels.your_name")), ("QLineEdit", tr("panels.enter_name")), ("QPushButton", tr("panels.say_hello"))):
            self._add(cls, text)
        self.refresh_preview()

    def retranslate(self):
        for i, key in enumerate(("panels.vertically", "panels.horizontal", "panels.two_column_grid")):
            self.layout_kind.setItemText(i, tr(key))
        for i, (cls, label, _) in enumerate(WIDGETS):
            self.add_kind.setItemText(i, "%s (%s)" % (tr(label), cls))

    def _add(self, cls, text):
        self.counter[cls] = self.counter.get(cls, 0) + 1
        base = {"QPushButton": "button", "QLabel": "label", "QLineEdit": "edit", "QTextEdit": "text",
                "QCheckBox": "check", "QRadioButton": "radio", "QComboBox": "combo", "QSpinBox": "spin",
                "QSlider": "slider", "QProgressBar": "progress", "QListWidget": "list"}.get(cls, "widget")
        obj = "%s%d" % (base, self.counter[cls])
        it = QListWidgetItem("%s" % text)
        it.setIcon(theme.kind_icon("class"))
        it.setData(ROLE_PATH, (cls, obj))
        it.setToolTip("%s — self.%s" % (cls, obj))
        it.setFlags(it.flags() | Qt.ItemFlag.ItemIsEditable)
        self.items.blockSignals(True)
        self.items.addItem(it)
        self.items.blockSignals(False)

    def add_widget(self):
        cls = self.add_kind.currentData()
        default = next(w[2] for w in WIDGETS if w[0] == cls)
        self._add(cls, tr(default))
        self.refresh_preview()

    def move(self, d):
        r = self.items.currentRow()
        if r < 0 or not 0 <= r + d < self.items.count():
            return
        it = self.items.takeItem(r)
        self.items.insertItem(r + d, it)
        self.items.setCurrentRow(r + d)
        self.refresh_preview()

    def remove(self):
        r = self.items.currentRow()
        if r >= 0:
            self.items.takeItem(r)
            self.refresh_preview()

    def spec(self):
        out = []
        for i in range(self.items.count()):
            it = self.items.item(i)
            cls, obj = it.data(ROLE_PATH)
            out.append((cls, obj, it.text()))
        return out

    def refresh_preview(self, *_):
        old = self.preview.layout()
        if old is not None:
            while old.count():
                w = old.takeAt(0).widget()
                if w:
                    w.deleteLater()
            QWidget().setLayout(old)
        outer = QVBoxLayout(self.preview)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        title = QLabel("■  " + (self.title.text() or tr("panels.form")))
        title.setObjectName("formTitle")
        outer.addWidget(title)
        inner = QWidget()
        kind = self.layout_kind.currentIndex()
        lay = QVBoxLayout(inner) if kind == 0 else QHBoxLayout(inner) if kind == 1 else QGridLayout(inner)
        lay.setContentsMargins(10, 8, 10, 10)
        for n, (cls, obj, text) in enumerate(self.spec()):
            w = build_widget(cls, text)
            if kind == 2:
                lay.addWidget(w, n // 2, n % 2)
            else:
                lay.addWidget(w)
        outer.addWidget(inner)
        outer.addStretch(1)

    def generate(self):
        lib = self.qt_version.currentText()
        cls_name = re.sub(r"\W", "", self.cls_name.text()) or "MyForm"
        spec = self.spec()
        used = sorted({c for c, _, _ in spec} | {"QApplication", "QWidget"})
        kind = self.layout_kind.currentIndex()
        layout_cls = ["QVBoxLayout", "QHBoxLayout", "QGridLayout"][kind]
        used.append(layout_cls)
        needs_qt = any(c == "QSlider" for c, _, _ in spec)
        lines = ["import sys", ""]
        if needs_qt:
            lines.append("from %s.QtCore import Qt" % lib)
        lines.append("from %s.QtWidgets import (%s)" % (lib, ", ".join(sorted(set(used)))))
        lines += ["", "", "class %s(QWidget):" % cls_name,
                  "    def __init__(self, parent=None):",
                  "        super().__init__(parent)",
                  "        self.setWindowTitle(%r)" % (self.title.text() or tr("panels.form")),
                  "        layout = %s(self)" % layout_cls, ""]
        handlers = []
        for n, (cls, obj, text) in enumerate(spec):
            t = repr(text)
            if cls in ("QPushButton", "QLabel", "QCheckBox", "QRadioButton"):
                lines.append("        self.%s = %s(%s)" % (obj, cls, t))
            elif cls == "QLineEdit":
                lines.append("        self.%s = QLineEdit()" % obj)
                lines.append("        self.%s.setPlaceholderText(%s)" % (obj, t))
            elif cls in ("QComboBox", "QListWidget"):
                lines.append("        self.%s = %s()" % (obj, cls))
                lines.append("        self.%s.addItems(%r)" % (obj, [x for x in text.split(";") if x]))
            elif cls == "QSlider":
                lines.append("        self.%s = QSlider(Qt.Orientation.Horizontal)" % obj)
                lines.append("        self.%s.setValue(%d)" % (obj, int(text) if text.isdigit() else 50))
            elif cls in ("QSpinBox", "QProgressBar"):
                lines.append("        self.%s = %s()" % (obj, cls))
                lines.append("        self.%s.setValue(%d)" % (obj, int(text) if text.lstrip("-").isdigit() else 0))
            elif cls == "QTextEdit":
                lines.append("        self.%s = QTextEdit()" % obj)
                if text:
                    lines.append("        self.%s.setPlainText(%s)" % (obj, t))
            else:
                lines.append("        self.%s = %s()" % (obj, cls))
            if kind == 2:
                lines.append("        layout.addWidget(self.%s, %d, %d)" % (obj, n // 2, n % 2))
            else:
                lines.append("        layout.addWidget(self.%s)" % obj)
            if cls == "QPushButton":
                lines.append("        self.%s.clicked.connect(self.on_%s_clicked)" % (obj, obj))
                handlers.append(obj)
        for obj in handlers:
            lines += ["", "    def on_%s_clicked(self):" % obj,
                      "        print(%r)" % (tr("panels.pressed_button") % obj)]
        lines += ["", "", 'if __name__ == "__main__":',
                  "    app = QApplication(sys.argv)",
                  "    window = %s()" % cls_name,
                  "    window.show()",
                  "    sys.exit(app.exec())", ""]
        fname = re.sub(r"(?<!^)(?=[A-Z])", "_", cls_name).lower() + ".py"
        self.generated.emit(fname, "\n".join(lines))


# ====================================================================== диалог нового файла
class NewFileDialog(QDialog):
    def __init__(self, parent, folders, default_folder, template_index=0):
        super().__init__(parent)
        self.setWindowTitle(tr("panels.add_new_item"))
        self.resize(640, 420)
        lay = QVBoxLayout(self)
        body = QHBoxLayout()
        self.list = QListWidget()
        for label, ext, _t, _w in templates.TEMPLATES:
            it = QListWidgetItem(theme.file_icon("x" + ext), "%s   %s" % (tr(label), ext))
            self.list.addItem(it)
        self.list.setIconSize(QSize(20, 20))
        body.addWidget(self.list, 3)
        side = QVBoxLayout()
        self.desc = QLabel()
        self.desc.setWordWrap(True)
        self.desc.setObjectName("hint")
        side.addWidget(self.desc)
        side.addStretch(1)
        body.addLayout(side, 2)
        lay.addLayout(body, 1)
        form = QFormLayout()
        self.name = QLineEdit()
        self.folder = QComboBox()
        self.folder.setEditable(True)
        for f in folders:
            self.folder.addItem(f)
        form.addRow(tr("common.name"), self.name)
        form.addRow(tr("panels.folder"), self.folder)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText(tr("common.add"))
        bb.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("panels.cancel"))
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.default_folder = default_folder
        self.list.currentRowChanged.connect(self._changed)
        self.list.setCurrentRow(template_index)
        self.name.setFocus()

    def _changed(self, row):
        label, ext, text, where = templates.TEMPLATES[row]
        hints = {"python": tr("panels.file_will_fall_into_project"), "unity": tr("panels.script_will_fall_into_assets"),
                 "unity_editor": tr("panels.editor_window_should_folder_assets"), None: ""}
        self.desc.setText("<b>%s</b><br><br>%s" % (label, hints.get(where, "")))
        if not self.name.text() or self.name.text().startswith("new_") or self.name.text().startswith("New"):
            self.name.setText("NewScript" if ext == ".cs" else "new_file")
        target = self.default_folder(where)
        if target:
            i = self.folder.findText(target)
            if i < 0:
                self.folder.addItem(target)
                i = self.folder.count() - 1
            self.folder.setCurrentIndex(i)

    def result_values(self):
        return self.list.currentRow(), self.name.text().strip(), self.folder.currentText().strip()
