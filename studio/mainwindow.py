"""Главное окно UniPy Studio."""
import glob
import json
import os
import re
import sys

from PyQt6.QtCore import Qt, QTimer, QSettings, QStandardPaths, QSize, QUrl
from PyQt6.QtGui import QAction, QActionGroup, QTextDocument, QGuiApplication, QDesktopServices
from PyQt6.QtWidgets import (QMainWindow, QTabWidget, QDockWidget, QWidget, QVBoxLayout, QHBoxLayout,
                             QLabel, QLineEdit, QToolButton, QComboBox, QFileDialog, QMessageBox,
                             QInputDialog, QCompleter, QFrame, QCheckBox, QApplication, QToolBar,
                             QSizePolicy, QPushButton, QDialog, QMenu)

from . import theme, templates, intellisense, lsp, indentation, symbols, toolchains
from .goto_all import GoToAll
from .analyzer_control import AnalyzerControl
from .build import BuildController
from .build_check import FileCheck
from .commands import CommandRegistry, KeyboardPage
from .debug.controller import DebugController
from .debug.session import STOPPED as DEBUG_STOPPED
from .document_safety import DocumentSafety
from .vcs.controller import VcsController
from .formatting import Formatter
from .navigation import NavigationHistory
from .options import Options, OptionsDialog
from .project_actions import ProjectActions
from .refactor_actions import RefactorActions
from .run_control import RunController
from .unity_console import UnityConsole
from .editor import CodeEditor
from . import languages
from .terminal.panel import TerminalPanel
from .panels import (SolutionExplorer, OutlinePanel, OutputPanel, ErrorList, UnityPanel,
                     PyQtDesigner, BusyBar, ReferencesPanel, find_unity_projects, outline)
from .theme import C
from .i18n import (LANGUAGE_NAMES, LANGUAGE_SETTING, bind_text, change_language,
                   refresh_text, selected_language, tr)

APP_NAME = "UniPy Studio"
INDENT_OPTIONS = {"editor/indent_style", "editor/indent_size", "editor/tab_width", "editor/use_editorconfig",
                  "editor/adapt_indent", "editor/trim_trailing"}


class FindBar(QFrame):
    def __init__(self, window):
        super().__init__()
        self.setObjectName("findBar")
        self.win = window
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(4)
        self.find = QLineEdit()
        bind_text(self.find, "mainwindow.find", "setPlaceholderText")
        self.find.setMinimumWidth(220)
        self.find.textChanged.connect(self.update_matches)
        self.find.returnPressed.connect(lambda: self.step(True))
        self.case = bind_text(QCheckBox(), "mainwindow.match_case_button")
        bind_text(self.case, "mainwindow.match_case", "setToolTip")
        self.case.toggled.connect(self.update_matches)
        self.count = QLabel("")
        self.count.setObjectName("hint")
        self.replace = QLineEdit()
        bind_text(self.replace, "mainwindow.replace_with", "setPlaceholderText")
        self.replace.setMinimumWidth(180)
        lay.addWidget(self.find)
        lay.addWidget(self.case)
        for text, tip, slot in (("↑", "mainwindow.previous_shift_f3", lambda: self.step(False)),
                                ("↓", "mainwindow.next_f3", lambda: self.step(True))):
            b = QToolButton()
            b.setText(text)
            bind_text(b, tip, "setToolTip")
            b.clicked.connect(slot)
            lay.addWidget(b)
        lay.addWidget(self.count)
        lay.addSpacing(10)
        lay.addWidget(self.replace)
        self.rep_one = bind_text(QPushButton(), "mainwindow.replace")
        self.rep_one.clicked.connect(self.replace_one)
        self.rep_all = bind_text(QPushButton(), "mainwindow.replace_all")
        self.rep_all.clicked.connect(self.replace_all)
        lay.addWidget(self.rep_one)
        lay.addWidget(self.rep_all)
        lay.addStretch(1)
        close = QToolButton()
        close.setIcon(theme.tool_icon("clear"))
        close.setProperty("tool_icon", "clear")
        close.clicked.connect(self.close_bar)
        lay.addWidget(close)
        self.hide()

    def open(self, replace=False):
        ed = self.win.editor()
        if ed and ed.textCursor().hasSelection():
            self.find.setText(ed.textCursor().selectedText())
        for w in (self.replace, self.rep_one, self.rep_all):
            w.setVisible(replace)
        self.show()
        self.find.setFocus()
        self.find.selectAll()
        self.update_matches()

    def close_bar(self):
        self.hide()
        ed = self.win.editor()
        if ed:
            ed.find_text = ""
            ed.update_selections()
            ed.setFocus()

    def flags(self):
        f = QTextDocument.FindFlag(0)
        if self.case.isChecked():
            f |= QTextDocument.FindFlag.FindCaseSensitively
        return f

    def update_matches(self, *_):
        ed = self.win.editor()
        if not ed:
            return
        text = self.find.text() if self.isVisible() else ""
        ed.find_text = text
        ed.update_selections()
        if text:
            hay = ed.toPlainText()
            n = hay.count(text) if self.case.isChecked() else hay.lower().count(text.lower())
            self.count.setText(tr("mainwindow.matches") % n if n else tr("mainwindow.no_results"))
        else:
            self.count.setText("")

    def step(self, forward=True):
        ed = self.win.editor()
        text = self.find.text()
        if not ed or not text:
            return
        f = self.flags()
        if not forward:
            f |= QTextDocument.FindFlag.FindBackward
        if not ed.find(text, f):
            c = ed.textCursor()
            c.movePosition(c.MoveOperation.Start if forward else c.MoveOperation.End)
            ed.setTextCursor(c)
            ed.find(text, f)

    def replace_one(self):
        ed = self.win.editor()
        if not ed:
            return
        c = ed.textCursor()
        same = c.selectedText() == self.find.text() if self.case.isChecked() else \
            c.selectedText().lower() == self.find.text().lower()
        if c.hasSelection() and same:
            c.insertText(self.replace.text())
        self.step(True)
        self.update_matches()

    def replace_all(self):
        ed = self.win.editor()
        text = self.find.text()
        if not ed or not text:
            return
        flags = 0 if self.case.isChecked() else re.IGNORECASE
        new, n = re.subn(re.escape(text), lambda m: self.replace.text(), ed.toPlainText(), flags=flags)
        if n:
            c = ed.textCursor()
            c.select(c.SelectionType.Document)
            c.insertText(new)
        self.win.status(tr("mainwindow.replaced") % n)
        self.update_matches()


class MainWindow(QMainWindow):
    def __init__(self, screenshot=None):
        super().__init__()
        self.screenshot_mode = bool(screenshot)
        self.settings = QSettings("UniPyStudio", "UniPyStudio")
        # параметры из «Средства → Параметры»; меню и клавиши меняют их тем же путём (options.set)
        self.options = Options(self.settings, parent=self)
        self.options.changed.connect(self._option_changed)
        self.font_size = self.options["editor/font_size"]
        self.font = theme.code_font(self.font_size)
        self.hint_size = self.options["editor/hint_size"]
        self.word_wrap = self.options["editor/word_wrap"]
        self.root = None
        # Окно связывает части между собой; сами действия живут в своих классах
        self.commands = CommandRegistry(self, self.settings)    # команды и их клавиши (Параметры → Клавиатура)
        self.nav = NavigationHistory(self)          # «Назад» / «Вперёд»
        self.runner = RunController(self)           # F5: запуск Python-файла
        self.builder = BuildController(self)        # Ctrl+Shift+B: dotnet build / проверка Python в фоне
        self.checker = FileCheck(self)              # Ctrl+F7: текущий файл
        self.refactoring = RefactorActions(self)    # ссылки, переименование, перенос типа, лампочка
        self.project = ProjectActions(self)         # новые файлы, Unity, PyQt-дизайнер
        self.analyzers = AnalyzerControl(self)      # состояние, перезапуск и обновление анализаторов
        self.debugger = DebugController(self)       # F5/F9/F10/F11: отладка по DAP (debugpy…)
        self.vcs = VcsController(self)              # Git: статус, полоски изменений, коммит, ветки
        self.formatter = Formatter(self)            # Ctrl+K, Ctrl+D: Roslyn, для Python — ruff/black
        self.setWindowIcon(theme.app_icon())
        self.resize(1440, 900)

        self.jedi = intellisense.JediWorker()
        self.jedi.finished.connect(self._worker_result)
        self.jedi.ready.connect(lambda msg: (self.output.write(tr("mainwindow.python_pyqt_completions") + msg, "common.build", C["dim"]),
                                             self.jedi_label.setText(tr("mainwindow.intellisense_ready"))))

        if theme.CONTROLLER is not None:
            theme.CONTROLLER.changed.connect(self._on_theme_changed)
        # анализаторы кода — по реестру языков (languages.SERVICE_FACTORIES)
        self.services_by_key = {key: make(self) for key, make in languages.SERVICE_FACTORIES.items()}
        self.csharp = self.services_by_key["csharp"]
        self.python_ls = self.services_by_key["python"]
        self.language_services = tuple(self.services_by_key.values())
        for key, svc in self.services_by_key.items():
            svc.finished.connect(self._worker_result)
            svc.diagnostics.connect(self._lsp_diagnostics)
            svc.faded.connect(lambda path, ranges: [ed.set_faded(ranges) for ed in self.editors() if ed.path == path])
            svc.status.connect(lambda text, state, k=key: self.analyzers.on_status(text, state, k))
            svc.restarted.connect(lambda reason, k=key: self.analyzers.on_restarted(k, reason))
            svc.restart_mb = self.options["analyzers/restart_mb"]

        # подсказки: сортировка, раньше выбранные одинаковые имена, подсказки из неподключённых пространств имён
        self.completion_alpha = self.options["intellisense/alpha"]
        lsp.OPTIONS["unimported"] = self.options["intellisense/unimported"]
        try:
            saved = json.loads(self.settings.value("intellisense/choices", "{}", type=str))
        except ValueError:
            saved = {}
        self.completion_choices = {k: v for k, v in saved.items()
                                   if isinstance(saved, dict) and isinstance(k, str) and isinstance(v, str)}
        for svc in self.language_services:
            svc.choices = self.completion_choices      # один общий словарь на все анализаторы

        self.csharp_index = intellisense.CSharpIndexWorker()
        self.csharp_index.finished.connect(self._worker_result)
        self._build_central()
        self._build_docks()
        for svc in self.language_services:
            svc.log.connect(lambda t: self.output.write(t, "common.build", C["dim"]))
        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self._build_status()
        self.outline_timer = QTimer(self, singleShot=True, interval=400, timeout=self.refresh_outline)
        self.safety = DocumentSafety(self)          # изменения файлов на диске, автовосстановление
        self.update_run_actions()                   # кнопки шагов отладки видны только во время отладки

    # ------------------------------------------------------------ параметры
    def _option_changed(self, key):
        """Один путь для окна «Параметры», меню и горячих клавиш."""
        value = self.options[key]
        if key == "editor/font_size":
            self.font_size = value
            self.font = theme.code_font(value)
            for ed in self.editors():
                ed.set_code_font(self.font)
        elif key == "editor/hint_size":
            self.hint_size = value
            for ed in self.editors():
                ed.apply_hint_size()
        elif key == "editor/word_wrap":
            self.word_wrap = value
            for ed in self.editors():
                ed.setLineWrapMode(ed.LineWrapMode.WidgetWidth if value else ed.LineWrapMode.NoWrap)
        elif key == "intellisense/unimported":
            lsp.OPTIONS["unimported"] = value
            self.a_unimported.setChecked(value)
            for svc in self.language_services:
                svc.configuration_changed()
        elif key == "intellisense/alpha":
            self.completion_alpha = value
        elif key == "analyzers/restart_mb":
            for svc in self.language_services:
                svc.restart_mb = value
        elif key.startswith(("documents/", "recovery/")):
            self.safety.option_changed(key)
        elif key == "editor/navigation_bar":
            for ed in self.editors():
                ed.set_navigation_bar(value)
                ed.set_symbols(ed.symbols)
        elif key == "editor/folding":
            for ed in self.editors():
                ed.folding.set_enabled(value)
                ed._update_gutter_width()
        elif key in INDENT_OPTIONS:
            for ed in self.editors():
                self.apply_indent(ed)
            self._show_indent(self.editor())

    # ------------------------------------------------------------ отступы
    def indent_defaults(self):
        o = self.options
        return indentation.IndentStyle(o["editor/indent_style"] == "tabs", o["editor/indent_size"],
                                       o["editor/tab_width"], trim_trailing=o["editor/trim_trailing"])

    def apply_indent(self, ed):
        """Отступ файла: .editorconfig → содержимое файла → параметры (indentation.resolve)."""
        ed.set_indent_style(indentation.resolve(ed.path, ed.toPlainText(), self.indent_defaults(),
                                                self.options["editor/use_editorconfig"],
                                                self.options["editor/adapt_indent"]))

    INDENT_SOURCES = {"editorconfig": "mainwindow.indent_from_editorconfig",
                      "file": "mainwindow.indent_from_file", "options": "mainwindow.indent_from_options"}

    def _show_indent(self, ed):
        if ed is None:
            self.indent_label.setText("")
            return
        kind, width = ed.indent.label()
        self.indent_label.setText(tr("mainwindow.indent_tabs" if kind == "tabs" else "mainwindow.indent_spaces") % width)
        self.indent_label.setToolTip(tr(self.INDENT_SOURCES[ed.indent.source]))

    # ------------------------------------------------------------ сборка интерфейса
    def _build_central(self):
        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setIconSize(QSize(16, 16))
        self.tabs.tabCloseRequested.connect(self.close_tab)
        bar = self.tabs.tabBar()
        bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        # Метод, а не lambda с bar внутри: обёртку панели вкладок (её создаёт Qt) держала бы
        # только сама lambda — сборщик мусора удаляет обоих, и правый клик роняет процесс (SIGSEGV)
        bar.customContextMenuRequested.connect(self._tab_bar_menu)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.findbar = FindBar(self)
        lay.addWidget(self.tabs, 1)
        self.empty = bind_text(QLabel(), "mainwindow.open_file_solution_explorer_ctrl")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.empty.customContextMenuRequested.connect(lambda pos: self._tab_menu(self.empty.mapToGlobal(pos), -1))
        theme.style(self.empty, "color:@dim@; background:@editor@; font-size:12pt;")
        lay.addWidget(self.empty, 1)
        self.tabs.hide()
        lay.insertWidget(0, self.findbar)
        self.setCentralWidget(central)

    def _dock(self, title, widget, area, name):
        d = bind_text(QDockWidget(self), title, "setWindowTitle")
        d.setObjectName(name)
        d.setWidget(widget)
        d.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable |
                      QDockWidget.DockWidgetFeature.DockWidgetMovable |
                      QDockWidget.DockWidgetFeature.DockWidgetFloatable)
        bar = QFrame()
        bar.setObjectName("dockTitle")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(8, 3, 3, 3)
        bl.setSpacing(2)
        lbl = bind_text(QLabel(), title)
        lbl.setObjectName("dockTitleText")
        bl.addWidget(lbl, 1)
        close = QToolButton()
        close.setIcon(theme.tool_icon("clear"))
        close.setProperty("tool_icon", "clear")
        close.setIconSize(QSize(12, 12))
        bind_text(close, "mainwindow.hide_panel", "setToolTip")
        close.clicked.connect(d.hide)
        bl.addWidget(close)
        d.setTitleBarWidget(bar)
        self.addDockWidget(area, d)
        return d

    def _build_docks(self):
        self.setDockNestingEnabled(True)
        self.setCorner(Qt.Corner.BottomLeftCorner, Qt.DockWidgetArea.LeftDockWidgetArea)
        self.setCorner(Qt.Corner.BottomRightCorner, Qt.DockWidgetArea.RightDockWidgetArea)
        self.explorer = SolutionExplorer()
        self.explorer.open_file.connect(self.open_path)
        self.explorer.run_file.connect(lambda p: self.runner.run_path(p))
        self.explorer.changed.connect(self._solution_changed)
        self.d_explorer = self._dock("mainwindow.solution_explorer", self.explorer,
                                     Qt.DockWidgetArea.LeftDockWidgetArea, "explorer")

        self.outline = OutlinePanel()
        self.outline.goto.connect(lambda line: self.editor() and self.editor().go_to_line(line))
        self.d_outline = self._dock("mainwindow.outline", self.outline, Qt.DockWidgetArea.LeftDockWidgetArea, "outline")
        self.splitDockWidget(self.d_explorer, self.d_outline, Qt.Orientation.Vertical)

        self.unity = UnityPanel(self.settings)
        self.unity.create_script.connect(self.project.create_unity_script)
        self.unity.insert_text.connect(self.project.insert_unity_message)
        self.unity.message.connect(lambda t, c: (self.output.write(t, "Unity", c), self.show_dock(self.d_output)))
        self.d_unity = self._dock("Unity", self.unity, Qt.DockWidgetArea.RightDockWidgetArea, "unity")

        self.designer = PyQtDesigner()
        self.designer.generated.connect(self.project.designer_generated)
        self.designer.new_template.connect(lambda i: self.project.new_file_dialog(template=i))
        self.designer.preview_ui.connect(self.project.preview_ui)
        self.d_designer = self._dock("mainwindow.pyqt_designer", self.designer, Qt.DockWidgetArea.RightDockWidgetArea, "designer")
        self.tabifyDockWidget(self.d_unity, self.d_designer)
        self.d_git = self._dock("vcs.changes_panel", self.vcs.panel, Qt.DockWidgetArea.RightDockWidgetArea, "git")
        self.tabifyDockWidget(self.d_designer, self.d_git)
        self.d_unity.raise_()

        self.output = OutputPanel(self.font)
        self.output.link.connect(self._open_link)
        self.d_output = self._dock("mainwindow.output", self.output, Qt.DockWidgetArea.BottomDockWidgetArea, "output")
        self.errors = ErrorList()
        self.errors.goto.connect(lambda p, l, c: self.open_path(p, l, c))
        self.d_errors = self._dock("mainwindow.error_list", self.errors, Qt.DockWidgetArea.BottomDockWidgetArea, "errors")
        self.terminal = TerminalPanel(self.font)
        self.d_terminal = self._dock("mainwindow.terminal", self.terminal, Qt.DockWidgetArea.BottomDockWidgetArea, "terminal")
        self.references = ReferencesPanel()
        self.references.goto.connect(lambda p, l, c: self.jump_to(p, l, c))
        self.d_references = self._dock("mainwindow.reference_search_results", self.references,
                                       Qt.DockWidgetArea.BottomDockWidgetArea, "references")
        self.unity_console = UnityConsole()
        self.unity_console.open_file.connect(lambda p, l: self.jump_to(p, l))
        self.unity_console.compile_changed.connect(self._unity_compile_changed)
        self.d_unity_console = self._dock("mainwindow.unity_console", self.unity_console,
                                          Qt.DockWidgetArea.BottomDockWidgetArea, "unity_console")
        D = self.debugger
        self.d_call_stack = self._dock("debug.call_stack", D.call_stack, Qt.DockWidgetArea.BottomDockWidgetArea, "call_stack")
        self.d_locals = self._dock("debug.locals", D.variables, Qt.DockWidgetArea.BottomDockWidgetArea, "locals")
        self.d_breakpoints = self._dock("debug.breakpoints", D.breakpoints_panel, Qt.DockWidgetArea.BottomDockWidgetArea,
                                        "breakpoints")
        self.splitDockWidget(self.d_errors, self.d_locals, Qt.Orientation.Horizontal)
        self.tabifyDockWidget(self.d_locals, self.d_call_stack)
        self.tabifyDockWidget(self.d_call_stack, self.d_breakpoints)
        for d in (self.d_call_stack, self.d_locals, self.d_breakpoints):
            d.hide()                        # появляются при остановке, как в VS
        self.tabifyDockWidget(self.d_errors, self.d_references)
        self.tabifyDockWidget(self.d_errors, self.d_output)
        self.tabifyDockWidget(self.d_output, self.d_terminal)
        self.tabifyDockWidget(self.d_output, self.d_unity_console)
        self.d_errors.raise_()
        self.resizeDocks([self.d_explorer, self.d_unity], [290, 370], Qt.Orientation.Horizontal)
        self.resizeDocks([self.d_errors], [230], Qt.Orientation.Vertical)
        self.resizeDocks([self.d_explorer, self.d_outline], [440, 200], Qt.Orientation.Vertical)

    def _build_actions(self):
        """Все команды окна — в реестре (commands.py): id, подпись, клавиши по умолчанию, функция.
        Свои клавиши пользователь назначает в «Параметры → Среда → Клавиатура»."""
        A = self.commands.add
        ed = lambda name: (lambda: self.editor() and getattr(self.editor(), name)())  # noqa
        R, P = self.refactoring, self.project
        self.a_new = A("File.NewFile", "common.new_file", lambda: P.new_file_dialog(), "Ctrl+N", "new", "mainwindow.add_new_item_ctrl_n")
        self.a_open_file = A("File.OpenFile", "mainwindow.open_file_2", self.open_file_dialog, "Ctrl+O")
        self.a_open_solution = A("File.OpenProject", "mainwindow.open_project_solution_2", self.open_solution_dialog,
                                 "Ctrl+Shift+O", "open", "mainwindow.open_project_solution_ctrl_shift")
        self.a_new_project = A("File.NewProject", "mainwindow.new_project", self.new_project_dialog, "Ctrl+Shift+N")
        self.a_open = A("File.OpenFolder", "mainwindow.open_folder", self.open_folder_dialog, "Ctrl+Shift+Alt+O")
        self.a_sample = A("File.OpenSampleSolution", "mainwindow.open_sample_solution", self.open_sample)
        self.a_save = A("File.SaveSelectedItems", "mainwindow.save", self.save_current, "Ctrl+S", "save", "mainwindow.save_ctrl_s")
        self.a_save_all = A("File.SaveAll", "mainwindow.save_all", self.save_all, "Ctrl+Shift+S", "saveall",
                            "mainwindow.save_all_ctrl_shift_s")
        self.a_close = A("Window.CloseDocumentWindow", "mainwindow.close_tab", lambda: self.close_tab(self.tabs.currentIndex()), "Ctrl+W")
        self.a_quit = A("File.Exit", "mainwindow.exit", self.close, "Ctrl+Q")
        self.a_undo = A("Edit.Undo", "mainwindow.undo", ed("undo"), "Ctrl+Z")
        self.a_redo = A("Edit.Redo", "mainwindow.redo", ed("redo"), ("Ctrl+Y", "Ctrl+Shift+Z"))
        self.a_cut = A("Edit.Cut", "mainwindow.cut", ed("cut"), "Ctrl+X")
        self.a_copy = A("Edit.Copy", "mainwindow.copy", ed("copy"), "Ctrl+C")
        self.a_paste = A("Edit.Paste", "mainwindow.paste", ed("paste"), "Ctrl+V")
        self.a_project_find = A("Edit.FindinFiles", "mainwindow.find_in_files", self.project_search, "Ctrl+Shift+F")
        self.a_project_replace = A("Edit.ReplaceinFiles", "mainwindow.replace_in_files", self.project_search, "Ctrl+Shift+H")
        self.a_settings = A("Tools.Options", "mainwindow.settings", self.show_settings, "Ctrl+,")
        self.a_next_tab = A("Window.NextDocumentWindow", "mainwindow.next_tab", lambda: self._step_tab(1), "Ctrl+Tab")
        self.a_prev_tab = A("Window.PreviousDocumentWindow", "mainwindow.previous_tab", lambda: self._step_tab(-1), "Ctrl+Shift+Tab")
        self.a_find = A("Edit.Find", "mainwindow.find_2", lambda: self.findbar.open(False), "Ctrl+F", "find")
        self.a_replace = A("Edit.Replace", "mainwindow.replace_2", lambda: self.findbar.open(True), "Ctrl+H")
        self.a_find_next = A("Edit.FindNext", "mainwindow.find_next", lambda: self.findbar.step(True), "F3")
        self.a_find_prev = A("Edit.FindPrevious", "mainwindow.find_previous", lambda: self.findbar.step(False), "Shift+F3")
        self.a_goto = A("Edit.GoTo", "mainwindow.go_line_2", self.goto_line_dialog, "Ctrl+G")
        self.a_comment = A("Edit.ToggleLineComment", "mainwindow.toggle_comment", ed("toggle_comment"), "Ctrl+/")
        self.a_format = A("Edit.FormatDocument", "mainwindow.format_document",
                          lambda: self.formatter.format(self.editor()), "Ctrl+K, Ctrl+D")
        self.a_format_sel = A("Edit.FormatSelection", "mainwindow.format_selection",
                              lambda: self.formatter.format(self.editor(), True), "Ctrl+K, Ctrl+F")
        self.a_dup = A("Edit.Duplicate", "mainwindow.duplicate_line", ed("duplicate_line"), "Ctrl+D")
        self.a_complete = A("Edit.ListMembers", "mainwindow.member_list_completion",
                            lambda: self.editor() and self.editor().request_completion(True), "Ctrl+Space")
        self.a_params = A("Edit.ParameterInfo", "mainwindow.parameter_info",
                          lambda: self.editor() and self.editor().request_signatures(True), "Ctrl+Shift+Space")
        self.a_definition = A("Edit.GoToDefinition", "mainwindow.go_definition", ed("go_to_definition"), "F12")
        self.a_goto_all = A("Edit.GoToAll", "mainwindow.go_to_all", lambda: self.goto_all(), "Ctrl+T")
        self.a_goto_member = A("Edit.GoToMember", "mainwindow.go_to_member", lambda: self.goto_all("m "), "Alt+\\")
        self.a_quick = A("Edit.GoToFile", "mainwindow.go_file", lambda: (self.quick.setFocus(), self.quick.selectAll()), "Ctrl+P")
        # масштаб кода — клавиши Visual Studio (Ctrl+Shift+. / Ctrl+Shift+,); Ctrl+− в VS — «Назад»
        self.a_zoom_in = A("View.ZoomIn", "mainwindow.increase_font_size", lambda: self.zoom(1), ("Ctrl+Shift+.", "Ctrl+="))
        self.a_zoom_out = A("View.ZoomOut", "mainwindow.decrease_font_size", lambda: self.zoom(-1), "Ctrl+Shift+,")
        self.a_back = A("View.NavigateBackward", "mainwindow.back", lambda: self.navigate(-1), "Ctrl+-", "back", "mainwindow.back_ctrl")
        self.a_fwd = A("View.NavigateForward", "mainwindow.forward", lambda: self.navigate(1), "Ctrl+Shift+-", "forward",
                       "mainwindow.forward_ctrl_shift")
        self.a_refs = A("Edit.FindAllReferences", "mainwindow.find_all_references", ed("find_references"), "Shift+F12")
        fold = lambda name: (lambda: self.editor() and self._fold(name))  # noqa
        self.a_fold_toggle = A("Edit.ToggleOutliningExpansion", "mainwindow.fold_toggle", fold("toggle"), "Ctrl+M, Ctrl+M")
        self.a_fold_all = A("Edit.ToggleAllOutlining", "mainwindow.fold_toggle_all", fold("toggle_all"), "Ctrl+M, Ctrl+L")
        self.a_fold_defs = A("Edit.CollapseToDefinitions", "mainwindow.fold_to_definitions", fold("collapse_to_definitions"),
                             "Ctrl+M, Ctrl+O")
        self.a_fold_collapse = A("Edit.CollapseAllOutlining", "mainwindow.fold_collapse_all", fold("collapse_all"), "Ctrl+M, Ctrl+A")
        self.a_fold_expand = A("Edit.ExpandAllOutlining", "mainwindow.fold_expand_all", fold("expand_all"), "Ctrl+M, Ctrl+X")
        self.a_rename = A("Refactor.Rename", "common.rename_2", R.rename_dialog, ("Ctrl+R, Ctrl+R", "F2"))
        self.a_move_type = A("Refactor.MoveTypeToFile", "common.move_type_own_file", R.move_type_to_file, "Ctrl+R, Ctrl+F")
        self.a_unimported = A("View.UnimportedNamespaceSuggestions", "mainwindow.suggestions_from_unimported_namespaces",
                              self.toggle_unimported, checkable=True)
        self.a_unimported.setChecked(lsp.OPTIONS["unimported"])
        self.a_hint_in = A("View.HintSizeUp", "mainwindow.increase_tooltip_size", lambda: self.zoom_hints(1), "Ctrl+Alt+=")
        self.a_hint_out = A("View.HintSizeDown", "mainwindow.decrease_tooltip_size", lambda: self.zoom_hints(-1), "Ctrl+Alt+-")
        D = self.debugger
        self.a_run = A("Debug.Start", "debug.start", D.start_or_continue, "F5", "run", "debug.start_tip")
        self.a_run2 = A("Debug.StartWithoutDebugging", "mainwindow.run_without_debugging", self.runner.run_selected, "Ctrl+F5")
        self.a_stop = A("Debug.StopDebugging", "mainwindow.stop", self._stop_all, "Shift+F5", "stop", "mainwindow.stop_shift_f5")
        self.a_restart = A("Debug.Restart", "debug.restart", D.restart, "Ctrl+Shift+F5")
        self.a_pause = A("Debug.BreakAll", "debug.break_all", D.pause, "Ctrl+Alt+Break")
        self.a_step_over = A("Debug.StepOver", "debug.step_over", D.step_over, "F10")
        self.a_step_into = A("Debug.StepInto", "debug.step_into", D.step_into, "F11")
        self.a_step_out = A("Debug.StepOut", "debug.step_out", D.step_out, "Shift+F11")
        self.a_breakpoint = A("Debug.ToggleBreakpoint", "debug.toggle_breakpoint", D.toggle_breakpoint, "F9")
        self.a_clear_breakpoints = A("Debug.DeleteAllBreakpoints", "debug.delete_all_breakpoints", D.store.clear,
                                     "Ctrl+Shift+F9")
        D.state_changed.connect(lambda _s: self.update_run_actions())
        B = self.builder
        self.a_build = A("Build.BuildSolution", "mainwindow.build_solution", B.build_solution, "Ctrl+Shift+B",
                         "build", "mainwindow.build_solution_tip")
        self.a_rebuild = A("Build.RebuildSolution", "mainwindow.rebuild_solution", lambda: B.build_solution(True),
                           "Ctrl+Alt+F7")
        self.a_clean = A("Build.CleanSolution", "mainwindow.clean_solution", B.clean_solution)
        self.a_build_project = A("Build.BuildSelection", "mainwindow.build_current_project", B.build_current_project,
                                 "Shift+F6")
        self.a_build_cancel = A("Build.Cancel", "mainwindow.cancel_build", B.cancel, "Ctrl+Break")
        self.a_build_cancel.setEnabled(False)
        B.started.connect(lambda: (self.a_build_cancel.setEnabled(True), self.a_build.setEnabled(False)))
        B.stopped.connect(lambda: (self.a_build_cancel.setEnabled(False), self.a_build.setEnabled(True)))
        self.a_build_file = A("Build.CheckCurrentFile", "mainwindow.check_current_file", self.checker.check_current, "Ctrl+F7")
        self.a_unity = A("Project.OpenInUnity", "common.open_unity", lambda: self.unity.open_in_unity(), None, "unity",
                         "mainwindow.open_project_unity")
        self.a_hub = A("Project.OpenUnityHub", "mainwindow.open_unity_hub", lambda: self.unity.open_hub())
        self.a_unity_script = A("Project.NewUnityScript", "mainwindow.new_unity_script", lambda: P.new_file_dialog(template=5))
        self.a_pyqt_window = A("Project.NewPyQtWindow", "mainwindow.new_pyqt_window", lambda: P.new_file_dialog(template=0))
        self.a_pip = A("Project.InstallRequirements", "mainwindow.install_requirements", P.install_requirements)
        self.a_designer_ui = A("Project.PreviewUiForm", "mainwindow.preview_ui_form", P.preview_ui)
        self.a_keys = A("Help.KeyboardShortcuts", "mainwindow.keyboard_shortcuts", self.show_keys, "F1")
        self.a_about = A("Help.About", "mainwindow.about", self.show_about)
        self.a_updates = A("Help.AnalyzerUpdates", "mainwindow.analyzer_updates", self.analyzers.show_updates)
        V = self.vcs
        self.a_git_changes = A("View.GitChanges", "vcs.changes_panel", lambda: self.show_dock(self.d_git), "Ctrl+0, Ctrl+G")
        self.a_git_diff = A("Git.CompareWithUnmodified", "vcs.compare_current", lambda: V.show_diff())
        self.a_git_fetch = A("Git.Fetch", "vcs.fetch", lambda: V.remote("fetch"))
        self.a_git_pull = A("Git.Pull", "vcs.pull", lambda: V.remote("pull"))
        self.a_git_push = A("Git.Push", "vcs.push", lambda: V.remote("push"))
        self.a_git_branches = A("Git.SwitchBranch", "vcs.switch_branch", V.branch_menu)
        self.a_restart = A("Tools.RestartAnalyzers", "mainwindow.restart_code_analyzers", self.analyzers.restart_all)
        self.a_terminal = A("View.Terminal", "mainwindow.terminal", lambda: self.show_dock(self.d_terminal))
        self.a_designer = A("View.PyQtDesigner", "mainwindow.pyqt_designer", lambda: self.show_dock(self.d_designer))
        self.a_unity_root = A("Tools.UnityVersionsFolder", "mainwindow.folder_with_version_unity", P.pick_unity_root)

    def _fold(self, name):
        """Команды «Правка → Структура» для текущего редактора."""
        ed = self.editor()
        if not ed.folding.enabled:
            self.status(tr("mainwindow.folding_disabled"))
            return
        if name == "toggle":
            if not ed.folding.toggle(ed.textCursor().blockNumber()):
                self.status(tr("mainwindow.no_block_here"))
        else:
            getattr(ed.folding, name)()

    def show_debug_docks(self):
        """При остановке — «Стек вызовов» и «Локальные»; в первый раз — половина ширины низа, как в VS."""
        first = self.d_locals.isHidden()
        for d in (self.d_call_stack, self.d_locals):
            self.show_dock(d)
        if first:
            half = max(300, self.width() // 2)
            self.resizeDocks([self.d_errors, self.d_locals], [half, half], Qt.Orientation.Horizontal)

    def set_branch_label(self, system, branch):
        self.branch_label.setText(("⎇ " + branch) if branch else "")
        self.branch_label.setToolTip(tr("vcs.branch_tip") % system if branch else "")

    def _stop_all(self):
        """Shift+F5: остановить отладку и запущенную программу."""
        self.debugger.stop()
        self.runner.stop()

    def update_run_actions(self):
        """Кнопки запуска и отладки по состоянию: идёт ли программа, стоит ли отладчик (как панель VS)."""
        debugging = self.debugger.active()
        stopped = debugging and self.debugger.session.state == DEBUG_STOPPED
        self.a_stop.setEnabled(debugging or self.runner.running())
        self.a_restart.setEnabled(debugging)
        self.a_pause.setEnabled(debugging and not stopped)
        for a in (self.a_step_over, self.a_step_into, self.a_step_out):
            a.setEnabled(stopped)
            a.setVisible(debugging)
        bind_text(self.a_run, "debug.continue" if stopped else "debug.start")
        if not debugging:
            for d in (self.d_call_stack, self.d_locals):
                d.hide()

    def _step_tab(self, step):
        self.tabs.setCurrentIndex((self.tabs.currentIndex() + step) % max(1, self.tabs.count()))

    def _build_menus(self):
        mb = self.menuBar()
        m = bind_text(mb.addMenu(""), "common.file", "setTitle")
        for a in (self.a_new_project, self.a_new, self.a_open_solution, self.a_open, self.a_open_file, self.a_sample):
            m.addAction(a)
        self.recent_menu = bind_text(m.addMenu(""), "mainwindow.recent_folders", "setTitle")
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        m.addSeparator()
        for a in (self.a_save, self.a_save_all, self.a_close):
            m.addAction(a)
        m.addSeparator()
        m.addAction(self.a_quit)
        m = bind_text(mb.addMenu(""), "mainwindow.edit", "setTitle")
        for a in (self.a_undo, self.a_redo, None, self.a_cut, self.a_copy, self.a_paste, None,
                  self.a_find, self.a_replace, self.a_project_find, self.a_project_replace, self.a_goto, None, self.a_comment, self.a_dup,
                  self.a_format, self.a_format_sel):
            m.addSeparator() if a is None else m.addAction(a)
        sub = bind_text(m.addMenu(""), "mainwindow.outlining", "setTitle")
        for a in (self.a_fold_toggle, self.a_fold_all, self.a_fold_defs, self.a_fold_collapse, self.a_fold_expand):
            sub.addAction(a)
        sub = m.addMenu("IntelliSense")
        for a in (self.a_complete, self.a_params, self.a_definition, self.a_refs):
            sub.addAction(a)
        m.addAction(self.a_goto_all)
        m.addAction(self.a_goto_member)
        m.addAction(self.a_rename)
        m.addAction(self.a_move_type)
        m = bind_text(mb.addMenu(""), "mainwindow.view", "setTitle")
        for d in (self.d_explorer, self.d_outline, self.d_unity, self.d_designer, self.d_output,
                  self.d_errors, self.d_references, self.d_terminal, self.d_unity_console,
                  self.d_call_stack, self.d_locals, self.d_breakpoints, self.d_git):
            m.addAction(d.toggleViewAction())
        m.addSeparator()
        m.addAction(self.a_back)
        m.addAction(self.a_fwd)
        m.addSeparator()
        m.addAction(self.a_quick)
        m.addAction(self.a_zoom_in)
        m.addAction(self.a_zoom_out)
        m.addAction(self.a_hint_in)
        m.addAction(self.a_hint_out)
        m.addAction(self.a_unimported)
        m.addSeparator()
        self.theme_menu = bind_text(m.addMenu(""), "mainwindow.theme", "setTitle")
        self.theme_menu.aboutToShow.connect(self._fill_theme_menu)
        self._build_language_menu(bind_text(m.addMenu(""), "mainwindow.language", "setTitle"))
        m = bind_text(mb.addMenu(""), "common.project", "setTitle")
        for a in (self.a_new, self.a_unity_script, self.a_pyqt_window, None, self.a_designer_ui, self.a_pip,
                  None, self.a_unity, self.a_hub):
            m.addSeparator() if a is None else m.addAction(a)
        m = bind_text(mb.addMenu(""), "common.build", "setTitle")
        for a in (self.a_build, self.a_rebuild, self.a_clean, None, self.a_build_project, self.a_build_file,
                  None, self.a_build_cancel):
            m.addSeparator() if a is None else m.addAction(a)
        m = bind_text(mb.addMenu(""), "mainwindow.debug", "setTitle")
        for a in (self.a_run, self.a_run2, self.a_stop, self.a_restart, None, self.a_pause, self.a_step_over,
                  self.a_step_into, self.a_step_out, None, self.a_breakpoint, self.a_clear_breakpoints):
            m.addAction(a)
        m = bind_text(mb.addMenu(""), "vcs.menu", "setTitle")
        for a in (self.a_git_changes, self.a_git_diff, None, self.a_git_fetch, self.a_git_pull, self.a_git_push,
                  None, self.a_git_branches):
            m.addSeparator() if a is None else m.addAction(a)
        m = bind_text(mb.addMenu(""), "mainwindow.tools", "setTitle")
        m.addAction(self.a_settings)
        for a in (self.a_terminal, self.a_designer, self.a_unity_root, None, self.a_restart):
            m.addSeparator() if a is None else m.addAction(a)
        self.restart_menu = bind_text(m.addMenu(""), "mainwindow.restart_analyzer_above_memory_limit", "setTitle")
        self.restart_menu.aboutToShow.connect(self.analyzers.fill_restart_menu)
        m = bind_text(mb.addMenu(""), "mainwindow.help", "setTitle")
        m.addAction(self.a_keys)
        m.addAction(self.a_updates)
        m.addAction(self.a_about)

    def _build_language_menu(self, menu):
        group = QActionGroup(menu)
        selected = selected_language()
        choices = [("", "mainwindow.system_default"), *LANGUAGE_NAMES.items()]
        for code, label in choices:
            action = bind_text(menu.addAction(""), label) if not code else menu.addAction(label)
            action.setCheckable(True)
            action.setData(code)
            group.addAction(action)
            action.setChecked(code == selected)
        group.triggered.connect(self._change_language)

    def _change_language(self, action):
        code = action.data()
        if code == selected_language():
            return
        self.settings.setValue(LANGUAGE_SETTING, code)
        self.settings.sync()
        change_language(code, QApplication.instance())
        refresh_text(self)
        self.findbar.update_matches()
        self.explorer.retranslate()
        self.outline.retranslate()
        self.output.retranslate()
        self.references.retranslate()
        self.errors.retranslate()
        self.unity.retranslate()
        self.unity_console.retranslate()
        self.designer.retranslate()
        self.vcs.panel.retranslate()
        if self.startup.count():
            self.startup.setItemText(0, tr("mainwindow.current_file"))
        self.analyzers.refresh_label()
        self.status(tr("mainwindow.running_ellipsis") if self.runner.running() else tr("common.ready"), 0)
        ed = self.editor()
        if ed:
            cursor = ed.textCursor()
            self._cursor_moved(cursor.blockNumber() + 1, cursor.positionInBlock() + 1)
            self.enc_label.setText(ed.encoding_label())
            self._show_indent(ed)
            ed.completer.popup().hide()
            ed.doc_tip.hide()
            ed.calltip.hide()
        else:
            self.pos_label.setText(tr("mainwindow.ln_1_col_1"))

    def _build_toolbar(self):
        tb = bind_text(QToolBar(), "mainwindow.standard", "setWindowTitle")
        tb.setObjectName("main_toolbar")
        tb.setMovable(False)
        tb.setIconSize(QSize(16, 16))
        self.addToolBar(tb)
        for a in (self.a_back, self.a_fwd, None, self.a_new, self.a_open_solution, self.a_save, self.a_save_all):
            tb.addSeparator() if a is None else tb.addAction(a)
        tb.addSeparator()
        lbl = bind_text(QLabel(), "mainwindow.run")
        tb.addWidget(lbl)
        self.startup = QComboBox()
        self.startup.setMinimumWidth(200)
        bind_text(self.startup, "mainwindow.file_run_with_f5", "setToolTip")
        tb.addWidget(self.startup)
        self.interp = QComboBox()
        self.interp.setMinimumWidth(150)
        bind_text(self.interp, "mainwindow.interpreter_python", "setToolTip")
        tb.addWidget(self.interp)
        self.interp.currentIndexChanged.connect(lambda: self.jedi.set_project(self.root, self.interp.currentData()))
        self.interp.currentIndexChanged.connect(lambda *_: self.python_ls.configuration_changed())
        run_btn = QToolButton()
        run_btn.setDefaultAction(self.a_run)
        run_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        run_btn.setObjectName("runButton")
        tb.addWidget(run_btn)
        tb.addAction(self.a_stop)
        for a in (self.a_step_over, self.a_step_into, self.a_step_out):
            tb.addAction(a)
        tb.addSeparator()
        tb.addAction(self.a_build)
        unity_btn = QToolButton()
        unity_btn.setDefaultAction(self.a_unity)
        unity_btn.setText("Unity")
        unity_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        tb.addWidget(unity_btn)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)
        self.quick = QLineEdit()
        bind_text(self.quick, "mainwindow.go_file_ctrl_p", "setPlaceholderText")
        self.quick.setFixedWidth(260)
        self.quick.addAction(theme.tool_icon("find"), QLineEdit.ActionPosition.LeadingPosition).setProperty("tool_icon", "find")
        self.quick_completer = QCompleter([], self)
        self.quick_completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.quick_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.quick_completer.setMaxVisibleItems(15)
        self.quick_completer.activated[str].connect(self._quick_open)
        self.quick.setCompleter(self.quick_completer)
        self.quick.returnPressed.connect(lambda: self._quick_open(self.quick.text()))
        tb.addWidget(self.quick)
        self._fill_interpreters()

    def _build_status(self):
        sb = self.statusBar()
        sb.setSizeGripEnabled(False)
        self.status_label = bind_text(QLabel(), "common.ready")
        sb.addWidget(self.status_label, 1)
        self.err_label = QLabel("✖ 0  ⚠ 0")
        self.err_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.err_label.mousePressEvent = lambda e: self.show_dock(self.d_errors)
        self.jedi_label = QLabel()
        self.analyzers.refresh_label()
        self.pos_label = bind_text(QLabel(), "mainwindow.ln_1_col_1")
        self.indent_label = QLabel()
        self.indent_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.indent_label.mousePressEvent = lambda e: self.show_settings(("options.text_editor", "options.indentation"))
        self.enc_label = QLabel("UTF-8")
        self.lang_label = QLabel("")
        self.branch_label = QLabel("")
        self.branch_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.branch_label.mousePressEvent = lambda e: self.vcs.branch_menu()
        self.busy_bar = BusyBar()
        self._busy_timeout = QTimer(self, singleShot=True, interval=30000,
                                    timeout=lambda: self.busy("", tr("mainwindow.server_failed_respond_definition_not")))
        for w in (self.busy_bar, self.branch_label, self.err_label, self.jedi_label, self.pos_label, self.indent_label,
                  self.enc_label, self.lang_label):
            sb.addPermanentWidget(w)

    # ------------------------------------------------------------ сервисы для редактора
    def status(self, text, timeout=6000):
        self.status_label.setText(text)
        if timeout:
            QTimer.singleShot(timeout, lambda: self.status_label.text() == text and self.status_label.setText(
                tr("mainwindow.running_ellipsis") if self.runner.running() else tr("common.ready")))

    # то, что редактор и панели зовут у окна, — передаётся в свои классы
    def jump_to(self, path, line, col=1):
        return self.nav.jump_to(path, line, col)

    def navigate(self, direction):
        self.nav.navigate(direction)

    def move_type_to_file(self):
        self.refactoring.move_type_to_file()

    def apply_code_action(self, edit, title):
        self.refactoring.apply_code_action(edit, title)

    def run_file(self, path):
        self.runner.run_path(path)

    def project_context(self):
        """Что знают наборы инструментов (toolchains) о текущем решении — без доступа к самому окну."""
        return toolchains.ProjectContext(
            root=self.root, files=getattr(self.explorer, "files", []),
            buffers={ed.path: ed.toPlainText() for ed in self.editors()},
            interpreter=self.interp.currentData() if hasattr(self, "interp") else None,
            options=self.options, open_in_unity=self.unity.open_in_unity, status=self.status)

    def zoom(self, delta):
        self.options.set("editor/font_size", self.font_size + delta)
        self.status(tr("mainwindow.font_size") % self.font_size)

    # ------------------------------------------------------------ навигация (Ctrl+− / Ctrl+Shift+−)
    # ------------------------------------------------------------ ссылки и переименование
    def busy(self, text, done_message=None):
        """Полоска «идёт работа» в строке состояния (переход к определению, декомпиляция)."""
        if text:
            self.busy_bar.start()
            self.statusBar().showMessage(text)
            self._busy_timeout.start()
        else:
            self._busy_timeout.stop()
            self.busy_bar.stop()
            self.statusBar().clearMessage()
            if done_message:
                self.status(done_message)

    def set_completion_alpha(self, alpha):
        """Кнопка «A→Z» в списке подсказок: по алфавиту или по релевантности (запоминается)."""
        self.options.set("intellisense/alpha", bool(alpha))

    def remember_completion(self, name, namespace):
        """Из одинаковых имён выбрали это (Debug из UnityEngine, а не из System.Diagnostics) —
        в следующий раз оно будет первым."""
        if not name or not namespace or self.completion_choices.get(name) == namespace:
            return
        self.completion_choices.pop(name, None)
        self.completion_choices[name] = namespace
        while len(self.completion_choices) > 500:                  # самые старые забываются
            self.completion_choices.pop(next(iter(self.completion_choices)))
        self.settings.setValue("intellisense/choices", json.dumps(self.completion_choices, ensure_ascii=False))

    def toggle_unimported(self):
        """Подсказки из ещё не подключённых пространств имён: удобно (using добавится сам), но Roslyn
        ищет по всем сборкам Unity и отвечает дольше. Применяется сразу, без перезапуска."""
        self.options.set("intellisense/unimported", self.a_unimported.isChecked())
        self.status(tr("mainwindow.suggestions_from_unimported_namespaces_2") +
                    (tr("mainwindow.on") if lsp.OPTIONS["unimported"] else tr("mainwindow.off_faster")))

    def zoom_hints(self, delta):
        """Размер подсказок — отдельно от размера кода."""
        self.options.set("editor/hint_size", self.hint_size + delta)
        self.status(tr("mainwindow.tooltip_font_size") % self.hint_size)

    def copy_text(self, text):
        QGuiApplication.clipboard().setText(text)
        self.status(tr("mainwindow.copied") + text)

    def open_external(self, path):
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def show_dock(self, d):
        d.show()
        d.raise_()

    # ------------------------------------------------------------ решение
    def sample_root(self):
        base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
        return os.path.join(base or os.path.expanduser("~/.local/share/UniPyStudio"), "Пример")

    def open_sample(self):
        self.set_root(templates.ensure_sample(self.sample_root()))
        self.open_path(os.path.join(self.root, "UnityGame", "Assets", "Scripts", "PlayerController.cs"))
        self.open_path(os.path.join(self.root, "PyQtApp", "main_window.py"))

    def start(self, path=None):
        if path and os.path.isdir(path):
            self.set_root(os.path.abspath(path))
            return
        if path and os.path.isfile(path):
            self.set_root(os.path.dirname(os.path.abspath(path)))
            self.open_path(os.path.abspath(path))
            return
        last = None if self.screenshot_mode else self.settings.value("solution/last")
        if last and os.path.isdir(last) and last != self.sample_root():
            self.set_root(last)
            for p in (self.settings.value("solution/open_files") or []):
                if os.path.isfile(p):
                    self.open_path(p)
        else:
            self.open_sample()

    def set_root(self, root):
        if not self.close_all_tabs():
            return False
        self.root = root
        self.jedi.set_project(root)
        for svc in self.language_services:
            svc.stop()
        self.explorer.set_root(root)
        self.unity.set_root(root)
        self.terminal.set_cwd(root)
        self.errors.root = root
        self.errors.groups.clear()
        self.errors.render()
        if not self.screenshot_mode:
            self.settings.setValue("solution/last", root)
            recent = [r for r in (self.settings.value("solution/recent") or []) if r != root]
            self.settings.setValue("solution/recent", [root] + recent[:8])
        self._update_title()
        self.output.write(tr("mainwindow.opened_solution") % root, "common.build", C["dim"])
        unity = find_unity_projects(root)
        self.unity_console.set_project(unity[0] if unity else None)
        self.debugger.load_solution(root)
        self.vcs.set_root(root)
        if unity:
            self.output.write(tr("mainwindow.found_unity_project") % ", ".join(os.path.relpath(u, root) or "." for u in unity),
                              "Unity", C["dim"])
        QTimer.singleShot(0, self.safety.offer_recovery)     # после того как откроются запомненные вкладки
        return True

    def _solution_changed(self):
        files = self.explorer.files
        rels = [os.path.relpath(p, self.root) for p in files]
        self.quick_completer.model().setStringList(rels)
        self._fill_run_targets()
        self._fill_interpreters()

    def _fill_run_targets(self):
        """Список «Запуск»: «Текущий файл» и то, что предлагает каждый набор инструментов решения."""
        current = self.startup.currentData()
        ctx = self.project_context()
        self.startup.blockSignals(True)
        self.startup.clear()
        self.startup.addItem(theme.tool_icon("run"), tr("mainwindow.current_file"), None)
        for tc in toolchains.active(ctx):
            icon = theme.badge(*tc.badge)
            for target in tc.run_targets(ctx):
                self.startup.addItem(icon, target.label, "%s::%s" % (tc.id, target.id))
        idx = self.startup.findData(current) if current else (1 if self.startup.count() > 1 else 0)
        self.startup.setCurrentIndex(max(0, idx))
        self.startup.blockSignals(False)

    def _fill_interpreters(self):
        cur = self.interp.currentData()
        self.interp.clear()
        seen = set()
        cands = [(sys.executable, "Python %d.%d" % sys.version_info[:2])]
        if self.root:
            for pattern in ("*/.venv/bin/python", "*/venv/bin/python", ".venv/bin/python", "venv/bin/python"):
                for p in glob.glob(os.path.join(self.root, pattern)):
                    cands.append((p, "venv: " + os.path.relpath(os.path.dirname(os.path.dirname(p)), self.root)))
        for exe in ("python3", "python"):
            from shutil import which
            w = which(exe)
            if w:
                cands.append((w, "%s (%s)" % (exe, w)))
        for p, label in cands:
            real = os.path.abspath(p)
            if real in seen:
                continue
            seen.add(real)
            self.interp.addItem(label, p)
        if cur:
            i = self.interp.findData(cur)
            if i >= 0:
                self.interp.setCurrentIndex(i)

    def _fill_recent(self):
        self.recent_menu.clear()
        for r in self.settings.value("solution/recent") or []:
            self.recent_menu.addAction(r, lambda r=r: self.set_root(r))
        if self.recent_menu.isEmpty():
            a = self.recent_menu.addAction(tr("mainwindow.status_empty"))
            a.setEnabled(False)

    def _update_title(self):
        ed = self.editor()
        parts = []
        if ed:
            parts.append(os.path.basename(ed.path) + ("*" if ed.document().isModified() else ""))
        if self.root:
            parts.append(os.path.basename(self.root.rstrip(os.sep)))
        parts.append(APP_NAME)
        self.setWindowTitle(" — ".join(parts))

    # ------------------------------------------------------------ вкладки
    def editors(self):
        return [self.tabs.widget(i) for i in range(self.tabs.count())]

    def editor(self):
        w = self.tabs.currentWidget()
        return w if isinstance(w, CodeEditor) else None

    def open_path(self, path, line=None, col=1):
        path = os.path.abspath(path)
        for i, ed in enumerate(self.editors()):
            if ed.path == path:
                self.tabs.setCurrentIndex(i)
                if line:
                    ed.go_to_line(line, col)
                return ed
        if not os.path.isfile(path):
            self.status(tr("mainwindow.file_not_found") + path)
            return None
        if os.path.getsize(path) > 5_000_000:
            QMessageBox.information(self, APP_NAME, tr("mainwindow.file_s_too_big_editor"))
            return None
        with open(path, "rb") as f:
            head = f.read(4096)
        if b"\0" in head and not head.startswith((b"\xff\xfe", b"\xfe\xff")):
            self.open_external(path)
            return None
        ed = CodeEditor(path, self, self.font)
        ed.setLineWrapMode(ed.LineWrapMode.WidgetWidth if self.word_wrap else ed.LineWrapMode.NoWrap)
        if not self.options["editor/folding"]:
            ed.folding.set_enabled(False)
            ed._update_gutter_width()
        ed.load()
        self.apply_indent(ed)
        ed.set_navigation_bar(self.options["editor/navigation_bar"])
        if lsp.is_metadata_path(path):
            ed.setReadOnly(True)        # исходник из сборки (UnityEngine.dll и т.п.)
        ed.cursor_moved.connect(self._cursor_moved)
        ed.diagnostics_changed.connect(lambda e=ed: self._diagnostics(e))
        ed.goto_requested.connect(lambda p, l: self.jump_to(p, l))
        ed.busy_changed.connect(self.busy)
        ed.references_found.connect(self.refactoring.show_references)
        ed.rename_ready.connect(self.refactoring.apply_rename)
        ed.textChanged.connect(self.outline_timer.start)
        ed.document().modificationChanged.connect(lambda _m, e=ed: self._modified(e))
        self.safety.track(ed)
        self.debugger.attach_editor(ed)
        self.vcs.attach_editor(ed)
        i = self.tabs.addTab(ed, theme.file_icon(path), os.path.basename(path))
        self.tabs.setTabToolTip(i, path)
        self.tabs.setCurrentIndex(i)
        self.tabs.show()
        self.empty.hide()
        self._diagnostics(ed)
        self._remember_open()
        if line:
            ed.go_to_line(line, col)
        ed.setFocus()
        return ed

    def _remember_open(self):
        if not self.screenshot_mode:
            self.settings.setValue("solution/open_files", [e.path for e in self.editors()])

    def _modified(self, ed):
        i = self.tabs.indexOf(ed)
        if i >= 0:
            self.tabs.setTabText(i, os.path.basename(ed.path) + (" ●" if ed.document().isModified() else ""))
        self._update_title()

    def _tab_changed(self, _i):
        ed = self.editor()
        self._update_title()
        self.refresh_outline()
        if ed:
            c = ed.textCursor()
            self._cursor_moved(c.blockNumber() + 1, c.positionInBlock() + 1)
            self.lang_label.setText(ed.language.name)
            self.enc_label.setText(ed.encoding_label())
            self.apply_indent(ed)               # .editorconfig мог измениться, пока вкладка была в фоне
            self._show_indent(ed)
            self.explorer.select_path(ed.path)
            self.errors.current = ed.path
            self.errors.render()
            if self.findbar.isVisible():
                self.findbar.update_matches()
        else:
            self.lang_label.setText("")
            self._show_indent(None)
        for e in self.editors():
            if e is not ed:
                e.find_text = ""

    def close_tab(self, i):
        if i < 0:
            return True
        ed = self.tabs.widget(i)
        if ed.document().isModified():
            r = QMessageBox.question(self, APP_NAME, tr("mainwindow.save_changes") % os.path.basename(ed.path),
                                     QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard |
                                     QMessageBox.StandardButton.Cancel)
            if r == QMessageBox.StandardButton.Cancel:
                return False
            if r == QMessageBox.StandardButton.Save and not ed.save():
                return False
        self.errors.set_group("mainwindow.editor", ed.path, [])
        self.errors.open_paths.discard(ed.path)
        self.safety.forget(ed)
        for svc in self.language_services:
            svc.close(ed.path)
        self.tabs.removeTab(i)
        ed.calltip.hide()
        ed.deleteLater()
        if not self.tabs.count():
            self.tabs.hide()
            self.empty.show()
        self._remember_open()
        self._update_title()
        return True

    def _tab_bar_menu(self, pos):
        bar = self.tabs.tabBar()
        self._tab_menu(bar.mapToGlobal(pos), bar.tabAt(pos))

    def _tab_menu(self, global_pos, index):
        """Правый клик по вкладке (или по пустому месту панели): закрыть, сохранить, открыть файл и т. д."""
        menu = QMenu(self)
        if index >= 0:
            ed, n = self.tabs.widget(index), self.tabs.count()
            menu.addAction(tr("mainwindow.save"), lambda: self._save_editor(ed)).setEnabled(ed.document().isModified())
            menu.addAction(tr("common.close"), lambda: self.close_tab(self.tabs.indexOf(ed)))
            menu.addAction(tr("mainwindow.close_others"), lambda: self._close_tabs(keep=ed)).setEnabled(n > 1)
            menu.addAction(tr("mainwindow.close_right"), lambda: self._close_tabs(right_of=ed)).setEnabled(index < n - 1)
            menu.addAction(tr("mainwindow.close_all"), self.close_all_tabs)
            menu.addSeparator()
            menu.addAction(tr("common.copy_path"), lambda: self.copy_text(ed.path))
            menu.addAction(tr("mainwindow.show_solution_explorer"),
                           lambda: (self.show_dock(self.d_explorer), self.explorer.select_path(ed.path)))
            menu.addAction(tr("common.open_folder_file_manager"),
                           lambda: self.open_external(os.path.dirname(ed.path)))
            menu.addSeparator()
        menu.addAction(self.a_new)
        menu.addAction(self.a_open_file)
        if index >= 0:
            menu.addAction(self.a_save_all)
        menu.exec(global_pos)
        menu.deleteLater()

    def _save_editor(self, ed):
        if ed.save():
            self.status(tr("mainwindow.saved") + os.path.basename(ed.path))

    def _close_tabs(self, keep=None, right_of=None):
        eds = self.editors()
        targets = eds[eds.index(right_of) + 1:] if right_of is not None else [e for e in eds if e is not keep]
        for ed in targets:
            if not self.close_tab(self.tabs.indexOf(ed)):
                break               # нажали «Отмена» при вопросе о сохранении

    def close_all_tabs(self):
        while self.tabs.count():
            if not self.close_tab(self.tabs.count() - 1):
                return False
        return True

    def save_current(self):
        ed = self.editor()
        if ed and ed.save():
            self.enc_label.setText(ed.encoding_label())
            self.status(tr("mainwindow.saved") + os.path.basename(ed.path))

    def save_all(self):
        """Сохраняет все изменённые вкладки. False — хотя бы одну сохранить не удалось (или отменили)."""
        n = 0
        ok = True
        for ed in self.editors():
            if ed.document().isModified():
                if ed.save():
                    n += 1
                else:
                    ok = False
        self.status(tr("mainwindow.saved_files") % n)
        return ok

    def tell_analyzers(self, changes):
        for svc in self.language_services:
            notify = getattr(svc, "files_changed", None)
            if notify:
                notify(changes)

    def on_path_renamed(self, old, new):
        self.tell_analyzers([(old, 3), (new, 1)])
        for i, ed in enumerate(self.editors()):
            if ed.path == old or ed.path.startswith(old + os.sep):
                before = ed.path
                ed.path = new + ed.path[len(old):]
                ed.disk.path = ed.path          # иначе старый путь «пропал бы» и вкладка считалась удалённой
                self.safety.renamed(before, ed.path)
                self.tabs.setTabText(i, os.path.basename(ed.path))
                self.tabs.setTabToolTip(i, ed.path)
        for path in self.debugger.store.paths():         # точки останова едут вместе с файлом или папкой
            if path == old or path.startswith(old + os.sep):
                self.debugger.store.rename(path, new + path[len(old):])

    def on_path_deleted(self, path):
        self.tell_analyzers([(path, 3)])
        for i in reversed(range(self.tabs.count())):
            ed = self.tabs.widget(i)
            if ed.path == path or ed.path.startswith(path + os.sep):
                ed.document().setModified(False)
                self.close_tab(i)

    # ------------------------------------------------------------ события редактора
    def _worker_result(self, res):
        for ed in self.editors():
            if id(ed) == res["editor"]:
                ed.on_worker_result(res)
                return

    def _lsp_diagnostics(self, path, diags):
        for ed in self.editors():
            if ed.path == path:
                ed.set_lsp_diagnostics(diags)

    def service_for(self, language):
        """Анализатор для языка (languages.Language) или None — для редактора."""
        return self.services_by_key.get(language.service) if language.service else None

    # ------------------------------------------------------------ перезапуск анализаторов

    def _cursor_moved(self, line, col):
        self.pos_label.setText(tr("mainwindow.ln_col") % (line, col))

    def _diagnostics(self, ed):
        self.errors.open_paths.add(ed.path)
        self.errors.set_group("mainwindow.editor", ed.path, ed.diagnostics)
        e, w = self.errors.counts()
        self.err_label.setText("✖ %d  ⚠ %d" % (e, w))

    def _unity_compile_changed(self, groups):
        """Ошибки компиляции из журнала Unity → «Список ошибок» (источник Unity)."""
        for path, items in groups.items():
            self.errors.set_group("Unity", path, items)
        e, w = self.errors.counts()
        self.err_label.setText("✖ %d  ⚠ %d" % (e, w))

    def refresh_outline(self):
        """Символы текущего файла → структура, панель навигации, Ctrl+T. От анализатора (documentSymbol),
        пока он не готов — из разбора текста; устаревший ответ (текст успел измениться) отбрасывается."""
        ed = self.editor()
        if not ed:
            self.outline.show_symbols(None, [])
            return
        text, revision = ed.toPlainText(), ed.document().revision()
        svc = self.service_for(ed.language)
        srv = svc.ready_for(ed.path) if svc is not None else None
        if srv is not None and srv.supports("documentSymbolProvider"):
            srv.document_symbols(ed.path, text, lambda found, e=ed, r=revision: self._symbols_arrived(e, r, found))
            return
        self._set_symbols(ed, symbols.from_outline(outline(text, ed.lang), ed.folding.ranges, ed.path))

    def _symbols_arrived(self, ed, revision, found):
        if ed not in self.editors():
            return
        if found is None:               # сервер не ответил — разбор текста
            found = symbols.from_outline(outline(ed.toPlainText(), ed.lang), ed.folding.ranges, ed.path)
        elif ed.document().revision() != revision:
            return                      # пока ждали, текст изменился — придёт новый ответ
        self._set_symbols(ed, found)

    def _set_symbols(self, ed, found):
        ed.set_symbols(found)
        if ed is self.editor():
            self.outline.show_symbols(ed.path, found)

    def goto_all(self, text=""):
        if not hasattr(self, "_goto_all"):
            self._goto_all = GoToAll(self)
        self._goto_all.popup(text)

    def _open_link(self, path, line):
        if not os.path.isabs(path) and self.runner.path:
            path = os.path.join(self.runner.path, path)        # папка, где запущена программа
        self.open_path(path, line)

    # ------------------------------------------------------------ диалоги
    def open_solution_dialog(self):
        """Как «Файл → Открыть → Проект или решение» в Visual Studio: выбрать .sln (у Unity он лежит в папке проекта)."""
        p, _ = QFileDialog.getOpenFileName(
            self, tr("mainwindow.open_project_solution"), self.root or os.path.expanduser("~"),
            tr("mainwindow.resolutions_projects_sln_slnx_csproj"))
        if p:
            self.set_root(os.path.dirname(os.path.abspath(p)))

    def new_project_dialog(self):
        """«Файл → Создать проект…»: шаблоны всех наборов инструментов (Python, .NET…)."""
        from .new_project import NewProjectDialog
        dialog = NewProjectDialog(self, toolchains.TOOLCHAINS, self._projects_folder())
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        template, folder, name, choices = dialog.result_values()
        self.settings.setValue("projects/folder", folder)
        job = template.toolchain.create_project(template, folder, name, choices)
        self.builder.run_job(job, tr("new_project.creating") % name, self._project_created)

    def _projects_folder(self):
        saved = self.settings.value("projects/folder")
        if saved and os.path.isdir(saved):
            return saved
        if self.root:
            return os.path.dirname(self.root.rstrip(os.sep))
        return os.path.expanduser("~")

    def _project_created(self, result):
        if not result.get("ok"):
            self.output.write(tr("new_project.failed"), "common.build", C["error"])
            self.status(tr("new_project.failed"), 15000)
            return
        root = result["root"]
        self.output.write(tr("new_project.created") % root, "common.build", C["ok"])
        if self.set_root(root):
            opened = next((p for p in result.get("open", []) if os.path.isfile(p)), None)
            if opened:
                self.open_path(opened)

    def open_folder_dialog(self):
        d = QFileDialog.getExistingDirectory(self, tr("mainwindow.open_folder_solution"), self.root or os.path.expanduser("~"))
        if d:
            self.set_root(d)

    def open_file_dialog(self):
        p, _ = QFileDialog.getOpenFileName(self, tr("mainwindow.open_file"), self.root or os.path.expanduser("~"),
                                           tr("mainwindow.code_py_cs_ui_json"))
        if p:
            self.open_path(p)

    def goto_line_dialog(self):
        ed = self.editor()
        if not ed:
            return
        n, ok = QInputDialog.getInt(self, tr("mainwindow.go_line"), tr("mainwindow.line_number_1") % ed.blockCount(),
                                    ed.textCursor().blockNumber() + 1, 1, ed.blockCount())
        if ok:
            ed.go_to_line(n)

    def _quick_open(self, rel):
        rel = rel.strip()
        if not rel or not self.root:
            return
        p = os.path.join(self.root, rel)
        if not os.path.isfile(p):
            matches = [f for f in self.explorer.files if rel.lower() in os.path.relpath(f, self.root).lower()]
            p = matches[0] if matches else None
        if p:
            self.open_path(p)
            self.quick.clear()

    def show_keys(self):
        from html import escape
        rows = []
        for action in self.actions():
            shortcuts = ", ".join(k.toString() for k in action.shortcuts())
            if shortcuts:
                rows.append("<tr><td><b>%s</b></td><td>%s</td></tr>" % (escape(shortcuts), escape(action.text())))
        rows.extend("<tr><td><b>%s</b></td><td>%s</td></tr>" % (escape(tr(keys)), escape(tr(text))) for keys, text in (
            ("Tab / Enter", "keys.accept_completion"),
            ("Tab / Shift+Tab", "keys.indent"),
            ("Escape", "keys.close_completions"), ("Ctrl+A", "keys.select_all"),
            ("Ctrl+Home / Ctrl+End", "keys.document_start_end"),
            ("Ctrl+← / Ctrl+→", "keys.word_navigation"),
            ("keys.shift_arrows", "keys.select_text"), ("keys.ctrl_click", "mainwindow.go_definition")))
        from PyQt6.QtWidgets import QTextBrowser
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("mainwindow.keyboard_shortcuts"))
        dialog.resize(760, 650)
        layout = QVBoxLayout(dialog)
        view = QTextBrowser()
        view.setHtml("<table cellpadding='5'>" + "".join(rows) + "</table>")
        layout.addWidget(view)
        dialog.exec()


    # ------------------------------------------------------------ тема
    def _fill_theme_menu(self):
        ctl = theme.CONTROLLER
        m = self.theme_menu
        m.clear()
        if ctl is None:
            return
        for mode, label in theme.MODES:
            a = m.addAction(tr(label), lambda md=mode: ctl.set_mode(md))
            a.setCheckable(True)
            a.setChecked(ctl.mode == mode)
        m.addSeparator()
        system = m.addAction(tr("mainwindow.system_default"), ctl.use_system)
        system.setCheckable(True)
        system.setChecked(ctl.source == theme.SYSTEM_SOURCE)
        own = m.addAction(tr("common.unipy_vs_theme"), ctl.use_unipy)
        own.setCheckable(True)
        own.setChecked(ctl.source == theme.UNIPY_SOURCE)
        if theme.KIT_AVAILABLE:
            m.addSeparator()
            for name in ctl.characters():
                a = m.addAction(name, lambda n=name: ctl.use_character(n))
                a.setCheckable(True)
                a.setChecked(ctl.source == "character" and ctl.kit_name == name)
        else:
            note = m.addAction(tr("mainwindow.character_themes_with_equestria_qt"))
            note.setEnabled(False)

    def _on_theme_changed(self):
        """Тема сменилась (персонаж, светлая/тёмная, KDE): перекрасить то, что рисуется кодом."""
        for obj in self.findChildren(QAction) + self.findChildren(QToolButton):
            name = obj.property("tool_icon")
            if isinstance(name, str):
                obj.setIcon(theme.tool_icon(name))
        for ed in self.editors():
            ed.apply_theme()
        self.output._rerender()
        self.errors.render()
        self.unity_console.refresh()
        self.explorer.refresh()
        if theme.CONTROLLER is not None:
            self.status(tr("mainwindow.theme_prefix") + theme.CONTROLLER.title())

    # ------------------------------------------------------------ обновления анализаторов
    def show_about(self):
        QMessageBox.about(self, APP_NAME, tr("mainwindow.code_editor_unity_c_python") % (
                              APP_NAME, "jedi " + intellisense.JEDI_VERSION if intellisense.JEDI_VERSION else tr("mainwindow.status_words"),
                              __import__("PyQt6.QtCore", fromlist=["x"]).PYQT_VERSION_STR))

    # ------------------------------------------------------------ запуск
    # ------------------------------------------------------------ проверка (сборка)
    # ------------------------------------------------------------ выход
    def closeEvent(self, e):
        search = getattr(self, "search_dialog", None)
        if search and search.worker and search.worker.isRunning():
            search.worker.requestInterruption()
            self.status(tr("common.stopping_search"))
            e.ignore()
            return
        if self.screenshot_mode:
            e.accept()
            return
        for ed in self.editors():
            if ed.document().isModified():
                r = QMessageBox.question(self, APP_NAME, tr("mainwindow.some_files_have_unsaved_changes"),
                                         QMessageBox.StandardButton.SaveAll | QMessageBox.StandardButton.Discard |
                                         QMessageBox.StandardButton.Cancel)
                if r == QMessageBox.StandardButton.Cancel:
                    e.ignore()
                    return
                if r == QMessageBox.StandardButton.SaveAll and not self.save_all():
                    e.ignore()          # что-то не сохранилось — не закрываемся, иначе правки пропадут
                    return
                break
        self.safety.shutdown()          # обычный выход: копии на случай падения больше не нужны
        self.settings.setValue("window/geometry", self.saveGeometry())
        self.settings.setValue("window/state", self.saveState())
        self.runner.shutdown()
        self.builder.cancel()
        self.debugger.shutdown()
        self.unity_console.watcher.stop()
        for svc in self.language_services:
            svc.stop()
        e.accept()

    def restore_layout(self):
        g = self.settings.value("window/geometry")
        s = self.settings.value("window/state")
        if g:
            self.restoreGeometry(g)
        if s:
            self.restoreState(s)

    def project_search(self):
        from .search import ProjectSearch
        if not hasattr(self, "search_dialog"):
            self.search_dialog = ProjectSearch(self)
        ed = self.editor()
        if ed and ed.textCursor().hasSelection():
            self.search_dialog.query.setPlainText(ed.textCursor().selectedText().replace("\u2029", "\n"))
        self.search_dialog.show()
        self.search_dialog.raise_()
        self.search_dialog.query.setFocus()

    def show_settings(self, page=None):
        OptionsDialog(self.options, self, page, extra_pages=(KeyboardPage(self.commands),)).exec()
