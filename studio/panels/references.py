"""«Результаты поиска ссылок» (Shift+F12)."""
import os
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QTreeWidget, QTreeWidgetItem, QLabel, QHeaderView
from .. import theme
from ..i18n import tr, plural


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
        self.title.setWordWrap(True)
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
