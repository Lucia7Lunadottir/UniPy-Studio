"""Реестр команд: у каждой команды — id, подпись, клавиши по умолчанию и функция.

Окно регистрирует команды здесь (`CommandRegistry.add`), а меню и панели инструментов берут готовые
QAction. Свои сочетания клавиш пользователь назначает в «Средства → Параметры → Среда → Клавиатура»
(`KeyboardPage`); они хранятся в QSettings как `keys/<id>` и переживают перезапуск. Пустая строка —
«без клавиш», отсутствие ключа — «как по умолчанию».
"""
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import (QTreeWidget, QTreeWidgetItem, QVBoxLayout, QHBoxLayout, QWidget, QLabel,
                             QKeySequenceEdit, QPushButton, QHeaderView)

from . import theme
from .i18n import bind_text, tr
from .options import OptionsPage, PageView

PORTABLE = QKeySequence.SequenceFormat.PortableText
NATIVE = QKeySequence.SequenceFormat.NativeText


def _keys(seqs):
    """Список сочетаний → строки PortableText (для сравнения и хранения)."""
    out = []
    for s in seqs:
        text = (s if isinstance(s, QKeySequence) else QKeySequence(s)).toString(PORTABLE)
        if text and text not in out:
            out.append(text)
    return out


class Command:
    """id — в духе Visual Studio (Edit.Undo, File.SaveAll): так же их зовут в её «Параметры → Клавиатура»."""
    def __init__(self, cid, text, action, defaults):
        self.id = cid
        self.text = text            # ключ перевода
        self.action = action
        self.defaults = defaults    # [str PortableText]

    def label(self):
        return tr(self.text).replace("&", "")


class CommandRegistry(QObject):
    """Все команды окна. `changed(id)` — у команды сменились клавиши."""
    changed = pyqtSignal(str)

    SETTINGS_GROUP = "keys"

    def __init__(self, window, qsettings):
        super().__init__(window)
        self.window = window
        self.qsettings = qsettings
        self._commands = {}

    def add(self, cid, text, slot, shortcuts=(), icon=None, tip=None, checkable=False):
        """Зарегистрировать команду и вернуть её QAction (он же добавлен в окно, чтобы клавиши работали везде)."""
        if cid in self._commands:
            raise ValueError("command id already registered: %s" % cid)
        if isinstance(shortcuts, str):
            shortcuts = (shortcuts,)
        shortcuts = shortcuts or ()
        a = bind_text(QAction(self.window), text)
        if icon:
            a.setIcon(theme.tool_icon(icon))
            a.setProperty("tool_icon", icon)        # значок перерисуется при смене темы
        if tip:
            bind_text(a, tip, "setToolTip")
        a.setCheckable(checkable)
        a.setProperty("command_id", cid)
        a.triggered.connect(lambda *_: slot())
        self.window.addAction(a)
        cmd = Command(cid, text, a, _keys(shortcuts))
        self._commands[cid] = cmd
        a.setShortcuts([QKeySequence(k) for k in self.shortcuts(cid)])
        return a

    def __contains__(self, cid):
        return cid in self._commands

    def __iter__(self):
        return iter(self._commands.values())

    def command(self, cid):
        return self._commands[cid]

    def action(self, cid):
        return self._commands[cid].action

    def defaults(self, cid):
        return list(self._commands[cid].defaults)

    def _override(self, cid):
        value = self.qsettings.value("%s/%s" % (self.SETTINGS_GROUP, cid))
        if value is None:
            return None
        if isinstance(value, (list, tuple)):
            value = ";".join(str(v) for v in value)
        return [k for k in str(value).split(";") if k]

    def shortcuts(self, cid):
        override = self._override(cid)
        return self.defaults(cid) if override is None else override

    def is_default(self, cid):
        return self.shortcuts(cid) == self.defaults(cid)

    def set_shortcuts(self, cid, keys):
        keys = _keys(keys)
        if keys == self.defaults(cid):
            self.qsettings.remove("%s/%s" % (self.SETTINGS_GROUP, cid))
        else:
            self.qsettings.setValue("%s/%s" % (self.SETTINGS_GROUP, cid), ";".join(keys))
        self._commands[cid].action.setShortcuts([QKeySequence(k) for k in keys])
        self.changed.emit(cid)

    def reset(self, cid):
        self.set_shortcuts(cid, self.defaults(cid))

    def owners(self, key, keymap=None):
        """Какие команды уже используют сочетание. keymap — {id: [keys]} несохранённых правок окна параметров."""
        key = _keys([key])
        if not key:
            return []
        keymap = keymap or {}
        return [c.id for c in self if key[0] in keymap.get(c.id, self.shortcuts(c.id))]


# ---------------------------------------------------------------- Параметры → Клавиатура
class KeyboardPage(OptionsPage):
    """«Среда → Клавиатура», как в Visual Studio: команда, её сочетания, назначить своё."""

    def __init__(self, registry):
        super().__init__(("options.environment", "commands.keyboard"), intro="commands.keyboard_intro")
        self.registry = registry

    def create_view(self, dialog):
        return KeyboardView(self, dialog)


class KeyboardView(PageView):
    def __init__(self, page, dialog):
        super().__init__(page, dialog)
        self.registry = page.registry
        self.pending = {}           # id → [keys] — изменено в окне, ещё не применено
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 0, 0)
        self.tree = QTreeWidget()
        self.tree.setRootIsDecorated(False)
        self.tree.setHeaderLabels([tr("commands.command"), tr("commands.shortcuts")])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setMinimumHeight(260)
        self.items = {}
        for cmd in sorted(self.registry, key=lambda c: c.label().casefold()):
            item = QTreeWidgetItem(self.tree, [cmd.label(), ""])
            item.setData(0, Qt.ItemDataRole.UserRole, cmd.id)
            item.setToolTip(0, cmd.id)
            self.items[cmd.id] = item
        self.tree.currentItemChanged.connect(lambda *_: self._selection_changed())
        lay.addWidget(self.tree, 1)

        row = QHBoxLayout()
        row.addWidget(QLabel(tr("commands.new_shortcut")))
        self.edit = QKeySequenceEdit()
        self.edit.setMaximumSequenceLength(2)          # аккорды вида Ctrl+R, Ctrl+R
        self.edit.keySequenceChanged.connect(lambda *_: self._sequence_changed())
        row.addWidget(self.edit, 1)
        self.assign = QPushButton(tr("commands.assign"))
        self.assign.clicked.connect(lambda: self._assign(replace=True))
        self.add = QPushButton(tr("commands.add"))
        self.add.clicked.connect(lambda: self._assign(replace=False))
        self.remove = QPushButton(tr("commands.remove"))
        self.remove.clicked.connect(self._remove)
        self.default = QPushButton(tr("commands.default"))
        self.default.clicked.connect(self._default)
        for b in (self.assign, self.add, self.remove, self.default):
            row.addWidget(b)
        lay.addLayout(row)
        self.conflict = QLabel()
        self.conflict.setWordWrap(True)
        theme.style(self.conflict, "color:@warning@;")
        lay.addWidget(self.conflict)
        self._widget = self._frame(body)
        self._selection_changed()

    def widget(self):
        return self._widget

    # --- значения
    def keys_of(self, cid):
        return self.pending.get(cid, self.registry.shortcuts(cid))

    def _keymap(self):
        return {c.id: self.keys_of(c.id) for c in self.registry}

    def _render(self, cid):
        item = self.items[cid]
        keys = self.keys_of(cid)
        item.setText(1, ", ".join(QKeySequence(k).toString(NATIVE) for k in keys))
        font = item.font(0)
        font.setBold(keys != self.registry.defaults(cid))       # изменённые — жирным
        item.setFont(0, font)
        item.setFont(1, font)

    def load(self):
        self.pending.clear()
        for cid in self.items:
            self._render(cid)

    def dirty(self):
        return any(keys != self.registry.shortcuts(cid) for cid, keys in self.pending.items())

    def apply(self):
        for cid, keys in list(self.pending.items()):
            if keys != self.registry.shortcuts(cid):
                self.registry.set_shortcuts(cid, keys)
        self.pending.clear()

    def reset_defaults(self):
        for cid in self.items:
            self.pending[cid] = self.registry.defaults(cid)
            self._render(cid)
        self._selection_changed()

    def filter(self, needle):
        page_hit = super().filter(needle)
        any_hit = False
        for cid, item in self.items.items():
            hit = page_hit or needle in (item.text(0) + " " + item.text(1) + " " + cid).casefold()
            item.setHidden(not hit)
            any_hit |= hit
        return any_hit

    # --- правка
    def current(self):
        item = self.tree.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item else None

    def _new_key(self):
        keys = _keys([self.edit.keySequence()])
        return keys[0] if keys else None

    def _selection_changed(self):
        cid = self.current()
        for b in (self.remove, self.default):
            b.setEnabled(cid is not None)
        self._sequence_changed()

    def _sequence_changed(self):
        key, cid = self._new_key(), self.current()
        self.assign.setEnabled(bool(key and cid))
        self.add.setEnabled(bool(key and cid))
        others = [o for o in self.registry.owners(key, self._keymap()) if o != cid] if key else []
        self.conflict.setText(tr("commands.used_by") % ", ".join(self.registry.command(o).label() for o in others)
                              if others else "")

    def _set(self, cid, keys):
        self.pending[cid] = keys
        self._render(cid)

    def _assign(self, replace):
        key, cid = self._new_key(), self.current()
        if not key or not cid:
            return
        for other in self.registry.owners(key, self._keymap()):     # сочетание переходит к этой команде
            if other != cid:
                self._set(other, [k for k in self.keys_of(other) if k != key])
        keys = [key] if replace else [*[k for k in self.keys_of(cid) if k != key], key]
        self._set(cid, keys)
        self.edit.clear()
        self.dialog.touched()

    def _remove(self):
        cid = self.current()
        if cid:
            self._set(cid, [])
            self.dialog.touched()

    def _default(self):
        cid = self.current()
        if cid:
            self._set(cid, self.registry.defaults(cid))
            self.dialog.touched()
