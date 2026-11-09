"""Панель Unity: установленные редакторы, открыть проект, скрипты по шаблонам, сообщения MonoBehaviour."""
import os
import re
import shutil
from PyQt6.QtCore import Qt, pyqtSignal, QProcess
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton, QLabel, QComboBox, QListWidget, QListWidgetItem, QFrame, QGroupBox, QFormLayout, QScrollArea
from .. import theme, unity_api, templates
from ..theme import C
from ..i18n import bind_text, tr
from .common import ROLE_PATH, find_unity_projects


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
