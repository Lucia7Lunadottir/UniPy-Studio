"""Перевод интерфейса: языки берутся из файлов studio/locales/<код>.json.

В коде — смысловые ключи «модуль.смысл», тексты — в файлах переводов:
    tr("mainwindow.save")                → "Save" / "Сохранить" / "Speichern" / ...
    tr("mainwindow.saved_files") % n     → подстановка как обычно

Переводы — studio/locales/<код>.json: {"mainwindow.save": "Save", ...}; образец для
переводчиков — en.json. Нет перевода на нужный язык → английский → сам ключ.

Новый язык: положить файл locales/<код>.json (например it.json, pt_BR.json) — и он сам появится
в меню «Вид → Язык». Название в меню берётся из ключа "__name__" внутри файла:
    {"__name__": "Italiano", "mainwindow.save": "Salva", ...}
Без этого ключа — родное название языка от Qt (QLocale), иначе код. Списка языков в коде нет.

Язык системы: EQUESTRIA_LANG, затем LANGUAGE / LC_ALL / LC_MESSAGES / LANG — первый, для которого
есть файл в locales/ (так и новый файл подхватывается сам, без правки кода).
"""
import json
import os
import re
from functools import lru_cache
from pathlib import Path

LOCALES_DIR = Path(__file__).with_name("locales")

_NAME_KEY = re.compile(r'"__name__"\s*:\s*"((?:[^"\\]|\\.)*)"')


def _discover():
    """{код: название} по файлам locales/*.json. Файлы не разбираются целиком (и не копятся в памяти):
    из каждого берётся только название."""
    found = {}
    try:
        files = sorted(LOCALES_DIR.glob("*.json"))
    except OSError:
        files = []
    for path in files:
        name = ""
        try:
            match = _NAME_KEY.search(path.read_text(encoding="utf-8"))
            if match:
                name = json.loads('"%s"' % match.group(1))
        except (OSError, ValueError):
            pass
        found[path.stem] = name or _native_name(path.stem) or path.stem
    return dict(sorted(found.items())) or {"en": "English"}


def _native_name(code):
    """Родное название языка от Qt («Deutsch», «日本語»); "" — Qt его не знает."""
    try:
        from PyQt6.QtCore import QLocale
    except ImportError:
        return ""
    locale = QLocale(code)
    if locale.language() == QLocale.Language.C:
        return ""
    name = locale.nativeLanguageName()
    return name[:1].upper() + name[1:]


LANGUAGE_NAMES = _discover()

_TRADITIONAL = ("zh_tw", "zh_hk", "zh_mo", "zh_hant")


def _normalize(locale_name):
    """Код языка из «ru_RU.UTF-8» → код файла в locales/ или ""."""
    name = (locale_name or "").split(":")[0].split(".")[0].split("@")[0].strip().replace("-", "_").lower()
    by_lower = {code.lower(): code for code in LANGUAGE_NAMES}
    if name in by_lower:                         # точное совпадение: pt_BR, zh_TW…
        return by_lower[name]
    if name.startswith(_TRADITIONAL):            # zh_TW нельзя обрезать до «zh»: получился бы упрощённый
        return by_lower.get("zh_tw") or by_lower.get("zh", "")
    return by_lower.get(name.split("_")[0], "")


def detect_language():
    for env in ("EQUESTRIA_LANG", "LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        for part in (os.environ.get(env) or "").split(":"):
            code = _normalize(part)
            if code:
                return code
    return "en"

LANGUAGE_SETTING = "interface/language"


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
    path = LOCALES_DIR / f"{lang}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def tr(key):
    """Текст по ключу. Чужой текст (не ключ, например документация анализатора) возвращается как есть."""
    translation = _load(LANG).get(key)
    if not translation and "_" in LANG:               # pt_BR → pt, zh_TW → zh
        translation = _load(LANG.split("_")[0]).get(key)
    return translation or _load("en").get(key) or key


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
    """Форма слова для числа n: ключи форм one/few/many переводятся здесь."""
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
