"""Подсветка кода: Python, C# (Unity), XML/.ui, JSON."""
import re
from PyQt6.QtGui import QSyntaxHighlighter, QTextCharFormat, QColor, QFont

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


class Highlighter(QSyntaxHighlighter):
    # состояния многострочных конструкций
    ST_TRIPLE_D, ST_TRIPLE_S, ST_BLOCK, ST_XML_COMMENT = 1, 2, 3, 4

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
                (re.compile(r"^\s*\[[^\]]*\]"), _fmt(C["type"])),
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
        mask = [True] * n          # True — обычный код (не строка и не комментарий)
        self.setCurrentBlockState(0)
        i = 0
        state = self.previousBlockState()
        if state > 0:
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
                        self.setFormat(i, end - i, self.json_key)
                    i = end
                    continue
            i += 1

        for rx, fmt in self.rules:
            for m in rx.finditer(text):
                s, e = m.start(), m.end()
                if s < n and mask[s]:
                    self.setFormat(s, e - s, fmt)

    def _ml_end(self, state):
        return {
            self.ST_TRIPLE_D: ('"""', self.f_string),
            self.ST_TRIPLE_S: ("'''", self.f_string),
            self.ST_BLOCK: ("*/", self.f_comment),
            self.ST_XML_COMMENT: ("-->", self.f_comment),
        }[state]

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
        self.setFormat(s, e - s, fmt)
        for k in range(s, min(e, len(mask))):
            mask[k] = False
