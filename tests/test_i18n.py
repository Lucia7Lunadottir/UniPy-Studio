import ast
import json
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from studio import i18n


class LocalizationTests(unittest.TestCase):
    def test_catalogs_cover_source_and_preserve_format_fields(self):
        folder = Path(i18n.__file__).with_name("locales")
        languages = set("en ru de fr es pt pl uk zh zh_TW ja ko".split())
        self.assertEqual({path.stem for path in folder.glob("*.json")}, languages)
        source = json.loads((folder / "ru.json").read_text(encoding="utf-8"))
        fields = re.compile(r"%[sdg]|\{name\}")
        calls = set()
        for path in [folder.parent.parent / "main.py", *folder.parent.glob("*.py")]:
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                    continue
                if node.func.id in ("tr", "plural", "bind_text"):
                    args = node.args if node.func.id == "tr" else node.args[1:]
                    if node.func.id == "bind_text":
                        args = args[:1]
                    calls.update(arg.value for arg in args if isinstance(arg, ast.Constant)
                                 and isinstance(arg.value, str) and re.search("[А-Яа-яЁё]", arg.value))
        self.assertLessEqual(calls, source.keys())
        for language in languages:
            with self.subTest(language=language):
                catalog = json.loads((folder / f"{language}.json").read_text(encoding="utf-8"))
                self.assertEqual(catalog.keys(), source.keys())
                for message, translation in catalog.items():
                    self.assertTrue(translation.strip(), message)
                    self.assertEqual(fields.findall(translation), fields.findall(message), message)

    def test_fallback_chain(self):
        catalogs = {
            "zh_TW": {"Сохранить": "儲存"},
            "zh": {"Открыть": "打开"},
            "en": {"Закрыть": "Close"},
        }
        with patch.object(i18n, "LANG", "zh_TW"), patch.object(i18n, "_load", catalogs.get):
            self.assertEqual(i18n.tr("Сохранить"), "儲存")
            self.assertEqual(i18n.tr("Открыть"), "打开")
            self.assertEqual(i18n.tr("Закрыть"), "Close")
            self.assertEqual(i18n.tr("Файл"), "Файл")


if __name__ == "__main__":
    unittest.main()
