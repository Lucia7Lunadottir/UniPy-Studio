"""Главное окно UniPy Studio."""
import glob
import json
import os
import re
import sys

from PyQt6.QtCore import Qt, QTimer, QSettings, QProcess, QProcessEnvironment, QPoint, QStandardPaths, QSize
from PyQt6.QtGui import QAction, QActionGroup, QKeySequence, QTextDocument, QPainter, QGuiApplication, QDesktopServices, QTextCursor
from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import (QMainWindow, QTabWidget, QDockWidget, QWidget, QVBoxLayout, QHBoxLayout,
                             QLabel, QLineEdit, QToolButton, QComboBox, QFileDialog, QMessageBox,
                             QInputDialog, QCompleter, QFrame, QCheckBox, QApplication, QToolBar,
                             QSizePolicy, QPushButton, QDialog, QFormLayout, QSpinBox, QDialogButtonBox, QMenu)

from . import theme, templates, intellisense, lsp, updates, refactor
from .editor import CodeEditor, lang_for, check_csharp, decode_source
from . import languages
from .panels import (SolutionExplorer, OutlinePanel, OutputPanel, ErrorList, TerminalPanel, UnityPanel,
                     PyQtDesigner, NewFileDialog, BusyBar, ReferencesPanel, find_unity_projects, HIDDEN_DIRS)
from .theme import C
from .i18n import (LANGUAGE_NAMES, LANGUAGE_SETTING, bind_text, change_language,
                   plural, refresh_text, selected_language, tr)

APP_NAME = "UniPy Studio"


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
        self.font_size = int(self.settings.value("editor/font_size", 11))
        self.font = theme.code_font(self.font_size)
        self.hint_size = float(self.settings.value("editor/hint_size", 11))
        self._nav_back = []         # история переходов: (путь, строка, столбец), как «Назад» в Visual Studio
        self._nav_fwd = []
        self.word_wrap = self.settings.value("editor/word_wrap", False, type=bool)
        self.root = None
        self.proc = None
        self.run_path = None
        self.run_stderr = ""
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
            svc.status.connect(lambda text, state, k=key: self._lsp_status(text, state, k))
            svc.restarted.connect(lambda reason, k=key: self._lsp_restarted(k, reason))
            svc.restart_mb = int(self.settings.value("analyzers/restart_mb", 1024))

        # подсказки: сортировка, раньше выбранные одинаковые имена, подсказки из неподключённых пространств имён
        self.completion_alpha = self.settings.value("intellisense/alpha", False, type=bool)
        lsp.OPTIONS["unimported"] = self.settings.value("intellisense/unimported", True, type=bool)
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
        self.outline_timer = QTimer(self, singleShot=True, interval=400, timeout=self._refresh_outline)

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
        self.explorer.run_file.connect(lambda p: self.run_file(p))
        self.explorer.changed.connect(self._solution_changed)
        self.d_explorer = self._dock("mainwindow.solution_explorer", self.explorer,
                                     Qt.DockWidgetArea.LeftDockWidgetArea, "explorer")

        self.outline = OutlinePanel()
        self.outline.goto.connect(lambda line: self.editor() and self.editor().go_to_line(line))
        self.d_outline = self._dock("mainwindow.outline", self.outline, Qt.DockWidgetArea.LeftDockWidgetArea, "outline")
        self.splitDockWidget(self.d_explorer, self.d_outline, Qt.Orientation.Vertical)

        self.unity = UnityPanel(self.settings)
        self.unity.create_script.connect(self._create_unity_script)
        self.unity.insert_text.connect(self._insert_unity_message)
        self.unity.message.connect(lambda t, c: (self.output.write(t, "Unity", c), self.show_dock(self.d_output)))
        self.d_unity = self._dock("Unity", self.unity, Qt.DockWidgetArea.RightDockWidgetArea, "unity")

        self.designer = PyQtDesigner()
        self.designer.generated.connect(self._designer_generated)
        self.designer.new_template.connect(lambda i: self.new_file_dialog(template=i))
        self.designer.preview_ui.connect(self.preview_ui)
        self.d_designer = self._dock("mainwindow.pyqt_designer", self.designer, Qt.DockWidgetArea.RightDockWidgetArea, "designer")
        self.tabifyDockWidget(self.d_unity, self.d_designer)

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
        self.tabifyDockWidget(self.d_errors, self.d_references)
        self.tabifyDockWidget(self.d_errors, self.d_output)
        self.tabifyDockWidget(self.d_output, self.d_terminal)
        self.d_errors.raise_()
        self.resizeDocks([self.d_explorer, self.d_unity], [290, 370], Qt.Orientation.Horizontal)
        self.resizeDocks([self.d_errors], [230], Qt.Orientation.Vertical)
        self.resizeDocks([self.d_explorer, self.d_outline], [440, 200], Qt.Orientation.Vertical)

    def _act(self, text, slot, shortcut=None, icon=None, tip=None):
        a = bind_text(QAction(self), text)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        if icon:
            a.setIcon(theme.tool_icon(icon))
            a.setProperty("tool_icon", icon)        # значок перерисуется при смене темы
        if tip:
            bind_text(a, tip, "setToolTip")
        a.triggered.connect(lambda *_: slot())
        self.addAction(a)
        return a

    def _build_actions(self):
        A = self._act
        ed = lambda name: (lambda: self.editor() and getattr(self.editor(), name)())  # noqa
        self.a_new = A("common.new_file", lambda: self.new_file_dialog(), "Ctrl+N", "new", "mainwindow.add_new_item_ctrl_n")
        self.a_open_file = A("mainwindow.open_file_2", self.open_file_dialog, "Ctrl+O")
        self.a_open_solution = A("mainwindow.open_project_solution_2", self.open_solution_dialog, "Ctrl+Shift+O", "open",
                                 "mainwindow.open_project_solution_ctrl_shift")
        self.a_open = A("mainwindow.open_folder", self.open_folder_dialog, "Ctrl+Shift+Alt+O")
        self.a_sample = A("mainwindow.open_sample_solution", self.open_sample)
        self.a_save = A("mainwindow.save", self.save_current, "Ctrl+S", "save", "mainwindow.save_ctrl_s")
        self.a_save_all = A("mainwindow.save_all", self.save_all, "Ctrl+Shift+S", "saveall", "mainwindow.save_all_ctrl_shift_s")
        self.a_close = A("mainwindow.close_tab", lambda: self.close_tab(self.tabs.currentIndex()), "Ctrl+W")
        self.a_quit = A("mainwindow.exit", self.close, "Ctrl+Q")
        self.a_undo = A("mainwindow.undo", ed("undo"), "Ctrl+Z")
        self.a_redo = A("mainwindow.redo", ed("redo"), "Ctrl+Y")
        self.a_cut = A("mainwindow.cut", ed("cut"), "Ctrl+X")
        self.a_copy = A("mainwindow.copy", ed("copy"), "Ctrl+C")
        self.a_paste = A("mainwindow.paste", ed("paste"), "Ctrl+V")
        self.a_project_find = A("mainwindow.find_in_files", self.project_search, "Ctrl+Shift+F")
        self.a_project_replace = A("mainwindow.replace_in_files", self.project_search, "Ctrl+Shift+H")
        self.a_settings = A("mainwindow.settings", self.show_settings, "Ctrl+,")
        self.a_next_tab = A("mainwindow.next_tab", lambda: self.tabs.setCurrentIndex((self.tabs.currentIndex() + 1) % max(1, self.tabs.count())), "Ctrl+Tab")
        self.a_prev_tab = A("mainwindow.previous_tab", lambda: self.tabs.setCurrentIndex((self.tabs.currentIndex() - 1) % max(1, self.tabs.count())), "Ctrl+Shift+Tab")
        self.a_redo.setShortcuts([QKeySequence("Ctrl+Y"), QKeySequence("Ctrl+Shift+Z")])
        self.a_find = A("mainwindow.find_2", lambda: self.findbar.open(False), "Ctrl+F", "find")
        self.a_replace = A("mainwindow.replace_2", lambda: self.findbar.open(True), "Ctrl+H")
        self.a_find_next = A("mainwindow.find_next", lambda: self.findbar.step(True), "F3")
        self.a_find_prev = A("mainwindow.find_previous", lambda: self.findbar.step(False), "Shift+F3")
        self.a_goto = A("mainwindow.go_line_2", self.goto_line_dialog, "Ctrl+G")
        self.a_comment = A("mainwindow.toggle_comment", ed("toggle_comment"), "Ctrl+/")
        self.a_dup = A("mainwindow.duplicate_line", ed("duplicate_line"), "Ctrl+D")
        self.a_complete = A("mainwindow.member_list_completion", lambda: self.editor() and self.editor().request_completion(True), "Ctrl+Space")
        self.a_params = A("mainwindow.parameter_info", lambda: self.editor() and self.editor().request_signatures(True), "Ctrl+Shift+Space")
        self.a_definition = A("mainwindow.go_definition", ed("go_to_definition"), "F12")
        self.a_quick = A("mainwindow.go_file", lambda: (self.quick.setFocus(), self.quick.selectAll()), "Ctrl+P")
        # масштаб кода — клавиши Visual Studio (Ctrl+Shift+. / Ctrl+Shift+,); Ctrl+− в VS — «Назад»
        self.a_zoom_in = A("mainwindow.increase_font_size", lambda: self.zoom(1), "Ctrl+Shift+.")
        self.a_zoom_in.setShortcuts([QKeySequence("Ctrl+Shift+."), QKeySequence("Ctrl+=")])
        self.a_zoom_out = A("mainwindow.decrease_font_size", lambda: self.zoom(-1), "Ctrl+Shift+,")
        self.a_back = A("mainwindow.back", lambda: self.navigate(-1), "Ctrl+-", "back", "mainwindow.back_ctrl")
        self.a_fwd = A("mainwindow.forward", lambda: self.navigate(1), "Ctrl+Shift+-", "forward", "mainwindow.forward_ctrl_shift")
        self.a_refs = A("mainwindow.find_all_references", ed("find_references"), "Shift+F12")
        self.a_rename = A("common.rename_2", self.rename_dialog, "Ctrl+R, Ctrl+R")
        self.a_rename.setShortcuts([QKeySequence("Ctrl+R, Ctrl+R"), QKeySequence("F2")])
        self.a_move_type = A("common.move_type_own_file", self.move_type_to_file, "Ctrl+R, Ctrl+F")
        self.a_unimported = A("mainwindow.suggestions_from_unimported_namespaces", self.toggle_unimported)
        self.a_unimported.setCheckable(True)
        self.a_unimported.setChecked(lsp.OPTIONS["unimported"])
        self.a_hint_in = A("mainwindow.increase_tooltip_size", lambda: self.zoom_hints(1), "Ctrl+Alt+=")
        self.a_hint_out = A("mainwindow.decrease_tooltip_size", lambda: self.zoom_hints(-1), "Ctrl+Alt+-")
        self.a_run = A("common.run", self.run_selected, "F5", "run", "mainwindow.run_f5")
        self.a_run2 = A("mainwindow.run_without_debugging", self.run_selected, "Ctrl+F5")
        self.a_stop = A("mainwindow.stop", self.stop_run, "Shift+F5", "stop", "mainwindow.stop_shift_f5")
        self.a_stop.setEnabled(False)
        self.a_build = A("mainwindow.check_solution", self.build_solution, "Ctrl+Shift+B", "build", "mainwindow.check_solution_ctrl_shift_b")
        self.a_build_file = A("mainwindow.check_current_file", self.build_current, "Ctrl+F7")
        self.a_unity = A("common.open_unity", lambda: self.unity.open_in_unity(), None, "unity", "mainwindow.open_project_unity")
        self.a_hub = A("mainwindow.open_unity_hub", lambda: self.unity.open_hub())
        self.a_unity_script = A("mainwindow.new_unity_script", lambda: self.new_file_dialog(template=5))
        self.a_pyqt_window = A("mainwindow.new_pyqt_window", lambda: self.new_file_dialog(template=0))
        self.a_pip = A("mainwindow.install_requirements", self.install_requirements)
        self.a_designer_ui = A("mainwindow.preview_ui_form", self.preview_ui)
        self.a_keys = A("mainwindow.keyboard_shortcuts", self.show_keys, "F1")
        self.a_about = A("mainwindow.about", self.show_about)
        self.a_updates = A("mainwindow.analyzer_updates", self.show_updates)

    def _build_menus(self):
        mb = self.menuBar()
        m = bind_text(mb.addMenu(""), "common.file", "setTitle")
        for a in (self.a_new, self.a_open_solution, self.a_open, self.a_open_file, self.a_sample):
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
                  self.a_find, self.a_replace, self.a_project_find, self.a_project_replace, self.a_goto, None, self.a_comment, self.a_dup):
            m.addSeparator() if a is None else m.addAction(a)
        sub = m.addMenu("IntelliSense")
        for a in (self.a_complete, self.a_params, self.a_definition, self.a_refs):
            sub.addAction(a)
        m.addAction(self.a_rename)
        m.addAction(self.a_move_type)
        m = bind_text(mb.addMenu(""), "mainwindow.view", "setTitle")
        for d in (self.d_explorer, self.d_outline, self.d_unity, self.d_designer, self.d_output,
                  self.d_errors, self.d_references, self.d_terminal):
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
        m.addAction(self.a_build)
        m.addAction(self.a_build_file)
        m = bind_text(mb.addMenu(""), "mainwindow.debug", "setTitle")
        for a in (self.a_run, self.a_run2, self.a_stop):
            m.addAction(a)
        m = bind_text(mb.addMenu(""), "mainwindow.tools", "setTitle")
        m.addAction(self.a_settings)
        bind_text(m.addAction("", lambda: self.show_dock(self.d_terminal)), "mainwindow.terminal")
        bind_text(m.addAction("", lambda: self.show_dock(self.d_designer)), "mainwindow.pyqt_designer")
        bind_text(m.addAction("", self._pick_unity_root), "mainwindow.folder_with_version_unity")
        m.addSeparator()
        bind_text(m.addAction("", self.restart_analyzers), "mainwindow.restart_code_analyzers")
        self.restart_menu = bind_text(m.addMenu(""), "mainwindow.restart_analyzer_above_memory_limit", "setTitle")
        self.restart_menu.aboutToShow.connect(self._fill_restart_menu)
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
        self.designer.retranslate()
        if self.startup.count():
            self.startup.setItemText(0, tr("mainwindow.current_file"))
        self._refresh_analyzer_label()
        self.status(tr("mainwindow.running_ellipsis") if self._running() else tr("common.ready"), 0)
        ed = self.editor()
        if ed:
            cursor = ed.textCursor()
            self._cursor_moved(cursor.blockNumber() + 1, cursor.positionInBlock() + 1)
            self.enc_label.setText(ed.encoding_label())
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
        self._analyzer_status = None
        self._refresh_analyzer_label()
        self.pos_label = bind_text(QLabel(), "mainwindow.ln_1_col_1")
        self.indent_label = bind_text(QLabel(), "mainwindow.spaces_4")
        self.enc_label = QLabel("UTF-8")
        self.lang_label = QLabel("")
        self.busy_bar = BusyBar()
        self._busy_timeout = QTimer(self, singleShot=True, interval=30000,
                                    timeout=lambda: self._busy("", tr("mainwindow.server_failed_respond_definition_not")))
        for w in (self.busy_bar, self.err_label, self.jedi_label, self.pos_label, self.indent_label,
                  self.enc_label, self.lang_label):
            sb.addPermanentWidget(w)

    # ------------------------------------------------------------ сервисы для редактора
    def status(self, text, timeout=6000):
        self.status_label.setText(text)
        if timeout:
            QTimer.singleShot(timeout, lambda: self.status_label.text() == text and self.status_label.setText(
                tr("mainwindow.running_ellipsis") if self._running() else tr("common.ready")))

    def zoom(self, delta):
        self.font_size = max(7, min(32, self.font_size + delta))
        self.settings.setValue("editor/font_size", self.font_size)
        self.font = theme.code_font(self.font_size)
        for ed in self.editors():
            ed.set_code_font(self.font)
        self.status(tr("mainwindow.font_size") % self.font_size)

    # ------------------------------------------------------------ навигация (Ctrl+− / Ctrl+Shift+−)
    def _here(self):
        ed = self.editor()
        if ed is None:
            return None
        c = ed.textCursor()
        return ed.path, c.blockNumber() + 1, c.positionInBlock() + 1

    def jump_to(self, path, line, col=1):
        """Переход (определение, ссылка), который запоминается для «Назад»."""
        here = self._here()
        ed = self.open_path(path, line, col)
        if ed is not None and here and here != self._here():
            self._nav_back.append(here)
            del self._nav_back[:-100]
            self._nav_fwd.clear()
        return ed

    def navigate(self, direction):
        src, dst = (self._nav_back, self._nav_fwd) if direction < 0 else (self._nav_fwd, self._nav_back)
        here = self._here()
        while src:
            path, line, col = src.pop()
            if os.path.isfile(path):
                if here:
                    dst.append(here)
                self.open_path(path, line, col)
                return
        self.status(tr("mainwindow.no_back_history") if direction < 0 else tr("mainwindow.no_forward_history"))

    # ------------------------------------------------------------ ссылки и переименование
    def _line_text(self, path, line):
        for ed in self.editors():
            if ed.path == path:
                block = ed.document().findBlockByNumber(line - 1)
                return block.text() if block.isValid() else ""
        try:
            with open(path, "rb") as f:
                text = decode_source(f.read())[1]
        except OSError:
            return ""
        lines = text.split("\n")
        return lines[line - 1].rstrip("\r") if 0 < line <= len(lines) else ""

    def _show_references(self, word, refs):
        if not refs:
            self.status(tr("mainwindow.no_references_found") % word)
            return
        self.references.show_results(word, refs, self.root, self._line_text)
        self.show_dock(self.d_references)
        self.status(tr("mainwindow.references_found") % len(refs))

    def rename_dialog(self):
        ed = self.editor()
        if ed is None or ed.isReadOnly():
            return
        word = ed.symbol_under_cursor()
        if not word or not re.fullmatch(r"[A-Za-z_]\w*", word):
            self.status(tr("mainwindow.put_cursor_name_needs_renamed"))
            return
        name, ok = QInputDialog.getText(self, tr("common.rename"), tr("mainwindow.new_name") % word, text=word)
        name = name.strip()
        if ok and name and name != word:
            if not re.fullmatch(r"[A-Za-z_]\w*", name):
                QMessageBox.warning(self, APP_NAME, tr("mainwindow.invalid_name") % name)
                return
            self._rename_from = (word, ed.path)
            ed.rename_symbol(name)

    def _apply_rename(self, new_name, data):
        """Правки переименования: открытые файлы меняются в редакторе (Ctrl+Z отменит), закрытые —
        пишутся на диск в их же кодировке и с их переводами строк."""
        if data.get("error"):
            QMessageBox.warning(self, APP_NAME, tr("mainwindow.rename_failed") % data["error"])
            return
        open_eds = {ed.path: ed for ed in self.editors()}
        edits = dict(data.get("edits") or {})
        skipped = []
        for path, new_code in (data.get("files") or {}).items():       # jedi: файл целиком
            ed = open_eds.get(path)
            if ed is not None and path != getattr(self, "_rename_from", ("", ""))[1]:
                try:
                    with open(path, "rb") as f:
                        on_disk = decode_source(f.read())[1].replace("\r\n", "\n")
                except OSError:
                    on_disk = None
                if on_disk != ed.toPlainText():         # jedi считал с диска, а в редакторе несохранённые правки
                    skipped.append(path)
                    continue
            base = ed.toPlainText() if ed is not None else None
            if base is None:
                with open(path, "rb") as f:
                    base = decode_source(f.read())[1].replace("\r\n", "\n")
            edits[path] = lsp.line_diff_edits(base, new_code.replace("\r\n", "\n"))
        changed_files, changed_places, failed = 0, 0, []
        for path, items in edits.items():
            if not items:
                continue
            if lsp.is_metadata_path(path):
                continue
            ed = open_eds.get(path)
            if ed is not None:
                ed.apply_edits(items)
            else:
                try:
                    with open(path, "rb") as f:
                        enc, text = decode_source(f.read())
                    newline = "\r\n" if "\r\n" in text else "\n"
                    text = lsp.apply_text_edits(text.replace("\r\n", "\n"), items)
                    data_out = text.replace("\n", newline).encode(enc)
                except (OSError, UnicodeEncodeError) as e:
                    failed.append("%s (%s)" % (os.path.basename(path), e))
                    continue
                with open(path, "wb") as f:
                    f.write(data_out)
            changed_files += 1
            changed_places += len(items)
            self.output.write(tr("mainwindow.rename") % (os.path.relpath(path, self.root or "/"), len(items)),
                              "common.build", C["dim"])
        msg = tr("mainwindow.updated_occurrences") % (
            getattr(self, "_rename_from", ("?",))[0], new_name, changed_places, changed_files,
            plural(changed_files, "common.file_one", "common.files_few", "common.files_few"))
        self.status(msg)
        problems = []
        if skipped:
            problems.append(tr("mainwindow.skipped_unsaved_changes_editor") +
                            "\n".join(os.path.basename(p) for p in skipped))
        if failed:
            problems.append(tr("mainwindow.couldn_t_write") + "\n".join(failed))
        old, src = getattr(self, "_rename_from", ("", ""))
        if src.endswith(".cs") and os.path.splitext(os.path.basename(src))[0] == old:
            problems.append(tr("mainwindow.name_matches_file_name_unity") % os.path.basename(src))
        if problems:
            QMessageBox.information(self, APP_NAME, msg + "\n\n" + "\n\n".join(problems))
        self._refresh_outline()

    def _busy(self, text, done_message=None):
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
        self.completion_alpha = bool(alpha)
        self.settings.setValue("intellisense/alpha", self.completion_alpha)

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
        lsp.OPTIONS["unimported"] = self.a_unimported.isChecked()
        self.settings.setValue("intellisense/unimported", lsp.OPTIONS["unimported"])
        for svc in self.language_services:
            svc.configuration_changed()
        self.status(tr("mainwindow.suggestions_from_unimported_namespaces_2") +
                    (tr("mainwindow.on") if lsp.OPTIONS["unimported"] else tr("mainwindow.off_faster")))

    def zoom_hints(self, delta):
        """Размер подсказок — отдельно от размера кода."""
        self.hint_size = max(7.0, min(24.0, self.hint_size + delta))
        self.settings.setValue("editor/hint_size", self.hint_size)
        for ed in self.editors():
            ed.apply_hint_size()
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
        if unity:
            self.output.write(tr("mainwindow.found_unity_project") % ", ".join(os.path.relpath(u, root) or "." for u in unity),
                              "Unity", C["dim"])
        return True

    def _solution_changed(self):
        files = self.explorer.files
        rels = [os.path.relpath(p, self.root) for p in files]
        self.quick_completer.model().setStringList(rels)
        current = self.startup.currentData()
        self.startup.blockSignals(True)
        self.startup.clear()
        self.startup.addItem(theme.badge("py", "#3572A5", "#FFD43B"), tr("mainwindow.current_file"), "")
        mains = []
        for p in files:
            if not p.endswith(".py"):
                continue
            if os.path.basename(p) == "main.py":
                mains.insert(0, p)
                continue
            try:
                if os.path.getsize(p) < 200000 and "__main__" in open(p, encoding="utf-8", errors="ignore").read():
                    mains.append(p)
            except OSError:
                pass
        for p in mains[:40]:
            self.startup.addItem(theme.file_icon(p), os.path.relpath(p, self.root), p)
        idx = self.startup.findData(current) if current else (1 if mains else 0)
        self.startup.setCurrentIndex(max(0, idx))
        self.startup.blockSignals(False)
        self._fill_interpreters()

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
        ed.load()
        if lsp.is_metadata_path(path):
            ed.setReadOnly(True)        # исходник из сборки (UnityEngine.dll и т.п.)
        ed.cursor_moved.connect(self._cursor_moved)
        ed.diagnostics_changed.connect(lambda e=ed: self._diagnostics(e))
        ed.goto_requested.connect(lambda p, l: self.jump_to(p, l))
        ed.busy_changed.connect(self._busy)
        ed.references_found.connect(self._show_references)
        ed.rename_ready.connect(self._apply_rename)
        ed.textChanged.connect(self.outline_timer.start)
        ed.document().modificationChanged.connect(lambda _m, e=ed: self._modified(e))
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
        self._refresh_outline()
        if ed:
            c = ed.textCursor()
            self._cursor_moved(c.blockNumber() + 1, c.positionInBlock() + 1)
            self.lang_label.setText(ed.language.name)
            self.enc_label.setText(ed.encoding_label())
            self.explorer.select_path(ed.path)
            self.errors.current = ed.path
            self.errors.render()
            if self.findbar.isVisible():
                self.findbar.update_matches()
        else:
            self.lang_label.setText("")
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
        n = 0
        for ed in self.editors():
            if ed.document().isModified() and ed.save():
                n += 1
        self.status(tr("mainwindow.saved_files") % n)

    def move_type_to_file(self):
        """Тип под курсором → в новый файл <Имя>.cs рядом (как «Move type to …cs» в Visual Studio).
        Если анализатор C# готов, лишние using не попадают в новый файл, а нужные только этому типу
        убираются из старого."""
        ed = self.editor()
        if not ed or ed.lang != "csharp" or ed.isReadOnly():
            self.status(tr("mainwindow.open_your_own_c_file"))
            return
        text = ed.toPlainText()
        pos = len(text.encode("utf-16-le")[:ed.textCursor().position() * 2].decode("utf-16-le", "ignore"))
        try:
            plan = refactor.move_type_plan(text, pos)
        except refactor.RefactorError as exc:
            self.status(str(exc), 12000)
            return
        target = os.path.join(os.path.dirname(ed.path), plan["file_name"])
        if os.path.exists(target):
            QMessageBox.information(self, APP_NAME, tr("mainwindow.file_already_exists_type_was") % plan["file_name"])
            return
        svc = self.service_for(ed.language)
        srv = svc.ready_for(ed.path) if svc is not None else None
        if srv is None or not hasattr(srv, "usings_report"):
            self._finish_move_type(ed, text, plan, target, None)
            return
        # Анализатор по очереди «видит» два варианта этого файла: только переносимый тип и всё остальное,
        # и говорит, какие using в каждом лишние. Файл на диске и вкладка при этом не меняются.
        revision = ed.document().revision()
        a, b = plan["remove"]
        rest_text = text[:a] + text[b:]
        timeout = QTimer(self)
        timeout.setSingleShot(True)
        state = {"done": False}

        def finish(report):
            if state["done"]:
                return
            state["done"] = True
            timeout.stop()
            timeout.deleteLater()
            self._busy("")
            svc.sync(ed.path, ed.toPlainText())        # серверу — обратно настоящий текст
            if ed.document().revision() != revision:
                self.status(tr("mainwindow.text_changed_during_analysis_repeat"))
                return
            self._finish_move_type(ed, text, plan, target, report, rest_text)

        def after_type(report_type):
            if state["done"]:
                return
            if report_type is None:
                finish(None)
                return
            srv.usings_report(ed.path, rest_text, lambda r: finish({"type": report_type, "rest": r}))

        timeout.timeout.connect(lambda: finish(None))
        timeout.start(12000)
        self._busy(tr("mainwindow.checking_which_usings_needed"))
        srv.usings_report(ed.path, plan["new_text"], after_type)

    def _finish_move_type(self, ed, text, plan, target, report, rest_text=None):
        a, b = plan["remove"]
        if rest_text is None:
            rest_text = text[:a] + text[b:]
        new_text = plan["new_text"]
        final_text = rest_text
        removed_new = removed_old = 0
        note = ""
        if report is None:
            note = tr("mainwindow.usings_were_not_checked_c")
        else:
            l0, l1 = plan["lines"]
            errors = [d for d in ed.diagnostics if d[3] == "error"] if ed.lsp_diagnostics is not None else []
            base_type = sum(1 for d in errors if l0 <= d[0] - 1 <= l1)      # ошибки, что были в файле и до переноса
            base_rest = len(errors) - base_type
            rt, rr = report["type"], report.get("rest")
            # Если из-за переноса появились новые ошибки (тип ссылается на соседей и т. п.), анализатор мог
            # посчитать нужный using лишним — тогда ничего не убираем, чтобы не сломать сборку.
            if rt["errors"] <= base_type:
                t_map = refactor.using_lines(new_text)
                unused_type = {t for ln, t in t_map if ln in set(rt["unused"])}
                drop_new = {ln for ln, t in t_map if t in unused_type}
                new_text = refactor.remove_usings(new_text, drop_new)
                removed_new = len(drop_new)
                if rr is not None and rr["errors"] <= base_rest:
                    r_map = refactor.using_lines(rest_text)
                    unused_rest = {t for ln, t in r_map if ln in set(rr["unused"])}
                    only_for_type = unused_rest - unused_type       # нужны перенесённому типу, остальному коду — нет
                    drop_old = {ln for ln, t in r_map if t in only_for_type}
                    final_text = refactor.remove_usings(rest_text, drop_old)
                    removed_old = len(drop_old)
                else:
                    note = tr("mainwindow.usings_old_file_left_as")
            else:
                note = tr("mainwindow.usings_left_as_errors_appeared")
        data = new_text.replace("\n", ed.newline)
        try:
            raw = data.encode(ed.encoding)
        except UnicodeEncodeError:
            raw = data.encode("utf-8-sig")
        try:
            with open(target, "wb") as f:
                f.write(raw)
        except OSError as exc:
            QMessageBox.warning(self, APP_NAME, tr("mainwindow.could_not_create_file") + str(exc))
            return
        ed.apply_edits(lsp.line_diff_edits(text, final_text))       # одна правка — одно Ctrl+Z
        ed.save()       # иначе до сохранения тип лежал бы в двух файлах сразу и Unity показал бы ошибку
        self._tell_analyzers([(target, 1)])
        self.open_path(target)
        msg = tr("mainwindow.type_moved") % (plan["name"], plan["file_name"])
        if removed_new or removed_old:
            msg += tr("mainwindow.unnecessary_usings_removed_new_file") % (removed_new, removed_old)
        self.status(msg + note, 12000)

    def apply_code_action(self, edit, title):
        """Правки выбранного действия лампочки (быстрое исправление или рефакторинг сервера)."""
        if not edit:
            self.status(tr("mainwindow.server_returned_no_edits_action"))
            return
        data = lsp.LanguageServer.workspace_edit_files(edit)
        failed = []
        for path in data["creates"]:
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                if not os.path.exists(path):
                    open(path, "wb").close()
            except OSError as exc:
                failed.append("%s (%s)" % (os.path.basename(path), exc))
        open_eds = {e.path: e for e in self.editors()}
        changed = 0
        for path, items in data["edits"].items():
            if not items or lsp.is_metadata_path(path):
                continue
            e = open_eds.get(path)
            if e is not None:
                e.apply_edits(items)            # в открытом файле — одним шагом отмены
            else:
                try:
                    with open(path, "rb") as f:
                        enc, old = decode_source(f.read())
                    newline = "\r\n" if "\r\n" in old else "\n"
                    new = lsp.apply_text_edits(old.replace("\r\n", "\n"), items)
                    with open(path, "wb") as f:
                        f.write(new.replace("\n", newline).encode(enc))
                except (OSError, UnicodeError) as exc:
                    failed.append("%s (%s)" % (os.path.basename(path), exc))
                    continue
            changed += 1
        for path in data["creates"]:
            if os.path.isfile(path):
                self.open_path(path)
        self._tell_analyzers([(p, 1) for p in data["creates"]])
        self.status(tr("mainwindow.done") + title, 8000)
        if failed:
            QMessageBox.warning(self, APP_NAME, tr("mainwindow.could_not_apply") + "\n".join(failed))
        self._refresh_outline()

    def _tell_analyzers(self, changes):
        for svc in self.language_services:
            notify = getattr(svc, "files_changed", None)
            if notify:
                notify(changes)

    def on_path_renamed(self, old, new):
        self._tell_analyzers([(old, 3), (new, 1)])
        for i, ed in enumerate(self.editors()):
            if ed.path == old or ed.path.startswith(old + os.sep):
                ed.path = new + ed.path[len(old):]
                self.tabs.setTabText(i, os.path.basename(ed.path))
                self.tabs.setTabToolTip(i, ed.path)

    def on_path_deleted(self, path):
        self._tell_analyzers([(path, 3)])
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
    RESTART_THRESHOLDS = ((0, "mainwindow.do_not_restart"), (800, "mainwindow.800_mb"), (1024, "mainwindow.1_gb"), (1536, "mainwindow.1_5_gb"), (2048, "mainwindow.2_gb"))

    def restart_analyzers(self):
        for svc in self.language_services:
            svc.restart(reason=tr("mainwindow.by_menu_command"))

    def _fill_restart_menu(self):
        m = self.restart_menu
        m.clear()
        current = int(self.settings.value("analyzers/restart_mb", 1024))
        for mb, label in self.RESTART_THRESHOLDS:
            a = m.addAction(tr(label), lambda v=mb: self._set_restart_mb(v))
            a.setCheckable(True)
            a.setChecked(mb == current)

    def _set_restart_mb(self, mb):
        self.settings.setValue("analyzers/restart_mb", mb)
        for svc in self.language_services:
            svc.restart_mb = mb
        self.status(tr("mainwindow.restarting_analyzer") + tr(dict(self.RESTART_THRESHOLDS)[mb]) +
                    (tr("mainwindow.idle_2_minutes") if mb else ""))

    def _lsp_restarted(self, key, reason):
        name = {"csharp": "C#", "python": "Python"}.get(key, key)
        self.status(tr("mainwindow.analyzer_restarted") % (name, reason), 10000)
        self.output.write(tr("mainwindow.analyzer_restarted") % (name, reason), "common.build", C["dim"])
        for ed in self.editors():
            if ed.language.service == key:
                ed.lsp_diagnostics = None
                ed.run_diagnostics()        # отдаёт текст новому серверу (он запустится сам)

    def _lsp_status(self, text, state, key):
        if not text:
            return
        self.status(text)
        lang = text.split(":", 1)[0]
        self._analyzer_status = lang, state
        self._refresh_analyzer_label()
        if state == "ready":
            for ed in self.editors():
                if ed.language.service == key:
                    ed.run_diagnostics()

    def _refresh_analyzer_label(self):
        if self._analyzer_status is None:
            text = tr("mainwindow.intellisense_ready") if self.jedi.available else tr("mainwindow.intellisense_words")
        else:
            lang, state = self._analyzer_status
            label = {"loading": "mainwindow.status_loading", "ready": "mainwindow.status_ready"}.get(state, "mainwindow.status_stopped")
            text = "%s: %s" % (lang, tr(label))
        self.jedi_label.setText(text)

    def _cursor_moved(self, line, col):
        self.pos_label.setText(tr("mainwindow.ln_col") % (line, col))

    def _diagnostics(self, ed):
        self.errors.open_paths.add(ed.path)
        self.errors.set_group("mainwindow.editor", ed.path, ed.diagnostics)
        e, w = self.errors.counts()
        self.err_label.setText("✖ %d  ⚠ %d" % (e, w))

    def _refresh_outline(self):
        ed = self.editor()
        if ed:
            self.outline.update_for(ed.path, ed.toPlainText(), ed.lang)
        else:
            self.outline.update_for(None, "", "")

    def _open_link(self, path, line):
        if not os.path.isabs(path) and self.run_path:
            path = os.path.join(os.path.dirname(self.run_path), path)
        self.open_path(path, line)

    # ------------------------------------------------------------ диалоги
    def open_solution_dialog(self):
        """Как «Файл → Открыть → Проект или решение» в Visual Studio: выбрать .sln (у Unity он лежит в папке проекта)."""
        p, _ = QFileDialog.getOpenFileName(
            self, tr("mainwindow.open_project_solution"), self.root or os.path.expanduser("~"),
            tr("mainwindow.resolutions_projects_sln_slnx_csproj"))
        if p:
            self.set_root(os.path.dirname(os.path.abspath(p)))

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

    def _default_folder(self, where):
        if not self.root:
            return ""
        unity = find_unity_projects(self.root)
        if where == "unity" and unity:
            return os.path.join(unity[0], "Assets", "Scripts")
        if where == "unity_editor" and unity:
            return os.path.join(unity[0], "Assets", "Editor")
        if where == "python":
            ed = self.editor()
            if ed and ed.path.endswith(".py"):
                return os.path.dirname(ed.path)
            for p in self.explorer.files:
                if p.endswith(".py"):
                    return os.path.dirname(p)
        return self.root

    def new_file_dialog(self, folder=None, template=0):
        if not self.root:
            self.open_folder_dialog()
            if not self.root:
                return
        folders = []
        for dirpath, dirnames, _ in os.walk(self.root):
            dirnames[:] = sorted(d for d in dirnames if d not in HIDDEN_DIRS and not d.startswith("."))
            folders.append(dirpath)
            if len(folders) > 300:
                break
        default = (lambda where: folder) if folder else self._default_folder
        dlg = NewFileDialog(self, folders, default, template)
        if dlg.exec():
            idx, name, target = dlg.result_values()
            self.create_from_template(idx, name, target)

    def create_from_template(self, idx, name, folder):
        label, ext, text, _where = templates.TEMPLATES[idx]
        name = name or "new_file"
        base = name[:-len(ext)] if name.endswith(ext) else name
        if ext == ".cs":
            base = re.sub(r"\W", "", base) or "NewScript"
        path = os.path.join(folder, base + ext)
        if os.path.exists(path):
            QMessageBox.warning(self, APP_NAME, tr("mainwindow.file_already_exists") + path)
            self.open_path(path)
            return
        os.makedirs(folder, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(templates.render(text, base))
        self.explorer.refresh()
        self.open_path(path)
        self.output.write(tr("mainwindow.file_created") % os.path.relpath(path, self.root), "common.build", C["dim"])

    def _create_unity_script(self, name, idx):
        if not find_unity_projects(self.root):
            QMessageBox.information(self, APP_NAME, tr("mainwindow.there_s_no_unity_project"))
            return
        self.create_from_template(idx, name, self._default_folder(templates.TEMPLATES[idx][3]))

    def _insert_unity_message(self, sig):
        ed = self.editor()
        if not ed or ed.lang != "csharp":
            self.status(tr("mainwindow.open_c_script_insert_method"))
            return
        c = ed.textCursor()
        line = c.block().text()
        indent = re.match(r"[ \t]*", line).group(0) or "    "
        if line.strip():
            c.movePosition(c.MoveOperation.EndOfBlock)
            c.insertText("\n\n" + indent)
        elif c.positionInBlock() < len(indent):
            c.movePosition(c.MoveOperation.EndOfBlock)
        body = "%s\n%s{\n%s    \n%s}" % (sig, indent, indent, indent)
        c.insertText(body)
        c.movePosition(c.MoveOperation.Up)
        c.movePosition(c.MoveOperation.EndOfBlock)
        ed.setTextCursor(c)
        ed.setFocus()

    def _designer_generated(self, fname, code):
        folder = self._default_folder("python")
        path = os.path.join(folder, fname)
        n = 2
        while os.path.exists(path):
            path = os.path.join(folder, fname[:-3] + "_%d.py" % n)
            n += 1
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)
        self.explorer.refresh()
        self.open_path(path)
        self.output.write(tr("mainwindow.pyqt_designer_created_click_run") %
                          os.path.relpath(path, self.root), "common.build", C["text"])

    def preview_ui(self):
        ed = self.editor()
        if not ed or not ed.path.endswith(".ui"):
            QMessageBox.information(self, APP_NAME, tr("mainwindow.open_file_form_ui_editor"))
            return
        import io
        from PyQt6 import uic
        try:
            w = uic.loadUi(io.StringIO(ed.toPlainText()))
        except Exception as e:  # noqa
            self.output.write(tr("mainwindow.could_not_open_form") % e, "common.build", C["error"])
            self.show_dock(self.d_output)
            return
        w.setParent(self, Qt.WindowType.Window)
        w.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        w.setStyleSheet("QWidget{background:#F0F0F0;color:#202020;} QLineEdit,QTextEdit{background:white;}")
        w.setWindowTitle(tr("mainwindow.preview_prefix") + (w.windowTitle() or os.path.basename(ed.path)))
        w.show()

    def _pick_unity_root(self):
        d = QFileDialog.getExistingDirectory(self, tr("mainwindow.folder_with_version_unity_hub"),
                                             self.unity.editors_root.text())
        if d:
            self.unity.editors_root.setText(d)
            self.unity._root_changed()
            self.show_dock(self.d_unity)

    def install_requirements(self):
        reqs = [p for p in self.explorer.files if os.path.basename(p) == "requirements.txt"]
        if not reqs:
            self.status(tr("mainwindow.no_requirements_txt_solution"))
            return
        self.show_dock(self.d_terminal)
        self.terminal.set_cwd(os.path.dirname(reqs[0]))
        self.terminal.input.setText('"%s" -m pip install --user -r requirements.txt' % self.interp.currentData())
        self.terminal.input.setFocus()
        self.status(tr("mainwindow.check_command_terminal_press_enter"))

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
        self.explorer.refresh()
        if theme.CONTROLLER is not None:
            self.status(tr("mainwindow.theme_prefix") + theme.CONTROLLER.title())

    # ------------------------------------------------------------ обновления анализаторов
    def _service_for_language(self, language):
        return self.services_by_key[language]

    def _before_analyzer_update(self, language):
        self._service_for_language(language).stop()       # файлы анализатора не должны быть заняты

    def _after_analyzer_update(self, language):
        svc = self._service_for_language(language)
        svc.stop()
        svc.rediscover()
        for ed in self.editors():
            if ed.language.service == language:
                ed.lsp_diagnostics = None
                ed.run_diagnostics()        # заодно запускает анализатор снова

    def show_updates(self):
        if not hasattr(self, "_updates_worker"):
            # один объект на всё время работы окна: фоновый поток не должен слать сигналы удалённому объекту
            self._updates_worker = updates.UpdateWorker(self)
            self._updates_dialog = updates.UpdatesDialog(self._updates_worker, self._before_analyzer_update,
                                                         self._after_analyzer_update, self)
        self._updates_dialog.check()
        self._updates_dialog.show()
        self._updates_dialog.raise_()

    def show_about(self):
        QMessageBox.about(self, APP_NAME, tr("mainwindow.code_editor_unity_c_python") % (
                              APP_NAME, "jedi " + intellisense.JEDI_VERSION if intellisense.JEDI_VERSION else tr("mainwindow.status_words"),
                              __import__("PyQt6.QtCore", fromlist=["x"]).PYQT_VERSION_STR))

    # ------------------------------------------------------------ запуск
    def _running(self):
        return self.proc is not None and self.proc.state() != QProcess.ProcessState.NotRunning

    def run_selected(self):
        target = self.startup.currentData()
        if target:
            self.run_file(target)
            return
        ed = self.editor()
        if ed and ed.lang == "python":
            self.run_file(ed.path)
        elif ed and ed.lang == "csharp":
            self.output.write(tr("mainwindow.c_scripts_run_unity_opening"), "Unity", C["warning"])
            self.show_dock(self.d_output)
            self.unity.open_in_unity()
        else:
            self.status(tr("mainwindow.open_file_python_select_boot"))

    def run_file(self, path):
        if self._running():
            if QMessageBox.question(self, APP_NAME, tr("mainwindow.program_already_running")) \
                    != QMessageBox.StandardButton.Yes:
                return
            self.stop_run()
            self.proc.waitForFinished(2000)
        self.save_all()
        self.run_path = path
        self.run_stderr = ""
        self.errors.clear_source("common.run")
        interp = self.interp.currentData() or sys.executable
        self.output.clear()
        self.output.write(tr("mainwindow.running") % os.path.relpath(path, self.root or os.path.dirname(path)), "common.run", C["link"])
        self.output.write("%s %s" % (interp, path), "common.run", C["dim"])
        self.show_dock(self.d_output)
        if self.proc is not None:
            self.proc.deleteLater()
        self.proc = QProcess(self)
        self.proc.setWorkingDirectory(os.path.dirname(path))
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        env.insert("PYTHONIOENCODING", "utf-8")
        self.proc.setProcessEnvironment(env)
        self.proc.readyReadStandardOutput.connect(self._read_out)
        self.proc.readyReadStandardError.connect(self._read_err)
        self.proc.finished.connect(self._run_finished)
        self.proc.errorOccurred.connect(lambda err: self.output.write(tr("mainwindow.could_not_run") % self.proc.errorString(), "common.run", C["error"]))
        self.proc.start(interp, ["-u", path])
        self.a_stop.setEnabled(True)
        self.status_label.setText(tr("mainwindow.running_ellipsis"))

    def _read_out(self):
        self.output.write(bytes(self.proc.readAllStandardOutput()).decode("utf-8", "replace"), "common.run")

    def _read_err(self):
        text = bytes(self.proc.readAllStandardError()).decode("utf-8", "replace")
        self.run_stderr = (self.run_stderr + text)[-256_000:]
        self.output.write(text, "common.run", C["error"])

    def _run_finished(self, code, _status):
        self.a_stop.setEnabled(False)
        self.output.write(tr("mainwindow.process_exited_with_code") % code, "common.run", C["dim"] if code == 0 else C["warning"])
        self.status(tr("common.ready") if code == 0 else tr("mainwindow.program_ended_with_error_code") % code, 0)
        if self.run_stderr and "Traceback" in self.run_stderr:
            frames = re.findall(r'File "(.+?)", line (\d+)', self.run_stderr)
            last = self.run_stderr.strip().splitlines()[-1]
            if frames:
                own = [f for f in frames if self.root and os.path.abspath(f[0]).startswith(self.root)] or frames
                path, line = own[-1]
                self.errors.set_group("common.run", os.path.abspath(path), [(int(line), 1, last, "error")])
                self.show_dock(self.d_errors)

    def stop_run(self):
        if self._running():
            self.proc.kill()
            self.output.write(tr("mainwindow.stopped_by_user"), "common.run", C["warning"])

    # ------------------------------------------------------------ проверка (сборка)
    def _check_file(self, path):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError as e:
            return [(1, 1, str(e), "error")]
        for ed in self.editors():
            if ed.path == path:
                text = ed.toPlainText()
        if path.endswith(".py"):
            try:
                compile(text, path, "exec")
            except SyntaxError as e:
                return [(e.lineno or 1, e.offset or 1, tr("common.syntax_error") % e.msg, "error")]
            return []
        if path.endswith(".cs"):
            return check_csharp(text)
        return []

    def build_solution(self):
        if not self.root:
            return
        self.errors.clear_source("common.build")
        self.output.clear()
        self.output.write(tr("mainwindow.checking_solution") % os.path.basename(self.root), "common.build", C["link"])
        per_project = {}
        for p in self.explorer.files:
            if not p.endswith((".py", ".cs")):
                continue
            proj = os.path.relpath(p, self.root).split(os.sep)[0]
            diags = self._check_file(p)
            stat = per_project.setdefault(proj, [0, 0, 0])
            stat[0] += 1
            stat[1] += sum(1 for d in diags if d[3] == "error")
            stat[2] += sum(1 for d in diags if d[3] != "error")
            if diags:
                self.errors.set_group("common.build", p, diags)
                for line, col, msg, sev in diags:
                    self.output.write("%s(%d,%d): %s: %s" % (os.path.relpath(p, self.root), line, col,
                                                           tr("mainwindow.error") if sev == "error" else tr("mainwindow.warning"), msg),
                                      "common.build", C["error"] if sev == "error" else C["warning"])
        ok = bad = 0
        for proj, (n, e, w) in sorted(per_project.items()):
            self.output.write(tr("mainwindow.files_checked_errors_warnings") % (proj, n, e, w), "common.build")
            if e:
                bad += 1
            else:
                ok += 1
        self.output.write(tr("mainwindow.check_passed_failed") % (ok, bad), "common.build",
                          C["ok"] if not bad else C["error"])
        self.show_dock(self.d_output)
        self.status(tr("mainwindow.check_complete_passed_failed") % (ok, bad))

    def build_current(self):
        ed = self.editor()
        if ed:
            ed.run_diagnostics()
            e = sum(1 for d in ed.diagnostics if d[3] == "error")
            self.status(tr("mainwindow.errors") % (os.path.basename(ed.path), e))
            self.show_dock(self.d_errors)

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
                if r == QMessageBox.StandardButton.SaveAll:
                    self.save_all()
                break
        self.settings.setValue("window/geometry", self.saveGeometry())
        self.settings.setValue("window/state", self.saveState())
        if self._running():
            self.proc.kill()
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

    def show_settings(self):
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("mainwindow.editor_settings"))
        form = QFormLayout(dialog)
        font = QSpinBox()
        font.setRange(7, 32)
        font.setValue(self.font_size)
        hints = QSpinBox()
        hints.setRange(7, 24)
        hints.setValue(round(self.hint_size))
        wrap = QCheckBox(tr("mainwindow.wrap_long_lines"))
        wrap.setChecked(self.word_wrap)
        form.addRow(tr("mainwindow.code_font_size"), font)
        form.addRow(tr("mainwindow.completion_font_size"), hints)
        form.addRow(wrap)
        form.addRow(QLabel(tr("mainwindow.settings_hint")))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.zoom(font.value() - self.font_size)
            self.zoom_hints(hints.value() - self.hint_size)
            self.word_wrap = wrap.isChecked()
            self.settings.setValue("editor/word_wrap", self.word_wrap)
            for ed in self.editors():
                ed.setLineWrapMode(ed.LineWrapMode.WidgetWidth if self.word_wrap else ed.LineWrapMode.NoWrap)


