"""«Назад» / «Вперёд» по переходам (Ctrl+− / Ctrl+Shift+−), как в Visual Studio."""
import os

from .i18n import tr


class NavigationHistory:
    """Запоминает, откуда ушли по F12, ссылке, Ctrl+клику. Окно отдаёт только open_path, editor() и status."""
    LIMIT = 100

    def __init__(self, window):
        self.win = window
        self.back = []          # (путь, строка, столбец)
        self.forward = []

    def here(self):
        ed = self.win.editor()
        if ed is None:
            return None
        c = ed.textCursor()
        return ed.path, c.blockNumber() + 1, c.positionInBlock() + 1

    def jump_to(self, path, line, col=1):
        """Переход (определение, ссылка), который запоминается для «Назад»."""
        here = self.here()
        ed = self.win.open_path(path, line, col)
        if ed is not None and here and here != self.here():
            self.back.append(here)
            del self.back[:-self.LIMIT]
            self.forward.clear()
        return ed

    def navigate(self, direction):
        src, dst = (self.back, self.forward) if direction < 0 else (self.forward, self.back)
        here = self.here()
        while src:
            path, line, col = src.pop()
            if os.path.isfile(path):
                if here:
                    dst.append(here)
                self.win.open_path(path, line, col)
                return
        self.win.status(tr("mainwindow.no_back_history") if direction < 0 else tr("mainwindow.no_forward_history"))
