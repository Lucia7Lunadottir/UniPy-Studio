"""CodeEditor — один открытый файл. Собран из миксинов (см. пакет editor); здесь сигналы и создание."""
import traceback
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QModelIndex
from PyQt6.QtGui import QStandardItemModel
from PyQt6.QtWidgets import QPlainTextEdit, QCompleter
from .. import languages
from ..documents import DiskState
from ..indentation import IndentStyle
from ..folding import Folding
from ..navbar import NavigationBar
from .. import theme
from .document import DocumentMixin
from .gutter import GutterMixin, LineNumberArea
from .markers import MarkersMixin
from .quick_actions import QuickActionsMixin
from .diagnostics import DiagnosticsMixin
from .appearance import AppearanceMixin
from .text_input import TypingMixin
from .completion import CallTip, CompletionDelegate, CompletionFooter, CompletionMixin, CompletionPopup
from .navigation import NavigationMixin
from .hover import HoverMixin
from .visual_aids import VisualAidsMixin


class CodeEditor(DocumentMixin, GutterMixin, MarkersMixin, QuickActionsMixin, DiagnosticsMixin, AppearanceMixin, TypingMixin, CompletionMixin, NavigationMixin, VisualAidsMixin, HoverMixin, QPlainTextEdit):
    cursor_moved = pyqtSignal(int, int)
    diagnostics_changed = pyqtSignal()
    goto_requested = pyqtSignal(str, int)
    busy_changed = pyqtSignal(str)     # текст «идёт работа» для строки состояния; "" — закончили
    references_found = pyqtSignal(str, object)   # слово, [{"path", "line", "col"}]
    rename_ready = pyqtSignal(str, object)       # новое имя, {"edits": …} | {"files": …} | {"error": …}
    saved = pyqtSignal(str)                      # путь; файл записан на диск
    def __init__(self, path, services, font, shared=None):
        """shared — основной редактор того же файла: это его второе окно (editor.split_view.SplitView)."""
        super().__init__()
        self.split_views = []          # вторые окна этого файла (Окно → Разделить); у них общий документ
        self.path = path
        self.services = services
        self.language = languages.for_path(path)      # всё о языке файла — в реестре languages.py
        self.lang = self.language.id
        self.diagnostics = []          # (строка, столбец, сообщение, "error"/"warning")
        self.lsp_diagnostics = None    # ошибки от языкового сервера (Roslyn); None — сервера нет
        self.encoding = "utf-8"
        self.newline = "\n"
        self.disk = DiskState(path)    # что было на диске при открытии/сохранении — чтобы заметить чужие изменения
        self.missing_on_disk = False   # файл удалили вне редактора; сохранение создаст его заново
        self.indent = IndentStyle()    # пробелы/табы и размер; окно ставит итог .editorconfig/файла/параметров
        self.breakpoints = None        # debug.breakpoints.BreakpointStore — ставит окно (отладчик)
        self.bookmarks = None          # bookmarks.store.BookmarkStore — ставит окно
        self.execution_line = None     # строка (с 1), где стоит отладчик; None — не стоит
        self.execution_is_top = True   # верхний кадр стека (жёлтая стрелка) или выбранный выше (зелёная)
        self.debug_hover = None        # функция(выражение, ответ(текст)) — значение при наведении во время отладки
        self.vcs_markers = {}          # {строка с 1: added/modified/deleted} относительно последнего коммита (vcs)
        self.faded = []                # [(стр, стлб, стр2, стлб2, вид)] с 0: неиспользуемое — бледным, устаревшее — зачёркнутым
        self._link = None              # слово под мышью при зажатом Ctrl — подчёркнуто, как ссылка в VS
        self.find_text = ""
        self._completion_items = {}
        self._completion_items_full = []    # всё, что дал анализатор; список на экране — его фильтр
        self._completion_anchor = None      # где начинается набираемое слово
        self._completion_base = ""          # начало слова, для которого собран список
        self._completion_exact = False      # анализатор отдал всё: при наборе фильтруем сами, без запроса
        self._enabled_groups = set()        # включённые кнопки-фильтры по видам
        self._bulb_line = None              # строка, где показана лампочка (быстрые действия)
        self._bulb_actions = []
        self._bulb_req = 0
        self._bulb_open_after = False       # Ctrl+. нажали раньше, чем пришёл ответ сервера
        self._bulb_timer = QTimer(self, singleShot=True, interval=600, timeout=self._refresh_bulb)
        self._req = 0
        self._notice_req = 0
        self._notice_timer = QTimer(self, singleShot=True, interval=350, timeout=self._pending_notice)
        self._sig_req = 0
        self.set_code_font(font)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setCursorWidth(2)
        self._apply_palette()
        if shared is None:
            self.highlighter = self._make_highlighter()
        else:                           # общий документ: подсветка и свёрнутые блоки у него уже есть
            self.setDocument(shared.document())
            self.highlighter = shared.highlighter
        self.gutter = LineNumberArea(self)
        # сворачивание блоков (Ctrl+M, Ctrl+M …); у второго окна — вид общего состояния
        self.folding = Folding(self) if shared is None else shared.folding.add_view(self)
        self.symbols = []               # символы файла (symbols.Symbol) — для структуры, панели навигации, Ctrl+T
        self.navbar = NavigationBar(self)
        self.navbar.goto.connect(lambda line: (self.go_to_line(line), self.setFocus()))
        self.navbar.hide()
        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._update_gutter)
        self.cursorPositionChanged.connect(self._on_cursor)
        self._update_gutter_width()

        # подсказки
        self.model = QStandardItemModel(self)
        self.completer = QCompleter(self.model, self)
        self._footer = None
        try:        # кнопки внизу списка; если что-то пойдёт не так — список подсказок работает и без них
            popup_view = CompletionPopup()
            self.completer.setPopup(popup_view)
            self._footer = CompletionFooter(self)
            popup_view.attach_footer(self._footer)
        except Exception:       # noqa: BLE001
            traceback.print_exc()
            self._footer = None
        self.completer.setWidget(self)
        self.completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setModelSorting(QCompleter.ModelSorting.UnsortedModel)
        self.completer.setMaxVisibleItems(10)
        self.completer.setWrapAround(False)
        popup = self.completer.popup()
        self._delegate = CompletionDelegate(self, popup)
        popup.setItemDelegate(self._delegate)
        theme.style(popup, "QListView { background:@menu@; border:1px solid @tooltip_border@; }")
        popup.installEventFilter(self)
        popup.viewport().installEventFilter(self)
        self.completer.activated[QModelIndex].connect(self._activated)
        self.completer.highlighted[str].connect(self._on_highlighted)
        self.calltip = CallTip(self, self.hint_pt())
        self.doc_tip = CallTip(self, self.hint_pt())

        self._diag_timer = QTimer(self, singleShot=True, interval=500, timeout=self.run_diagnostics)
        self._hover_timer = QTimer(self, singleShot=True, interval=550, timeout=self._do_hover)
        self._hover_pos = None
        self.textChanged.connect(self._diag_timer.start)
        self.textChanged.connect(self._bulb_timer.start)
        self.setMouseTracking(True)
        self._on_cursor()

    @property
    def primary(self):
        """Основной редактор файла (у второго окна — тот, чей это вид). Всё «про файл» — у него."""
        return self

    def refresh_views(self):
        """Общее для окон файла изменилось (ошибки, бледный код, отладка, Git, символы) — перерисовать вторые окна."""
        for view in self.split_views:
            view.mirror_primary()
