"""Живые сценарии C#/Unity на настоящем Roslyn — см. tests/live/harness.py.
Каждый — в обе стороны: заведомая ошибка/подсказка ДОЛЖНА появиться, на чистом коде — НЕ должно лишнего."""
import unittest

from tests.live.harness import LiveUnityProject, expectations, skip_reason

SKIP = skip_reason()
CLEAN = ("Assets/Scripts/Player.cs", "Assets/Scripts/Enemy.cs", "Assets/Scripts/Boss.cs")


@unittest.skipIf(SKIP, SKIP or "")
class LiveCSharp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.live = LiveUnityProject()
        try:
            cls.live.start()
        except Exception:
            cls.live.stop()
            raise

    def tearDown(self):
        crashes, self.live.crashes[:] = list(self.live.crashes), []
        self.assertEqual(crashes, [], "исключения в обработчиках редактора")
        # анализатор не должен отвечать ошибками и просить того, чего мы не умеем (раньше это было молча)
        from studio.i18n import tr
        marks = [tr("lsp.request_failed").split("%s")[1].strip(": "), tr("lsp.server_request_unsupported").split("%s")[0]]
        problems = [l for l in self.live.log if any(m and m in l for m in marks)]
        self.live.log[:] = []
        self.assertEqual(problems, [], "ошибки анализатора во время сценария")

    @classmethod
    def tearDownClass(cls):
        cls.live.stop()

    # ---------------------------------------------------------------- ошибки
    def test_clean_scripts_have_no_errors_or_warnings(self):
        for rel in CLEAN:
            with self.subTest(rel):
                items = self.live.diagnostics_for(rel)
                self.assertEqual([d for d in items if d[3] in ("error", "warning")], [],
                                 "на чистом коде не должно быть ни ошибок, ни предупреждений")

    def test_every_planted_error_is_reported_on_its_line(self):
        rel = "Assets/Scripts/Broken.cs"
        text = self.live.read(rel)
        expected = expectations(text)
        self.assertTrue(expected)
        got = self.live.by_line(self.live.diagnostics_for(rel, text))
        for line, codes in expected.items():
            with self.subTest(line=line, codes=codes):
                self.assertEqual(got.get(line, set()) & codes, codes, "ожидалось на строке %d" % line)
        unexpected = {line: codes for line, codes in got.items() if line not in expected}
        self.assertEqual(unexpected, {}, "лишние ошибки на строках без пометки")

    def test_error_appears_and_disappears_while_typing(self):
        rel = "Assets/Scripts/Enemy.cs"
        clean = self.live.read(rel)
        broken = clean.replace("player.Damage(_damage);", "player.Damage(\"ten\");")
        self.assertIn("CS1503", {c for codes in self.live.by_line(self.live.diagnostics_for(rel, broken)).values()
                                 for c in codes})
        self.assertEqual(self.live.diagnostics_for(rel, clean), [], "после исправления ошибка должна уйти")

    # ---------------------------------------------------------------- подсказки (через набор в редакторе)
    def type_in(self, rel, anchor, typed, insert=""):
        """Открыть файл в редакторе, вставить insert после anchor (без клавиш — переводы строк QTest.keyClicks
        не умеет) и набрать typed клавишами в конце вставки."""
        from PyQt6.QtTest import QTest
        text = self.live.read(rel)
        at = text.index(anchor) + len(anchor)
        text = text[:at] + insert + text[at:]
        ed = self.live.editor(rel, text)
        c = ed.textCursor()
        c.setPosition(at + len(insert))
        ed.setTextCursor(c)
        QTest.keyClicks(ed, typed)
        return ed

    def wait_popup(self, ed, seconds=10):
        self.live.wait(lambda: ed.completer.popup().isVisible() and ed._completion_items, seconds)
        return set(ed._completion_items) if ed.completer.popup().isVisible() else set()

    def accept(self, ed, name):
        ed.insert_completion(name, ed._completion_items[name])
        self.live.wait(lambda: False, 1.0)

    def test_member_completion_and_insert(self):
        ed = self.type_in("Assets/Scripts/Player.cs", "private void Update()\n    {\n", "transform.Transl", insert="        ")
        names = self.wait_popup(ed)
        self.assertIn("Translate", names)
        self.assertNotIn("AddForce", names, "у Transform нет AddForce — чужие члены подсказываться не должны")
        self.accept(ed, "Translate")
        self.assertIn("transform.Translate()", ed.toPlainText())

    def test_override_offers_and_inserts_method_with_body(self):
        rel = "Assets/Scripts/Enemy.cs"
        ed = self.type_in(rel, "private int _damage = 10;\n", "public override ", insert="\n    ")
        names = self.wait_popup(ed)
        self.assertIn("ToString()", names, "после «override » должны быть методы для переопределения")
        self.accept(ed, "ToString()")
        text = ed.toPlainText()
        self.assertIn("public override string ToString()\n    {\n        return base.ToString();\n    }", text)
        self.assertNotIn("ToString()()", text)
        caret_line = ed.textCursor().block().text()
        self.assertIn("base.ToString()", caret_line, "курсор — в теле нового метода, как в Visual Studio")
        self.assertEqual(self.live.diagnostics_for(rel, text), [], "вставленный метод должен компилироваться")

    def test_no_override_list_without_override(self):
        ed = self.type_in("Assets/Scripts/Enemy.cs", "private int _damage = 10;\n", "public void ", insert="\n    ")
        self.live.wait(lambda: False, 1.5)
        shown = set(ed._completion_items) if ed.completer.popup().isVisible() else set()
        self.assertFalse({"ToString()", "Equals(object other)", "GetHashCode()"} & shown)

    # ---------------------------------------------------------------- запросы к анализатору (как их шлёт редактор)
    def ask(self, kind, rel, anchor, inside=0, text=None, extra=None):
        """kind в позиции «anchor + inside» файла rel; ответ — как его получает редактор (data)."""
        text = self.live.read(rel) if text is None else text
        at = text.index(anchor) + inside
        line = text[:at].count("\n") + 1
        col = len(text[text.rfind("\n", 0, at) + 1:at].encode("utf-16-le")) // 2
        box = []
        handler = lambda res: res.get("editor") == 4242 and box.append(res["data"])     # noqa: E731
        self.live.server.result.connect(handler)
        try:
            self.live.server.submit(kind, req=1, editor=4242, code=text, line=line, col=col,
                                    path=self.live.path(rel), extra=extra)
            self.live.wait(lambda: box, 30)
        finally:
            self.live.server.result.disconnect(handler)
        return box[0] if box else None

    def test_go_to_definition_own_code(self):
        got = self.ask("definition", "Assets/Scripts/Enemy.cs", "player.Damage", inside=len("player.D"))
        self.assertIsNotNone(got)
        self.assertTrue(got["path"].endswith("Player.cs"))
        lines = self.live.read("Assets/Scripts/Player.cs").splitlines()
        self.assertIn("public void Damage", lines[got["line"] - 1])

    def test_go_to_definition_unity_class(self):
        got = self.ask("definition", "Assets/Scripts/Player.cs", "Rigidbody _body", inside=2)
        self.assertIsNotNone(got, "F12 в класс Unity должен открыть его исходник (из UnityEngine.dll)")
        with open(got["path"], encoding="utf-8") as f:
            self.assertIn("class Rigidbody", f.read())

    def test_no_definition_on_keyword_space(self):
        got = self.ask("definition", "Assets/Scripts/Player.cs", "    public int Health", inside=1)
        self.assertIsNone(got, "на пустом месте переходить некуда")

    def test_hover_shows_unity_doc(self):
        got = self.ask("hover", "Assets/Scripts/Player.cs", "transform.Translate", inside=len("transform.Tr"))
        self.assertTrue(got and "Translate" in str(got))

    def test_references_find_declaration_and_call(self):
        got = self.ask("references", "Assets/Scripts/Player.cs", "public void Damage", inside=len("public void D"))
        files = sorted(os_path_name(r) for r in got or [])
        self.assertEqual(files, ["Enemy.cs", "Player.cs"], "объявление + вызов; Boss.cs не должен попасть")

    def test_rename_changes_declaration_and_callers_only(self):
        got = self.ask("rename", "Assets/Scripts/Player.cs", "public void Damage", inside=len("public void D"),
                       extra={"new_name": "TakeDamage"})
        self.assertTrue(got and not got.get("error"), got)
        files = sorted(os_path_name(path) for path in got.get("edits", {}))
        self.assertEqual(files, ["Enemy.cs", "Player.cs"], "объявление + вызов; Boss.cs не трогать")

    def test_signature_help_while_typing_arguments(self):
        ed = self.type_in("Assets/Scripts/Player.cs", "private void Update()\n    {\n", "Mathf.Clamp(1, ",
                          insert="        ")
        self.live.wait(lambda: ed.calltip.isVisible() and "Clamp" in ed.calltip.text(), 10)
        tip = ed.calltip.text()
        self.assertIn("Clamp", tip)
        self.assertRegex(tip, r"<b>[^<]*<span[^>]*>min</span></b>", "второй аргумент — min, он выделен")
        from PyQt6.QtTest import QTest
        QTest.keyClicks(ed, "5)")
        self.live.wait(lambda: not ed.calltip.isVisible(), 5)
        self.assertFalse(ed.calltip.isVisible(), "после закрывающей скобки подсказка параметров должна уйти")

    # ---------------------------------------------------------------- лампочка
    def actions_at(self, rel, text, anchor, inside=0):
        at = text.index(anchor) + inside
        line = text[:at].count("\n")
        col = at - text.rfind("\n", 0, at) - 1
        self.live.diagnostics_for(rel, text)            # лампочке нужны ошибки строки
        box = []
        self.live.server.code_actions(self.live.path(rel), line, col, box.append, explicit=True)
        self.live.wait(lambda: box, 20)
        return box[0] if box else []

    def resolve(self, item, scope=None):
        box = []
        self.live.server.resolve_code_action(item, box.append, scope=scope)
        self.live.wait(lambda: box, 30)
        return box[0] if box else None

    def apply(self, rel, text, edit):
        from studio.lsp import LanguageServer, apply_text_edits
        files = LanguageServer.workspace_edit_files(edit)
        edits = files["edits"].get(self.live.path(rel)) or []
        return apply_text_edits(text, edits)

    def test_quick_fix_generates_missing_method(self):
        rel = "Assets/Scripts/Broken.cs"
        text = self.live.read(rel).replace("int count = \"text\";", "int count = 1;")
        actions = self.actions_at(rel, text, "NoSuchMethod();", inside=3)
        fix = [a for a in actions if "NoSuchMethod" in a["title"] and not self.live.server.code_action_children(a)]
        self.assertTrue(fix, [a["title"] for a in actions])
        fixed = self.apply(rel, text, self.resolve(fix[0]))
        codes = {c for codes in self.live.by_line(self.live.diagnostics_for(rel, fixed)).values() for c in codes}
        self.assertNotIn("CS0103", codes, "после «Создать метод» ошибки «имя не существует» быть не должно")

    def test_nested_and_fix_all_actions_apply(self):
        rel = "Assets/Scripts/Player.cs"
        text = self.live.read(rel)
        actions = self.actions_at(rel, text, "Mathf.Clamp(Health", inside=len("Mathf.Clamp(H"))
        nested = [a for a in actions if self.live.server.code_action_children(a)]
        self.assertTrue(nested, "ожидался вложенный пункт (подменю) — " + str([a["title"] for a in actions]))
        child = self.live.server.code_action_children(nested[0])[0]
        self.assertTrue(self.resolve(child), "вариант подменю должен давать правку")
        fix_all = [a for a in actions if self.live.server.fix_all_scopes(a)]
        for a in fix_all[:1]:
            self.assertTrue(self.resolve(a, scope="Document"), "«Исправить все» в документе должно давать правку")


def os_path_name(location):
    """Имя файла из ответа: путь, (путь, …) или {"path": …}."""
    import os
    if isinstance(location, dict):
        location = location.get("path", "")
    elif isinstance(location, (tuple, list)):
        location = location[0]
    return os.path.basename(location)


if __name__ == "__main__":
    unittest.main()
