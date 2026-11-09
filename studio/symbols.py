"""Символы файла и решения: классы, методы, поля — с диапазоном строк.

Один формат для всех источников:
- `from_document_symbols` — ответ анализатора (textDocument/documentSymbol: Roslyn, pyright);
- `from_workspace_symbols` — поиск по решению (workspace/symbol, Ctrl+T);
- `from_outline` — запасной разбор текста (panels.outline), конец блока берётся из folding.
Структура файла, панель навигации над редактором и «Перейти ко всему» читают только Symbol.
Без Qt.
"""
from .lsp import uri_to_path, is_metadata_path

# SymbolKind из LSP → вид для значков (theme.KIND_ICON)
LSP_KINDS = {1: "path", 2: "module", 3: "namespace", 4: "module", 5: "class", 6: "method", 7: "property",
             8: "field", 9: "method", 10: "enum", 11: "interface", 12: "function", 13: "variable",
             14: "const", 15: "variable", 16: "variable", 17: "variable", 18: "variable", 19: "variable",
             20: "variable", 21: "const", 22: "const", 23: "struct", 24: "event", 25: "method", 26: "type"}
TYPE_KINDS = {"class", "struct", "interface", "enum", "type"}
MEMBER_KINDS = {"method", "function", "property", "field", "event", "const"}
CONTAINER_KINDS = TYPE_KINDS | {"namespace", "module"}


class Symbol:
    """name, kind (как в theme.KIND_ICON), строки start..end с 1, detail (сигнатура/тип), children."""
    __slots__ = ("name", "kind", "start", "end", "detail", "children", "path", "container")

    def __init__(self, name, kind, start, end=None, detail="", children=None, path=None, container=""):
        self.name = name
        self.kind = kind
        self.start = start
        self.end = max(start, end or start)
        self.detail = detail or ""
        self.children = list(children or [])
        self.path = path
        self.container = container or ""

    def __repr__(self):
        return "Symbol(%r, %r, %d-%d)" % (self.name, self.kind, self.start, self.end)

    def walk(self, depth=0):
        yield depth, self
        for ch in self.children:
            yield from ch.walk(depth + 1)

    def contains(self, line):
        return self.start <= line <= self.end


def walk(symbols):
    for s in symbols:
        yield from s.walk()


def _kind(code):
    return LSP_KINDS.get(code, "word")


def _name(raw):
    name = raw.get("name", "")
    detail = raw.get("detail") or ""
    if _kind(raw.get("kind")) in ("method", "function") and "(" not in name and detail.startswith("("):
        name += detail
    return name


def from_document_symbols(result, path=None):
    """documentSymbol: иерархия DocumentSymbol или плоский список SymbolInformation."""
    if not result:
        return []
    if "location" in result[0]:                     # SymbolInformation — плоский, иерархию строим по контейнеру
        flat = []
        for raw in result:
            rng = raw["location"].get("range", {})
            flat.append(Symbol(raw.get("name", ""), _kind(raw.get("kind")), rng.get("start", {}).get("line", 0) + 1,
                               rng.get("end", {}).get("line", 0) + 1, container=raw.get("containerName", ""),
                               path=path))
        return nest(flat)

    def convert(raw):
        rng = raw.get("range", {})
        children = sorted((convert(ch) for ch in raw.get("children") or []), key=lambda s: s.start)
        return Symbol(_name(raw), _kind(raw.get("kind")), rng.get("start", {}).get("line", 0) + 1,
                      rng.get("end", {}).get("line", 0) + 1, raw.get("detail", ""), children, path)
    return sorted((convert(raw) for raw in result), key=lambda s: s.start)


def nest(flat):
    """Плоский список с диапазонами → дерево (символ внутри диапазона другого — его ребёнок)."""
    roots, stack = [], []
    for s in sorted(flat, key=lambda s: (s.start, -s.end)):
        while stack and not (stack[-1].start <= s.start and s.end <= stack[-1].end and stack[-1] is not s):
            stack.pop()
        (stack[-1].children if stack else roots).append(s)
        stack.append(s)
    return roots


def from_workspace_symbols(result, limit=300):
    """workspace/symbol → плоский список Symbol с путём (без исходников из сборок)."""
    out = []
    for raw in result or []:
        loc = raw.get("location") or {}
        path = uri_to_path(loc.get("uri", ""))
        if not path or is_metadata_path(path):
            continue
        start = (loc.get("range") or {}).get("start", {}).get("line", 0) + 1
        out.append(Symbol(raw.get("name", ""), _kind(raw.get("kind")), start, path=path,
                          container=raw.get("containerName", "")))
        if len(out) >= limit:
            break
    return out


def from_outline(entries, folds=(), path=None):
    """panels.outline [(глубина, вид, имя, строка с 1)] → дерево Symbol; конец — из блока сворачивания."""
    ends = {r.start + 1: r.end + 1 for r in folds}
    roots, stack = [], []
    for depth, kind, name, line in entries:
        end = ends.get(line) or ends.get(line + 1) or line      # «{» на следующей строке — стиль Unity
        s = Symbol(name, kind, line, end, path=path)
        while stack and stack[-1][0] >= depth:
            stack.pop()
        (stack[-1][1].children if stack else roots).append(s)
        stack.append((depth, s))
    return roots


def at_line(symbols, line):
    """(тип, член) под строкой: самый вложенный тип и самый вложенный член внутри него."""
    type_, member = None, None
    for _depth, s in walk(symbols):
        if not s.contains(line):
            continue
        if s.kind in TYPE_KINDS:
            type_, member = s, None
        elif s.kind in MEMBER_KINDS and (member is None or s.start >= member.start):
            member = s
    return type_, member


def matches(query, text):
    """Подходит ли имя под запрос: подстрока без учёта регистра или «горбы» (PC → PlayerController)."""
    if not query:
        return True
    q = query.casefold()
    if q in text.casefold():
        return True
    humps = "".join(ch for i, ch in enumerate(text) if ch.isupper() or (i == 0 and ch.isalpha()))
    return humps.casefold().startswith(q)


def score(query, text):
    """Меньше — выше в списке: точное совпадение, начало, «горбы», подстрока."""
    if not query:
        return (3, len(text), text)
    t, q = text.casefold(), query.casefold()
    if t == q:
        return (0, len(text), text)
    if t.startswith(q):
        return (1, len(text), text)
    if q in t:
        return (2, t.index(q), text)
    return (3, len(text), text)
