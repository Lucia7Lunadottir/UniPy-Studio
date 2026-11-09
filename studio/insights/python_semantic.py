"""Семантическая подсветка Python без анализатора: по дереву ast и областям видимости.

Области видимости в Python лексические, поэтому имя разрешается точно: параметр, локальная переменная,
функция, класс, модуль, встроенное имя. Что такое имя, импортированное через `from x import y`, ast не знает —
это подсказывает import_kinds (jedi в фоновом потоке, intellisense.JediWorker._semantic_tokens);
без подсказки такие имена не раскрашиваются. Атрибуты раскрашиваются только у self/cls (методы и поля класса).
Без Qt; ответ — [(строка с 0, столбец UTF-16, длина UTF-16, роль)], роли — как в semantic.ROLES.
"""
import ast
import builtins

from .positions import Lines, utf16_len

TYPE, FUNCTION, PARAM, LOCAL, NAMESPACE, MEMBER = "type", "function", "param", "local", "namespace", "member"


def _builtin_kind(name):
    obj = getattr(builtins, name, None)
    if obj is None:
        return None
    if isinstance(obj, type):
        return TYPE
    return FUNCTION if callable(obj) else None


class _Scope:
    def __init__(self, node, parent, kind):
        self.node = node
        self.parent = parent
        self.kind = kind                    # "module" / "class" / "function"
        self.names = {}                     # имя → роль
        self.globals = set()
        self.members = {}                   # для класса: атрибуты self.x и методы → роль


class _Collector(ast.NodeVisitor):
    """Первый проход: какие имена связаны в каждой области (до любого использования — как в Python)."""

    def __init__(self, import_kinds):
        self.import_kinds = import_kinds or {}
        self.scopes = {}                    # узел → _Scope
        self.stack = []

    def scope(self):
        return self.stack[-1]

    def bind(self, name, role):
        sc = self.scope()
        target = sc
        if name in sc.globals:
            while target.parent is not None:
                target = target.parent
        target.names.setdefault(name, role)

    def push(self, node, kind):
        sc = _Scope(node, self.stack[-1] if self.stack else None, kind)
        self.scopes[node] = sc
        self.stack.append(sc)
        return sc

    def visit_Module(self, node):
        self.push(node, "module")
        self.generic_visit(node)
        self.stack.pop()

    def visit_ClassDef(self, node):
        self.bind(node.name, TYPE)
        for d in node.decorator_list + node.bases + [k.value for k in node.keywords]:
            self.visit(d)
        sc = self.push(node, "class")
        for stmt in node.body:
            self.visit(stmt)
        for name, role in sc.names.items():
            sc.members.setdefault(name, MEMBER if role in (LOCAL, PARAM) else role)
        self.stack.pop()

    def _function(self, node, name=None):
        if name:
            self.bind(name, FUNCTION)
        args = node.args
        for d in getattr(node, "decorator_list", []) + args.defaults + [d for d in args.kw_defaults if d]:
            self.visit(d)
        for a in (args.posonlyargs + args.args + args.kwonlyargs + [x for x in (args.vararg, args.kwarg) if x]):
            if a.annotation is not None:
                self.visit(a.annotation)
        if getattr(node, "returns", None) is not None:
            self.visit(node.returns)
        sc = self.push(node, "function")
        for a in (args.posonlyargs + args.args + args.kwonlyargs + [x for x in (args.vararg, args.kwarg) if x]):
            sc.names[a.arg] = PARAM
        body = node.body if isinstance(node.body, list) else [node.body]
        for stmt in body:
            self.visit(stmt)
        self.stack.pop()
        owner = sc.parent
        if owner is not None and owner.kind == "class":
            first = (args.posonlyargs + args.args)[:1]
            if first and first[0].arg in ("self", "cls"):
                self._self_attributes(node, first[0].arg, owner)

    @staticmethod
    def _self_attributes(func, self_name, cls_scope):
        for sub in ast.walk(func):
            if (isinstance(sub, ast.Attribute) and isinstance(sub.ctx, ast.Store)
                    and isinstance(sub.value, ast.Name) and sub.value.id == self_name):
                cls_scope.members.setdefault(sub.attr, MEMBER)

    def visit_FunctionDef(self, node):
        self._function(node, node.name)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node):
        self._function(node)

    def _comprehension(self, node):
        sc = self.push(node, "function")
        for gen in node.generators:
            self.visit(gen)
        for part in ("elt", "key", "value"):
            if getattr(node, part, None) is not None:
                self.visit(getattr(node, part))
        self.stack.pop()
        return sc

    visit_ListComp = visit_SetComp = visit_GeneratorExp = visit_DictComp = _comprehension

    def visit_Global(self, node):
        self.scope().globals.update(node.names)

    visit_Nonlocal = visit_Global

    def visit_Name(self, node):
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.bind(node.id, MEMBER if self.scope().kind == "class" else LOCAL)    # в теле класса — поле

    def visit_arg(self, node):
        pass

    def visit_ExceptHandler(self, node):
        if node.name:
            self.bind(node.name, LOCAL)
        self.generic_visit(node)

    def visit_Import(self, node):
        for alias in node.names:
            self.bind(alias.asname or alias.name.split(".")[0], NAMESPACE)

    def visit_ImportFrom(self, node):
        for alias in node.names:
            if alias.name == "*":
                continue
            role = self.import_kinds.get((node.module or "", alias.name))
            if role:
                self.bind(alias.asname or alias.name, role)

    def visit_MatchAs(self, node):
        if node.name:
            self.bind(node.name, LOCAL)
        self.generic_visit(node)

    def visit_MatchStar(self, node):
        if node.name:
            self.bind(node.name, LOCAL)


class _Emitter(ast.NodeVisitor):
    """Второй проход: каждое упоминание имени → роль по ближайшей области, где оно связано."""

    def __init__(self, scopes, lines):
        self.scopes = scopes
        self.lines = lines
        self.stack = []
        self.out = []

    def emit(self, line1, byte_col, name, role):
        if role is None:
            return
        line0 = line1 - 1
        self.out.append((line0, self.lines.utf16_from_bytes(line0, byte_col), utf16_len(name), role))

    def resolve(self, name):
        sc = self.stack[-1]
        first = True
        while sc is not None:
            # тело класса видно только коду прямо в классе, не его методам (правило Python)
            if (first or sc.kind != "class") and name in sc.names:
                return sc.names[name]
            first = False
            sc = sc.parent
        return _builtin_kind(name)

    def _enter(self, node):
        sc = self.scopes.get(node)
        if sc is not None:
            self.stack.append(sc)
        return sc

    def _leave(self, sc):
        if sc is not None:
            self.stack.pop()

    def visit_Module(self, node):
        sc = self._enter(node)
        self.generic_visit(node)
        self._leave(sc)

    def _def_name(self, node, keyword, role):
        """Имя в строке «def имя» / «class Имя» — у ast нет его столбца, ищем после ключевого слова."""
        line0 = node.lineno - 1
        text = self.lines.text(line0)
        byte_text = text.encode("utf-8")
        start = node.col_offset
        idx = byte_text.find((keyword + " ").encode(), start)
        if idx < 0:
            return
        idx += len(keyword) + 1
        while idx < len(byte_text) and byte_text[idx:idx + 1] in (b" ", b"\t"):
            idx += 1
        self.emit(node.lineno, idx, node.name, role)

    def visit_ClassDef(self, node):
        for d in node.decorator_list + node.bases + [k.value for k in node.keywords]:
            self.visit(d)
        self._def_name(node, "class", TYPE)
        sc = self._enter(node)
        for stmt in node.body:
            self.visit(stmt)
        self._leave(sc)

    def _function(self, node, keyword=None):
        args = node.args
        for d in getattr(node, "decorator_list", []) + args.defaults + [d for d in args.kw_defaults if d]:
            self.visit(d)
        params = args.posonlyargs + args.args + args.kwonlyargs + [x for x in (args.vararg, args.kwarg) if x]
        for a in params:
            if a.annotation is not None:
                self.visit(a.annotation)
        if getattr(node, "returns", None) is not None:
            self.visit(node.returns)
        if keyword:
            self._def_name(node, keyword, FUNCTION)
        sc = self._enter(node)
        for a in params:
            if a.arg not in ("self", "cls"):        # self/cls красит обычная подсветка (как ключевые слова)
                self.emit(a.lineno, a.col_offset, a.arg, PARAM)
        body = node.body if isinstance(node.body, list) else [node.body]
        for stmt in body:
            self.visit(stmt)
        self._leave(sc)

    def visit_FunctionDef(self, node):
        self._function(node, "def")

    def visit_AsyncFunctionDef(self, node):
        self._function(node, "def")

    def visit_Lambda(self, node):
        self._function(node)

    def _comprehension(self, node):
        sc = self._enter(node)
        for gen in node.generators:
            self.visit(gen)
        for part in ("elt", "key", "value"):
            if getattr(node, part, None) is not None:
                self.visit(getattr(node, part))
        self._leave(sc)

    visit_ListComp = visit_SetComp = visit_GeneratorExp = visit_DictComp = _comprehension

    def visit_Name(self, node):
        if node.id in ("self", "cls"):
            return
        self.emit(node.lineno, node.col_offset, node.id, self.resolve(node.id))

    def visit_Attribute(self, node):
        self.visit(node.value)
        if isinstance(node.value, ast.Name) and node.value.id in ("self", "cls"):
            cls = self._enclosing_class()
            role = cls.members.get(node.attr) if cls is not None else None
            if role is not None:
                end_line, end_col = node.end_lineno, node.end_col_offset
                attr_bytes = len(node.attr.encode("utf-8"))
                self.emit(end_line, end_col - attr_bytes, node.attr, role)

    def _enclosing_class(self):
        for sc in reversed(self.stack):
            if sc.kind == "class":
                return sc
        return None


def analyze(code, import_kinds=None):
    """Токены файла. import_kinds — {(модуль, имя): роль} для `from модуль import имя`."""
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return None                         # недописанный код — оставить прошлую раскраску
    collector = _Collector(import_kinds)
    collector.visit(tree)
    emitter = _Emitter(collector.scopes, Lines(code))
    emitter.visit(tree)
    return sorted(set(emitter.out))


def from_imports(code):
    """[(модуль, имя, строка с 1, столбец в символах)] из `from модуль import имя` — что спросить у jedi."""
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return []
    lines = Lines(code)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            text = lines.text(node.lineno - 1)
            for alias in node.names:
                if alias.name == "*":
                    continue
                col = text.find(alias.name, text.find(" import ") if " import " in text else 0)
                if col >= 0:
                    out.append((node.module or "", alias.name, node.lineno, col))
    return out
