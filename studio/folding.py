"""Сворачивание блоков кода (как «Структура» в Visual Studio: Ctrl+M, Ctrl+M и т. д.).

Две части:
- `fold_ranges(text, language)` — где блоки (без Qt): фигурные скобки C#/C-подобных языков, #region,
  многострочные комментарии, группы using/import; Python — по ast (классы, функции, if/for/with…);
  XML — элементы; Markdown — разделы по заголовкам;
- `Folding` — свёрнутые блоки в одном редакторе: скрывает строки, рисует +/− на поле номеров
  и «…» после свёрнутой строки, разворачивает блок, если курсор попал внутрь.
Блоки ищутся сами, без анализатора: это мгновенно и работает до загрузки Roslyn.
"""
import ast
import re

from PyQt6.QtCore import QObject, QRectF, QTimer, Qt
from PyQt6.QtGui import QColor, QPen, QTextCursor

from . import refactor

# Виды блоков: «Свернуть до определений» (Ctrl+M, Ctrl+O) сворачивает member — тела методов
TYPE, MEMBER, BLOCK, REGION, COMMENT, IMPORTS, NAMESPACE = (
    "type", "member", "block", "region", "comment", "imports", "namespace")


class FoldRange:
    """Строки start..end (с 0): start остаётся видимой, start+1..end прячутся."""
    __slots__ = ("start", "end", "kind")

    def __init__(self, start, end, kind=BLOCK):
        self.start, self.end, self.kind = start, end, kind

    def __repr__(self):
        return "FoldRange(%d, %d, %r)" % (self.start, self.end, self.kind)

    def __eq__(self, other):
        return isinstance(other, FoldRange) and (self.start, self.end, self.kind) == (other.start, other.end, other.kind)

    def contains(self, line):
        return self.start < line <= self.end


_TYPE_HEADER = re.compile(r"\b(class|struct|interface|enum|record|union|impl|trait|mod|Shader|SubShader|Pass)\b")
_NAMESPACE_HEADER = re.compile(r"^\s*namespace\b")
_REGION = re.compile(r"^\s*(?:#|//\s*|#\s*)region\b", re.I)
_ENDREGION = re.compile(r"^\s*(?:#|//\s*|#\s*)end\s?region\b", re.I)


def _regions(lines):
    out, stack = [], []
    for i, line in enumerate(lines):
        if _ENDREGION.match(line):
            if stack:
                start = stack.pop()
                if i > start:
                    out.append(FoldRange(start, i, REGION))
        elif _REGION.match(line):
            stack.append(i)
    return out


def _runs(lines, test, kind, minimum=2):
    """Подряд идущие строки, подходящие под test (///-комментарии, using …;) — один блок."""
    out, start = [], None
    for i, line in enumerate(lines + [""]):
        if i < len(lines) and test(line):
            if start is None:
                start = i
            continue
        if start is not None and i - start >= minimum:
            out.append(FoldRange(start, i - 1, kind))
        start = None
    return out


def _brace_ranges(text, lines, with_brackets=False):
    """Пары { } (и [ ] для JSON) вне строк и комментариев. Если «{» стоит одна на строке (стиль Unity/C#),
    блок начинается со строки объявления выше — как в Visual Studio."""
    masked = refactor.mask(text)[0].split("\n")
    opening = "{[" if with_brackets else "{"
    closing = {"}": "{", "]": "["}
    out, stack = [], []           # stack: (символ, строка заголовка, вид)
    for i, line in enumerate(masked):
        for ch in line:
            if ch in opening:
                header = i
                if not line.strip().rstrip("{[").strip() and i > 0:
                    j = i - 1
                    while j >= 0 and not masked[j].strip():
                        j -= 1
                    if j >= 0 and not masked[j].rstrip().endswith((";", "}", "{")):
                        header = j
                parent = stack[-1][2] if stack else None
                src = masked[header]
                if _NAMESPACE_HEADER.match(src):
                    kind = NAMESPACE
                elif _TYPE_HEADER.search(src):
                    kind = TYPE
                elif parent == TYPE:
                    kind = MEMBER
                else:
                    kind = BLOCK
                stack.append((ch, header, kind))
            elif ch in closing:
                if stack and stack[-1][0] == closing[ch]:
                    _ch, header, kind = stack.pop()
                    if i > header:
                        out.append(FoldRange(header, i, kind))
    return out


def _python_ranges(text, lines):
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return _indent_ranges(lines)
    out = []
    compound = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.If, ast.For, ast.AsyncFor, ast.While,
                ast.With, ast.AsyncWith, ast.Try, getattr(ast, "TryStar", ast.Try), getattr(ast, "Match", ast.If))
    for node in ast.walk(tree):
        if isinstance(node, compound) and getattr(node, "end_lineno", None):
            start, end = node.lineno - 1, node.end_lineno - 1
            if isinstance(node, ast.ClassDef):
                kind = TYPE
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = MEMBER
            else:
                kind = BLOCK
            if end > start:
                out.append(FoldRange(start, end, kind))
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str) and node.end_lineno > node.lineno:
            out.append(FoldRange(node.lineno - 1, node.end_lineno - 1, COMMENT))    # docstring / длинная строка
    imports = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    if len(imports) >= 2 and imports[-1].end_lineno - imports[0].lineno >= 1:
        out.append(FoldRange(imports[0].lineno - 1, imports[-1].end_lineno - 1, IMPORTS))
    return out


def _indent_ranges(lines):
    """Запасной путь для Python с синтаксической ошибкой: блок — строки с бо́льшим отступом после «:»."""
    def width(s):
        return len(s) - len(s.lstrip(" \t"))
    out = []
    for i, line in enumerate(lines):
        if not line.rstrip().endswith(":") or not line.strip():
            continue
        base, end = width(line), None
        for j in range(i + 1, len(lines)):
            if not lines[j].strip():
                continue
            if width(lines[j]) <= base:
                break
            end = j
        if end is not None:
            out.append(FoldRange(i, end, MEMBER if line.lstrip().startswith(("def ", "async def ")) else
                                 TYPE if line.lstrip().startswith("class ") else BLOCK))
    return out


_TAG = re.compile(r"<!--.*?-->|<(/?)([A-Za-z_][\w:.-]*)[^<>]*?(/?)>", re.S)


def _xml_ranges(text):
    out, stack = [], []
    for m in _TAG.finditer(text):
        if m.group(0).startswith("<!--"):
            a, b = text.count("\n", 0, m.start()), text.count("\n", 0, m.end())
            if b > a:
                out.append(FoldRange(a, b, COMMENT))
            continue
        closing, name, self_closing = m.group(1), m.group(2), m.group(3)
        line = text.count("\n", 0, m.start())
        if self_closing:
            continue
        if not closing:
            stack.append((name, line))
        else:
            while stack:
                open_name, start = stack.pop()
                if open_name == name:
                    if line > start:
                        out.append(FoldRange(start, line, BLOCK))
                    break
    return out


def _markdown_ranges(lines):
    out, heads = [], []
    for i, line in enumerate(lines):
        m = re.match(r"(#{1,6})\s", line)
        if m:
            heads.append((i, len(m.group(1))))
    for k, (i, level) in enumerate(heads):
        end = len(lines) - 1
        for j, lvl in heads[k + 1:]:
            if lvl <= level:
                end = j - 1
                break
        while end > i and not lines[end].strip():
            end -= 1
        if end > i:
            out.append(FoldRange(i, end, REGION))
    return out


def fold_ranges(text, language):
    """Все блоки файла, отсортированные по началу (внешние раньше внутренних)."""
    lines = text.split("\n")
    lang_id = getattr(language, "id", language)
    if lang_id == "python":
        ranges = _python_ranges(text, lines) + _regions(lines)
    elif lang_id == "xml":
        ranges = _xml_ranges(text)
    elif lang_id == "markdown":
        ranges = _markdown_ranges(lines)
    elif getattr(language, "braces", False) or lang_id in ("csharp", "json"):
        ranges = _brace_ranges(text, lines, with_brackets=lang_id == "json") + _regions(lines)
        if lang_id != "json":
            ranges += _runs(lines, lambda s: s.lstrip().startswith("///"), COMMENT)
            ranges += _runs(lines, lambda s: re.match(r"\s*(global\s+)?using\s+[\w.=\s]+;", s) is not None, IMPORTS)
            ranges += _block_comments(text)
    else:
        return []
    unique = {}
    for r in ranges:                # одна строка — один блок (самый длинный), как в VS
        if r.end > r.start and (r.start not in unique or r.end > unique[r.start].end):
            unique[r.start] = r
    return sorted(unique.values(), key=lambda r: (r.start, -r.end))


def _block_comments(text):
    """Многострочные /* … */ вне строковых литералов."""
    out, i, n = [], 0, len(text)
    while i < n:
        two = text[i:i + 2]
        if two == "//":
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif two == "/*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            a, b = text.count("\n", 0, i), text.count("\n", 0, j)
            if b > a:
                out.append(FoldRange(a, b, COMMENT))
            i = j
        elif text[i] == '"':
            i += 1
            while i < n and text[i] not in '"\n':
                i += 2 if text[i] == "\\" else 1
            i += 1
        else:
            i += 1
    return out


# ---------------------------------------------------------------- в редакторе
class Folding(QObject):
    """Свёрнутые блоки одного CodeEditor. Свёрнутость запоминается курсором на строке заголовка —
    он сам сдвигается при правках выше, поэтому блок остаётся свёрнутым."""
    COLUMN = 14                     # ширина колонки +/− на поле номеров строк
    DELAY_MS = 250

    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.enabled = True
        self.ranges = []
        self._by_start = {}
        self._collapsed = []        # QTextCursor на началах строк-заголовков
        self._hidden = set()        # номера строк, которые сейчас спрятаны
        self._timer = QTimer(self, singleShot=True, interval=self.DELAY_MS, timeout=self.recompute)
        editor.document().contentsChange.connect(lambda *_: self._timer.start())
        editor.cursorPositionChanged.connect(self._cursor_moved)

    # --- блоки
    def set_enabled(self, on):
        self.enabled = bool(on)
        if not self.enabled:
            self.expand_all()
            self.ranges, self._by_start = [], {}
        else:
            self.recompute()
        self.editor.gutter.update()

    def recompute(self):
        if not self.enabled:
            return
        self.ranges = fold_ranges(self.editor.toPlainText(), self.editor.language)
        self._by_start = {r.start: r for r in self.ranges}
        keep = []
        for cur in self._collapsed:
            if cur.blockNumber() in self._by_start and cur.blockNumber() not in [c.blockNumber() for c in keep]:
                keep.append(cur)
        self._collapsed = keep
        self._apply()

    def range_at(self, line):
        return self._by_start.get(line)

    def innermost(self, line):
        """Блок, который начинается на этой строке, иначе самый вложенный, внутри которого строка."""
        own = self._by_start.get(line)
        if own is not None:
            return own
        found = None
        for r in self.ranges:
            if r.start < line <= r.end and (found is None or r.start >= found.start):
                found = r
        return found

    def is_collapsed(self, line):
        return any(c.blockNumber() == line for c in self._collapsed)

    def collapsed_lines(self):
        return sorted(c.blockNumber() for c in self._collapsed)

    # --- свернуть / развернуть
    def _cursor_for(self, line):
        block = self.editor.document().findBlockByNumber(line)
        cur = QTextCursor(block)
        return cur

    def set_collapsed(self, line, collapsed, apply=True):
        if line not in self._by_start:
            return
        self._collapsed = [c for c in self._collapsed if c.blockNumber() != line]
        if collapsed:
            self._collapsed.append(self._cursor_for(line))
        if apply:
            self._apply()

    def toggle(self, line):
        r = self.innermost(line)
        if r is None:
            return False
        self.set_collapsed(r.start, not self.is_collapsed(r.start))
        if self.is_collapsed(r.start):
            self._move_cursor_out(r)
        return True

    def collapse_all(self, kinds=None):
        for r in self.ranges:
            if kinds is None or r.kind in kinds:
                self.set_collapsed(r.start, True, apply=False)
        self._apply()
        for r in self.ranges:
            if self.is_collapsed(r.start):
                self._move_cursor_out(r)

    def expand_all(self):
        self._collapsed = []
        self._apply()

    def toggle_all(self):
        """Ctrl+M, Ctrl+L: если что-то свёрнуто — развернуть всё, иначе свернуть всё."""
        if self._collapsed:
            self.expand_all()
        else:
            self.collapse_all()

    def collapse_to_definitions(self):
        """Ctrl+M, Ctrl+O: свернуть тела методов, #region и комментарии к ним (как VS)."""
        self.collapse_all({MEMBER, REGION, COMMENT, IMPORTS})

    def ensure_visible(self, line):
        """Развернуть всё, что прячет строку (поиск, переход к ошибке, F12)."""
        changed = False
        for cur in list(self._collapsed):
            r = self._by_start.get(cur.blockNumber())
            if r is not None and r.contains(line):
                self._collapsed.remove(cur)
                changed = True
        if changed:
            self._apply()
        return changed

    def _move_cursor_out(self, r):
        c = self.editor.textCursor()
        if r.contains(c.blockNumber()):
            c.setPosition(self.editor.document().findBlockByNumber(r.start).position())
            c.movePosition(QTextCursor.MoveOperation.EndOfBlock)
            self.editor.setTextCursor(c)

    def _cursor_moved(self):
        if self._hidden:
            line = self.editor.textCursor().blockNumber()
            if line in self._hidden:
                self.ensure_visible(line)

    def _apply(self):
        """Спрятать строки свёрнутых блоков, показать остальные. Трогаются только изменившиеся строки."""
        doc = self.editor.document()
        hidden = set()
        for cur in self._collapsed:
            r = self._by_start.get(cur.blockNumber())
            if r is not None:
                hidden.update(range(r.start + 1, r.end + 1))
        changed = hidden ^ self._hidden
        if not changed:
            return
        for n in changed:
            block = doc.findBlockByNumber(n)
            if block.isValid():
                block.setVisible(n not in hidden)
        self._hidden = hidden
        lo, hi = min(changed), max(changed)
        first, last = doc.findBlockByNumber(max(0, lo - 1)), doc.findBlockByNumber(hi)
        if not last.isValid():
            last = doc.lastBlock()
        doc.markContentsDirty(first.position(), last.position() + last.length() - first.position())
        self.editor.viewport().update()
        self.editor.gutter.update()
        self.editor.ensureCursorVisible()

    # --- рисование
    def paint_marker(self, painter, line, x, top, height, color):
        """+/− на поле номеров строк у строки-заголовка."""
        if not self.enabled or line not in self._by_start:
            return
        size = min(9, height - 4)
        rect = QRectF(x + (self.COLUMN - size) / 2, top + (height - size) / 2, size, size)
        painter.save()
        painter.setPen(QPen(QColor(color), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)
        mid = rect.center()
        painter.drawLine(int(rect.left() + 2), int(mid.y()), int(rect.right() - 1), int(mid.y()))
        if self.is_collapsed(line):
            painter.drawLine(int(mid.x()), int(rect.top() + 2), int(mid.x()), int(rect.bottom() - 1))
        painter.restore()

    def collapsed_box(self, block):
        """Прямоугольник «…» после текста свёрнутой строки (в координатах viewport) или None."""
        if not self.is_collapsed(block.blockNumber()) or not block.isVisible():
            return None
        ed = self.editor
        geo = ed.blockBoundingGeometry(block).translated(ed.contentOffset())
        layout = block.layout()
        if layout is None or layout.lineCount() == 0:
            return None
        line = layout.lineAt(layout.lineCount() - 1)
        fm = ed.fontMetrics()
        x = geo.left() + line.naturalTextWidth() + fm.horizontalAdvance(" ") + ed.document().documentMargin()
        y = geo.top() + line.y()
        return QRectF(x, y + 1, fm.horizontalAdvance("…") + 10, line.height() - 2)

    def paint_boxes(self, painter, colors):
        ed = self.editor
        block = ed.firstVisibleBlock()
        bottom = ed.viewport().rect().bottom()
        painter.save()
        while block.isValid():
            geo = ed.blockBoundingGeometry(block).translated(ed.contentOffset())
            if geo.top() > bottom:
                break
            box = self.collapsed_box(block)
            if box is not None:
                painter.setPen(QPen(QColor(colors["border"]), 1))
                painter.setBrush(QColor(colors["back"]))
                painter.drawRoundedRect(box, 3, 3)
                painter.setPen(QColor(colors["text"]))
                painter.drawText(box, Qt.AlignmentFlag.AlignCenter, "…")
            block = block.next()
        painter.restore()

    def box_at(self, pos):
        """Строка, по чьему «…» щёлкнули (pos — QPointF в координатах viewport), или None."""
        block = self.editor.cursorForPosition(pos.toPoint()).block()
        box = self.collapsed_box(block)
        return block.blockNumber() if box is not None and box.contains(pos) else None

    def hidden_text(self, line):
        """Текст свёрнутого блока — для подсказки при наведении на «…»."""
        r = self._by_start.get(line)
        if r is None:
            return ""
        lines = self.editor.toPlainText().split("\n")[r.start + 1:r.end + 1]
        if len(lines) > 30:
            lines = lines[:30] + ["…"]
        return "\n".join(lines)
