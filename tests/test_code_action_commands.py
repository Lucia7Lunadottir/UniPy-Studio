"""Лампочка: действия Roslyn, которые приходят командой клиента, а не правкой.
«Ввести параметр для …» (nestedCodeAction) — подменю вариантов; «Исправить все: …» (fixAllCodeAction) —
выбор области и запрос codeAction/resolveFixAll. Раньше оба молча ничего не делали."""
import unittest

from studio.lsp import LanguageServer

DATA = {"UniqueIdentifier": "x", "Range": {}, "TextDocument": {"uri": "file:///a.cs"}}
CHILD = {"title": "в новую перегрузку", "kind": "refactor", "data": DATA}
NESTED = {"title": 'Ввести параметр для "_open"', "kind": "refactor", "data": DATA,
          "command": {"command": "roslyn.client.nestedCodeAction",
                      "arguments": [dict(DATA, NestedCodeActions=[CHILD, {"title": "без правки"}])]}}
FIX_ALL = {"title": "Исправить все: тело выражения", "kind": "refactor", "data": DATA,
           "command": {"command": "roslyn.client.fixAllCodeAction",
                       "arguments": [dict(DATA, FixAllFlavors=["Solution", "Document", "ContainingType"])]}}


class FakeConn:
    def __init__(self):
        self.sent = []

    def request(self, method, params, callback):
        self.sent.append((method, params))
        callback({"edit": {"changes": {}}}, None)


class CodeActionCommandsTest(unittest.TestCase):
    def test_nested_kept_with_children(self):
        self.assertEqual([a["title"] for a in LanguageServer._code_actions([NESTED])], [NESTED["title"]])
        self.assertEqual(LanguageServer.code_action_children(NESTED), [CHILD])    # пункт без правки отброшен

    def test_fix_all_scopes_in_visual_studio_order(self):
        self.assertEqual(LanguageServer.fix_all_scopes(FIX_ALL), ["Document", "ContainingType", "Solution"])
        self.assertEqual(LanguageServer.fix_all_scopes(CHILD), [])

    def test_fix_all_resolves_with_scope(self):
        srv = LanguageServer.__new__(LanguageServer)
        srv.state, srv.conn = "ready", FakeConn()
        got = []
        srv.resolve_code_action(FIX_ALL, got.append, scope="Project")
        self.assertEqual(srv.conn.sent, [("codeAction/resolveFixAll",
                                          {"title": FIX_ALL["title"], "data": DATA, "scope": "Project"})])
        self.assertEqual(got, [{"changes": {}}])


if __name__ == "__main__":
    unittest.main()
