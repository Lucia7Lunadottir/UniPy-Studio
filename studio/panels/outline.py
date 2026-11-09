"""Панель «Структура» и запасной разбор текста (outline), пока анализатор не готов."""
import ast
import os
import re
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QTreeWidget, QTreeWidgetItem, QTreeWidgetItemIterator, QLabel
from .. import theme, unity_api
from ..i18n import tr
from .common import ROLE_LINE


# ====================================================================== структура файла
class OutlinePanel(QWidget):
    goto = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel(tr("panels.no_open_file"))
        self.path = None
        self.title.setObjectName("hint")
        lay.addWidget(self.title)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(14)
        self.tree.itemClicked.connect(lambda it, _: self.goto.emit(it.data(0, ROLE_LINE) or 1))
        lay.addWidget(self.tree, 1)

    def retranslate(self):
        if not self.path:
            self.title.setText(tr("panels.no_open_file"))
        items = QTreeWidgetItemIterator(self.tree)
        while (item := items.value()) is not None:
            item.setToolTip(0, tr("panels.line_2") % item.data(0, ROLE_LINE))
            items += 1

    def update_for(self, path, text, lang):
        """Запасной путь без анализатора: разбор текста (outline)."""
        from .. import symbols
        self.show_symbols(path, symbols.from_outline(outline(text, lang), path=path) if path else [])

    def show_symbols(self, path, items):
        """Дерево символов (symbols.Symbol): от анализатора (documentSymbol) или из разбора текста.
        Раскрытые узлы и прокрутка сохраняются, если файл тот же."""
        same = path == self.path
        collapsed = set()
        if same:
            it = QTreeWidgetItemIterator(self.tree)
            while (item := it.value()) is not None:
                if item.childCount() and not item.isExpanded():
                    collapsed.add((item.text(0), item.data(0, ROLE_LINE)))
                it += 1
        scroll = self.tree.verticalScrollBar().value()
        self.path = path
        self.tree.clear()
        if not path:
            self.title.setText(tr("panels.no_open_file"))
            return
        self.title.setText("  " + os.path.basename(path))

        def add(parent, sym):
            it = QTreeWidgetItem([sym.name])
            it.setIcon(0, theme.kind_icon(sym.kind))
            it.setData(0, ROLE_LINE, sym.start)
            it.setToolTip(0, (sym.detail + "\n" if sym.detail else "") + tr("panels.line_2") % sym.start)
            (parent.addChild if parent is not None else self.tree.addTopLevelItem)(it)
            for ch in sym.children:
                if ch.kind not in ("variable",):        # локальные переменные в структуре не нужны
                    add(it, ch)
            it.setExpanded((sym.name, sym.start) not in collapsed)
        for sym in items:
            add(None, sym)
        if same:
            self.tree.verticalScrollBar().setValue(scroll)


def outline(text, lang):
    out = []
    if lang == "python":
        try:
            tree = ast.parse(text)

            def walk(node, depth):
                for ch in ast.iter_child_nodes(node):
                    if isinstance(ch, ast.ClassDef):
                        out.append((depth, "class", ch.name, ch.lineno))
                        walk(ch, depth + 1)
                    elif isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        args = [a.arg for a in ch.args.args if a.arg not in ("self", "cls")]
                        out.append((depth, "method" if depth else "function",
                                    "%s(%s)" % (ch.name, ", ".join(args)), ch.lineno))
            walk(tree, 0)
            return out
        except SyntaxError:
            pass
        for no, line in enumerate(text.splitlines(), 1):
            m = re.match(r"(\s*)(class|def)\s+(\w+)", line)
            if m:
                out.append((len(m.group(1)) // 4, "class" if m.group(2) == "class" else "function", m.group(3), no))
    elif lang == "csharp":
        depth = 0
        for no, line in enumerate(text.splitlines(), 1):
            s = line.strip()
            m = re.match(r"(?:\[.*?\]\s*)*(?:(?:public|private|protected|internal|static|sealed|abstract|partial)\s+)*(class|struct|interface|enum)\s+(\w+)", s)
            if m:
                out.append((depth, "class" if m.group(1) != "enum" else "enum", m.group(2), no))
            else:
                m = re.match(r"(?:(?:public|private|protected|internal|static|override|virtual|async|abstract)\s+)*"
                             r"([\w<>\[\],]+)\s+(\w+)\s*\(([^)]*)\)?\s*(?:\{.*)?$", s)
                if m and m.group(1) not in ("new", "return", "else", "if", "while", "for", "foreach", "switch", "using") and depth >= 1:
                    kind = "event" if m.group(2) in {x[0] for x in unity_api.MESSAGES} else "method"
                    out.append((depth, kind, "%s()" % m.group(2), no))
                else:
                    m = re.match(r"(?:\[.*?\]\s*)*(?:(?:public|private|protected|internal|static|readonly|const)\s+)+([\w<>\[\],]+)\s+(\w+)\s*(?:=|;|\{)", s)
                    if m and depth >= 1:
                        out.append((depth, "field", "%s : %s" % (m.group(2), m.group(1)), no))
            depth += line.count("{") - line.count("}")
            depth = max(depth, 0)
    elif lang == "xml":
        for no, line in enumerate(text.splitlines(), 1):
            m = re.search(r'<widget class="(\w+)" name="(\w+)"', line)
            if m:
                out.append((0, "class", "%s : %s" % (m.group(2), m.group(1)), no))
    elif lang == "markdown":
        for no, line in enumerate(text.splitlines(), 1):
            m = re.match(r"(#+)\s+(.*)", line)
            if m:
                out.append((len(m.group(1)) - 1, "field", m.group(2), no))
    return out
