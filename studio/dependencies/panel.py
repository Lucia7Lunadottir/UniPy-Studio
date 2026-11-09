"""Панель «Зависимости»: что требуется проекту и что установлено в выбранном окружении."""
import os

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QHBoxLayout, QHeaderView, QLabel, QPushButton, QTreeWidget, QTreeWidgetItem,
                             QVBoxLayout, QWidget)

from .. import theme
from ..i18n import tr
from .base import MISMATCH, MISSING, OK, UNKNOWN

ROLE = Qt.ItemDataRole.UserRole
STATES = {OK: ("✔", "#57AB5A", "dependencies.state_ok"), MISSING: ("✖", "#E5534B", "dependencies.state_missing"),
          MISMATCH: ("⚠", "#D29922", "dependencies.state_mismatch"), UNKNOWN: ("?", "#8B949E", "dependencies.state_unknown")}


class DependenciesPanel(QWidget):
    install = pyqtSignal()
    create_venv = pyqtSignal()
    refresh = pyqtSignal()
    open_location = pyqtSignal(str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        self.env = QLabel()
        self.env.setWordWrap(True)
        lay.addWidget(self.env)
        bar = QHBoxLayout()
        self.install_button = QPushButton()
        self.install_button.clicked.connect(self.install)
        self.venv_button = QPushButton()
        self.venv_button.clicked.connect(self.create_venv)
        self.refresh_button = QPushButton()
        self.refresh_button.clicked.connect(self.refresh)
        for b in (self.install_button, self.venv_button, self.refresh_button):
            bar.addWidget(b)
        bar.addStretch(1)
        lay.addLayout(bar)
        self.tree = QTreeWidget()
        self.tree.setRootIsDecorated(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.itemDoubleClicked.connect(self._open)
        lay.addWidget(self.tree, 1)
        self.note = QLabel()
        self.note.setWordWrap(True)
        theme.style(self.note, "color:@dim@;")
        lay.addWidget(self.note)
        self.retranslate()
        self.show_requirements(None, False, [], checking=False)

    def retranslate(self):
        self.install_button.setText(tr("dependencies.install_missing"))
        self.venv_button.setText(tr("dependencies.create_venv"))
        self.refresh_button.setText(tr("dependencies.refresh"))
        self.tree.setHeaderLabels([tr("dependencies.package"), tr("dependencies.required"),
                                   tr("dependencies.installed"), tr("dependencies.source")])

    def show_requirements(self, interpreter, system, requirements, checking, root=None, can_venv=False):
        self.tree.clear()
        if interpreter is None:
            self.env.setText(tr("dependencies.no_project"))
            for b in (self.install_button, self.venv_button, self.refresh_button):
                b.setEnabled(False)
            self.note.setText("")
            return
        kind = tr("dependencies.system_env") if system else tr("dependencies.venv_env")
        self.env.setText("%s\n%s" % (interpreter, kind))
        for r in requirements:
            glyph, color, _name = STATES[r.state]
            source = os.path.relpath(r.source, root) if root and r.source else r.source
            if r.group:
                source += " [%s]" % r.group
            item = QTreeWidgetItem(["%s  %s" % (glyph if not checking else "…", r.name), r.spec or "*",
                                    r.installed or "—", "%s:%d" % (source, r.line)])
            item.setData(0, ROLE, (r.source, r.line))
            self.tree.addTopLevelItem(item)
            if not checking:
                item.setForeground(0, QColor(color))
                tip = tr(STATES[r.state][2])
                if system and r.state != OK:
                    tip += "\n" + (tr("dependencies.from_repo") % r.system_package if r.system_package
                                   else tr("dependencies.not_in_repo"))
                item.setToolTip(0, tip)
        missing = [r for r in requirements if r.state in (MISSING, MISMATCH)]
        self.install_button.setEnabled(not checking and bool(missing))
        self.venv_button.setVisible(can_venv)
        self.venv_button.setEnabled(not checking)
        self.refresh_button.setEnabled(not checking)
        if checking:
            self.note.setText(tr("dependencies.checking"))
        elif not requirements:
            self.note.setText(tr("dependencies.no_requirements"))
        elif not missing:
            self.note.setText(tr("dependencies.all_ok"))
        elif system and any(r.system_package is None for r in missing):
            self.note.setText(tr("dependencies.need_venv"))
        else:
            self.note.setText(tr("dependencies.missing_count") % len(missing))

    def _open(self, item, _col):
        data = item.data(0, ROLE)
        if data and data[0] and os.path.isfile(data[0]):
            self.open_location.emit(data[0], data[1])
