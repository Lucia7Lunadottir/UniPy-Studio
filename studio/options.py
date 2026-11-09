"""Параметры программы: реестр, хранилище и окно «Средства → Параметры» (как в Visual Studio).

Чтобы добавить параметр, достаточно одной записи в `PAGES`: окно само построит для него поле,
кнопку сброса и поиск. Кто использует параметр — подписывается на `Options.changed(key)`
и читает `options[key]`; меню, горячие клавиши и окно меняют значение одним путём (`options.set`),
поэтому они не расходятся.

Виды параметров — подклассы `Option`: каждый сам знает, как прочитать значение из QSettings,
какой виджет нарисовать и как достать из него значение.
"""
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QVBoxLayout, QTreeWidget, QTreeWidgetItem, QStackedWidget,
                             QWidget, QFormLayout, QCheckBox, QSpinBox, QComboBox, QLabel, QLineEdit,
                             QDialogButtonBox, QPushButton, QScrollArea, QFrame)

from .i18n import tr
from . import theme


# ---------------------------------------------------------------- виды параметров
class Option:
    """Один параметр: ключ в QSettings, значение по умолчанию, подпись и пояснение (ключи перевода)."""

    def __init__(self, key, default, label, hint=None):
        self.key = key
        self.default = default
        self.label = label
        self.hint = hint

    def cast(self, raw):
        return raw

    def read(self, qsettings):
        raw = qsettings.value(self.key, self.default)
        try:
            return self.cast(raw)
        except (TypeError, ValueError):
            return self.default

    # окно параметров
    def make_editor(self):
        raise NotImplementedError

    def editor_value(self, widget):
        raise NotImplementedError

    def set_editor_value(self, widget, value):
        raise NotImplementedError

    def connect(self, widget, slot):
        """slot() — когда в поле что-то поменяли (чтобы включить «Применить»)."""
        raise NotImplementedError

    def has_own_label(self):
        """Флажок пишет подпись сам; остальным нужна подпись слева."""
        return False

    def search_text(self):
        return " ".join(tr(t) for t in (self.label, self.hint) if t)


class BoolOption(Option):
    def cast(self, raw):
        if isinstance(raw, str):
            return raw.lower() in ("true", "1", "yes")
        return bool(raw)

    def make_editor(self):
        return QCheckBox(tr(self.label))

    def editor_value(self, widget):
        return widget.isChecked()

    def set_editor_value(self, widget, value):
        widget.setChecked(bool(value))

    def connect(self, widget, slot):
        widget.toggled.connect(lambda *_: slot())

    def has_own_label(self):
        return True


class NumberOption(Option):
    """Целое число в диапазоне; `kind=float` — хранится дробным (так исторически хранится размер подсказок)."""

    def __init__(self, key, default, label, minimum, maximum, hint=None, suffix=None, kind=int):
        super().__init__(key, default, label, hint)
        self.minimum = minimum
        self.maximum = maximum
        self.suffix = suffix
        self.kind = kind

    def clamp(self, value):
        return self.kind(max(self.minimum, min(self.maximum, value)))

    def cast(self, raw):
        return self.clamp(self.kind(float(raw)))

    def make_editor(self):
        w = QSpinBox()
        w.setRange(int(self.minimum), int(self.maximum))
        if self.suffix:
            w.setSuffix(tr(self.suffix))
        return w

    def editor_value(self, widget):
        return self.kind(widget.value())

    def set_editor_value(self, widget, value):
        widget.setValue(int(round(value)))

    def connect(self, widget, slot):
        widget.valueChanged.connect(lambda *_: slot())


class ChoiceOption(Option):
    """Выбор из списка: choices = ((значение, ключ подписи), …)."""

    def __init__(self, key, default, label, choices, hint=None):
        super().__init__(key, default, label, hint)
        self.choices = tuple(choices)

    def cast(self, raw):
        kind = type(self.default)
        value = kind(float(raw)) if kind is int else kind(raw)
        return value if value in [v for v, _ in self.choices] else self.default

    def make_editor(self):
        w = QComboBox()
        for value, label in self.choices:
            w.addItem(tr(label), value)
        return w

    def editor_value(self, widget):
        return widget.currentData()

    def set_editor_value(self, widget, value):
        i = widget.findData(value)
        widget.setCurrentIndex(max(0, i))

    def connect(self, widget, slot):
        widget.currentIndexChanged.connect(lambda *_: slot())

    def search_text(self):
        return super().search_text() + " " + " ".join(tr(label) for _, label in self.choices)


class OptionsPage:
    """Страница окна параметров. path — цепочка ключей перевода: («раздел», «страница»).
    Страница — только описание; для каждого открытого окна она создаёт свой вид (`create_view`),
    поэтому одно и то же описание можно показывать много раз."""

    def __init__(self, path, intro=None):
        self.path = tuple(path)
        self.intro = intro

    def title(self):
        return " › ".join(tr(p) for p in self.path)

    def create_view(self, dialog):
        raise NotImplementedError


class PageView:
    """Вид страницы в одном окне параметров: поля, применение, сброс, поиск."""

    def __init__(self, page, dialog):
        self.page = page
        self.dialog = dialog

    def widget(self):
        raise NotImplementedError

    def load(self):
        """Показать текущие значения."""

    def dirty(self):
        return False

    def apply(self):
        """Записать изменённое."""

    def reset_defaults(self):
        """Поля → значения по умолчанию (запишутся по «Применить»)."""

    def filter(self, needle):
        """Скрыть то, что не подходит под поиск. True — на странице что-то осталось."""
        return not needle or needle in self.page.title().casefold()

    def _frame(self, body):
        """Общая рамка: заголовок, пояснение, содержимое с прокруткой."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        host = QWidget()
        lay = QVBoxLayout(host)
        title = QLabel(self.page.title())
        theme.style(title, "font-size:12pt; font-weight:600;")
        lay.addWidget(title)
        if self.page.intro:
            intro = QLabel(tr(self.page.intro))
            intro.setWordWrap(True)
            lay.addWidget(intro)
        if isinstance(body, QWidget):
            lay.addWidget(body, 1)
        else:
            lay.addLayout(body)
            lay.addStretch(1)
        scroll.setWidget(host)
        return scroll


class Page(OptionsPage):
    """Страница из параметров `Option` — поля строятся сами."""

    def __init__(self, path, options, intro=None):
        super().__init__(path, intro)
        self.options = tuple(options)

    def create_view(self, dialog):
        return FormView(self, dialog)


class FormView(PageView):
    def __init__(self, page, dialog):
        super().__init__(page, dialog)
        self.store = dialog.options
        self.fields = {}            # ключ → (параметр, поле, строка-контейнер)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
        for option in page.options:
            row = QWidget()
            rl = QVBoxLayout(row)
            rl.setContentsMargins(0, 2, 0, 6)
            rl.setSpacing(2)
            field = option.make_editor()
            option.connect(field, dialog.touched)
            if option.has_own_label():
                rl.addWidget(field)
            else:
                line = QHBoxLayout()
                line.addWidget(QLabel(tr(option.label)))
                line.addWidget(field)
                line.addStretch(1)
                rl.addLayout(line)
            if option.hint:
                hint = QLabel(tr(option.hint))
                hint.setWordWrap(True)
                theme.style(hint, "color:@dim@;")
                rl.addWidget(hint)
            form.addRow(row)
            self.fields[option.key] = (option, field, row)
        self._widget = self._frame(form)

    def widget(self):
        return self._widget

    def load(self):
        for key, (option, field, _row) in self.fields.items():
            field.blockSignals(True)
            option.set_editor_value(field, self.store[key])
            field.blockSignals(False)

    def dirty(self):
        return any(option.cast(option.editor_value(field)) != self.store[key]
                   for key, (option, field, _r) in self.fields.items())

    def apply(self):
        for key, (option, field, _row) in self.fields.items():
            self.store.set(key, option.editor_value(field))

    def reset_defaults(self):
        for option, field, _row in self.fields.values():
            option.set_editor_value(field, option.default)

    def filter(self, needle):
        page_hit = super().filter(needle)
        any_hit = False
        for option, _field, row in self.fields.values():
            hit = page_hit or needle in option.search_text().casefold()
            row.setVisible(hit)
            any_hit |= hit
        return any_hit


# ---------------------------------------------------------------- реестр
RESTART_THRESHOLDS = ((0, "mainwindow.do_not_restart"), (800, "mainwindow.800_mb"), (1024, "mainwindow.1_gb"),
                      (1536, "mainwindow.1_5_gb"), (2048, "mainwindow.2_gb"))

PAGES = (
    Page(("options.text_editor", "options.general"), (
        NumberOption("editor/font_size", 11, "mainwindow.code_font_size", 7, 32, suffix="options.pt"),
        NumberOption("editor/hint_size", 11.0, "mainwindow.completion_font_size", 7, 24, suffix="options.pt",
                     kind=float, hint="options.hint_size_hint"),
        BoolOption("editor/word_wrap", False, "mainwindow.wrap_long_lines"),
    )),
    Page(("options.text_editor", "IntelliSense"), (
        BoolOption("intellisense/unimported", True, "mainwindow.suggestions_from_unimported_namespaces",
                   hint="options.unimported_hint"),
        BoolOption("intellisense/alpha", False, "options.completion_alpha"),
    )),
    Page(("options.environment", "options.documents"), (
        BoolOption("documents/detect_external", True, "options.detect_external"),
        BoolOption("documents/reload_unmodified", True, "options.reload_unmodified",
                   hint="options.reload_unmodified_hint"),
        BoolOption("documents/save_before_run", True, "options.save_before_run"),
    )),
    Page(("options.environment", "options.autorecover"), (
        BoolOption("recovery/enabled", True, "options.recovery_enabled", hint="options.recovery_hint"),
        NumberOption("recovery/interval_sec", 30, "options.recovery_interval", 5, 600, suffix="options.seconds"),
        NumberOption("recovery/keep_days", 7, "options.recovery_keep_days", 1, 90, suffix="options.days"),
    )),
    Page(("options.analyzers", "options.general"), (
        ChoiceOption("analyzers/restart_mb", 1024, "options.restart_mb",
                     RESTART_THRESHOLDS, hint="options.restart_hint"),
    )),
)


class Options(QObject):
    """Текущие значения параметров поверх QSettings. `changed(key)` — после каждой смены."""
    changed = pyqtSignal(str)

    def __init__(self, qsettings, pages=PAGES, parent=None):
        super().__init__(parent)
        self.qsettings = qsettings
        self.pages = pages
        self.by_key = {o.key: o for page in pages for o in getattr(page, "options", ())}
        self._values = {key: o.read(qsettings) for key, o in self.by_key.items()}

    def __getitem__(self, key):
        return self._values[key]

    def option(self, key):
        return self.by_key[key]

    def set(self, key, value):
        option = self.by_key[key]
        value = option.cast(value)
        if self._values[key] == value:
            return False
        self._values[key] = value
        self.qsettings.setValue(key, value)
        self.changed.emit(key)
        return True

    def reset(self, key):
        return self.set(key, self.by_key[key].default)


# ---------------------------------------------------------------- окно
class OptionsDialog(QDialog):
    """Дерево страниц слева, страница справа, поиск сверху. Изменения вступают в силу по «ОК»/«Применить».
    extra_pages — страницы, которым нужно что-то из окна программы (например, «Клавиатура» — реестр команд)."""

    def __init__(self, options, parent=None, start_page=None, extra_pages=()):
        super().__init__(parent)
        self.options = options
        self.setWindowTitle(tr("options.title"))
        self.resize(860, 560)
        self.views = []             # (элемент дерева, вид страницы, индекс в стопке)

        root = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("options.search"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        root.addWidget(self.search)

        body = QHBoxLayout()
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setFixedWidth(240)
        self.stack = QStackedWidget()
        body.addWidget(self.tree)
        body.addWidget(self.stack, 1)
        root.addLayout(body, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel |
                                   QDialogButtonBox.StandardButton.Apply)
        self.apply_button = buttons.button(QDialogButtonBox.StandardButton.Apply)
        self.apply_button.setEnabled(False)

        sections = {}
        for page in (*options.pages, *extra_pages):
            parent_item = self.tree.invisibleRootItem()
            for depth, part in enumerate(page.path[:-1]):
                key = page.path[:depth + 1]
                if key not in sections:
                    sections[key] = QTreeWidgetItem(parent_item, [tr(part)])
                    sections[key].setExpanded(True)
                parent_item = sections[key]
            item = QTreeWidgetItem(parent_item, [tr(page.path[-1])])
            view = page.create_view(self)
            index = self.stack.addWidget(view.widget())
            item.setData(0, Qt.ItemDataRole.UserRole, index)
            self.views.append((item, view, index))
        self.tree.currentItemChanged.connect(self._page_selected)

        reset = QPushButton(tr("options.reset_page"))
        reset.clicked.connect(self._reset_page)
        buttons.addButton(reset, QDialogButtonBox.ButtonRole.ResetRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.apply_button.clicked.connect(self.apply)
        root.addWidget(buttons)

        for _item, view, _index in self.views:
            view.load()
        first = next((it for it, v, _ in self.views if v.page.path == tuple(start_page or ())), self.views[0][0])
        self.tree.setCurrentItem(first)

    def field(self, key):
        """Поле параметра по ключу (для тестов и переходов «показать этот параметр»)."""
        for _item, view, _index in self.views:
            entry = getattr(view, "fields", {}).get(key)
            if entry:
                return entry[1]
        raise KeyError(key)

    def touched(self):
        self.apply_button.setEnabled(any(view.dirty() for _it, view, _i in self.views))

    def _page_selected(self, item, _previous):
        index = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if index is None and item is not None and item.childCount():
            index = item.child(0).data(0, Qt.ItemDataRole.UserRole)
        if index is not None:
            self.stack.setCurrentIndex(index)

    def current_view(self):
        index = self.stack.currentIndex()
        return next((v for _it, v, i in self.views if i == index), None)

    def _reset_page(self):
        view = self.current_view()
        if view is not None:
            view.reset_defaults()
            self.touched()

    def _filter(self, text):
        """Поиск по названиям страниц, подписям, пояснениям, вариантам выбора и командам."""
        needle = text.strip().casefold()
        first = None
        for item, view, _index in self.views:
            hit = view.filter(needle)
            item.setHidden(not hit)
            if hit and first is None:
                first = item
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            top.setHidden(all(top.child(j).isHidden() for j in range(top.childCount())))
        current = self.tree.currentItem()
        if first is not None and (current is None or current.isHidden()):
            self.tree.setCurrentItem(first)

    def apply(self):
        for _item, view, _index in self.views:
            if view.dirty():
                view.apply()
        self.apply_button.setEnabled(False)

    def accept(self):
        self.apply()
        super().accept()
