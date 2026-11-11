"""CodeLens: «Ссылок: N» у классов, методов и свойств; щелчок — список ссылок в панели «Результаты поиска ссылок».

В Visual Studio подпись стоит строкой над объявлением; QPlainTextEdit не умеет вставлять строки, которых нет
в тексте, поэтому подпись — в конце строки объявления (как вариант «справа» у Code Vision в Rider).
Символы — те же, что у структуры файла (editor.symbols). Число ссылок — textDocument/references у языкового
сервера (по запросу на символ, только для видимых), пока сервер не готов — jedi (Python).
Методы-сообщения Unity (Update, Awake…) без ссылок помечаются «Сообщение Unity», как в VS Tools for Unity.
"""
import re
import time

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QFontMetricsF

from .. import symbols as symbols_mod
from ..i18n import tr
from ..theme import C
from .base import Insight
from .positions import utf16_len

KINDS = symbols_mod.TYPE_KINDS | {"method", "function", "property"}


class Lens:
    __slots__ = ("key", "name", "line", "col")

    def __init__(self, key, name, line, col):
        self.key, self.name, self.line, self.col = key, name, line, col


def bare_name(name):
    """«Update()», «List<T>», «Go(int a)» → «Update», «List», «Go»."""
    return re.split(r"[(<\s]", name, maxsplit=1)[0]


def lenses(symbols, lines):
    """Символы файла → места для CodeLens (строка с 0 и столбец имени в UTF-16). Ключ — контейнер+имя+вид:
    не зависит от номера строки, поэтому число не пропадает, пока над методом печатают."""
    out = []
    seen = {}
    for _depth, sym in symbols_mod.walk(symbols):
        if sym.kind not in KINDS:
            continue
        name = bare_name(sym.name)
        line0 = sym.start - 1
        text = lines[line0] if 0 <= line0 < len(lines) else ""
        m = re.search(r"(?<![\w$])%s(?!\w)" % re.escape(name), text) if name else None
        if m is None:
            continue
        key = "%s/%s/%s" % (sym.container, name, sym.kind)
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            key += "#%d" % seen[key]                # перегрузки: Go(int) и Go(string)
        out.append(Lens(key, name, line0, utf16_len(text[:m.start()])))
    return out


class CodeLens(Insight):
    id = "codelens"
    kinds = ("reference_count", "code_lens")
    follows_scroll = True
    # Поиск ссылок — самый тяжёлый запрос анализатора (по всему решению). Пока печатают, его не шлём:
    # иначе подсказки (IntelliSense) стоят в очереди за ним — и у Roslyn, и у pyright.
    IDLE_MS = 3000

    def __init__(self, controller):
        super().__init__(controller)
        self.lenses = []
        self.counts = {}                # ключ → {"count", "locations"}
        self._rects = {}                # окно (id) → [(QRectF, Lens)]
        self._queue = []                # символы, о которых ещё не спросили сервер
        self._in_flight = 0.0           # когда ушёл запрос, ответа на который ещё нет (0 — нет такого)
        self._recount = True            # после правки числа могли измениться — пересчитать видимые
        self._idle = QTimer(controller, singleShot=True, interval=self.IDLE_MS, timeout=self._after_idle)

    def edited(self):
        self._queue = []                # начатое не отменяем (pyright падает на отмене ссылок), новое не шлём
        self._recount = True
        self._idle.start()

    def symbols_changed(self):
        self.request()

    def _after_idle(self):
        if self.ctl._shown():
            self.request()

    def request(self):
        ed = self.ctl.editor
        self.lenses = lenses(ed.symbols, ed.toPlainText().split("\n"))
        if self._idle.isActive() or not self.enabled:
            self.ctl.update_views()     # печатают: подписи остаются прежними, пересчёт — после паузы
            return
        first, last = self.ctl.visible_lines(margin=60)
        visible = [lens for lens in self.lenses if first <= lens.line <= last]
        if not self._recount:           # прокрутка: спросить только о тех, у кого числа ещё нет
            visible = [lens for lens in visible if lens.key not in self.counts]
        self._recount = False
        if not visible:
            self.ctl.update_views()
            return
        code = ed.toPlainText()
        if self.ctl.server("referencesProvider") is not None:
            # по одному: pyright, получив новый поиск ссылок, отменяет прежний и падает на этом
            # («this._token.cancel is not a function») — так ни одно число и не приходило
            self._queue = visible
            # ответ так и не пришёл (сервер перезапустился) — не ждать его вечно
            if not self._in_flight or time.monotonic() - self._in_flight > 10:
                self._ask_next()
            return
        source, jedi = self.ctl.python_source() if ed.lang == "python" else (None, None)
        if source == "jedi":                        # pyright нет совсем (пока грузится — ждём его, не jedi)
            line_text = code.split("\n")
            symbols = [(lens.key, lens.line + 1, _chars(line_text[lens.line], lens.col)) for lens in visible]
            jedi.submit("code_lens", req=self.ctl.revision(), editor=id(ed), code=code, line=1, col=0, path=ed.path,
                        extra={"symbols": symbols})

    def _ask_next(self):
        srv = self.ctl.server("referencesProvider")
        if srv is None or not self._queue:
            self._in_flight = 0.0
            return
        lens = self._queue.pop(0)
        ed = self.ctl.editor
        self._in_flight = time.monotonic()
        srv.submit("reference_count", self.ctl.revision(), id(ed), ed.toPlainText(), lens.line + 1, lens.col,
                   ed.path, {"key": lens.key})

    def on_result(self, res):
        data = res.get("data") or {}
        if res["kind"] == "reference_count":
            self._ask_next()
            if data.get("key") is not None:
                self.counts[data["key"]] = {"count": data.get("count", 0), "locations": data.get("locations", [])}
        else:
            self.counts.update(data.get("counts") or {})
            if data.get("partial"):
                self.ctl.retry_later()
        self.ctl.update_views()

    def clear(self):
        self.lenses, self.counts, self._rects = [], {}, {}
        self._queue, self._in_flight = [], 0.0
        self._recount = True
        self._idle.stop()
        self.ctl.update_views()

    # --- вид
    def label(self, lens):
        info = self.counts.get(lens.key)
        if info is None:
            return None
        if info["count"] == 0 and self._unity_message(lens):
            return tr("insights.unity_message")
        return tr("insights.references") % info["count"]

    def _unity_message(self, lens):
        from ..lsp import UNITY_MESSAGE_NAMES
        srv = self.ctl.server("referencesProvider")
        return (self.ctl.editor.lang == "csharp" and lens.name in UNITY_MESSAGE_NAMES
                and getattr(srv, "is_unity_project", False))

    def font(self):
        f = QFont(self.ctl.editor.font())
        f.setPointSizeF(max(6.0, f.pointSizeF() * 0.85))
        return f

    def paint(self, view, painter, blocks):
        rects = []
        self._rects[id(view)] = rects
        if not self.lenses:
            return
        by_line = {}
        for lens in self.lenses:
            by_line.setdefault(lens.line, []).append(lens)
        font = self.font()
        fm = QFontMetricsF(font)
        space = view.fontMetrics().horizontalAdvance(" ")
        painter.save()
        painter.setFont(font)
        painter.setPen(QColor(C["hint"]))
        for block, geo in blocks:
            here = by_line.get(block.blockNumber())
            layout = block.layout()
            if not here or geo is None or layout is None or layout.lineCount() == 0:
                continue
            text = " | ".join(t for t in (self.label(lens) for lens in here) if t)
            if not text:
                continue
            line = layout.lineAt(layout.lineCount() - 1)
            x = geo.left() + line.x() + line.naturalTextWidth() + 3 * space
            rect = QRectF(x, geo.top() + line.y(), fm.horizontalAdvance(text) + 4, line.height())
            painter.drawText(rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
            rects.append((rect, here[0]))
        painter.restore()

    def _hit(self, view, pos):
        return next((lens for rect, lens in self._rects.get(id(view), ()) if rect.contains(pos)), None)

    def hover(self, view, pos):
        return self._hit(view, pos) is not None

    def click(self, view, pos):
        lens = self._hit(view, pos)
        if lens is None:
            return False
        info = self.counts.get(lens.key) or {}
        self.ctl.editor.references_found.emit(lens.name, info.get("locations") or [])
        return True


def _chars(text, col16):
    """Столбец UTF-16 → в символах (для jedi)."""
    if text.isascii():
        return col16
    n = 0
    for i, ch in enumerate(text):
        if n >= col16:
            return i
        n += 2 if ord(ch) > 0xFFFF else 1
    return len(text)
