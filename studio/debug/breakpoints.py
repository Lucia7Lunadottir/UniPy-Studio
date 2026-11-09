"""Точки останова: какие строки каких файлов. Только данные — без редактора и без отладчика.

Редактор рисует их на поле и двигает при правках (BreakpointAnchors), сеанс отладки отправляет адаптеру
(setBreakpoints), панель «Точки останова» показывает список. Все они слушают `changed(path)`.
Хранятся между запусками — для каждого решения отдельно (QSettings, ключ debug/breakpoints/<корень>).
"""
import hashlib
import json

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QTextCursor


class Breakpoint:
    __slots__ = ("line", "condition", "enabled", "verified", "message")

    def __init__(self, line, condition="", enabled=True):
        self.line = int(line)
        self.condition = condition or ""
        self.enabled = bool(enabled)
        self.verified = None            # None — ещё не отправлена; True/False — ответ отладчика
        self.message = ""               # почему не привязалась (от адаптера)

    def to_json(self):
        return {"line": self.line, "condition": self.condition, "enabled": self.enabled}


class BreakpointStore(QObject):
    changed = pyqtSignal(str)           # путь; "" — изменилось всё (загрузка другого решения)

    def __init__(self, qsettings=None, parent=None):
        super().__init__(parent)
        self.qsettings = qsettings
        self.root = None
        self._by_path = {}              # путь → {строка: Breakpoint}

    # --- чтение
    def paths(self):
        return sorted(p for p, items in self._by_path.items() if items)

    def for_path(self, path):
        return sorted(self._by_path.get(path, {}).values(), key=lambda b: b.line)

    def get(self, path, line):
        return self._by_path.get(path, {}).get(line)

    def all(self):
        return [(p, b) for p in self.paths() for b in self.for_path(p)]

    # --- правка
    def toggle(self, path, line):
        """F9 / щелчок по полю: есть — убрать, нет — поставить. True — точка теперь есть."""
        items = self._by_path.setdefault(path, {})
        if line in items:
            del items[line]
            present = False
        else:
            items[line] = Breakpoint(line)
            present = True
        self._changed(path)
        return present

    def set_enabled(self, path, line, enabled):
        bp = self.get(path, line)
        if bp is not None and bp.enabled != enabled:
            bp.enabled = enabled
            self._changed(path)

    def set_condition(self, path, line, condition):
        bp = self.get(path, line)
        if bp is not None:
            bp.condition = condition.strip()
            bp.verified = None
            self._changed(path)

    def remove(self, path, line):
        if self._by_path.get(path, {}).pop(line, None) is not None:
            self._changed(path)

    def clear(self):
        paths = self.paths()
        self._by_path.clear()
        for p in paths:
            self._changed(p)

    def move_lines(self, path, mapping):
        """Правки в файле сдвинули строки: {старая: новая}. Две точки на одной строке — остаётся одна."""
        old = self._by_path.get(path, {})
        if not old or all(a == b for a, b in mapping.items()):
            return
        moved = {}
        for line, bp in old.items():
            bp.line = mapping.get(line, line)
            moved.setdefault(bp.line, bp)
        self._by_path[path] = moved
        self._changed(path, save=True)

    def rename(self, old, new):
        if old in self._by_path:
            self._by_path[new] = self._by_path.pop(old)
            self._changed(old)
            self._changed(new)

    def set_verified(self, path, line, verified, message=""):
        bp = self.get(path, line)
        if bp is not None:
            bp.verified, bp.message = verified, message or ""

    def reset_verified(self):
        for _p, bp in self.all():
            bp.verified, bp.message = None, ""

    # --- хранение
    def _key(self):
        digest = hashlib.sha1((self.root or "").encode("utf-8", "surrogateescape")).hexdigest()[:16]
        return "debug/breakpoints/" + digest

    def load(self, root):
        self.root = root
        self._by_path = {}
        if self.qsettings is not None and root:
            try:
                data = json.loads(self.qsettings.value(self._key(), "{}", type=str))
            except (ValueError, TypeError):
                data = {}
            for path, items in (data.items() if isinstance(data, dict) else []):
                self._by_path[path] = {int(i["line"]): Breakpoint(i["line"], i.get("condition", ""), i.get("enabled", True))
                                       for i in items if isinstance(i, dict) and "line" in i}
        self.changed.emit("")

    def save(self):
        if self.qsettings is None or not self.root:
            return
        data = {p: [b.to_json() for b in self.for_path(p)] for p in self.paths()}
        self.qsettings.setValue(self._key(), json.dumps(data, ensure_ascii=False))

    def _changed(self, path, save=True):
        if save:
            self.save()
        self.changed.emit(path)


class BreakpointAnchors(QObject):
    """Точки одного открытого файла держатся за строки курсорами (как свёрнутые блоки в folding.py):
    вставили строки выше — точка уехала вместе с кодом. Через паузу после правки сообщает хранилищу."""
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
        for bp in self.store.for_path(self.editor.path):
            block = doc.findBlockByNumber(bp.line - 1)
            if block.isValid():
                self._cursors[bp.line] = QTextCursor(block)

    def sync(self):
        mapping = {line: cur.blockNumber() + 1 for line, cur in self._cursors.items()}
        if any(a != b for a, b in mapping.items()):
            self.store.move_lines(self.editor.path, mapping)       # → changed → reanchor
