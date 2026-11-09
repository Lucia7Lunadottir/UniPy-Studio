"""Панель навигации над кодом (как в Visual Studio): слева — типы файла, справа — члены выбранного типа.
Следует за курсором; выбор в списке переносит к символу. Символы приходят из symbols.py
(анализатор или разбор текста) — панель о их происхождении не знает."""
from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QWidget, QSizePolicy

from . import symbols, theme
from .i18n import tr


class NavigationBar(QWidget):
    goto = pyqtSignal(int)          # строка с 1

    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.setObjectName("navigationBar")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 1, 2, 1)
        lay.setSpacing(2)
        self.types = QComboBox()
        self.members = QComboBox()
        for box in (self.types, self.members):
            box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            box.setMaxVisibleItems(25)
            lay.addWidget(box, 1)
        self.types.activated.connect(self._type_chosen)
        self.members.activated.connect(self._member_chosen)
        self._symbols = []
        self._type_list = []        # (symbols.Symbol | None) по пунктам левого списка; None — «уровень файла»
        self._member_list = []
        self._timer = QTimer(self, singleShot=True, interval=120, timeout=self.follow_cursor)
        editor.cursorPositionChanged.connect(self._timer.start)

    def bar_height(self):
        return self.sizeHint().height()

    def set_symbols(self, items):
        self._symbols = items or []
        self._type_list = [s for _d, s in symbols.walk(self._symbols) if s.kind in symbols.TYPE_KINDS]
        if self._top_members():
            self._type_list.insert(0, None)
        self.types.blockSignals(True)
        self.types.clear()
        for s in self._type_list:
            if s is None:
                self.types.addItem(theme.kind_icon("module"), tr("navbar.file_level"))
            else:
                self.types.addItem(theme.kind_icon(s.kind), s.name)
        self.types.blockSignals(False)
        self.follow_cursor(force=True)

    def _top_members(self):
        return [s for s in self._symbols if s.kind in symbols.MEMBER_KINDS]

    def _members_of(self, type_):
        if type_ is None:
            return self._top_members()
        return [ch for ch in type_.children if ch.kind in symbols.MEMBER_KINDS]

    def _fill_members(self, type_):
        self._member_list = self._members_of(type_)
        self.members.blockSignals(True)
        self.members.clear()
        for s in self._member_list:
            self.members.addItem(theme.kind_icon(s.kind), s.name)
        self.members.blockSignals(False)

    def follow_cursor(self, force=False):
        if not self.isVisible() and not force:
            return
        line = self.editor.textCursor().blockNumber() + 1
        type_, member = symbols.at_line(self._symbols, line)
        if type_ is None and None in self._type_list:
            member = next((s for s in self._top_members() if s.contains(line)), None)
        ti = self._type_list.index(type_) if type_ in self._type_list else (0 if self._type_list else -1)
        self.types.blockSignals(True)
        self.types.setCurrentIndex(ti)
        self.types.blockSignals(False)
        current_type = self._type_list[ti] if ti >= 0 else None
        if force or self._member_list != self._members_of(current_type):
            self._fill_members(current_type)
        self.members.blockSignals(True)
        self.members.setCurrentIndex(self._member_list.index(member) if member in self._member_list else -1)
        self.members.blockSignals(False)

    def _type_chosen(self, index):
        if 0 <= index < len(self._type_list):
            s = self._type_list[index]
            self._fill_members(s)
            if s is not None:
                self.goto.emit(s.start)

    def _member_chosen(self, index):
        if 0 <= index < len(self._member_list):
            self.goto.emit(self._member_list[index].start)
