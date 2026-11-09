import ast
import json
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from studio import i18n

FOLDER = Path(i18n.__file__).with_name("locales")
KEY = re.compile(r"^[a-z0-9_]+\.[A-Za-z0-9_.]+$")
CYRILLIC = re.compile("[А-Яа-яЁё]")
# Русский текст в коде допустим только как содержимое шаблонов новых файлов (переводится через
# TEMPLATE_PHRASES) и как имя папки примера на диске.
CYRILLIC_ALLOWED = {"templates.py"}
CYRILLIC_ALLOWED_VALUES = {"Пример", "equestria-qt-kit старше 1.2"}
# строки вида «x.y», которые не ключи перевода: имя файла и виды действий LSP
NOT_KEYS = {"main.py", "refactor.extract", "refactor.inline", "refactor.rewrite"}


def code_files():
    # rglob: модули в подпакетах (studio/toolchains/…) тоже обязаны переводить свои строки
    return [FOLDER.parent.parent / "main.py", *sorted(FOLDER.parent.rglob("*.py"))]


def string_constants(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                  and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings]


class LocalizationTests(unittest.TestCase):
    def test_catalogs_cover_keys_and_preserve_format_fields(self):
        languages = set("en ru de fr es pt pl uk zh zh_TW ja ko".split())
        self.assertEqual({path.stem for path in FOLDER.glob("*.json")}, languages)
        source = json.loads((FOLDER / "en.json").read_text(encoding="utf-8"))
        prefixes = {key.split(".")[0] for key in source if KEY.match(key)}
        used = {n.value for path in code_files() for n in string_constants(path)
                if KEY.match(n.value) and n.value.split(".")[0] in prefixes and n.value not in NOT_KEYS}
        self.assertEqual(sorted(used - source.keys()), [])
        fields = re.compile(r"%[sdg]|\{name\}")
        for language in languages:
            with self.subTest(language=language):
                catalog = json.loads((FOLDER / f"{language}.json").read_text(encoding="utf-8"))
                self.assertEqual(catalog.keys(), source.keys())
                for key, translation in catalog.items():
                    self.assertTrue(translation.strip(), key)
                    self.assertEqual(fields.findall(translation), fields.findall(source[key]), key)

    def test_no_untranslated_russian_in_code(self):
        found = [f"{path.name}:{n.lineno}: {n.value[:50]!r}" for path in code_files()
                 if path.name not in CYRILLIC_ALLOWED for n in string_constants(path)
                 if CYRILLIC.search(n.value) and n.value not in CYRILLIC_ALLOWED_VALUES]
        self.assertEqual(found, [])

    def test_fallback_chain(self):
        catalogs = {
            "zh_TW": {"mainwindow.save": "儲存"},
            "zh": {"mainwindow.open_file": "打开"},
            "en": {"common.close": "Close"},
        }
        with patch.object(i18n, "LANG", "zh_TW"), patch.object(i18n, "_load", catalogs.get):
            self.assertEqual(i18n.tr("mainwindow.save"), "儲存")
            self.assertEqual(i18n.tr("mainwindow.open_file"), "打开")
            self.assertEqual(i18n.tr("common.close"), "Close")
            self.assertEqual(i18n.tr("missing.key"), "missing.key")
            self.assertEqual(i18n.tr("void Update()"), "void Update()")   # чужой текст — как есть


if __name__ == "__main__":
    unittest.main()
