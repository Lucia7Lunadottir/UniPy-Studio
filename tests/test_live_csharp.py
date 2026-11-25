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


if __name__ == "__main__":
    unittest.main()
