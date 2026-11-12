"""Умный отступ при Enter и при наборе «{», как в Visual Studio. Без Qt: правила работают со строками.

  • после «{», «(», «[» — на уровень глубже; между «{» и «}» Enter разворачивает блок;
  • C-подобные языки: после `if (…)`, `for (…)`, `while (…)`, `else` без «{» — на уровень глубже,
    а после этой одной строки (`x();`) — обратно к уровню `if`; «{» под таким `if` встаёт вровень с ним;
  • Python: после «:» — глубже, после return/pass/break/continue/raise — на уровень мельче.
"""
import re

_OPENERS = ("{", "[", "(")
_CLOSERS = ("}", "]", ")")


def leading(text):
    return text[:len(text) - len(text.lstrip(" \t"))]


def _code(text):
    """Строка без хвостового «//»-комментария и пробелов (строки в кавычках не разбираем — для отступа хватает)."""
    cut = text.find("//")
    return (text if cut < 0 else text[:cut]).rstrip()


class IndentRules:
    """Общее для всех языков: скобки."""
    expands_pairs = False       # Enter между «{» и «}» — блок из трёх строк

    def newline_indent(self, before, previous, unit):
        """Отступ новой строки. before — текст строки до курсора, previous — предыдущая непустая строка (или "")."""
        own = leading(before)
        if _code(before).endswith(_OPENERS):
            return own + unit
        return own

    def open_brace_indent(self, before, previous):
        """Где встать «{», набранной в пустой строке; None — оставить как есть."""
        return None


class BraceRules(IndentRules):
    """C#, C++, JS, шейдеры…: блоки в { }, однострочные тела у if/for/while/else."""
    expands_pairs = True
    HEADER = re.compile(r"^(?:\}\s*)?(?:(?:else\s+)?if|for|foreach|while|using|lock|fixed)\s*\(.*\)$|^(?:\}\s*)?else$")

    def is_header(self, text):
        """`if (…)` / `else` без своей «{» — следующая строка будет телом."""
        return bool(self.HEADER.match(_code(text).strip()))

    def newline_indent(self, before, previous, unit):
        code = _code(before)
        own = leading(before)
        if code.endswith(_OPENERS) or self.is_header(code):
            return own + unit
        # тело однострочного if закончилось — назад к уровню if
        if previous and self.is_header(previous) and code.endswith(";") and len(own) > len(leading(previous)):
            return leading(previous)
        return own

    def open_brace_indent(self, before, previous):
        if not before.strip() and previous and self.is_header(previous):
            return leading(previous)
        return None


class ColonRules(IndentRules):
    """Python: блоки после «:»."""
    DEDENT = re.compile(r"^(?:return|pass|break|continue|raise)\b")

    def newline_indent(self, before, previous, unit):
        own = leading(before)
        code = before.rstrip()
        if "#" in code and "'" not in code and '"' not in code:     # «# комментарий» в конце, без строк рядом
            code = code.split("#", 1)[0].rstrip()
        if code.endswith(":") or code.endswith(_OPENERS):
            return own + unit
        if self.DEDENT.match(code.strip()):
            return own[:max(0, len(own) - len(unit))]
        return own


def rules_for(language):
    if language.colon_blocks:
        return ColonRules()
    if language.braces:
        return BraceRules()
    return IndentRules()
