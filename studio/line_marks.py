"""Отметки на строках файлов — общее для точек останова и закладок. Только данные, без редактора.

LineMarkStore — какие строки каких файлов отмечены; хранится между запусками для каждого решения отдельно
(QSettings, ключ <prefix>/<хеш корня>). LineAnchors держит отметки открытого файла курсорами за строки:
вставили строки выше — отметка уехала вместе с кодом. Все слушают `changed(path)`.
Новый вид отметок = подкласс LineMark (поля и to_json/from_json) + подкласс LineMarkStore (prefix, item_class).
"""
import hashlib
import json

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QTextCursor


class LineMark:
    __slots__ = ("line",)

    def __init__(self, line):
        self.line = int(line)

    def to_json(self):
        return {"line": self.line}

    @classmethod
    def from_json(cls, data):
        return cls(data["line"])


class LineMarkStore(QObject):
    changed = pyqtSignal(str)           # путь; "" — изменилось всё (загрузка другого решения)
    prefix = "marks"                    # ключ в QSettings
    item_class = LineMark

    def __init__(self, qsettings=None, parent=None):
        super().__init__(parent)
        self.qsettings = qsettings
        self.root = None
        self._by_path = {}              # путь → {строка: отметка}

    # --- чтение
    def paths(self):
        return sorted(p for p, items in self._by_path.items() if items)

    def for_path(self, path):
        return sorted(self._by_path.get(path, {}).values(), key=lambda m: m.line)

    def get(self, path, line):
        return self._by_path.get(path, {}).get(line)

    def all(self):
        return [(p, m) for p in self.paths() for m in self.for_path(p)]

    # --- правка
    def toggle(self, path, line):
        """Есть — убрать, нет — поставить. True — отметка теперь есть."""
        items = self._by_path.setdefault(path, {})
        if line in items:
            del items[line]
            present = False
        else:
            items[line] = self.item_class(line)
            present = True
        self._changed(path)
        return present

    def remove(self, path, line):
        if self._by_path.get(path, {}).pop(line, None) is not None:
            self._changed(path)

    def clear(self):
        paths = self.paths()
        self._by_path.clear()
        for p in paths:
            self._changed(p)

    def move_lines(self, path, mapping):
        """Правки в файле сдвинули строки: {старая: новая}. Две отметки на одной строке — остаётся одна."""
        old = self._by_path.get(path, {})
        if not old or all(a == b for a, b in mapping.items()):
            return
        moved = {}
        for line, mark in old.items():
            mark.line = mapping.get(line, line)
            moved.setdefault(mark.line, mark)
        self._by_path[path] = moved
        self._changed(path)

    def rename(self, old, new):
        if old in self._by_path:
            self._by_path[new] = self._by_path.pop(old)
            self._changed(old)
            self._changed(new)

    # --- хранение
    def _key(self):
        digest = hashlib.sha1((self.root or "").encode("utf-8", "surrogateescape")).hexdigest()[:16]
        return "%s/%s" % (self.prefix, digest)

    def load(self, root):
        self.root = root
        self._by_path = {}
        if self.qsettings is not None and root:
            try:
                data = json.loads(self.qsettings.value(self._key(), "{}", type=str))
            except (ValueError, TypeError):
                data = {}
            for path, items in (data.items() if isinstance(data, dict) else []):
                marks = {}
                for item in items:
                    if isinstance(item, dict) and "line" in item:
                        mark = self.item_class.from_json(item)
                        marks[mark.line] = mark
                self._by_path[path] = marks
        self.changed.emit("")

    def save(self):
        if self.qsettings is None or not self.root:
            return
        data = {p: [m.to_json() for m in self.for_path(p)] for p in self.paths()}
        self.qsettings.setValue(self._key(), json.dumps(data, ensure_ascii=False))

    def _changed(self, path, save=True):
        if save:
            self.save()
        self.changed.emit(path)


class LineAnchors(QObject):
    """Отметки одного открытого файла держатся за строки курсорами (как свёрнутые блоки в folding.py).
    Через паузу после правки сообщает хранилищу новые номера строк."""
    DELAY_MS = 400

    def __init__(self, editor, store):
        super().__init__(editor)
        self.editor = editor
        self.store = store
        self._cursors = {}              # строка (с 1) при последней синхронизации → QTextCursor
        self._timer = QTimer(self, singleShot=True, interval=self.DELAY_MS, timeout=self.sync)
        editor.document().contentsChange.connect(lambda *_: self._timer.start())
        store.changed.connect(self._store_changed)
        self.reanchor()

    def _store_changed(self, path):
        if path in ("", self.editor.path):
            self.reanchor()
            self.editor.gutter.update()

    def reanchor(self):
        doc = self.editor.document()
        self._cursors = {}
        for mark in self.store.for_path(self.editor.path):
            block = doc.findBlockByNumber(mark.line - 1)
            if block.isValid():
                self._cursors[mark.line] = QTextCursor(block)

    def sync(self):
        mapping = {line: cur.blockNumber() + 1 for line, cur in self._cursors.items()}
        if any(a != b for a, b in mapping.items()):
            self.store.move_lines(self.editor.path, mapping)       # → changed → reanchor
