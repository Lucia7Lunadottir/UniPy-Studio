"""Проверка ошибок: анализатор (LSP) и собственная быстрая проверка, пока его нет."""
import ast
import re
from ..i18n import tr


# ====================================================================== проверки
def python_warnings(tree, text):
    """Лёгкие предупреждения: неиспользуемые импорты."""
    out = []
    imported = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                imported[(a.asname or a.name).split(".")[0]] = node.lineno
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name != "*":
                    imported[a.asname or a.name] = node.lineno
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    used |= {n.value.id for n in ast.walk(tree) if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)}
    all_names = set(re.findall(r"__all__\s*=\s*\[([^\]]*)\]", text))
    for name, line in imported.items():
        if name not in used and name not in "".join(all_names):
            out.append((line, 1, tr("editor.import_not_used") % name, "warning"))
    return out


def check_csharp(text):
    """Простая проверка C#: парность скобок и незакрытые строки."""
    out = []
    stack = []
    pairs = {")": "(", "]": "[", "}": "{"}
    i, n, line, col = 0, len(text), 1, 1
    in_block = False
    while i < n:
        ch = text[i]
        if in_block:
            if text.startswith("*/", i):
                in_block = False
                i += 2
                col += 2
                continue
        elif text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            col += j - i
            i = j
            continue
        elif text.startswith("/*", i):
            in_block = True
        elif ch in "\"'":
            start_col, j = col, i + 1
            verbatim = i > 0 and text[i - 1] == "@"
            while j < n and text[j] != ch and (verbatim or text[j] != "\n"):
                j += 2 if (text[j] == "\\" and not verbatim) else 1
            if j >= n or text[j] == "\n":
                out.append((line, start_col, tr("editor.unclosed_string"), "error"))
            col += j - i + 1
            i = j + 1
            continue
        elif ch in "([{":
            stack.append((ch, line, col))
        elif ch in ")]}":
            if stack and stack[-1][0] == pairs[ch]:
                stack.pop()
            else:
                out.append((line, col, tr("editor.unexpected_closing_bracket") % ch, "error"))
        if ch == "\n":
            line += 1
            col = 1
        else:
            col += 1
        i += 1
    for ch, l, c in stack[-3:]:
        out.append((l, c, tr("editor.unclosed_bracket") % ch, "error"))
    # простое предупреждение: забытая точка с запятой
    for no, ln in enumerate(text.splitlines(), 1):
        s = ln.strip()
        if re.match(r"^(?:[\w.<>\[\]]+\s+)?[\w.\[\]]+\s*(?:[+\-*/]?=)\s*[^;{}]*[\w)\]\"']$", s) and \
                not s.startswith(("if", "for", "while", "else", "//", "[", "#", "case", "using", "namespace")) \
                and "=>" not in s and not s.endswith(","):
            out.append((no, len(ln) - len(ln.lstrip()) + 1, tr("editor.maybe_you_missed_at_end"), "warning"))
    return out


class DiagnosticsMixin:
    """Ошибки и предупреждения вкладки."""

    def run_diagnostics(self, *, sync_server=True):
        text = self.toPlainText()
        diags = []
        svc = self.services.service_for(self.language)
        if sync_server and svc is not None:
            svc.sync(self.path, text)
        if self.lang == "python":
            try:
                tree = ast.parse(text, filename=self.path)
                diags.extend(python_warnings(tree, text))
            except SyntaxError as e:
                if self.lsp_diagnostics is None:        # pyright сам сообщает о синтаксисе
                    diags.append((e.lineno or 1, e.offset or 1, tr("common.syntax_error") % e.msg, "error"))
            except Exception:
                pass
            if self.lsp_diagnostics is not None:
                diags = sorted(set(diags) | set(self.lsp_diagnostics))
        elif self.lang == "csharp":
            diags.extend(self.lsp_diagnostics if self.lsp_diagnostics is not None else check_csharp(text))
        elif self.lang == "json":
            import json
            try:
                json.loads(text) if text.strip() else None
            except json.JSONDecodeError as e:
                diags.append((e.lineno, e.colno, tr("editor.json_error") % e.msg, "error"))
        elif self.lsp_diagnostics is not None:      # языки, у которых есть только анализатор
            diags = list(self.lsp_diagnostics)
        if diags != self.diagnostics:
            self.diagnostics = diags
            self.update_selections()
            self.gutter.update()
            self.diagnostics_changed.emit()

    def set_lsp_diagnostics(self, diags):
        self.lsp_diagnostics = diags
        # Applying an LSP reply must not request another LSP diagnostic round.
        self.run_diagnostics(sync_server=False)
