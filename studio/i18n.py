"""Перевод интерфейса на 12 языков Equestria OS.

Строки в коде пишутся по-русски и оборачиваются в tr():
    tr("Сохранить")                 → "Save" / "Speichern" / ...
    tr("Сохранено файлов: %d") % n  → подстановка как обычно

Переводы — studio/locales/<язык>.json: {"Сохранить": "Save", ...}.
Нет перевода на нужный язык → английский → исходная русская строка.

Язык определяет equestria-qt-kit (EQUESTRIA_LANG, затем LANGUAGE / LC_ALL /
LC_MESSAGES / LANG). Kit необязателен: без него — та же логика здесь.
"""
import json
import os
from functools import lru_cache
from pathlib import Path

try:
    from equestria_kit.i18n import detect_language
except ModuleNotFoundError as error:
    if error.name != "equestria_kit":
        raise
    _LANGS = ("en", "ru", "de", "fr", "es", "pt", "pl", "uk", "zh", "zh_TW", "ja", "ko")
    _TRADITIONAL = ("zh_tw", "zh_hk", "zh_mo", "zh_hant", "zh-tw", "zh-hk", "zh-mo", "zh-hant")

    def _normalize(locale_name):
        # zh_TW нельзя обрезать до «zh»: получился бы упрощённый китайский
        name = (locale_name or "").split(":")[0].split(".")[0].split("@")[0].strip().lower()
        if name.startswith(_TRADITIONAL):
            return "zh_TW"
        code = name[:2]
        return code if code in _LANGS else ""

    def detect_language():
        for env in ("EQUESTRIA_LANG", "LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
            for part in (os.environ.get(env) or "").split(":"):
                code = _normalize(part)
                if code:
                    return code
        return "en"

LANGUAGE_NAMES = {
    "en": "English", "ru": "Русский", "de": "Deutsch", "fr": "Français",
    "es": "Español", "pt": "Português", "pl": "Polski", "uk": "Українська",
    "zh": "简体中文", "zh_TW": "繁體中文", "ja": "日本語", "ko": "한국어",
}
LANGUAGE_SETTING = "interface/language"
SOURCE_LANG = "ru"


def selected_language():
    try:
        from PyQt6.QtCore import QSettings
    except ImportError:
        return ""
    code = QSettings("UniPyStudio", "UniPyStudio").value(LANGUAGE_SETTING, "", type=str)
    return code if code in LANGUAGE_NAMES else ""


LANG = selected_language() or detect_language()


@lru_cache(maxsize=None)
def _load(lang):
    path = Path(__file__).with_name("locales") / f"{lang}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def tr(text):
    translation = _load(LANG).get(text)
    if LANG == SOURCE_LANG:
        return translation or text
    if not translation and LANG == "zh_TW":
        translation = _load("zh").get(text)
    return translation or _load("en").get(text) or text


def bind_text(obj, message, setter="setText"):
    bindings = getattr(obj, "_localized_text", None)
    if bindings is None:
        bindings = obj._localized_text = {}
    bindings[setter] = message
    getattr(obj, setter)(tr(message))
    return obj


def refresh_text(root):
    from PyQt6.QtCore import QObject

    for obj in (root, *root.findChildren(QObject)):
        for setter, message in getattr(obj, "_localized_text", {}).items():
            getattr(obj, setter)(tr(message))


def change_language(code, app):
    global LANG
    LANG = code or detect_language()
    install_qt_translation(app)


def install_qt_translation(app):
    from PyQt6.QtCore import QLibraryInfo, QLocale, QTranslator

    locale = QLocale({"zh": "zh_CN", "pt": "pt_BR"}.get(LANG, LANG))
    path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    old = getattr(app, "_locale_translator", None)
    if old is not None:
        app.removeTranslator(old)
        old.deleteLater()
    translator = app._locale_translator = QTranslator(app)
    if translator.load(locale, "qtbase", "_", path):
        app.installTranslator(translator)


def plural(n, one, few, many):
    """Форма слова для числа n; формы передаются русские и переводятся здесь."""
    if LANG in ("ru", "uk"):
        if n % 10 == 1 and n % 100 != 11:
            return tr(one)
        if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
            return tr(few)
        return tr(many)
    if LANG == "pl":
        if n == 1:
            return tr(one)
        return tr(few) if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else tr(many)
    return tr(one) if n == 1 or (LANG in ("fr", "pt") and n == 0) else tr(many)
