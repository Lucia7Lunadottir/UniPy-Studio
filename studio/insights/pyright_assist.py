"""Подсказки в коде для Python через pyright — без jedi.

pyright не отдаёт семантические токены и подсказки в строке, но знает всё нужное по отдельным вопросам:
что за имя в `from x import y` (hover: «(class) QLabel»), какие параметры у вызова (signatureHelp),
какого типа переменная (hover: «(variable) cfg: Config»). Где спрашивать — решает ast в лёгком потоке
(ast_worker, python_semantic, python_inlay). Так jedi со своими модулями PyQt6 и подпроцессом (сотни МБ)
не запускается, пока работает pyright.
"""
import re

from PyQt6 import sip

from . import python_inlay, python_semantic
from .ast_worker import worker
from .positions import Lines

HOVER_ROLES = {"class": "type", "type": "type", "type alias": "type", "function": "function", "method": "function",
               "module": "namespace", "variable": "local", "constant": "local", "property": "member"}


def hover_line(result):
    """Первая строка кода из ответа hover pyright: «(class) QLabel», «(variable) x: int»."""
    contents = (result or {}).get("contents") if isinstance(result, dict) else None
    if isinstance(contents, dict):
        text = contents.get("value", "")
    elif isinstance(contents, list):
        text = "\n".join(c.get("value", "") if isinstance(c, dict) else str(c) for c in contents)
    else:
        text = contents or ""
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("```"):
            return line
    return ""


def hover_role(result):
    m = re.match(r"\(([\w ]+)\)", hover_line(result))
    return HOVER_ROLES.get(m.group(1)) if m else None


def hover_type(result, name):
    """«(variable) cfg: Config» → «Config»; неизвестный тип — None."""
    m = re.match(r"\((?:variable|constant)\)\s+%s:\s*(.+)$" % re.escape(name), hover_line(result))
    if not m:
        return None
    t = m.group(1).strip()
    return None if not t or t in ("Unknown", "None") or "Unknown" in t else t


def signature_params(result):
    """Ответ signatureHelp → [(имя, только позиционный, *args/**kwargs)] — как у jedi в JediWorker."""
    sigs = (result or {}).get("signatures") or []
    if not sigs:
        return []
    sig = sigs[min(result.get("activeSignature") or 0, len(sigs) - 1)]
    label = sig.get("label", "")
    raw = []
    for p in sig.get("parameters") or []:
        lab = p.get("label", "")
        if isinstance(lab, list) and len(lab) == 2:
            lab = label[lab[0]:lab[1]]
        raw.append(re.split(r"[:=]", str(lab), maxsplit=1)[0].strip())
    slash = raw.index("/") if "/" in raw else -1
    out = []
    for i, name in enumerate(raw):
        if name in ("/", "*"):
            continue
        out.append((name.lstrip("*"), i < slash, name.startswith("*")))
    return out


def _semantic_job(code, kinds):
    return python_semantic.analyze(code, kinds), python_semantic.from_imports(code)


def _inlay_job(code, first, last, params, types):
    sites = python_inlay.call_sites(code, first, last) if params else []
    assigns = [a for a in python_inlay.assignments(code, first, last) if not a.obvious] if types else []
    return sites, assigns


class PyrightAssist:
    """Один на контроллер подсказок файла. done(data) — тот же формат, что у jedi (JediWorker)."""

    def __init__(self, controller):
        self.ctl = controller
        self.kinds = {}                 # (модуль, имя) → роль (или None — pyright не знает)

    def _alive(self):
        return not sip.isdeleted(self.ctl)

    def _position(self, line0, col16):
        return {"textDocument": {"uri": _uri(self.ctl.editor.path)}, "position": {"line": line0, "character": col16}}

    # --- семантика
    def semantic(self, srv, done):
        ed = self.ctl.editor
        code, path = ed.toPlainText(), ed.path
        lines = Lines(code)

        def analyzed(result, asked=False):
            if result is None or not self._alive():
                return
            tokens, imports = result
            if tokens is not None:
                done({"tokens": tokens})
            missing = [(m, n, ln, cl) for m, n, ln, cl in imports if (m, n) not in self.kinds]
            if asked or not missing:
                return
            pending = [len(missing)]

            def got(key, reply):
                self.kinds[key] = hover_role(reply)
                pending[0] -= 1
                if pending[0] == 0 and self._alive() and any(self.kinds.get((m, n)) for m, n, _l, _c in missing):
                    worker().submit(("semantic", id(self)), _semantic_job, (code, self._known()),
                                    lambda r: analyzed(r, asked=True))
            for module, name, ln, cl in missing:
                params = self._position(ln - 1, lines.utf16_from_chars(ln - 1, cl))
                srv.query(path, code, "textDocument/hover", params, lambda r, k=(module, name): got(k, r))
        worker().submit(("semantic", id(self)), _semantic_job, (code, self._known()), analyzed)

    def _known(self):
        return {k: v for k, v in self.kinds.items() if v}

    # --- подсказки в строке
    def inlay(self, srv, first, last, params, types, done):
        ed = self.ctl.editor
        code, path = ed.toPlainText(), ed.path
        lines = Lines(code)

        def found(result):
            if result is None or not self._alive():
                return
            sites, assigns = result
            hints = []
            by_callee = {}
            for site in sites:
                by_callee.setdefault(site.callee, []).append(site)
            pending = [len(by_callee) + len(assigns)]
            if not pending[0]:
                done({"hints": []})
                return

            def finish():
                pending[0] -= 1
                if pending[0] == 0 and self._alive():
                    done({"hints": hints})

            def signature(group, reply):
                names = signature_params(reply)
                for site in group:
                    for index, line0, col16, text in site.args:
                        if index >= len(names):
                            break
                        name, positional_only, star = names[index]
                        if star:
                            break
                        if not positional_only and python_inlay.needs_param_hint(name, text):
                            hints.append((line0, col16, name + ":", "parameter"))
                finish()

            def var_type(a, reply):
                t = hover_type(reply, a.name)
                if t:
                    hints.append((a.line0, a.end16, ": " + t, "type"))
                finish()
            batch = ("inlay", id(self))
            srv.cancel_queries(batch)       # прошлая прокрутка/правка: её ответы уже не нужны
            for group in by_callee.values():
                first_site = group[0]
                pos = self._position(first_site.line - 1, lines.utf16_from_chars(first_site.line - 1, first_site.col))
                srv.query(path, code, "textDocument/signatureHelp", pos, lambda r, g=group: signature(g, r), batch)
            for a in assigns:
                pos = self._position(a.line0, lines.utf16_from_chars(a.line0, a.col))
                srv.query(path, code, "textDocument/hover", pos, lambda r, x=a: var_type(x, r), batch)
        worker().submit(("inlay", id(self)), _inlay_job, (code, first, last, params, types), found)


def _uri(path):
    from ..lsp import path_to_uri
    return path_to_uri(path)
