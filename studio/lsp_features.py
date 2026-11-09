"""Возможности языкового сервера (LSP), которые запрашивает редактор: подсказки, справка, ссылки…

Одна возможность — один класс: какой метод LSP, что объявить серверу при initialize (capability),
как собрать параметры и как разобрать ответ. LanguageServer.submit(kind, …) общий для всех;
чтобы подключить новую функцию сервера, достаточно класса здесь и строки в FEATURES —
и обработки результата там, где он нужен (редактор получает {"kind", "req", "editor", "data"}).
"""
import re

from .intellisense import file_imports


class Request:
    """Что спросил редактор: текст, позиция (строка с 1, столбец в UTF-16), путь, доп. данные."""
    __slots__ = ("code", "line", "col", "path", "extra", "line_text")

    def __init__(self, code, line, col, path, extra=None):
        self.code, self.line, self.col, self.path = code, line, col, path
        self.extra = extra or {}
        lines = code.split("\n")
        self.line_text = lines[line - 1][:col] if 0 < line <= len(lines) else ""


class Feature:
    kind = ""                   # как его зовёт редактор: "complete", "hover"…
    method = ""                 # метод LSP
    capability = {}             # часть ClientCapabilities.textDocument, которую объявляем серверу

    def params(self, server, position, request):
        return position

    def convert(self, server, result, request, reply):
        """Ответ сервера → reply(data для редактора). Можно вызвать reply позже (переход в сборку)."""
        reply(result)

    def failed(self, server, error, request, reply):
        reply(None)


class Completion(Feature):
    kind, method = "complete", "textDocument/completion"
    capability = {"completion": {"completionItem": {"snippetSupport": False,
                                                    "documentationFormat": ["markdown", "plaintext"]},
                                 "contextSupport": True}}

    def convert(self, server, result, request, reply):
        prefix = re.search(r"\w*$", request.line_text).group(0)
        reply(server._completions(result, request.line_text, prefix, file_imports(request.code)))


class SignatureHelp(Feature):
    kind, method = "signatures", "textDocument/signatureHelp"
    capability = {"signatureHelp": {"signatureInformation": {
        "documentationFormat": ["markdown", "plaintext"], "parameterInformation": {"labelOffsetSupport": True}}}}

    def convert(self, server, result, request, reply):
        reply(server._signatures(result))


class Hover(Feature):
    kind, method = "hover", "textDocument/hover"
    capability = {"hover": {"contentFormat": ["markdown", "plaintext"]}}

    def convert(self, server, result, request, reply):
        reply(server._hover(result))


class References(Feature):
    kind, method = "references", "textDocument/references"

    def params(self, server, position, request):
        return dict(position, context={"includeDeclaration": True})

    def convert(self, server, result, request, reply):
        reply(server._locations(result))


class Rename(Feature):
    kind, method = "rename", "textDocument/rename"

    def params(self, server, position, request):
        return dict(position, newName=request.extra.get("new_name", ""))

    def convert(self, server, result, request, reply):
        reply(server._workspace_edit(result))

    def failed(self, server, error, request, reply):
        reply({"error": error.get("message", "")})


class Definition(Feature):
    """F12. Класс из сборки (UnityEngine.dll) сервер отдаёт по своему адресу — open_location его раскрывает."""
    kind, method = "definition", "textDocument/definition"
    capability = {"definition": {"linkSupport": True}}

    def convert(self, server, result, request, reply):
        loc = server._first_location(result)
        if not loc:
            reply(None)
            return
        server.open_location(loc[0], loc[1], lambda path, line: reply({"path": path, "line": line} if path else None))


class Implementation(Definition):
    kind, method = "implementation", "textDocument/implementation"
    capability = {"implementation": {"linkSupport": True}}


# --- подсказки в коде (insights): ответ — данные для студии/insights, не для курсора

# роли тем UniPy для типов семантических токенов (LSP SemanticTokenTypes и расширения Roslyn)
SEMANTIC_ROLES = {
    "namespace": "namespace", "class": "type", "record": "type", "recordClass": "type", "delegate": "type",
    "type": "type", "struct": "struct", "recordStruct": "struct", "interface": "interface", "enum": "enum",
    "typeParameter": "type_param", "method": "function", "function": "function", "extensionMethod": "function",
    "property": "member", "field": "member", "event": "member", "enumMember": "enum_member",
    "constant": "enum_member", "parameter": "param", "variable": "local", "local": "local",
    "excludedCode": "excluded",
}


class SemanticTokens(Feature):
    """Раскраска по смыслу (класс / интерфейс / поле / параметр…) — целиком для файла."""
    kind, method = "semantic_tokens", "textDocument/semanticTokens/full"
    capability = {"semanticTokens": {"requests": {"full": True}, "formats": ["relative"],
                                     "tokenTypes": list(SEMANTIC_ROLES), "tokenModifiers": ["static", "readonly"],
                                     "overlappingTokenSupport": False, "multilineTokenSupport": False}}

    def params(self, server, position, request):
        return {"textDocument": position["textDocument"]}

    def convert(self, server, result, request, reply):
        legend = ((server.server_capabilities or {}).get("semanticTokensProvider") or {}).get("legend") or {}
        reply({"tokens": decode_semantic_tokens((result or {}).get("data") or [], legend.get("tokenTypes") or [])})


def decode_semantic_tokens(data, token_types):
    """Пятёрки LSP (Δстрока, Δначало, длина, тип, модификаторы) → [(строка, столбец, длина, роль)];
    типы без роли (ключевые слова, строки, числа — их красит обычная подсветка) пропускаются."""
    out, line, start = [], 0, 0
    for i in range(0, len(data) - 4, 5):
        d_line, d_start, length, ttype = data[i], data[i + 1], data[i + 2], data[i + 3]
        line += d_line
        start = start + d_start if d_line == 0 else d_start
        name = token_types[ttype] if 0 <= ttype < len(token_types) else ""
        role = SEMANTIC_ROLES.get(name)
        if role:
            out.append((line, start, length, role))
    return out


class InlayHints(Feature):
    """Имена параметров и типы прямо в строке — для видимых строк extra["first"]..extra["last"] (с 0)."""
    kind, method = "inlay_hints", "textDocument/inlayHint"
    capability = {"inlayHint": {"resolveSupport": {"properties": []}}}

    def params(self, server, position, request):
        return {"textDocument": position["textDocument"],
                "range": {"start": {"line": request.extra.get("first", 0), "character": 0},
                          "end": {"line": request.extra.get("last", 0) + 1, "character": 0}}}

    def convert(self, server, result, request, reply):
        hints = []
        for h in result or []:
            label = h.get("label")
            if isinstance(label, list):
                label = "".join(part.get("value", "") for part in label)
            pos = h.get("position") or {}
            text = (" " if h.get("paddingLeft") else "") + (label or "").strip() + (" " if h.get("paddingRight") else "")
            hints.append((pos.get("line", 0), pos.get("character", 0), text.strip(),
                          "type" if h.get("kind") == 1 else "parameter"))
        reply({"hints": hints})


class ReferenceCount(Feature):
    """Сколько раз используется символ (CodeLens «Ссылок: N»); без самого объявления."""
    kind, method = "reference_count", "textDocument/references"

    def params(self, server, position, request):
        return dict(position, context={"includeDeclaration": False})

    def convert(self, server, result, request, reply):
        locations = server._locations(result)
        reply({"key": request.extra.get("key"), "count": len(result or []), "locations": locations})


FEATURES = {f.kind: f for f in (Completion(), SignatureHelp(), Hover(), References(), Rename(), Definition(),
                                Implementation(), SemanticTokens(), InlayHints(), ReferenceCount())}


def text_document_capabilities():
    """Объединённые capability всех возможностей — в initialize."""
    out = {}
    for feature in FEATURES.values():
        out.update(feature.capability)
    return out
