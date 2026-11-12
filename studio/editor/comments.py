"""Закомментировать / раскомментировать строки, как в Visual Studio (Ctrl+K, Ctrl+C / Ctrl+K, Ctrl+U).

Без Qt: стиль комментария получает строки выделения и возвращает правки
Edit(строка, столбец, сколько убрать, что вставить) — редактор применяет их одним шагом отмены.
"""
from dataclasses import dataclass

COMMENT, UNCOMMENT, TOGGLE = "comment", "uncomment", "toggle"


@dataclass(frozen=True)
class Edit:
    line: int           # номер строки в списке (с 0)
    col: int
    remove: int
    insert: str = ""


def _indent_of(text):
    return len(text) - len(text.lstrip(" \t"))


class CommentStyle:
    def edits(self, lines, mode):
        """Правки для строк lines (всё выделение) в режиме COMMENT / UNCOMMENT / TOGGLE."""
        if mode == TOGGLE:
            mode = UNCOMMENT if self.is_commented(lines) else COMMENT
        return self.comment(lines) if mode == COMMENT else self.uncomment(lines)

    def is_commented(self, lines):
        raise NotImplementedError

    def comment(self, lines):
        raise NotImplementedError

    def uncomment(self, lines):
        raise NotImplementedError


class LineCommentStyle(CommentStyle):
    """«//» или «#» в начале каждой строки — в столбце самого левого отступа выделения, как в VS."""

    def __init__(self, mark):
        self.mark = mark

    def is_commented(self, lines):
        filled = [t for t in lines if t.strip()]
        return bool(filled) and all(t.lstrip().startswith(self.mark) for t in filled)

    def comment(self, lines):
        filled = [(n, t) for n, t in enumerate(lines) if t.strip()]
        if not filled:
            return []
        col = min(_indent_of(t) for _, t in filled)
        return [Edit(n, col, 0, self.mark + " ") for n, _ in filled]

    def uncomment(self, lines):
        out = []
        for n, t in enumerate(lines):
            col = _indent_of(t)
            if not t.startswith(self.mark, col):
                continue
            end = col + len(self.mark)
            out.append(Edit(n, col, len(self.mark) + (t[end:end + 1] == " ")))
        return out


class BlockCommentStyle(CommentStyle):
    """Языки без строчного комментария (XML, HTML, CSS): выделенные строки — в один «<!-- … -->» / «/* … */»."""

    def __init__(self, opening, closing):
        self.opening, self.closing = opening, closing

    def _span(self, lines):
        filled = [n for n, t in enumerate(lines) if t.strip()]
        return (filled[0], filled[-1]) if filled else None

    def is_commented(self, lines):
        span = self._span(lines)
        return bool(span) and lines[span[0]].lstrip().startswith(self.opening) \
            and lines[span[1]].rstrip().endswith(self.closing)

    def comment(self, lines):
        span = self._span(lines)
        if not span:
            return []
        first, last = span
        end = len(lines[last].rstrip())
        tail = Edit(last, end, 0, " " + self.closing)
        head = Edit(first, _indent_of(lines[first]), 0, self.opening + " ")
        return [tail, head]         # сначала конец: на одной строке начало сдвинуло бы столбец конца

    def uncomment(self, lines):
        if not self.is_commented(lines):
            return []
        first, last = self._span(lines)
        text = lines[last].rstrip()
        cut = len(self.closing) + (text[:-len(self.closing)].endswith(" "))
        tail = Edit(last, len(text) - cut, cut)
        col = _indent_of(lines[first])
        after = col + len(self.opening)
        head = Edit(first, col, len(self.opening) + (lines[first][after:after + 1] == " "))
        return [tail, head]


def style_for(language):
    """Как комментировать в этом языке; None — комментариев нет (простой текст)."""
    if language.line_comment:
        return LineCommentStyle(language.line_comment)
    if language.block_comment:
        return BlockCommentStyle(*language.block_comment)
    return None
