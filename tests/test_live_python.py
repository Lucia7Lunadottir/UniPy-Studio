"""Живые сценарии Python на настоящем pyright с настройками UniPy (профиль под PyQt6) — см. tests/live/harness.py.
В обе стороны: помеченные ошибки должны появиться нужного вида, на чистом коде PyQt6 — ничего."""
import unittest

from tests.live.harness import LivePythonProject, expectations, python_skip_reason

SKIP = python_skip_reason()


@unittest.skipIf(SKIP, SKIP or "")
class LivePython(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.live = LivePythonProject()
        try:
            cls.live.start()
        except Exception:
            cls.live.stop()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.live.stop()

    def tearDown(self):
        crashes, self.live.crashes[:] = list(self.live.crashes), []
        self.assertEqual(crashes, [], "исключения в обработчиках")

    def test_clean_pyqt_code_has_no_errors_or_warnings(self):
        items = self.live.diagnostics_for("app/window.py")
        self.assertEqual([d for d in items if d[3] in ("error", "warning")], [])

    def test_planted_problems_have_expected_kind(self):
        rel = "app/broken.py"
        text = self.live.read(rel)
        expected = expectations(text)
        got = self.live.by_line(self.live.diagnostics_for(rel, text))
        for line, kinds in expected.items():
            with self.subTest(line=line, kinds=kinds):
                self.assertEqual(got.get(line, set()), kinds, "строка %d" % line)
        self.assertEqual({l: k for l, k in got.items() if l not in expected}, {}, "лишнее на строках без пометки")

    def test_error_goes_away_after_fix(self):
        rel = "app/broken.py"
        text = self.live.read(rel)
        fixed = text.replace("self.titel", "self.title")
        line = text[:text.index("self.titel")].count("\n") + 1
        self.assertNotIn(line, self.live.by_line(self.live.diagnostics_for(rel, fixed)))


if __name__ == "__main__":
    unittest.main()
