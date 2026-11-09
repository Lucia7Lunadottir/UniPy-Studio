"""Реестр языков: всё, что редактор знает о языке, — в одном месте.

Чтобы добавить язык, достаточно записи Language(...) в LANGUAGES:
  • расширения файлов, знак комментария, отступы (после «{» или после «:»);
  • подсветка — своя (builtin_highlighter) или Pygments (сотни языков; лексер по имени файла
    или явно в pygments_lexer);
  • анализатор кода — service: ключ в SERVICE_FACTORIES (языковой сервер из lsp.py).
Файлы, которых нет в реестре, тоже подсвечиваются, если Pygments знает их язык.
"""
import os
from dataclasses import dataclass
from .i18n import tr

try:    # необязательно: без Pygments языки без своей подсветки показываются простым текстом
    from pygments.lexers import get_lexer_by_name, get_lexer_for_filename
    from pygments.util import ClassNotFound
    PYGMENTS_AVAILABLE = True
except ImportError:
    PYGMENTS_AVAILABLE = False


@dataclass(frozen=True)
class Language:
    id: str
    name: str
    extensions: tuple = ()
    line_comment: str = ""
    braces: bool = False            # блоки в { }: отступ после «{», «}» выравнивается по открывающей
    colon_blocks: bool = False      # блоки после «:» (Python)
    builtin_highlighter: bool = False
    pygments_lexer: str = ""        # имя лексера Pygments; пусто — по имени файла
    service: str = ""               # анализатор кода: ключ в SERVICE_FACTORIES

    def make_lexer(self, path):
        """Лексер Pygments для файла или None. Без обрезки переводов строк — позиции должны совпадать."""
        if not PYGMENTS_AVAILABLE:
            return None
        opts = {"stripnl": False, "ensurenl": False}
        try:
            if self.pygments_lexer:
                return get_lexer_by_name(self.pygments_lexer, **opts)
            return get_lexer_for_filename(os.path.basename(path), **opts)
        except ClassNotFound:
            return None


LANGUAGES = (
    Language("python", "Python", (".py", ".pyw", ".pyi"), "#", colon_blocks=True,
             builtin_highlighter=True, service="python"),
    Language("csharp", "C#", (".cs",), "//", braces=True, builtin_highlighter=True, service="csharp"),
    Language("xml", "XML", (".ui", ".xml", ".uxml", ".qrc", ".csproj", ".props", ".targets"),
             builtin_highlighter=True),
    Language("json", "JSON", (".json", ".asmdef", ".asmref", ".inputactions"), "//", braces=True,
             builtin_highlighter=True),
    Language("markdown", "Markdown", (".md",), builtin_highlighter=True),
    # шейдеры Unity: .shader — ShaderLab с HLSL внутри; Pygments красит его как HLSL
    Language("hlsl", "HLSL", (".hlsl", ".hlsli", ".cginc", ".compute", ".shader", ".fx"), "//", braces=True,
             pygments_lexer="hlsl"),
    Language("glsl", "GLSL", (".glsl", ".vert", ".frag", ".geom"), "//", braces=True, pygments_lexer="glsl"),
    Language("css", "USS / CSS", (".uss", ".css", ".qss"), braces=True, pygments_lexer="css"),
    Language("cpp", "C++", (".cpp", ".cc", ".cxx", ".hpp", ".hh", ".h", ".c"), "//", braces=True),
    Language("javascript", "JavaScript", (".js", ".mjs", ".cjs"), "//", braces=True),
    Language("typescript", "TypeScript", (".ts", ".tsx"), "//", braces=True),
    Language("html", "HTML", (".html", ".htm")),
    Language("rust", "Rust", (".rs",), "//", braces=True),
    Language("shell", "Shell", (".sh", ".bash"), "#"),
    Language("toml", "TOML", (".toml",), "#"),
    Language("yaml", "YAML", (".yml", ".yaml"), "#"),
    Language("ini", "INI", (".ini", ".cfg", ".desktop"), "#"),
)
PLAIN = Language("plain", tr("common.text"))

BY_ID = {lang.id: lang for lang in LANGUAGES}
BY_EXT = {ext: lang for lang in LANGUAGES for ext in lang.extensions}
BY_NAME = {"PKGBUILD": BY_ID["shell"], "CMakeLists.txt": Language("cmake", "CMake", (), "#")}


def for_path(path):
    """Язык файла: по реестру, иначе — любой, который знает Pygments, иначе простой текст."""
    base = os.path.basename(path)
    if base in BY_NAME:
        return BY_NAME[base]
    lang = BY_EXT.get(os.path.splitext(base)[1].lower())
    if lang is not None:
        return lang
    if PYGMENTS_AVAILABLE:
        try:
            lexer = get_lexer_for_filename(base)
            return Language("other", lexer.name, (), "#" if "#" in getattr(lexer, "aliases", ()) else "")
        except ClassNotFound:
            pass
    return PLAIN


def all_extensions():
    return {ext for lang in LANGUAGES for ext in lang.extensions}


# ---------------------------------------------------------------- анализаторы кода
def _csharp_service(window):
    from . import lsp
    return lsp.CSharpService(window)


def _python_service(window):
    from . import lsp
    import sys
    return lsp.PythonService(lambda: window.interp.currentData() or sys.executable, window)


SERVICE_FACTORIES = {"csharp": _csharp_service, "python": _python_service}
