"""Новые файлы и помощники проекта: шаблоны, скрипты Unity, PyQt-дизайнер, предпросмотр .ui,
папка версий Unity, установка requirements.txt."""
import os
import re

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFileDialog, QMessageBox

from . import templates
from .i18n import tr
from .panels import NewFileDialog, HIDDEN_DIRS, find_unity_projects
from .theme import C

APP_NAME = "UniPy Studio"


class ProjectActions:
    def __init__(self, window):
        self.win = window

    def default_folder(self, where):
        if not self.win.root:
            return ""
        unity = find_unity_projects(self.win.root)
        if where == "unity" and unity:
            return os.path.join(unity[0], "Assets", "Scripts")
        if where == "unity_editor" and unity:
            return os.path.join(unity[0], "Assets", "Editor")
        if where == "python":
            ed = self.win.editor()
            if ed and ed.path.endswith(".py"):
                return os.path.dirname(ed.path)
            for p in self.win.explorer.files:
                if p.endswith(".py"):
                    return os.path.dirname(p)
        return self.win.root

    def new_file_dialog(self, folder=None, template=0):
        if not self.win.root:
            self.win.open_folder_dialog()
            if not self.win.root:
                return
        folders = []
        for dirpath, dirnames, _ in os.walk(self.win.root):
            dirnames[:] = sorted(d for d in dirnames if d not in HIDDEN_DIRS and not d.startswith("."))
            folders.append(dirpath)
            if len(folders) > 300:
                break
        default = (lambda where: folder) if folder else self.default_folder
        dlg = NewFileDialog(self.win, folders, default, template)
        if dlg.exec():
            idx, name, target = dlg.result_values()
            self.create_from_template(idx, name, target)

    def create_from_template(self, idx, name, folder):
        label, ext, text, _where = templates.TEMPLATES[idx]
        name = name or "new_file"
        base = name[:-len(ext)] if name.endswith(ext) else name
        if ext == ".cs":
            base = re.sub(r"\W", "", base) or "NewScript"
        path = os.path.join(folder, base + ext)
        if os.path.exists(path):
            QMessageBox.warning(self.win, APP_NAME, tr("mainwindow.file_already_exists") + path)
            self.win.open_path(path)
            return
        os.makedirs(folder, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(templates.render(text, base))
        self.win.explorer.refresh()
        self.win.open_path(path)
        self.win.output.write(tr("mainwindow.file_created") % os.path.relpath(path, self.win.root), "common.build", C["dim"])

    def create_unity_script(self, name, idx):
        if not find_unity_projects(self.win.root):
            QMessageBox.information(self.win, APP_NAME, tr("mainwindow.there_s_no_unity_project"))
            return
        self.create_from_template(idx, name, self.default_folder(templates.TEMPLATES[idx][3]))

    def insert_unity_message(self, sig):
        ed = self.win.editor()
        if not ed or ed.lang != "csharp":
            self.win.status(tr("mainwindow.open_c_script_insert_method"))
            return
        c = ed.textCursor()
        line = c.block().text()
        indent = re.match(r"[ \t]*", line).group(0) or ed.indent.unit()
        if line.strip():
            c.movePosition(c.MoveOperation.EndOfBlock)
            c.insertText("\n\n" + indent)
        elif c.positionInBlock() < len(indent):
            c.movePosition(c.MoveOperation.EndOfBlock)
        body = "%s\n%s{\n%s%s\n%s}" % (sig, indent, indent, ed.indent.unit(), indent)
        c.insertText(body)
        c.movePosition(c.MoveOperation.Up)
        c.movePosition(c.MoveOperation.EndOfBlock)
        ed.setTextCursor(c)
        ed.setFocus()

    def designer_generated(self, fname, code):
        folder = self.default_folder("python")
        path = os.path.join(folder, fname)
        n = 2
        while os.path.exists(path):
            path = os.path.join(folder, fname[:-3] + "_%d.py" % n)
            n += 1
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)
        self.win.explorer.refresh()
        self.win.open_path(path)
        self.win.output.write(tr("mainwindow.pyqt_designer_created_click_run") %
                          os.path.relpath(path, self.win.root), "common.build", C["text"])

    def preview_ui(self):
        ed = self.win.editor()
        if not ed or not ed.path.endswith(".ui"):
            QMessageBox.information(self.win, APP_NAME, tr("mainwindow.open_file_form_ui_editor"))
            return
        import io
        from PyQt6 import uic
        try:
            w = uic.loadUi(io.StringIO(ed.toPlainText()))
        except Exception as e:  # noqa
            self.win.output.write(tr("mainwindow.could_not_open_form") % e, "common.build", C["error"])
            self.win.show_dock(self.win.d_output)
            return
        w.setParent(self.win, Qt.WindowType.Window)
        w.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        w.setStyleSheet("QWidget{background:#F0F0F0;color:#202020;} QLineEdit,QTextEdit{background:white;}")
        w.setWindowTitle(tr("mainwindow.preview_prefix") + (w.windowTitle() or os.path.basename(ed.path)))
        w.show()

    def pick_unity_root(self):
        d = QFileDialog.getExistingDirectory(self.win, tr("mainwindow.folder_with_version_unity_hub"),
                                             self.win.unity.editors_root.text())
        if d:
            self.win.unity.editors_root.setText(d)
            self.win.unity._root_changed()
            self.win.show_dock(self.win.d_unity)

    def install_requirements(self):
        reqs = [p for p in self.win.explorer.files if os.path.basename(p) == "requirements.txt"]
        if not reqs:
            self.win.status(tr("mainwindow.no_requirements_txt_solution"))
            return
        self.win.show_dock(self.win.d_terminal)
        self.win.terminal.type_text('cd "%s" && "%s" -m pip install --user -r requirements.txt'
                                    % (os.path.dirname(reqs[0]), self.win.interp.currentData()))
        self.win.status(tr("mainwindow.check_command_terminal_press_enter"))
