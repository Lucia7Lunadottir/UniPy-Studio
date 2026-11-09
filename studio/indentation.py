"""Отступы: пробелы или табуляция, размер — и откуда это взялось (как в Visual Studio).

Порядок, в котором решается стиль файла:
1. `.editorconfig` (поиск от папки файла вверх до `root = true`) — то же читают Roslyn и Unity-проекты;
2. содержимое самого файла («адаптивное форматирование» VS): если файл уже с табами — продолжаем табами;
3. «Средства → Параметры → Текстовый редактор → Отступы».
Модуль без Qt — его легко проверять тестами.
"""
import os
import re
from functools import lru_cache


class IndentStyle:
    """Как делать отступ. source — откуда взят стиль: "editorconfig", "file" или "options"."""

    def __init__(self, use_tabs=False, size=4, tab_width=None, source="options",
                 trim_trailing=False, final_newline=None):
        self.use_tabs = bool(use_tabs)
        self.size = max(1, int(size))
        self.tab_width = max(1, int(tab_width or size))
        self.source = source
        self.trim_trailing = trim_trailing          # trim_trailing_whitespace из .editorconfig или параметров
        self.final_newline = final_newline          # insert_final_newline: True/False/None (не трогать)

    def __eq__(self, other):
        return isinstance(other, IndentStyle) and vars(self) == vars(other)

    def __repr__(self):
        return "IndentStyle(%s)" % ", ".join("%s=%r" % kv for kv in vars(self).items())

    # --- текст отступа
    def unit(self):
        """Один уровень отступа."""
        return "\t" if self.use_tabs else " " * self.size

    def column(self, text):
        """Ширина начала строки в столбцах (таб — до следующей остановки)."""
        col = 0
        for ch in text:
            col = (col // self.tab_width + 1) * self.tab_width if ch == "\t" else col + 1
        return col

    def make(self, columns):
        """Отступ шириной columns в стиле файла."""
        if self.use_tabs:
            return "\t" * (columns // self.tab_width) + " " * (columns % self.tab_width)
        return " " * columns

    def tab_insertion(self, before):
        """Что вставить по Tab, если курсор после текста before (без выделения)."""
        if self.use_tabs:
            return "\t"
        col = self.column(before)
        return " " * (self.size - col % self.size)

    def dedent(self, leading):
        """Сколько символов убрать в начале строки при Shift+Tab (leading — пробелы/табы в начале)."""
        if not leading:
            return 0
        if leading.endswith("\t"):
            return 1
        spaces = len(leading) - len(leading.rstrip(" "))
        return min(self.size, spaces) or 1

    def backspace_width(self, before):
        """Backspace в начале строки из одних пробелов стирает до предыдущей остановки (как VS)."""
        if self.use_tabs or not before or before.strip(" "):
            return 1
        col = len(before)
        return col % self.size or self.size

    def label(self):
        return ("tabs", self.tab_width) if self.use_tabs else ("spaces", self.size)


# ---------------------------------------------------------------- по содержимому файла
def detect(text, max_lines=2000):
    """Стиль по самому файлу: None, если отступов нет (тогда — параметры).
    Табы: если строк, начинающихся с таба, больше, чем с пробелов. Размер: самая частая
    разница отступа между соседними строками (2, 3, 4 или 8)."""
    tabs = spaces = 0
    deltas = {}
    previous = 0
    for line in text.split("\n", max_lines)[:max_lines]:
        if not line.strip():
            continue
        lead = line[:len(line) - len(line.lstrip(" \t"))]
        if lead.startswith("\t"):
            tabs += 1
        elif lead.startswith(" "):
            if line.lstrip().startswith("*"):        # продолжение /* … */ — смещено на 1, не считается
                continue
            spaces += 1
            width = len(lead)
            delta = abs(width - previous)
            if delta in (2, 3, 4, 8):
                deltas[delta] = deltas.get(delta, 0) + 1
            previous = width
            continue
        previous = 0
    if not tabs and not spaces:
        return None
    if tabs > spaces:
        return IndentStyle(use_tabs=True, size=4, source="file")
    size = max(deltas, key=lambda d: (deltas[d], d == 4)) if deltas else 4
    return IndentStyle(use_tabs=False, size=size, source="file")


# ---------------------------------------------------------------- .editorconfig
def _glob_to_regex(pattern):
    """Шаблон секции .editorconfig → regex для пути относительно папки файла .editorconfig."""
    i, n, out = 0, len(pattern), []
    depth = 0
    while i < n:
        c = pattern[i]
        if c == "*":
            if pattern[i:i + 2] == "**":
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            j = pattern.find("]", i + 1)
            if j < 0:
                out.append(r"\[")
            else:
                body = pattern[i + 1:j]
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append("[" + body.replace("\\", "\\\\") + "]")
                i = j
        elif c == "{":
            j = pattern.find("}", i + 1)
            body = pattern[i + 1:j] if j > 0 else ""
            m = re.fullmatch(r"(-?\d+)\.\.(-?\d+)", body)
            if m:
                lo, hi = sorted((int(m.group(1)), int(m.group(2))))
                out.append("(?:%s)" % "|".join(str(k) for k in range(lo, hi + 1)))
                i = j
            elif j > 0 and "," in body:
                out.append("(?:")
                depth += 1
            else:
                out.append(r"\{")
        elif c == "," and depth:
            out.append("|")
        elif c == "}" and depth:
            out.append(")")
            depth -= 1
        elif c == "\\" and i + 1 < n:
            out.append(re.escape(pattern[i + 1]))
            i += 1
        else:
            out.append(re.escape(c))
        i += 1
    return "".join(out)


def section_matches(pattern, rel_path):
    """Подходит ли секция [pattern] к пути (относительно папки .editorconfig, с /)."""
    pattern = pattern.strip()
    if "/" in pattern.rstrip("/"):
        regex = _glob_to_regex(pattern.lstrip("/"))
        return re.fullmatch(regex, rel_path) is not None
    return re.fullmatch(_glob_to_regex(pattern), rel_path.rsplit("/", 1)[-1]) is not None \
        or re.fullmatch("(?:.*/)?" + _glob_to_regex(pattern), rel_path) is not None


@lru_cache(maxsize=256)
def _parse(path, _mtime):
    """[(шаблон, {ключ: значение})] и root. Кэш по времени изменения файла."""
    sections, root, current = [], False, None
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return (), False
    for raw in lines:
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        if line.startswith("[") and line.endswith("]"):
            current = (line[1:-1], {})
            sections.append(current)
            continue
        if "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        key = key.lower()
        if current is None:
            if key == "root":
                root = value.lower() == "true"
            continue
        current[1][key] = value.lower() if key not in ("charset",) else value
    return tuple((p, dict(v)) for p, v in sections), root


def editorconfig_for(path, stop_at=None):
    """Свойства .editorconfig для файла: от дальних папок к ближним, внутри файла — сверху вниз."""
    folder = os.path.dirname(os.path.abspath(path))
    chain = []
    while True:
        candidate = os.path.join(folder, ".editorconfig")
        try:
            mtime = os.stat(candidate).st_mtime_ns
        except OSError:
            mtime = None
        if mtime is not None:
            sections, root = _parse(candidate, mtime)
            chain.append((folder, sections))
            if root:
                break
        parent = os.path.dirname(folder)
        if parent == folder or (stop_at and os.path.abspath(folder) == os.path.abspath(stop_at)):
            break
        folder = parent
    props = {}
    for folder, sections in reversed(chain):
        rel = os.path.relpath(os.path.abspath(path), folder).replace(os.sep, "/")
        for pattern, values in sections:
            if section_matches(pattern, rel):
                props.update(values)
    return props


def _bool(value):
    return {"true": True, "false": False}.get(str(value).lower())


def style_from_editorconfig(props, fallback):
    """IndentStyle из свойств .editorconfig; чего там нет — берётся из fallback. None — нет свойств об отступах."""
    keys = ("indent_style", "indent_size", "tab_width", "trim_trailing_whitespace", "insert_final_newline")
    if not any(k in props for k in keys):
        return None
    use_tabs = {"tab": True, "space": False}.get(props.get("indent_style"), fallback.use_tabs)
    tab_width = int(props["tab_width"]) if props.get("tab_width", "").isdigit() else None
    size_raw = props.get("indent_size", "")
    if size_raw == "tab":
        size = tab_width or fallback.tab_width
    elif size_raw.isdigit():
        size = int(size_raw)
    else:
        size = fallback.size if not use_tabs else (tab_width or fallback.tab_width)
    trim = _bool(props.get("trim_trailing_whitespace"))
    return IndentStyle(use_tabs, size, tab_width or size, source="editorconfig",
                       trim_trailing=fallback.trim_trailing if trim is None else trim,
                       final_newline=_bool(props.get("insert_final_newline")))


def resolve(path, text, defaults, use_editorconfig=True, adapt=True):
    """Итоговый стиль файла: .editorconfig → содержимое файла → параметры (defaults)."""
    if use_editorconfig and path:
        style = style_from_editorconfig(editorconfig_for(path), defaults)
        if style is not None:
            return style
    if adapt:
        found = detect(text)
        if found is not None:
            found.trim_trailing = defaults.trim_trailing
            return found
    return defaults


def apply_save_rules(text, style):
    """Перед записью: пробелы в конце строк и перевод строки в конце файла (если так велят правила)."""
    if style.trim_trailing:
        text = "\n".join(line.rstrip(" \t") for line in text.split("\n"))
    if style.final_newline is True and text and not text.endswith("\n"):
        text += "\n"
    elif style.final_newline is False:
        text = text.rstrip("\n")
    return text
