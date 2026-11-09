"""«Файл → Создать проект…» (Ctrl+Shift+N), как «Создание проекта» в Visual Studio.

Шаблоны собираются со всех наборов инструментов (toolchains): Python отдаёт свои сразу, .NET — из
`dotnet new list` чуть позже. Окно ничего не знает о языках: только ProjectTemplate и Toolchain.
"""
import os
import re

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLineEdit, QComboBox, QListWidget,
                             QListWidgetItem, QLabel, QFormLayout, QPushButton, QCheckBox, QDialogButtonBox,
                             QFileDialog)

from . import theme
from .i18n import tr

ROLE = Qt.ItemDataRole.UserRole
BAD_NAME = re.compile(r'[\\/:*?"<>|]|^\.|\s$')


class NewProjectDialog(QDialog):
    def __init__(self, parent, toolchains, folder):
        super().__init__(parent)
        self.setWindowTitle(tr("new_project.title"))
        self.resize(820, 560)
        self.templates = []
        self.unavailable = {}           # набор → подсказка, что поставить
        lay = QVBoxLayout(self)

        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("new_project.search"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._fill)
        self.language = QComboBox()
        self.language.addItem(tr("new_project.all_languages"), "")
        self.language.currentIndexChanged.connect(self._fill)
        top.addWidget(self.search, 1)
        top.addWidget(self.language)
        lay.addLayout(top)

        self.list = QListWidget()
        self.list.currentItemChanged.connect(lambda *_: self._selected())
        self.list.itemDoubleClicked.connect(lambda *_: self.accept())
        lay.addWidget(self.list, 1)
        self.about = QLabel()
        self.about.setWordWrap(True)
        theme.style(self.about, "color:@dim@;")
        lay.addWidget(self.about)
        self.loading = QLabel(tr("new_project.loading"))
        theme.style(self.loading, "color:@dim@;")
        lay.addWidget(self.loading)

        form = QFormLayout()
        self.name = QLineEdit()
        self.name.textChanged.connect(self._validate)
        form.addRow(tr("new_project.name"), self.name)
        where = QHBoxLayout()
        self.folder = QLineEdit(folder)
        self.folder.textChanged.connect(self._validate)
        browse = QPushButton(tr("new_project.browse"))
        browse.clicked.connect(self._browse)
        where.addWidget(self.folder, 1)
        where.addWidget(browse)
        form.addRow(tr("new_project.location"), where)
        self.solution = QCheckBox(tr("new_project.with_solution"))
        self.solution.setChecked(True)
        form.addRow(self.solution)
        lay.addLayout(form)
        self.problem = QLabel()
        theme.style(self.problem, "color:@error@;")
        lay.addWidget(self.problem)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.create_button = self.buttons.addButton(tr("new_project.create"), QDialogButtonBox.ButtonRole.AcceptRole)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)

        self._auto_name = ""
        # сначала — сколько наборов ждём: Python и Unity отвечают сразу, и счётчик, увеличиваемый по одному,
        # падал до нуля раньше, чем .NET начинал загрузку («Загрузка шаблонов…» пропадала слишком рано)
        self._pending = len(toolchains)
        for tc in toolchains:
            ok, hint = tc.available()
            if not ok:
                self.unavailable[tc] = hint
        for tc in list(toolchains):
            tc.load_templates(lambda found, t=tc: self._loaded(t, found))
        self._validate()
        self.search.setFocus()

    # --- шаблоны
    def _loaded(self, tc, found):
        self._pending -= 1
        for template in found:
            template.toolchain = tc
            self.templates.append(template)
            if template.language and self.language.findData(template.language) < 0:
                self.language.addItem(template.language, template.language)
        self.loading.setVisible(self._pending > 0 or bool(self.unavailable))
        if self._pending <= 0:
            notes = ["%s: %s" % (tc.name, hint) for tc, hint in self.unavailable.items()]
            self.loading.setText(tr("new_project.missing_tools") % "; ".join(notes) if notes else "")
        self._fill()

    def _fill(self):
        current = self.list.currentItem().data(ROLE) if self.list.currentItem() else None
        needle = self.search.text().strip().casefold()
        lang = self.language.currentData()
        self.list.clear()
        for t in self.templates:
            title = tr(t.name)
            if lang and t.language != lang:
                continue
            if needle and needle not in " ".join((title, t.id, t.tags, t.language)).casefold():
                continue
            item = QListWidgetItem(theme.badge(*t.toolchain.badge), title)
            item.setData(ROLE, t)
            item.setToolTip("%s · %s%s" % (t.toolchain.name, t.id, " · " + t.tags if t.tags else ""))
            self.list.addItem(item)
            if t is current:
                self.list.setCurrentItem(item)
        if self.list.currentRow() < 0 and self.list.count():
            self.list.setCurrentRow(0)
        self._validate()

    def _selected(self):
        t = self.template()
        if t is None:
            self.about.setText("")
        else:
            parts = [tr(t.description) if t.description else "", t.tags.replace("/", " · ")]
            self.about.setText("\n".join(p for p in parts if p))
            self.solution.setVisible("solution" in t.options)
            if not self.name.text() or self.name.text() == self._auto_name:     # имя не правили руками
                self._auto_name = self._free_name(t)
                self.name.setText(self._auto_name)
        self._validate()

    def _free_name(self, t):
        """Как в VS: ConsoleApp1, AvaloniaApp1… — первое свободное в выбранной папке."""
        base = re.sub(r"[^A-Za-z0-9]", "", t.id.title())[:24]
        if not base or not base[0].isalpha():
            base = "Project"
        if not base.lower().endswith(("app", "lib", "library", "service", "api")):
            base += "App"
        folder = self.folder.text()
        n = 1
        while os.path.exists(os.path.join(folder, "%s%d" % (base, n))):
            n += 1
        return "%s%d" % (base, n)

    def template(self):
        item = self.list.currentItem()
        return item.data(ROLE) if item else None

    # --- проверка
    def problems(self):
        name, folder = self.name.text().strip(), self.folder.text().strip()
        t = self.template()
        if t is None:
            return tr("new_project.choose_template")
        if t.toolchain in self.unavailable:
            return tr("new_project.tool_missing") % (t.toolchain.name, self.unavailable[t.toolchain])
        if not name:
            return tr("new_project.enter_name")
        if BAD_NAME.search(name):
            return tr("new_project.bad_name")
        if not folder or not os.path.isabs(folder):
            return tr("new_project.bad_folder")
        target = os.path.join(folder, name)
        if os.path.exists(target) and (not os.path.isdir(target) or os.listdir(target)):
            return tr("new_project.exists") % target
        return ""

    def _validate(self):
        text = self.problems()
        self.problem.setText(text)
        self.create_button.setEnabled(not text)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, tr("new_project.location"), self.folder.text())
        if d:
            self.folder.setText(d)

    def accept(self):
        if not self.problems():
            super().accept()

    def result_values(self):
        t = self.template()
        choices = {"solution": self.solution.isChecked()} if "solution" in t.options else {}
        return t, self.folder.text().strip(), self.name.text().strip(), choices
