"""Подсветка кода: своя для Python, C# (Unity), XML/.ui, JSON, Markdown; остальное — через Pygments."""
import re
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QSyntaxHighlighter, QTextCharFormat, QColor, QFont, QTextBlockUserData

from .theme import C
from . import unity_api


def _fmt(color, bold=False, italic=False):
    f = QTextCharFormat()
    f.setForeground(QColor(color))
    if bold:
        f.setFontWeight(QFont.Weight.Bold)
    if italic:
        f.setFontItalic(True)
    return f


PY_CONTROL = ("if elif else for while return break continue try except finally raise with "
              "yield import from as pass await match case").split()
PY_KEYWORDS = ("def class lambda None True False and or not in is global nonlocal async del "
               "assert").split()
PY_BUILTINS = ("print len range str int float bool list dict set tuple open isinstance super "
               "enumerate zip map filter sorted reversed min max sum abs any all round type "
               "getattr setattr hasattr input repr format iter next id hash vars dir "
               "staticmethod classmethod property object Exception ValueError TypeError "
               "KeyError IndexError RuntimeError").split()

CS_CONTROL = ("if else for foreach while do return break continue switch case default try "
              "catch finally throw yield goto using await").split()
CS_KEYWORDS = ("public private protected internal static readonly const void class struct "
               "interface enum namespace new this base override virtual abstract sealed "
               "partial null true false var in out ref is as get set value async event "
               "delegate operator implicit explicit params typeof sizeof nameof where "
               "int float double bool string char byte long short uint object decimal "
               "dynamic").split()


class AttributeState(QTextBlockUserData):
    def __init__(self, depth=0):
        super().__init__()
        self.depth = depth


class Highlighter(QSyntaxHighlighter):
    # состояния многострочных конструкций
    ST_TRIPLE_D, ST_TRIPLE_S, ST_BLOCK, ST_XML_COMMENT, ST_VERBATIM = 1, 2, 3, 4, 5

    def __init__(self, document, lang):
        super().__init__(document)
        self.lang = lang
        self._build()

    def rebuild(self):
        """Тема сменилась — заново собрать форматы из текущих цветов и перекрасить текст."""
        self._build()
        self.rehighlight()

    def _build(self):
        lang = self.lang
        self.f_string = _fmt(C["string"])
        self.f_attribute = _fmt(C["type"])
        self.f_attribute_param = _fmt(C["param"])
        self.f_attribute_punctuation = _fmt(C["text"])
        self.f_attribute_string = self.f_string
        self.f_comment = _fmt(C["comment"])
        self.f_doc = _fmt(C["comment"], italic=True)
        self.rules = []
        kw = _fmt(C["keyword"])
        ctl = _fmt(C["control"])
        typ = _fmt(C["type"])
        fn = _fmt(C["function"])
        num = _fmt(C["number"])
        if lang == "python":
            self.rules = [
                (re.compile(r"\b[A-Z][A-Za-z0-9_]*\b"), typ),
                (re.compile(r"\b[A-Za-z_]\w*(?=\s*\()"), fn),
                (re.compile(r"\b(?:%s)\b" % "|".join(PY_BUILTINS)), fn),
                (re.compile(r"\b(?:%s)\b" % "|".join(PY_KEYWORDS)), kw),
                (re.compile(r"\b(?:%s)\b" % "|".join(PY_CONTROL)), ctl),
                (re.compile(r"\b(?:self|cls)\b"), _fmt(C["keyword"], italic=True)),
                (re.compile(r"(?<=\bdef\s)\s*\w+"), fn),
                (re.compile(r"(?<=\bclass\s)\s*\w+"), typ),
                (re.compile(r"@[\w.]+"), _fmt(C["decorator"])),
                (re.compile(r"\b(?:0x[0-9A-Fa-f]+|\d+\.?\d*(?:e[+-]?\d+)?)\b"), num),
                (re.compile(r"\b(?:pyqtSignal|pyqtSlot|pyqtProperty)\b"), _fmt(C["function"])),
            ]
        elif lang == "csharp":
            unity_types = "|".join(sorted(unity_api.CLASSES.keys(), key=len, reverse=True))
            self.rules = [
                (re.compile(r"\b[A-Z][A-Za-z0-9_]*\b"), typ),
                (re.compile(r"\b[A-Za-z_]\w*(?=\s*(?:<[\w,\s]*>)?\s*\()"), fn),
                (re.compile(r"\b(?:%s)\b" % unity_types), typ),
                (re.compile(r"\b(?:%s)\b" % "|".join(CS_KEYWORDS)), kw),
                (re.compile(r"\b(?:%s)\b" % "|".join(CS_CONTROL)), ctl),
                (re.compile(r"^\s*#\s*\w+.*$"), _fmt(C["preproc"])),
                (re.compile(r"\b\d+\.?\d*[fFdDmM]?\b"), num),
            ]
        elif lang == "xml":
            self.rules = [
                (re.compile(r"</?[\w:.-]+|/?>"), _fmt(C["tag"])),
                (re.compile(r"\b[\w:-]+(?==)"), _fmt(C["attr"])),
                (re.compile(r"&\w+;"), _fmt(C["control"])),
            ]
        elif lang == "json":
            self.rules = [
                (re.compile(r"\b(?:true|false|null)\b"), kw),
                (re.compile(r"-?\b\d+\.?\d*(?:[eE][+-]?\d+)?\b"), num),
            ]
        elif lang == "markdown":
            self.rules = [
                (re.compile(r"^#{1,6}\s.*$"), _fmt(C["keyword"], bold=True)),
                (re.compile(r"`[^`]+`"), _fmt(C["string"])),
                (re.compile(r"\*\*[^*]+\*\*"), _fmt(C["bright"], bold=True)),
                (re.compile(r"^\s*[-*]\s"), _fmt(C["control"])),
            ]
        self.json_key = _fmt(C["param"])

    # ------------------------------------------------------------------
    def highlightBlock(self, text):
        n = len(text)
        self.offsets = [0]
        for ch in text:
            self.offsets.append(self.offsets[-1] + (2 if ord(ch) > 0xFFFF else 1))
        mask = [True] * n          # True — обычный код (не строка и не комментарий)
        self.setCurrentBlockState(0)
        i = 0
        state = self.previousBlockState()
        if state == self.ST_VERBATIM:
            i = self._verbatim(text, 0, mask, continuation=True)
            if self.currentBlockState() == self.ST_VERBATIM:
                return
        elif state > 0:
            end_tok, fmt = self._ml_end(state)
            j = text.find(end_tok)
            if j < 0:
                self._paint(0, n, fmt, mask)
                self.setCurrentBlockState(state)
                return
            i = j + len(end_tok)
            self._paint(0, i, fmt, mask)

        lang = self.lang
        while i < n:
            ch = text[i]
            if lang == "python":
                if ch == "#":
                    self._paint(i, n, self.f_comment, mask)
                    break
                if text.startswith('"""', i) or text.startswith("'''", i):
                    tok = text[i:i + 3]
                    j = text.find(tok, i + 3)
                    if j < 0:
                        self._paint(i, n, self.f_string, mask)
                        self.setCurrentBlockState(self.ST_TRIPLE_D if tok == '"""' else self.ST_TRIPLE_S)
                        break
                    self._paint(i, j + 3, self.f_string, mask)
                    i = j + 3
                    continue
                if ch in "\"'":
                    i = self._string(text, i, ch, mask)
                    continue
            elif lang == "csharp":
                raw = re.match(r'"{3,}', text[i:])
                if raw:
                    token = raw.group()
                    end = text.find(token, i + len(token))
                    self._paint(i, n if end < 0 else end + len(token), self.f_string, mask)
                    if end < 0:
                        self.setCurrentBlockState(100 + len(token))
                        break
                    i = end + len(token)
                    continue
                if ch == '"' and (text[max(0, i-1):i] == '@' or text[max(0, i-2):i] == '@$'):
                    i = self._verbatim(text, i, mask)
                    continue
                if text.startswith("//", i):
                    self._paint(i, n, self.f_doc if text.startswith("///", i) else self.f_comment, mask)
                    break
                if text.startswith("/*", i):
                    j = text.find("*/", i + 2)
                    if j < 0:
                        self._paint(i, n, self.f_comment, mask)
                        self.setCurrentBlockState(self.ST_BLOCK)
                        break
                    self._paint(i, j + 2, self.f_comment, mask)
                    i = j + 2
                    continue
                if ch in "\"'":
                    i = self._string(text, i, ch, mask)
                    continue
            elif lang == "xml":
                if text.startswith("<!--", i):
                    j = text.find("-->", i + 4)
                    if j < 0:
                        self._paint(i, n, self.f_comment, mask)
                        self.setCurrentBlockState(self.ST_XML_COMMENT)
                        break
                    self._paint(i, j + 3, self.f_comment, mask)
                    i = j + 3
                    continue
                if ch == '"':
                    i = self._string(text, i, ch, mask)
                    continue
            elif lang == "json":
                if ch == '"':
                    end = self._string(text, i, ch, mask)
                    rest = text[end:].lstrip()
                    if rest.startswith(":"):
                        self._format_span(i, end, self.json_key)
                    i = end
                    continue
            i += 1

        for rx, fmt in self.rules:
            for m in rx.finditer(text):
                s, e = m.start(), m.end()
                if all(mask[s:e]):
                    self._format_span(s, e, fmt)

        if lang == "csharp":
            self._highlight_attributes(text, mask)

    def _format_span(self, start, end, fmt):
        self.setFormat(self.offsets[start], self.offsets[end] - self.offsets[start], fmt)

    def _ml_end(self, state):
        if state >= 100:
            return ('"' * (state - 100), self.f_string)
        return {
            self.ST_TRIPLE_D: ('"""', self.f_string),
            self.ST_TRIPLE_S: ("'''", self.f_string),
            self.ST_BLOCK: ("*/", self.f_comment),
            self.ST_XML_COMMENT: ("-->", self.f_comment),
        }[state]

    def _verbatim(self, text, i, mask, continuation=False):
        j = i if continuation else i + 1
        while j < len(text):
            if text[j:j+2] == '""':
                j += 2
            elif text[j] == '"':
                self._paint(i, j + 1, self.f_string, mask)
                return j + 1
            else:
                j += 1
        self._paint(i, len(text), self.f_string, mask)
        self.setCurrentBlockState(self.ST_VERBATIM)
        return len(text)


    def _string(self, text, i, quote, mask):
        n = len(text)
        j = i + 1
        while j < n:
            if text[j] == "\\":
                j += 2
                continue
            if text[j] == quote:
                j += 1
                break
            j += 1
        j = min(j, n)
        self._paint(i, j, self.f_string, mask)
        return j

    def _paint(self, s, e, fmt, mask):
        if e <= s:
            return
        self._format_span(s, e, fmt)
        for k in range(s, min(e, len(mask))):
            mask[k] = False

    def _highlight_attributes(self, text, mask):
        code = "".join(char if allowed else " " for char, allowed in zip(text, mask))
        previous = self.currentBlock().previous().userData()
        continuation = isinstance(previous, AttributeState) and previous.depth > 0
        if continuation:
            code = "[" + code
            text = " " + text
            mask = [True] + mask
            self.offsets = [0] + self.offsets
        self.setCurrentBlockUserData(AttributeState())
        start = re.match(r"\s*\[\s*(?=(?:\w+\s*:\s*)?[A-Za-z_]\w*(?:\s*[.(,\]]))", code)
        if continuation:
            start = re.match(r"\[", code)
        while start:
            opening = code.index("[", start.start(), start.end())
            depth, end = 1, opening + 1
            while end < len(code) and depth:
                if code[end] == "[":
                    depth += 1
                elif code[end] == "]":
                    depth -= 1
                end += 1
            if depth:
                self.setCurrentBlockUserData(AttributeState(depth))
            section = code[opening:end]
            for match in re.finditer(r"[\[\](),=]", section):
                self._format_span(opening + match.start(), opening + match.end(),
                                  self.f_attribute_punctuation)
            for match in re.finditer(r"\b[A-Za-z_]\w*(?=\s*=\s*(?!=))", section):
                self._format_span(opening + match.start(), opening + match.end(),
                                  self.f_attribute_param)
            depth = 0
            expect_name = not continuation
            for token in re.finditer(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*|[():,]", section):
                value = token.group()
                if value == "(":
                    depth += 1
                elif value == ")":
                    depth -= 1
                elif depth == 0 and value in (":", ","):
                    expect_name = not continuation
                elif depth == 0 and expect_name:
                    self._format_span(opening + token.start(), opening + token.end(), self.f_attribute)
                    expect_name = False
            for i in range(opening, end):
                if not mask[i] and self.format(self.offsets[i]) == self.f_string:
                    self._format_span(i, i + 1, self.f_attribute_string)
            start = re.compile(r"\s*\[\s*(?=[A-Za-z_]\w*)").match(code, end)



# ====================================================================== Pygments: остальные языки
class PygmentsHighlighter(QSyntaxHighlighter):
    """Подсветка любого языка, который знает Pygments (HLSL/шейдеры Unity, C++, JS, Rust…).

    Pygments разбирает текст целиком (многострочные комментарии и строки), поэтому документ
    разбирается заново не на каждую букву, а через RELEX_MS после последней правки."""
    RELEX_MS = 120
    RELEX_MS_BIG = 600              # для больших файлов — реже, чтобы не тормозить набор
    BIG = 200_000

    # тип токена Pygments → роль темы (ищется по цепочке родителей: Keyword.Type → Keyword)
    ROLES = {
        "Keyword.Type": "type", "Keyword.Constant": "keyword", "Keyword": "keyword",
        "Name.Class": "type", "Name.Namespace": "type", "Name.Builtin.Pseudo": "keyword",
        "Name.Builtin": "type", "Name.Function": "function", "Name.Decorator": "decorator",
        "Name.Tag": "tag", "Name.Attribute": "attr", "Name.Variable": "param", "Name.Constant": "number",
        "Name.Label": "control", "String": "string", "Comment.Preproc": "preproc",
        "Comment.PreprocFile": "string", "Comment": "comment", "Number": "number", "Operator.Word": "keyword",
        "Generic.Heading": "keyword", "Generic.Subheading": "keyword", "Generic.Deleted": "error",
        "Generic.Inserted": "ok",
    }

    def __init__(self, document, lexer):
        super().__init__(document)
        self.lexer = lexer
        self._spans = {}            # номер строки → [(начало UTF-16, длина, роль)]
        self._formats = {}
        self._timer = QTimer(singleShot=True, timeout=self._relex)
        document.contentsChange.connect(self._changed)
        self._build()
        self._relex()

    def _build(self):
        self._formats = {role: _fmt(C[role], italic=(role == "comment")) for role in set(self.ROLES.values())}

    def rebuild(self):
        self._build()
        self.rehighlight()

    def _changed(self, _pos, removed, added):
        if removed or added:        # смена одних только цветов (rehighlight) текст не меняет
            self._timer.start(self.RELEX_MS_BIG if self.document().characterCount() > self.BIG else self.RELEX_MS)

    def _role(self, ttype):
        while ttype is not None:
            role = self.ROLES.get(str(ttype)[6:])      # "Token.Keyword.Type" → "Keyword.Type"
            if role:
                return role
            ttype = ttype.parent
        return None

    def _relex(self):
        text = self.document().toPlainText()
        starts, pos = [], 0
        lines = text.split("\n")
        for line in lines:
            starts.append(pos)
            pos += len(line) + 1
        spans = {}
        line_no = 0
        try:
            tokens = list(self.lexer.get_tokens_unprocessed(text))
        except Exception:   # noqa: BLE001 — сбой лексера на недописанном коде не должен ронять редактор
            tokens = []
        for start, ttype, value in tokens:
            role = self._role(ttype)
            if not role or not value:
                continue
            # токен может занимать несколько строк (многострочный комментарий) — режем по строкам
            while line_no + 1 < len(starts) and starts[line_no + 1] <= start:
                line_no += 1
            ln, off, rest = line_no, start - starts[line_no], value
            while rest:
                line = lines[ln] if ln < len(lines) else ""
                piece = rest[:max(0, len(line) - off)]
                if piece:
                    u_off = _utf16_len(line[:off])
                    spans.setdefault(ln, []).append((u_off, _utf16_len(piece), role))
                rest = rest[len(piece):]
                if rest.startswith("\n"):
                    rest = rest[1:]
                    ln, off = ln + 1, 0
                elif rest:
                    break
        self._spans = spans
        self.rehighlight()

    def highlightBlock(self, text):
        for start, length, role in self._spans.get(self.currentBlock().blockNumber(), ()):
            self.setFormat(start, length, self._formats[role])


def _utf16_len(s):
    """Позиции в Qt считаются в UTF-16: эмодзи и т.п. занимают две позиции."""
    return len(s) if s.isascii() or all(ord(ch) < 0x10000 for ch in s) else len(s.encode("utf-16-le")) // 2
