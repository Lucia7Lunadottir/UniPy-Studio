"""Редактор кода: CodeEditor собран из миксинов, по одному на ответственность (файл, поле номеров,
подсветки, лампочка, проверка ошибок, внешний вид, ввод, подсказки, переходы, мышь). Здесь — реэкспорт:
`from .editor import CodeEditor, decode_source` работает как раньше."""
from .source import ENCODING_NAME, decode_source, lang_for  # noqa: F401
from .document import DocumentMixin  # noqa: F401
from .gutter import BREAKPOINT_COLUMN, GutterMixin, LineNumberArea  # noqa: F401
from .markers import MarkersMixin  # noqa: F401
from .quick_actions import QuickActionsMixin  # noqa: F401
from .diagnostics import DiagnosticsMixin, check_csharp, python_warnings  # noqa: F401
from .appearance import AppearanceMixin  # noqa: F401
from .text_input import TypingMixin  # noqa: F401
from .completion import CSHARP, CallTip, CompletionDelegate, CompletionFooter, CompletionMixin, CompletionPopup, DEFAULT_HINT_PT, ROLE_DETAIL, ROLE_DOC, ROLE_ITEM, side_pad  # noqa: F401
from .navigation import NavigationMixin  # noqa: F401
from .hover import HoverMixin  # noqa: F401
from .code_editor import CodeEditor  # noqa: F401
