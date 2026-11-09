"""Диалог «Добавить новый элемент» (Ctrl+N)."""
from PyQt6.QtCore import QSize
from PyQt6.QtWidgets import QVBoxLayout, QHBoxLayout, QLineEdit, QLabel, QComboBox, QListWidget, QListWidgetItem, QDialog, QDialogButtonBox, QFormLayout
from .. import theme, templates
from ..i18n import tr


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
