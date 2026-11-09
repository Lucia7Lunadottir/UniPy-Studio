"""Семантическая подсветка: классы, интерфейсы, структуры, поля, параметры, локальные переменные — разными
цветами, как в Visual Studio. Источник — языковой сервер (Roslyn: textDocument/semanticTokens/full), для Python —
разбор ast в потоке jedi (python_semantic), потому что pyright этого не даёт.
Цвет кладётся поверх обычной подсветки (ключевые слова, строки, комментарии она красит сама)."""
from PyQt6.QtGui import QColor

from ..theme import C
from .base import Insight


class SemanticHighlighting(Insight):
    id = "semantic"
    kinds = ("semantic_tokens",)

    def __init__(self, controller):
        super().__init__(controller)
        self.by_line = {}               # строка → [(начало, длина, роль)]

    def request(self):
        ed = self.ctl.editor
        code = ed.toPlainText()
        srv = self.ctl.server("semanticTokensProvider")
        if srv is not None:
            srv.submit("semantic_tokens", self.ctl.revision(), id(ed), code, 1, 0, ed.path)
            return
        if ed.lang != "python":
            return
        source, provider = self.ctl.python_source()
        if source == "pyright":
            self.ctl.assist().semantic(provider, self.ctl.answer("semantic_tokens"))
        elif source == "jedi":
            provider.submit("semantic_tokens", req=self.ctl.revision(), editor=id(ed), code=code, line=1, col=0,
                        path=ed.path, extra={"code": code})

    def on_result(self, res):
        data = res.get("data") or {}
        tokens = data.get("tokens")
        if tokens is None:
            return                      # недописанный код или ошибка — прошлая раскраска остаётся
        if res.get("req") != self.ctl.revision():
            self.ctl.retry_later()      # текст успел измениться — позиции уже не те
            return
        by_line = {}
        for line, col, length, role in tokens:
            by_line.setdefault(line, []).append((col, length, role))
        changed = {n for n in set(by_line) | set(self.by_line) if by_line.get(n) != self.by_line.get(n)}
        self.by_line = by_line
        if data.get("partial"):
            self.ctl.retry_later()
        self.ctl.rehighlight(changed)

    def clear(self):
        changed, self.by_line = set(self.by_line), {}
        self.ctl.rehighlight(changed)

    def formats(self, line):
        out = []
        for col, length, role in self.by_line.get(line, ()):
            color = C["dim"] if role == "excluded" else C.get(role)
            if color:
                out.append((col, length, _foreground(QColor(color))))
        return out


def _foreground(color):
    def change(fmt):
        fmt.setForeground(color)
    return change
