"""Рефакторинг C# без Qt: «Перенести тип в отдельный файл» (как в Visual Studio).

Работает по тексту: находит типы верхнего уровня (class, struct, interface, enum, record),
учитывая строки и комментарии, и готовит план:
  • текст нового файла (using-директивы + namespace + сам тип);
  • какой кусок вырезать из исходного файла.
Редактор применяет план (окно: MainWindow.move_type_to_file), поэтому Ctrl+Z возвращает вырезанное.
"""
import re

from .i18n import tr


class RefactorError(Exception):
    """Понятное пользователю объяснение, почему перенос невозможен."""


# ---------------------------------------------------------------- маска текста
def mask(text):
    """Копия текста той же длины: комментарии, строки и символы заменены пробелами
    (переводы строк остаются). Так скобки и ключевые слова внутри них не мешают разбору.
    Второй результат — начала строк, которые лежат ВНУТРИ многострочной строки
    (их нельзя сдвигать при смене отступа: это изменило бы значение строки)."""
    out = list(text)
    inside_string = set()
    n = len(text)
    i = 0

    def blank(a, b, is_string=False):
        for k in range(a, min(b, n)):
            if out[k] != "\n":
                out[k] = " "
            elif is_string:
                inside_string.add(k + 1)

    while i < n:
        c = text[i]
        two = text[i:i + 2]
        if two == "//":
            j = text.find("\n", i)
            j = n if j < 0 else j
            blank(i, j)
            i = j
        elif two == "/*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            blank(i, j)
            i = j
        elif c in "@$\"" and re.match(r'(?:\$@|@\$|@|\$)?"', text[i:i + 3]):
            m = re.match(r'(\$@|@\$|@|\$)?("+)', text[i:i + 12])
            prefix, quotes = m.group(1) or "", m.group(2)
            verbatim = "@" in prefix
            start = i
            i += len(prefix)
            if len(quotes) >= 3:                      # сырая строка: """ … """
                q = len(quotes)
                j = text.find('"' * q, i + q)
                j = n if j < 0 else j + q
                i = j
            else:
                i += 1
                while i < n:
                    if verbatim:
                        if text[i] == '"':
                            if text[i + 1:i + 2] == '"':
                                i += 2
                                continue
                            i += 1
                            break
                    else:
                        if text[i] == "\\":
                            i += 2
                            continue
                        if text[i] == '"':
                            i += 1
                            break
                        if text[i] == "\n":           # незакрытая строка: не уходим дальше строки
                            break
                    i += 1
            blank(start, i, is_string=True)
        elif c == "'":
            m = re.match(r"'(?:\\.[^']*|[^\\'\n])'", text[i:i + 12])
            if m:
                blank(i, i + m.end())
                i += m.end()
            else:
                i += 1
        else:
            i += 1
    return "".join(out), inside_string


# ---------------------------------------------------------------- разбор файла
_EVENT = re.compile(
    r"(?P<brace>[{}])"
    r"|(?P<semi>;)"
    r"|(?P<fns>\bnamespace\s+(?P<fnsname>[\w.]+)\s*(?=(?P<fnsend>[;{])))"
    r"|(?P<using>^[ \t]*(?P<global>global\s+)?using\s+(?!var\b|\()[^;{}()]*;)"
    r"|(?P<type>\b(?:(?P<kind>class|struct|interface|enum)|record(?:\s+(?:class|struct))?)\s+(?P<name>@?[A-Za-z_]\w*))",
    re.M)


class TypeInfo:
    def __init__(self, name, kind, begin, end):
        self.name, self.kind = name, kind
        self.begin, self.end = begin, end        # keyword … последняя скобка (индексы в тексте)


def parse(text):
    """→ (типы верхнего уровня, using-директивы [(позиция, строка)], пространства имён по типам)."""
    masked, in_string = mask(text)
    stack = []              # ("ns", имя) — блок namespace; "blk" — любой другой блок; TypeInfo — тип
    types, usings = [], []
    pending = None          # ("ns", имя) | TypeInfo — ждёт «{» или «;»
    file_ns = ""

    def top_level():
        """Мы снаружи всех типов и методов: внутри только блоки namespace."""
        return all(isinstance(s, tuple) for s in stack)

    for m in _EVENT.finditer(masked):
        if m.group("brace") == "{":
            stack.append("blk" if pending is None else pending)
            pending = None
        elif m.group("brace") == "}":
            if stack:
                s = stack.pop()
                if isinstance(s, TypeInfo):
                    s.end = m.end()
                    types.append(s)
        elif m.group("semi"):
            if isinstance(pending, TypeInfo):          # record Point(int X, int Y);
                pending.end = m.end()
                types.append(pending)
            pending = None
        elif m.group("fns"):
            if pending is None and top_level():
                if m.group("fnsend") == ";":
                    file_ns = m.group("fnsname")
                else:
                    pending = ("ns", m.group("fnsname"))
        elif m.group("using"):
            if pending is None and top_level() and not m.group("global"):
                usings.append((m.start(), text[m.start():m.end()].strip()))
        elif m.group("type"):
            if pending is not None or not top_level():
                continue
            before = masked[:m.start()].rstrip()[-1:]
            if before in (":", ","):                   # where T : class — это не объявление
                continue
            info = TypeInfo(m.group("name").lstrip("@"), m.group("kind") or "record", m.start(), None)
            info.file_ns = file_ns
            info.ns = [s[1] for s in stack]
            pending = info
    # незакрытые типы (файл с ошибкой) — не трогаем
    return sorted(types, key=lambda t: t.begin), usings, masked, in_string, file_ns


# ---------------------------------------------------------------- план переноса
def _lines(text):
    starts = [0]
    for m in re.finditer("\n", text):
        starts.append(m.end())
    return starts


def _leading_begin(text, masked, begin):
    """Начало строки с типом + атрибуты, XML-комментарии и комментарии прямо над ним."""
    starts = _lines(text)
    li = max(k for k, s in enumerate(starts) if s <= begin)
    first = li
    open_brackets = 0
    k = li - 1
    while k >= 0:
        a, b = starts[k], starts[k + 1] - 1 if k + 1 < len(starts) else len(text)
        raw = text[a:b].strip()
        m = masked[a:b].strip()
        if not raw:
            if open_brackets:
                k -= 1
                continue
            break
        if not m:                                  # строка только из комментария
            first = k
        elif open_brackets > 0 or m.startswith("[") or m.endswith("]"):
            open_brackets += m.count("]") - m.count("[")
            if open_brackets < 0:
                open_brackets = 0
            first = k
        else:
            break
        k -= 1
    return starts[first], first, li


def file_name_for(name):
    return name + ".cs"


def move_type_plan(text, pos):
    """План переноса типа, в котором стоит курсор (pos — индекс в тексте).
    → {"name", "file_name", "new_text", "remove": (с, по)}; иначе RefactorError."""
    types, usings, masked, in_string, file_ns = parse(text)
    if not types:
        raise RefactorError(tr("В файле нет типов верхнего уровня (class, struct, interface, enum, record)"))
    if len(types) == 1:
        raise RefactorError(tr("В файле только один тип — переносить нечего"))
    cur = next((t for t in types if t.begin <= pos <= t.end), None)
    if cur is None:
        raise RefactorError(tr("Поставьте курсор внутрь типа, который нужно перенести"))

    begin, first_line, kw_line = _leading_begin(text, masked, cur.begin)
    starts = _lines(text)
    kw_line_start = starts[kw_line]
    prefix = masked[kw_line_start:cur.begin]
    if re.search(r"[;{}]", prefix):
        raise RefactorError(tr("Перед типом на той же строке есть другой код — разнесите их по строкам"))
    last_line = max(k for k, s in enumerate(starts) if s <= cur.end - 1)
    line_end = starts[last_line + 1] - 1 if last_line + 1 < len(starts) else len(text)
    if masked[cur.end:line_end].strip():
        raise RefactorError(tr("После типа на той же строке есть другой код — разнесите их по строкам"))

    body = text[begin:cur.end]
    depth = 0
    for line in masked[begin:cur.end].split("\n"):
        d = re.match(r"\s*#\s*(if|endif)\b", line)
        if d:
            depth += 1 if d.group(1) == "if" else -1
            if depth < 0:
                break
    if depth != 0:
        raise RefactorError(tr("Тип разрезан директивами #if/#endif — перенесите его вручную"))

    # --- текст нового файла
    names = ([file_ns] if file_ns else []) + cur.ns
    namespace = ".".join(n for n in names if n)
    block_style = bool(cur.ns)
    lines = body.split("\n")
    body_starts = _lines(body)
    inside = {s - begin for s in in_string if begin <= s <= cur.end}
    first_indent = re.match(r"[ \t]*", lines[0]).group(0)
    unit = "\t" if "\t" in first_indent else "    "
    out_lines = []
    for k, line in enumerate(lines):
        if body_starts[k] in inside:                 # продолжение многострочной строки — как есть
            out_lines.append(line)
            continue
        if first_indent and line.startswith(first_indent):
            line = line[len(first_indent):]
        elif first_indent:
            line = line.lstrip(" \t") if not line.strip() else line
        if namespace and block_style and line.strip():
            line = unit + line
        out_lines.append(line)
    body_text = "\n".join(out_lines)

    seen, use_lines = set(), []
    for _, line in usings:
        if line not in seen:
            seen.add(line)
            use_lines.append(line)
    parts = []
    if use_lines:
        parts.append("\n".join(use_lines))
    if namespace and block_style:
        parts.append("namespace %s\n{\n%s\n}" % (namespace, body_text))
    elif namespace:
        parts.append("namespace %s;\n\n%s" % (namespace, body_text))
    else:
        parts.append(body_text)
    new_text = "\n\n".join(parts).rstrip("\n") + "\n"

    # --- что вырезать: строки типа + лишние пустые строки на стыке
    n = len(starts)

    def line_text(k):
        if k < 0 or k >= n:
            return None
        end = starts[k + 1] - 1 if k + 1 < n else len(text)
        return text[starts[k]:end].strip()

    l0, l1 = first_line, last_line
    around = (line_text(l0 - 1) or "", line_text(l1 + 1) or "")
    if re.match(r"#\s*if\b", around[0]) or re.match(r"#\s*endif\b", around[1]):
        raise RefactorError(tr("Тип обёрнут в #if … #endif — перенесите его вручную, чтобы не потерять условие"))
    prev = line_text(l0 - 1)                      # None — начало файла
    if prev is None or prev == "" or prev.endswith("{"):
        while line_text(l1 + 1) == "":
            l1 += 1
    nxt = line_text(l1 + 1)                       # None — конец файла
    if (nxt is None or nxt.startswith("}")) and prev == "":
        l0 -= 1
    a = starts[l0]
    b = starts[l1 + 1] if l1 + 1 < n else len(text)
    return {"name": cur.name, "file_name": file_name_for(cur.name), "new_text": new_text, "remove": (a, b)}
