"""Где в Python показать подсказки в строке (inlay hints) — по дереву ast, без Qt.

Имена параметров: у позиционных аргументов вызова («print_box(10, 20)» → «width: 10, height: 20»).
Типы: у переменной при первом присваивании без аннотации («x = load()» → «x: Config»).
Что за функция и какой тип — решает jedi в фоновом потоке (intellisense.JediWorker._inlay_hints);
здесь только места и правила, когда подсказка лишняя (как в Pylance и Roslyn).
"""
import ast

from .positions import Lines


class CallSite:
    """Вызов: где спросить jedi о сигнатуре (строка с 1, столбец в символах) и позиционные аргументы."""
    __slots__ = ("callee", "line", "col", "args")

    def __init__(self, callee, line, col, args):
        self.callee = callee            # текст вызываемого выражения — ключ кэша сигнатур
        self.line, self.col = line, col
        self.args = args                # [(номер, строка с 0, столбец UTF-16, текст аргумента)]


def _in_range(node, first, last):
    return first <= node.lineno - 1 <= last


def call_sites(code, first, last):
    """Вызовы с позиционными аргументами на строках first..last (с 0)."""
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return []
    lines = Lines(code)
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args or not _in_range(node, first, last):
            continue
        args = []
        for i, a in enumerate(node.args):
            if isinstance(a, ast.Starred):
                break                       # после *args номера параметров уже не известны
            line0 = a.lineno - 1
            args.append((i, line0, lines.utf16_from_bytes(line0, a.col_offset), ast.get_source_segment(code, a) or ""))
        if not args:
            continue
        first_arg = node.args[0]
        out.append(CallSite(ast.get_source_segment(code, node.func) or "", first_arg.lineno,
                            lines.chars_from_bytes(first_arg.lineno - 1, first_arg.col_offset), args))
    return out


def needs_param_hint(param_name, arg_text):
    """Подсказка лишняя, если аргумент и так называется как параметр (x → x, self.x → x, get_x() → x)."""
    if not param_name or param_name.startswith("_"):
        return False
    tail = arg_text.strip().rstrip("()").split(".")[-1].lower()
    name = param_name.lower()
    return not (tail == name or tail.endswith("_" + name) or tail == "get_" + name)


class Assignment:
    """Переменная, которой присвоили значение без аннотации: где спросить тип и куда поставить подсказку."""
    __slots__ = ("name", "line", "col", "line0", "end16", "obvious")

    def __init__(self, name, line, col, line0, end16, obvious):
        self.name = name
        self.line, self.col = line, col     # для jedi: строка с 1, столбец в символах
        self.line0, self.end16 = line0, end16   # подсказка — сразу после имени
        self.obvious = obvious              # справа вызов класса или литерал — тип и так виден


def _obvious(value):
    if isinstance(value, (ast.Constant, ast.List, ast.Dict, ast.Set, ast.Tuple, ast.JoinedStr,
                          ast.ListComp, ast.DictComp, ast.SetComp)):
        return True
    if isinstance(value, ast.Call):
        func = value.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        return bool(name[:1].isupper())     # Config(...) — тип в самом выражении
    return False


_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def _walk_scope(scope):
    """Узлы одной области видимости — без тел вложенных функций и классов (у них свои переменные)."""
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, _SCOPES):
            stack.extend(ast.iter_child_nodes(node))


def assignments(code, first, last):
    """Первые присваивания имён на строках first..last (повторные — тип уже показан выше)."""
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return []
    lines = Lines(code)
    seen = set()
    out = []
    scopes = [tree] + [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for scope in scopes:
        for node in sorted(_walk_scope(scope), key=lambda n: (getattr(n, "lineno", 0), getattr(n, "col_offset", 0))):
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if not isinstance(target, ast.Name):
                continue
            key = (id(scope), target.id)
            if key in seen:
                continue
            seen.add(key)
            if not _in_range(target, first, last):
                continue
            line0 = target.lineno - 1
            out.append(Assignment(target.id, target.lineno, lines.chars_from_bytes(line0, target.col_offset),
                                  line0, lines.utf16_from_bytes(line0, target.end_col_offset), _obvious(node.value)))
    return out
