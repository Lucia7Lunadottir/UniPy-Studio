"""«Список ошибок»: группы по источнику и файлу, фильтры, переход к строке."""
import os
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QBrush
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QToolButton, QTableWidget, QTableWidgetItem, QHeaderView, QComboBox, QAbstractItemView
from ..theme import C
from ..i18n import tr
from .common import ROLE_LINE, ROLE_PATH, FlowRow


class ErrorList(QWidget):
    goto = pyqtSignal(str, int, int)

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        bar = FlowRow(margins=(6, 2, 6, 2))         # узкое окно — фильтры переносятся
        self.scope = QComboBox()
        self.scope.addItems([tr("panels.entire_solution"), tr("panels.open_documents"), tr("panels.current_document")])
        self.scope.currentIndexChanged.connect(lambda *_: self.refresh())
        bar.addWidget(self.scope)
        self.btn_err = QToolButton(checkable=True, checked=True)
        self.btn_warn = QToolButton(checkable=True, checked=True)
        for b in (self.btn_err, self.btn_warn):
            b.toggled.connect(lambda *_: self.refresh())
            bar.addWidget(b)
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
        self.refresh()

    def set_group(self, source, path, diags):
        key = (source, path)
        if diags:
            self.groups[key] = list(diags)
        else:
            self.groups.pop(key, None)
        self.refresh()

    def clear_source(self, source):
        for key in [k for k in self.groups if k[0] == source]:
            del self.groups[key]
        self.refresh()

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

    def refresh(self):
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
