"""Закладки решения: строки файлов с необязательным именем. Хранение и сдвиг строк — line_marks.LineMarkStore."""
from ..line_marks import LineMark, LineMarkStore


class Bookmark(LineMark):
    __slots__ = ("name", "enabled")

    def __init__(self, line, name="", enabled=True):
        super().__init__(line)
        self.name = name or ""
        self.enabled = bool(enabled)

    def to_json(self):
        return {"line": self.line, "name": self.name, "enabled": self.enabled}

    @classmethod
    def from_json(cls, data):
        return cls(data["line"], data.get("name", ""), data.get("enabled", True))


class BookmarkStore(LineMarkStore):
    prefix = "bookmarks"
    item_class = Bookmark

    def set_name(self, path, line, name):
        mark = self.get(path, line)
        if mark is not None and mark.name != name.strip():
            mark.name = name.strip()
            self._changed(path)

    def set_enabled(self, path, line, enabled):
        mark = self.get(path, line)
        if mark is not None and mark.enabled != enabled:
            mark.enabled = enabled
            self._changed(path)

    def step(self, path, line, forward=True):
        """Следующая (предыдущая) включённая закладка после строки line файла path — по всем файлам
        решения по кругу, как Ctrl+K, Ctrl+N в Visual Studio. (путь, строка) или None."""
        marks = [(p, m.line) for p, m in self.all() if m.enabled]
        if not marks:
            return None
        here = (path or "", line)
        if forward:
            return next((m for m in marks if m > here), marks[0])
        return next((m for m in reversed(marks) if m < here), marks[-1])
