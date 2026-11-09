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


FEATURES = {f.kind: f for f in (Completion(), SignatureHelp(), Hover(), References(), Rename(), Definition(),
                                Implementation())}


def text_document_capabilities():
    """Объединённые capability всех возможностей — в initialize."""
    out = {}
    for feature in FEATURES.values():
        out.update(feature.capability)
    return out
