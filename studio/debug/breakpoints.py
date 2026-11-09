"""Точки останова: какие строки каких файлов. Только данные — без редактора и без отладчика.

Редактор рисует их на поле и двигает при правках (BreakpointAnchors), сеанс отладки отправляет адаптеру
(setBreakpoints), панель «Точки останова» показывает список. Все они слушают `changed(path)`.
Хранятся между запусками — для каждого решения отдельно (QSettings, ключ debug/breakpoints/<корень>);
хранение и сдвиг строк общие с закладками — line_marks.py.
"""
from ..line_marks import LineAnchors, LineMark, LineMarkStore


class Breakpoint(LineMark):
    __slots__ = ("condition", "enabled", "verified", "message")

    def __init__(self, line, condition="", enabled=True):
        super().__init__(line)
        self.condition = condition or ""
        self.enabled = bool(enabled)
        self.verified = None            # None — ещё не отправлена; True/False — ответ отладчика
        self.message = ""               # почему не привязалась (от адаптера)

    def to_json(self):
        return {"line": self.line, "condition": self.condition, "enabled": self.enabled}

    @classmethod
    def from_json(cls, data):
        return cls(data["line"], data.get("condition", ""), data.get("enabled", True))


class BreakpointStore(LineMarkStore):
    """Точки останова решения (общее хранение и сдвиг строк — в line_marks.LineMarkStore)."""
    prefix = "debug/breakpoints"
    item_class = Breakpoint

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

    def set_verified(self, path, line, verified, message=""):
        bp = self.get(path, line)
        if bp is not None:
            bp.verified, bp.message = verified, message or ""

    def reset_verified(self):
        for _p, bp in self.all():
            bp.verified, bp.message = None, ""


BreakpointAnchors = LineAnchors         # точки открытого файла держатся за строки (line_marks.LineAnchors)
