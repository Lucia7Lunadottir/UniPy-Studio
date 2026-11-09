"""«Перенести в отдельный файл» для Python (как Move symbol to new file в Visual Studio): класс или функция
верхнего уровня под курсором → новый модуль рядом. Без Qt — только текст и ast.

Что делает план:
- новый файл: нужные перенесённому коду импорты (только используемые) + импорт из старого модуля того,
  чем код пользуется оттуда + сам код (с декораторами и комментарием над ним);
- старый файл: код вырезан; если имя там ещё используется — импорт из нового модуля;
- другие файлы проекта с `from старый import Имя` — переправлены на новый модуль.
"""
import ast
import os
import re

from .i18n import tr


class MoveError(Exception):
    pass


def snake_case(name):
    """PlayerController → player_controller; HTTPServer → http_server."""
    s = re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", name)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)
    return s.lower()


DEFINITIONS = (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def _bound_names(node):
    """Имена, которые оператор верхнего уровня создаёт в модуле."""
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return [(a.asname or a.name).split(".")[0] for a in node.names if a.name != "*"]
    if isinstance(node, DEFINITIONS):
        return [node.name]
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        return [n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)]
    return []


def _used_names(node):
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _first_line(node, lines):
    """С декораторов и сплошного комментария прямо над определением."""
    start = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])]) - 1
    while start > 0 and lines[start - 1].lstrip().startswith("#"):
        start -= 1
    return start


def symbol_at(tree, line):
    """Определение верхнего уровня, внутри которого строка (с 1)."""
    for node in tree.body:
        if isinstance(node, DEFINITIONS):
            first = min([node.lineno] + [d.lineno for d in node.decorator_list])
            if first <= line <= node.end_lineno:
                return node
    return None


def can_move(text, line):
    """Есть ли под курсором что перенести (для лампочки и меню) — без исключений."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return False
    node = symbol_at(tree, line)
    others = [n for n in tree.body if n is not node and not isinstance(n, (ast.Import, ast.ImportFrom))
              and not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]
    return node is not None and bool(others)


def _import_text(node, names):
    """Тот же импорт, но только с нужными именами."""
    parts = []
    for a in node.names:
        bound = (a.asname or a.name).split(".")[0]
        if bound in names:
            parts.append(a.name + (" as " + a.asname if a.asname else ""))
    if not parts:
        return None
    if isinstance(node, ast.Import):
        return "import " + ", ".join(parts)
    return "from %s%s import %s" % ("." * node.level, node.module or "", ", ".join(parts))


def _import_insert_line(tree, lines):
    """После последнего импорта верхнего уровня (или после docstring / __future__)."""
    after = 0
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            after = node.end_lineno
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and node is tree.body[0]:
            after = node.end_lineno
        else:
            break
    return after


def _collapse_blank(text):
    return re.sub(r"\n{4,}", "\n\n\n", text)


def move_symbol_plan(text, line, path):
    """План переноса: {"name", "file_name", "new_path", "new_text", "rest_text", "module", "new_module",
    "relative", "circular"} или MoveError с понятным текстом."""
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        raise MoveError(tr("refactor.python_syntax") % (e.lineno or 0)) from e
    node = symbol_at(tree, line)
    if node is None:
        raise MoveError(tr("refactor.python_no_symbol"))
    if not can_move(text, line):
        raise MoveError(tr("refactor.python_alone") % node.name)
    folder = os.path.dirname(path)
    module = os.path.splitext(os.path.basename(path))[0]
    new_module = snake_case(node.name) if isinstance(node, ast.ClassDef) else node.name
    if new_module == module:
        new_module += "_" + ("class" if isinstance(node, ast.ClassDef) else "func")
    new_path = os.path.join(folder, new_module + ".py")
    if os.path.exists(new_path):
        raise MoveError(tr("refactor.python_exists") % os.path.basename(new_path))
    relative = os.path.isfile(os.path.join(folder, "__init__.py"))

    lines = text.split("\n")
    start, end = _first_line(node, lines), node.end_lineno
    moved = "\n".join(lines[start:end])

    # что перенесённый код берёт из модуля: импорты — копируются, остальное — импорт из старого модуля
    used = _used_names(node) - {node.name}
    for decorator in node.decorator_list:
        used |= _used_names(decorator)
    head, from_old = [], []
    for other in tree.body:
        if other is node:
            continue
        bound = set(_bound_names(other)) & used
        if not bound:
            if isinstance(other, ast.ImportFrom) and other.module == "__future__":
                head.append(ast.get_source_segment(text, other))
            continue
        if isinstance(other, (ast.Import, ast.ImportFrom)):
            line_text = _import_text(other, bound)
            if line_text and line_text not in head:
                head.append(line_text)
        else:
            from_old.extend(sorted(bound - set(from_old)))
    prefix = "." if relative else ""
    if from_old:
        head.append("from %s%s import %s" % (prefix, module, ", ".join(from_old)))
    new_text = ("\n".join(head) + "\n\n\n" if head else "") + moved.rstrip() + "\n"

    # старый файл: без кода; если имя ещё нужно — импорт из нового модуля
    rest_lines = lines[:start] + lines[end:]
    rest = _collapse_blank("\n".join(rest_lines))
    try:
        rest_tree = ast.parse(rest)
    except SyntaxError:
        rest_tree = None
    still_used = rest_tree is not None and node.name in _used_names(rest_tree)
    if still_used:
        import_line = "from %s%s import %s" % (prefix, new_module, node.name)
        rest_lines = rest.split("\n")
        at = _import_insert_line(rest_tree, rest_lines)
        rest_lines.insert(at, import_line)
        rest = "\n".join(rest_lines)
    return {"name": node.name, "file_name": os.path.basename(new_path), "new_path": new_path,
            "new_text": new_text, "rest_text": rest, "module": module, "new_module": new_module,
            "relative": relative, "folder": folder,
            "circular": bool(from_old) and still_used}       # оба модуля импортируют друг друга на верхнем уровне


def update_importers(plan, files):
    """Другие файлы с `from <старый модуль> import Имя` → импорт Имени из нового модуля.
    files: {путь: текст}. Возвращает {путь: новый текст} — только изменённые."""
    name, module, new_module = plan["name"], plan["module"], plan["new_module"]
    out = {}
    for path, text in files.items():
        if os.path.abspath(path) in (os.path.abspath(plan["new_path"]), os.path.join(plan["folder"], module + ".py")):
            continue
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            continue
        edits = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            parts = node.module.split(".")
            if parts[-1] != module or not any(a.name == name for a in node.names):
                continue
            if node.level and os.path.abspath(os.path.join(os.path.dirname(path), *[".."] * (node.level - 1),
                                                           *parts[:-1])) != os.path.abspath(plan["folder"]):
                continue                                    # одноимённый модуль из другой папки
            if not node.level and len(parts) == 1 and os.path.dirname(os.path.abspath(path)) != plan["folder"]:
                continue
            new_mod = ".".join(parts[:-1] + [new_module])
            dots = "." * node.level
            moved = [a for a in node.names if a.name == name]
            kept = [a for a in node.names if a.name != name]
            alias = lambda a: a.name + (" as " + a.asname if a.asname else "")     # noqa: E731
            indent = " " * node.col_offset
            replacement = ""
            if kept:
                replacement = "from %s%s import %s\n%s" % (dots, node.module, ", ".join(map(alias, kept)), indent)
            replacement += "from %s%s import %s" % (dots, new_mod, ", ".join(map(alias, moved)))
            edits.append((node.lineno, node.col_offset, node.end_lineno, node.end_col_offset, replacement))
        if edits:
            out[path] = _apply(text, edits)
    return out


def _apply(text, edits):
    lines = text.split("\n")
    offsets = [0]
    for ln in lines:
        offsets.append(offsets[-1] + len(ln) + 1)

    def at(line, col):        # col из ast — в байтах UTF-8
        row = lines[line - 1]
        return offsets[line - 1] + len(row.encode("utf-8")[:col].decode("utf-8", "ignore"))
    for l1, c1, l2, c2, new in sorted(edits, reverse=True):
        a, b = at(l1, c1), at(l2, c2)
        text = text[:a] + new + text[b:]
    return text
