"""Подсказки в строке (inlay hints), как в Visual Studio: имена параметров у аргументов вызова («width: 10»)
и типы переменных («x: Config»). Источник — языковой сервер (Roslyn: textDocument/inlayHint), для Python —
jedi (python_inlay), потому что pyright этого не даёт. Только для видимых строк — обновляются при прокрутке.

Как появляется место: символу перед подсказкой добавляется межбуквенный интервал шириной в подсказку
(слой поверх подсветки — formats()), а в получившемся зазоре рисуется сама подсказка (paint()).
Текст файла, курсор и стек отмены не меняются."""
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetricsF

from ..theme import C
from .base import Insight

PAD = 4                         # отступ текста подсказки внутри плашки
GAP = 3                         # зазор между плашкой и кодом


class InlayHints(Insight):
    id = "inlay"
    kinds = ("inlay_hints",)
    follows_scroll = True

    def __init__(self, controller):
        super().__init__(controller)
        self.by_line = {}               # строка → [(столбец UTF-16, текст, вид)]
        self._range = None

    @property
    def enabled(self):
        s = self.ctl.settings
        return bool(s.get("inlay_parameters") or s.get("inlay_types"))

    def _wanted(self, kind):
        return self.ctl.settings.get("inlay_types" if kind == "type" else "inlay_parameters", False)

    def request(self):
        ed = self.ctl.editor
        first, last = self.ctl.visible_lines()
        self._range = (first, last)
        code = ed.toPlainText()
        srv = self.ctl.server("inlayHintProvider")
        if srv is not None:
            srv.submit("inlay_hints", self.ctl.revision(), id(ed), code, 1, 0, ed.path,
                       {"first": first, "last": last})
            return
        jedi = self.ctl.jedi()
        if jedi is not None:
            s = self.ctl.settings
            jedi.submit("inlay_hints", req=self.ctl.revision(), editor=id(ed), code=code, line=1, col=0, path=ed.path,
                        extra={"code": code, "first": first, "last": last,
                               "params": bool(s.get("inlay_parameters")), "types": bool(s.get("inlay_types"))})

    def on_result(self, res):
        data = res.get("data") or {}
        if "hints" not in data:
            return
        if res.get("req") != self.ctl.revision():
            self.ctl.retry_later()
            return
        first, last = self._range or (0, self.ctl.editor.blockCount())
        by_line = {n: v for n, v in self.by_line.items() if not first <= n <= last}   # вне диапазона — как было
        for line, col, text, kind in data["hints"]:
            if self._wanted(kind) and col > 0 and text:
                by_line.setdefault(line, []).append((col, text, kind))
        changed = {n for n in set(by_line) | set(self.by_line) if by_line.get(n) != self.by_line.get(n)}
        self.by_line = by_line
        if data.get("partial"):
            self.ctl.retry_later()
        self.ctl.rehighlight(changed)

    def clear(self):
        changed, self.by_line = set(self.by_line), {}
        self.ctl.rehighlight(changed)

    # --- вид
    def font(self):
        f = QFont(self.ctl.editor.font())
        f.setPointSizeF(max(6.0, f.pointSizeF() * 0.85))
        return f

    def width(self, text):
        return QFontMetricsF(self.font()).horizontalAdvance(text) + 2 * PAD + GAP

    def formats(self, line):
        out = []
        for col, text, _kind in self.by_line.get(line, ()):
            out.append((col - 1, 1, _letter_spacing(self.width(text))))
        return out

    def paint(self, view, painter, blocks):
        if not self.by_line:
            return
        font = self.font()
        fm = QFontMetricsF(font)
        back = QColor(C["hint"])
        back.setAlpha(38)
        painter.save()
        painter.setFont(font)
        for block, geo in blocks:
            hints = self.by_line.get(block.blockNumber())
            layout = block.layout()
            if not hints or geo is None or layout is None or layout.lineCount() == 0:
                continue
            for col, text, _kind in hints:
                line = layout.lineForTextPosition(col)
                if not line.isValid():
                    continue
                right = geo.left() + line.cursorToX(col)[0] - GAP     # зазор кончается у символа col
                w = fm.horizontalAdvance(text) + 2 * PAD
                h = min(line.height() - 2, fm.height() + 2)
                rect = QRectF(right - w, geo.top() + line.y() + (line.height() - h) / 2, w, h)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(back)
                painter.drawRoundedRect(rect, 3, 3)
                painter.setPen(QColor(C["hint"]))
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()


def _letter_spacing(extra):
    def change(fmt):
        fmt.setFontLetterSpacingType(QFont.SpacingType.AbsoluteSpacing)
        fmt.setFontLetterSpacing(fmt.fontLetterSpacing() + extra)
    return change
