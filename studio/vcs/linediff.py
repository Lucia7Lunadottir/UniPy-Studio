"""Полоски изменений на поле редактора (как в VS и VS Code): какие строки текущего текста добавлены
или изменены относительно последнего коммита, и где строки удалены. Без Qt."""
import difflib

ADDED, MODIFIED, DELETED_BELOW = "added", "modified", "deleted"


def line_markers(base, current):
    """{номер строки с 1: ADDED | MODIFIED | DELETED_BELOW}. DELETED_BELOW — после этой строки что-то удалено
    (0 — удалено в самом начале файла). base None — файл новый: все строки добавлены."""
    cur = current.split("\n")
    if base is None:
        return {i: ADDED for i in range(1, len(cur) + 1)}
    old = base.replace("\r\n", "\n").split("\n")
    out = {}
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, cur, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        if op == "insert":
            for j in range(j1, j2):
                out[j + 1] = ADDED
        elif op == "delete":
            out.setdefault(j1, DELETED_BELOW)
        else:                                   # replace: общая часть — изменена, лишнее — добавлено
            common = min(i2 - i1, j2 - j1)
            for j in range(j1, j1 + common):
                out[j + 1] = MODIFIED
            for j in range(j1 + common, j2):
                out[j + 1] = ADDED
            if i2 - i1 > j2 - j1:
                out.setdefault(j2, DELETED_BELOW)
    return out


def hunks(base, current):
    """Блоки правок для просмотра сравнения: [(op, строки base i1..i2, строки current j1..j2)] с 0."""
    old = (base or "").replace("\r\n", "\n").split("\n") if base is not None else []
    cur = current.split("\n")
    return [(op, i1, i2, j1, j2) for op, i1, i2, j1, j2 in
            difflib.SequenceMatcher(None, old, cur, autojunk=False).get_opcodes()]
