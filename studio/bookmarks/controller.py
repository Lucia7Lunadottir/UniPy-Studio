"""Закладки в окне: хранилище решения, привязка к открытым редакторам, команды и окно «Закладки».
Из окна берёт: settings, editor(), jump_to, show_dock, d_bookmarks, status."""
from PyQt6.QtCore import QObject

from ..i18n import tr
from ..line_marks import LineAnchors
from .panel import BookmarksPanel
from .store import BookmarkStore


class BookmarkController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.win = window
        self.store = BookmarkStore(window.settings, self)
        self.panel = BookmarksPanel(self.store)
        self.panel.goto.connect(lambda path, line: window.jump_to(path, line))
        self.panel.step.connect(self.step)

    def attach_editor(self, ed):
        ed.bookmarks = self.store
        ed.bookmark_anchors = LineAnchors(ed, self.store)

    def load_solution(self, root):
        self.store.load(root)

    def toggle(self):
        """Ctrl+K, Ctrl+K — на строке курсора."""
        ed = self.win.editor()
        if ed is not None:
            self.store.toggle(ed.path, ed.textCursor().blockNumber() + 1)

    def step(self, forward=True):
        """Ctrl+K, Ctrl+N / Ctrl+K, Ctrl+P — по включённым закладкам всех файлов по кругу."""
        ed = self.win.editor()
        here = (ed.path, ed.textCursor().blockNumber() + 1) if ed is not None else ("", 0)
        target = self.store.step(*here, forward=forward)
        if target is None:
            self.win.status(tr("bookmarks.none"))
            return
        self.win.jump_to(*target)

    def clear(self):
        self.store.clear()

    def show_window(self):
        self.win.show_dock(self.win.d_bookmarks)
