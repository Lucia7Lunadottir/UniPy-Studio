"""Главное окно UniPy Studio."""
import glob
import os
import re
import sys

from PyQt6.QtCore import Qt, QTimer, QSettings, QProcess, QProcessEnvironment, QPoint, QStandardPaths, QSize
from PyQt6.QtGui import QAction, QKeySequence, QTextDocument, QPainter, QGuiApplication, QDesktopServices
from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import (QMainWindow, QTabWidget, QDockWidget, QWidget, QVBoxLayout, QHBoxLayout,
                             QLabel, QLineEdit, QToolButton, QComboBox, QFileDialog, QMessageBox,
                             QInputDialog, QCompleter, QFrame, QCheckBox, QApplication, QToolBar,
                             QSizePolicy, QPushButton)

from . import theme, templates, intellisense, lsp, updates
from .editor import CodeEditor, lang_for, check_csharp, decode_source
from . import languages
from .panels import (SolutionExplorer, OutlinePanel, OutputPanel, ErrorList, TerminalPanel, UnityPanel,
                     PyQtDesigner, NewFileDialog, BusyBar, ReferencesPanel, find_unity_projects, HIDDEN_DIRS)
from .theme import C

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
        self.find.setPlaceholderText("Найти")
        self.find.setMinimumWidth(220)
        self.find.textChanged.connect(self.update_matches)
        self.find.returnPressed.connect(lambda: self.step(True))
        self.case = QCheckBox("Аа")
        self.case.setToolTip("С учётом регистра")
        self.case.toggled.connect(self.update_matches)
        self.count = QLabel("")
        self.count.setObjectName("hint")
        self.replace = QLineEdit()
        self.replace.setPlaceholderText("Заменить на")
        self.replace.setMinimumWidth(180)
        lay.addWidget(self.find)
        lay.addWidget(self.case)
        for text, tip, slot in (("↑", "Предыдущее (Shift+F3)", lambda: self.step(False)),
                                ("↓", "Следующее (F3)", lambda: self.step(True))):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            lay.addWidget(b)
        lay.addWidget(self.count)
        lay.addSpacing(10)
        lay.addWidget(self.replace)
        self.rep_one = QPushButton("Заменить")
        self.rep_one.clicked.connect(self.replace_one)
        self.rep_all = QPushButton("Заменить все")
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
            self.count.setText("%d совпад." % n if n else "Нет результатов")
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
        self.win.status("Заменено: %d" % n)
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
        self.root = None
        self.proc = None
        self.run_path = None
        self.run_stderr = ""
        self.setWindowIcon(theme.app_icon())
        self.resize(1440, 900)

        self.jedi = intellisense.JediWorker()
        self.jedi.finished.connect(self._worker_result)
        self.jedi.ready.connect(lambda msg: (self.output.write("Подсказки Python/PyQt: " + msg, "Сборка", C["dim"]),
                                             self.jedi_label.setText("IntelliSense: готово")))

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
            svc.status.connect(lambda text, k=key: self._lsp_status(text, k))

        self._build_central()
        self._build_docks()
        for svc in self.language_services:
            svc.log.connect(lambda t: self.output.write(t, "Сборка", C["dim"]))
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
        self.tabs.currentChanged.connect(self._tab_changed)
        self.findbar = FindBar(self)
        lay.addWidget(self.tabs, 1)
        self.empty = QLabel("Откройте файл в обозревателе решений\n\n"
                            "Ctrl+N — новый файл     Ctrl+P — перейти к файлу     F5 — запуск")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        theme.style(self.empty, "color:@dim@; background:@editor@; font-size:12pt;")
        lay.addWidget(self.empty, 1)
        self.tabs.hide()
        lay.insertWidget(0, self.findbar)
        self.setCentralWidget(central)

    def _dock(self, title, widget, area, name):
        d = QDockWidget(title, self)
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
        lbl = QLabel(title)
        lbl.setObjectName("dockTitleText")
        bl.addWidget(lbl, 1)
        close = QToolButton()
        close.setIcon(theme.tool_icon("clear"))
        close.setProperty("tool_icon", "clear")
        close.setIconSize(QSize(12, 12))
        close.setToolTip("Скрыть панель")
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
        self.d_explorer = self._dock("Обозреватель решений", self.explorer,
                                     Qt.DockWidgetArea.LeftDockWidgetArea, "explorer")

        self.outline = OutlinePanel()
        self.outline.goto.connect(lambda line: self.editor() and self.editor().go_to_line(line))
        self.d_outline = self._dock("Структура", self.outline, Qt.DockWidgetArea.LeftDockWidgetArea, "outline")
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
        self.d_designer = self._dock("Конструктор PyQt", self.designer, Qt.DockWidgetArea.RightDockWidgetArea, "designer")
        self.tabifyDockWidget(self.d_unity, self.d_designer)

        self.output = OutputPanel(self.font)
        self.output.link.connect(self._open_link)
        self.d_output = self._dock("Вывод", self.output, Qt.DockWidgetArea.BottomDockWidgetArea, "output")
        self.errors = ErrorList()
        self.errors.goto.connect(lambda p, l, c: self.open_path(p, l, c))
        self.d_errors = self._dock("Список ошибок", self.errors, Qt.DockWidgetArea.BottomDockWidgetArea, "errors")
        self.terminal = TerminalPanel(self.font)
        self.d_terminal = self._dock("Терминал", self.terminal, Qt.DockWidgetArea.BottomDockWidgetArea, "terminal")
        self.references = ReferencesPanel()
        self.references.goto.connect(lambda p, l, c: self.jump_to(p, l, c))
        self.d_references = self._dock("Результаты поиска ссылок", self.references,
                                       Qt.DockWidgetArea.BottomDockWidgetArea, "references")
        self.tabifyDockWidget(self.d_errors, self.d_references)
        self.tabifyDockWidget(self.d_errors, self.d_output)
        self.tabifyDockWidget(self.d_output, self.d_terminal)
        self.d_errors.raise_()
        self.resizeDocks([self.d_explorer, self.d_unity], [290, 370], Qt.Orientation.Horizontal)
        self.resizeDocks([self.d_errors], [230], Qt.Orientation.Vertical)
        self.resizeDocks([self.d_explorer, self.d_outline], [440, 200], Qt.Orientation.Vertical)

    def _act(self, text, slot, shortcut=None, icon=None, tip=None):
        a = QAction(text, self)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        if icon:
            a.setIcon(theme.tool_icon(icon))
            a.setProperty("tool_icon", icon)        # значок перерисуется при смене темы
        if tip:
            a.setToolTip(tip)
        a.triggered.connect(lambda *_: slot())
        self.addAction(a)
        return a

    def _build_actions(self):
        A = self._act
        ed = lambda name: (lambda: self.editor() and getattr(self.editor(), name)())  # noqa
        self.a_new = A("Новый файл…", lambda: self.new_file_dialog(), "Ctrl+N", "new", "Добавить новый элемент (Ctrl+N)")
        self.a_open_file = A("Открыть файл…", self.open_file_dialog, "Ctrl+O")
        self.a_open_solution = A("Открыть проект или решение…", self.open_solution_dialog, "Ctrl+Shift+O", "open",
                                 "Открыть проект или решение (Ctrl+Shift+O)")
        self.a_open = A("Открыть папку…", self.open_folder_dialog, "Ctrl+Shift+Alt+O")
        self.a_sample = A("Открыть пример решения", self.open_sample)
        self.a_save = A("Сохранить", self.save_current, "Ctrl+S", "save", "Сохранить (Ctrl+S)")
        self.a_save_all = A("Сохранить все", self.save_all, "Ctrl+Shift+S", "saveall", "Сохранить все (Ctrl+Shift+S)")
        self.a_close = A("Закрыть вкладку", lambda: self.close_tab(self.tabs.currentIndex()), "Ctrl+W")
        self.a_quit = A("Выход", self.close, "Ctrl+Q")
        self.a_undo = A("Отменить", ed("undo"), "Ctrl+Z")
        self.a_redo = A("Повторить", ed("redo"), "Ctrl+Y")
        self.a_cut = A("Вырезать", ed("cut"), "Ctrl+X")
        self.a_copy = A("Копировать", ed("copy"), "Ctrl+C")
        self.a_paste = A("Вставить", ed("paste"), "Ctrl+V")
        self.a_find = A("Найти…", lambda: self.findbar.open(False), "Ctrl+F", "find")
        self.a_replace = A("Заменить…", lambda: self.findbar.open(True), "Ctrl+H")
        self.a_find_next = A("Найти далее", lambda: self.findbar.step(True), "F3")
        self.a_find_prev = A("Найти ранее", lambda: self.findbar.step(False), "Shift+F3")
        self.a_goto = A("Перейти к строке…", self.goto_line_dialog, "Ctrl+G")
        self.a_comment = A("Закомментировать / раскомментировать", ed("toggle_comment"), "Ctrl+/")
        self.a_dup = A("Дублировать строку", ed("duplicate_line"), "Ctrl+D")
        self.a_complete = A("Список членов (подсказки)", lambda: self.editor() and self.editor().request_completion(True), "Ctrl+Space")
        self.a_params = A("Сведения о параметрах", lambda: self.editor() and self.editor().request_signatures(True), "Ctrl+Shift+Space")
        self.a_definition = A("Перейти к определению", ed("go_to_definition"), "F12")
        self.a_quick = A("Перейти к файлу…", lambda: (self.quick.setFocus(), self.quick.selectAll()), "Ctrl+P")
        # масштаб кода — клавиши Visual Studio (Ctrl+Shift+. / Ctrl+Shift+,); Ctrl+− в VS — «Назад»
        self.a_zoom_in = A("Увеличить шрифт", lambda: self.zoom(1), "Ctrl+Shift+.")
        self.a_zoom_in.setShortcuts([QKeySequence("Ctrl+Shift+."), QKeySequence("Ctrl+=")])
        self.a_zoom_out = A("Уменьшить шрифт", lambda: self.zoom(-1), "Ctrl+Shift+,")
        self.a_back = A("Назад", lambda: self.navigate(-1), "Ctrl+-", "back", "Назад (Ctrl+−)")
        self.a_fwd = A("Вперёд", lambda: self.navigate(1), "Ctrl+Shift+-", "forward", "Вперёд (Ctrl+Shift+−)")
        self.a_refs = A("Найти все ссылки", ed("find_references"), "Shift+F12")
        self.a_rename = A("Переименовать…", self.rename_dialog, "Ctrl+R, Ctrl+R")
        self.a_rename.setShortcuts([QKeySequence("Ctrl+R, Ctrl+R"), QKeySequence("F2")])
        self.a_hint_in = A("Крупнее подсказки", lambda: self.zoom_hints(1), "Ctrl+Alt+=")
        self.a_hint_out = A("Мельче подсказки", lambda: self.zoom_hints(-1), "Ctrl+Alt+-")
        self.a_run = A("Запуск", self.run_selected, "F5", "run", "Запустить (F5)")
        self.a_run2 = A("Запуск без отладки", self.run_selected, "Ctrl+F5")
        self.a_stop = A("Остановить", self.stop_run, "Shift+F5", "stop", "Остановить (Shift+F5)")
        self.a_stop.setEnabled(False)
        self.a_build = A("Проверить решение", self.build_solution, "Ctrl+Shift+B", "build", "Проверить решение (Ctrl+Shift+B)")
        self.a_build_file = A("Проверить текущий файл", self.build_current, "Ctrl+F7")
        self.a_unity = A("Открыть в Unity", lambda: self.unity.open_in_unity(), None, "unity", "Открыть проект в Unity")
        self.a_hub = A("Открыть Unity Hub", lambda: self.unity.open_hub())
        self.a_unity_script = A("Новый скрипт Unity…", lambda: self.new_file_dialog(template=5))
        self.a_pyqt_window = A("Новое окно PyQt…", lambda: self.new_file_dialog(template=0))
        self.a_pip = A("Установить зависимости (requirements.txt)", self.install_requirements)
        self.a_designer_ui = A("Показать форму .ui", self.preview_ui)
        self.a_keys = A("Горячие клавиши", self.show_keys, "F1")
        self.a_about = A("О программе", self.show_about)
        self.a_updates = A("Обновления анализаторов…", self.show_updates)

    def _build_menus(self):
        mb = self.menuBar()
        m = mb.addMenu("Файл")
        for a in (self.a_new, self.a_open_solution, self.a_open, self.a_open_file, self.a_sample):
            m.addAction(a)
        self.recent_menu = m.addMenu("Недавние папки")
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        m.addSeparator()
        for a in (self.a_save, self.a_save_all, self.a_close):
            m.addAction(a)
        m.addSeparator()
        m.addAction(self.a_quit)
        m = mb.addMenu("Правка")
        for a in (self.a_undo, self.a_redo, None, self.a_cut, self.a_copy, self.a_paste, None,
                  self.a_find, self.a_replace, self.a_goto, None, self.a_comment, self.a_dup):
            m.addSeparator() if a is None else m.addAction(a)
        sub = m.addMenu("IntelliSense")
        for a in (self.a_complete, self.a_params, self.a_definition, self.a_refs):
            sub.addAction(a)
        m.addAction(self.a_rename)
        m = mb.addMenu("Вид")
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
        m.addSeparator()
        self.theme_menu = m.addMenu("Тема")
        self.theme_menu.aboutToShow.connect(self._fill_theme_menu)
        m = mb.addMenu("Проект")
        for a in (self.a_new, self.a_unity_script, self.a_pyqt_window, None, self.a_designer_ui, self.a_pip,
                  None, self.a_unity, self.a_hub):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu("Сборка")
        m.addAction(self.a_build)
        m.addAction(self.a_build_file)
        m = mb.addMenu("Отладка")
        for a in (self.a_run, self.a_run2, self.a_stop):
            m.addAction(a)
        m = mb.addMenu("Средства")
        m.addAction("Терминал", lambda: self.show_dock(self.d_terminal))
        m.addAction("Конструктор PyQt", lambda: self.show_dock(self.d_designer))
        m.addAction("Папка с версиями Unity…", self._pick_unity_root)
        m = mb.addMenu("Справка")
        m.addAction(self.a_keys)
        m.addAction(self.a_updates)
        m.addAction(self.a_about)

    def _build_toolbar(self):
        tb = QToolBar("Стандартная")
        tb.setObjectName("main_toolbar")
        tb.setMovable(False)
        tb.setIconSize(QSize(16, 16))
        self.addToolBar(tb)
        for a in (self.a_back, self.a_fwd, None, self.a_new, self.a_open_solution, self.a_save, self.a_save_all):
            tb.addSeparator() if a is None else tb.addAction(a)
        tb.addSeparator()
        lbl = QLabel("Запуск:")
        tb.addWidget(lbl)
        self.startup = QComboBox()
        self.startup.setMinimumWidth(200)
        self.startup.setToolTip("Какой файл запускать по F5")
        tb.addWidget(self.startup)
        self.interp = QComboBox()
        self.interp.setMinimumWidth(150)
        self.interp.setToolTip("Интерпретатор Python")
        tb.addWidget(self.interp)
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
        self.quick.setPlaceholderText("Перейти к файлу (Ctrl+P)")
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
        self.status_label = QLabel("Готово")
        sb.addWidget(self.status_label, 1)
        self.err_label = QLabel("✖ 0  ⚠ 0")
        self.err_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.err_label.mousePressEvent = lambda e: self.show_dock(self.d_errors)
        self.jedi_label = QLabel("IntelliSense: загрузка…" if self.jedi.available else "IntelliSense: по словам")
        self.pos_label = QLabel("Стр 1   Стлб 1")
        self.indent_label = QLabel("Пробелы: 4")
        self.enc_label = QLabel("UTF-8")
        self.lang_label = QLabel("")
        self.busy_bar = BusyBar()
        self._busy_timeout = QTimer(self, singleShot=True, interval=30000,
                                    timeout=lambda: self._busy("", "Сервер не ответил — определение не найдено"))
        for w in (self.busy_bar, self.err_label, self.jedi_label, self.pos_label, self.indent_label,
                  self.enc_label, self.lang_label):
            sb.addPermanentWidget(w)

    # ------------------------------------------------------------ сервисы для редактора
    def status(self, text, timeout=6000):
        self.status_label.setText(text)
        if timeout:
            QTimer.singleShot(timeout, lambda: self.status_label.text() == text and self.status_label.setText(
                "Выполняется…" if self._running() else "Готово"))

    def zoom(self, delta):
        self.font_size = max(7, min(32, self.font_size + delta))
        self.settings.setValue("editor/font_size", self.font_size)
        self.font = theme.code_font(self.font_size)
        for ed in self.editors():
            ed.set_code_font(self.font)
        self.status("Размер шрифта: %d" % self.font_size)

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
        self.status("Назад больше некуда" if direction < 0 else "Вперёд больше некуда")

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
            self.status("Ссылки на «%s» не найдены" % word)
            return
        self.references.show_results(word, refs, self.root, self._line_text)
        self.show_dock(self.d_references)
        self.status("Найдено ссылок: %d" % len(refs))

    def rename_dialog(self):
        ed = self.editor()
        if ed is None or ed.isReadOnly():
            return
        word = ed.symbol_under_cursor()
        if not word or not re.fullmatch(r"[A-Za-z_]\w*", word):
            self.status("Поставьте курсор на имя, которое нужно переименовать")
            return
        name, ok = QInputDialog.getText(self, "Переименовать", "Новое имя для «%s»:" % word, text=word)
        name = name.strip()
        if ok and name and name != word:
            if not re.fullmatch(r"[A-Za-z_]\w*", name):
                QMessageBox.warning(self, APP_NAME, "«%s» — недопустимое имя." % name)
                return
            self._rename_from = (word, ed.path)
            ed.rename_symbol(name)

    def _apply_rename(self, new_name, data):
        """Правки переименования: открытые файлы меняются в редакторе (Ctrl+Z отменит), закрытые —
        пишутся на диск в их же кодировке и с их переводами строк."""
        if data.get("error"):
            QMessageBox.warning(self, APP_NAME, "Переименовать не удалось: %s" % data["error"])
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
            self.output.write("Переименование: %s — %d" % (os.path.relpath(path, self.root or "/"), len(items)),
                              "Сборка", C["dim"])
        msg = "«%s» → «%s»: изменено мест %d в %d %s" % (
            getattr(self, "_rename_from", ("?",))[0], new_name, changed_places, changed_files,
            "файле" if changed_files == 1 else "файлах")
        self.status(msg)
        problems = []
        if skipped:
            problems.append("Не тронуты (в редакторе есть несохранённые правки):\n" +
                            "\n".join(os.path.basename(p) for p in skipped))
        if failed:
            problems.append("Не удалось записать:\n" + "\n".join(failed))
        old, src = getattr(self, "_rename_from", ("", ""))
        if src.endswith(".cs") and os.path.splitext(os.path.basename(src))[0] == old:
            problems.append("Это имя совпадает с именем файла %s. Unity требует, чтобы класс MonoBehaviour "
                            "назывался как файл: переименуйте файл в Unity (тогда сохранится .meta "
                            "и ссылки в сценах)." % os.path.basename(src))
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

    def zoom_hints(self, delta):
        """Размер подсказок — отдельно от размера кода."""
        self.hint_size = max(7.0, min(24.0, self.hint_size + delta))
        self.settings.setValue("editor/hint_size", self.hint_size)
        for ed in self.editors():
            ed.apply_hint_size()
        self.status("Размер подсказок: %g" % self.hint_size)

    def copy_text(self, text):
        QGuiApplication.clipboard().setText(text)
        self.status("Скопировано: " + text)

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
        self.output.write("Решение открыто: %s" % root, "Сборка", C["dim"])
        unity = find_unity_projects(root)
        if unity:
            self.output.write("Найден проект Unity: %s" % ", ".join(os.path.relpath(u, root) or "." for u in unity),
                              "Unity", C["dim"])
        return True

    def _solution_changed(self):
        files = self.explorer.files
        rels = [os.path.relpath(p, self.root) for p in files]
        self.quick_completer.model().setStringList(rels)
        current = self.startup.currentData()
        self.startup.blockSignals(True)
        self.startup.clear()
        self.startup.addItem(theme.badge("py", "#3572A5", "#FFD43B"), "Текущий файл", "")
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
            real = os.path.realpath(p)
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
            a = self.recent_menu.addAction("пусто")
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
            self.status("Файл не найден: " + path)
            return None
        if os.path.getsize(path) > 5_000_000:
            QMessageBox.information(self, APP_NAME, "Файл слишком большой для редактора.")
            return None
        with open(path, "rb") as f:
            head = f.read(4096)
        if b"\0" in head:
            self.open_external(path)
            return None
        ed = CodeEditor(path, self, self.font)
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
            r = QMessageBox.question(self, APP_NAME, "Сохранить изменения в «%s»?" % os.path.basename(ed.path),
                                     QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard |
                                     QMessageBox.StandardButton.Cancel)
            if r == QMessageBox.StandardButton.Cancel:
                return False
            if r == QMessageBox.StandardButton.Save and not ed.save():
                return False
        self.errors.set_group("Редактор", ed.path, [])
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

    def close_all_tabs(self):
        while self.tabs.count():
            if not self.close_tab(self.tabs.count() - 1):
                return False
        return True

    def save_current(self):
        ed = self.editor()
        if ed and ed.save():
            self.enc_label.setText(ed.encoding_label())
            self.status("Сохранено: " + os.path.basename(ed.path))

    def save_all(self):
        n = 0
        for ed in self.editors():
            if ed.document().isModified() and ed.save():
                n += 1
        self.status("Сохранено файлов: %d" % n)

    def on_path_renamed(self, old, new):
        for i, ed in enumerate(self.editors()):
            if ed.path == old or ed.path.startswith(old + os.sep):
                ed.path = new + ed.path[len(old):]
                self.tabs.setTabText(i, os.path.basename(ed.path))
                self.tabs.setTabToolTip(i, ed.path)

    def on_path_deleted(self, path):
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

    def _lsp_status(self, text, key):
        if not text:
            return
        self.status(text)
        lang = text.split(":", 1)[0]
        self.jedi_label.setText("%s: %s" % (lang, "загрузка…" if "загрузка" in text else
                                            "готово" if "загружено" in text else "остановлен"))
        if "загружено" in text:
            for ed in self.editors():
                if ed.language.service == key:
                    ed.run_diagnostics()

    def _cursor_moved(self, line, col):
        self.pos_label.setText("Стр %d   Стлб %d" % (line, col))

    def _diagnostics(self, ed):
        self.errors.open_paths.add(ed.path)
        self.errors.set_group("Редактор", ed.path, ed.diagnostics)
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
            self, "Открыть проект или решение", self.root or os.path.expanduser("~"),
            "Решения и проекты (*.sln *.slnx *.csproj);;Все файлы (*)")
        if p:
            self.set_root(os.path.dirname(os.path.abspath(p)))

    def open_folder_dialog(self):
        d = QFileDialog.getExistingDirectory(self, "Открыть папку (решение)", self.root or os.path.expanduser("~"))
        if d:
            self.set_root(d)

    def open_file_dialog(self):
        p, _ = QFileDialog.getOpenFileName(self, "Открыть файл", self.root or os.path.expanduser("~"),
                                           "Код (*.py *.cs *.ui *.json *.md *.txt);;Все файлы (*)")
        if p:
            self.open_path(p)

    def goto_line_dialog(self):
        ed = self.editor()
        if not ed:
            return
        n, ok = QInputDialog.getInt(self, "Перейти к строке", "Номер строки (1–%d):" % ed.blockCount(),
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
            QMessageBox.warning(self, APP_NAME, "Файл уже существует:\n" + path)
            self.open_path(path)
            return
        os.makedirs(folder, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(templates.render(text, base))
        self.explorer.refresh()
        self.open_path(path)
        self.output.write("Создан файл: %s" % os.path.relpath(path, self.root), "Сборка", C["dim"])

    def _create_unity_script(self, name, idx):
        if not find_unity_projects(self.root):
            QMessageBox.information(self, APP_NAME, "В открытой папке нет проекта Unity (папки Assets и ProjectSettings).")
            return
        self.create_from_template(idx, name, self._default_folder(templates.TEMPLATES[idx][3]))

    def _insert_unity_message(self, sig):
        ed = self.editor()
        if not ed or ed.lang != "csharp":
            self.status("Откройте скрипт C#, чтобы вставить метод")
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
        self.output.write("Конструктор PyQt создал %s — запустите его кнопкой «Запуск»" %
                          os.path.relpath(path, self.root), "Сборка", C["text"])

    def preview_ui(self):
        ed = self.editor()
        if not ed or not ed.path.endswith(".ui"):
            QMessageBox.information(self, APP_NAME, "Откройте файл формы .ui в редакторе.")
            return
        import io
        from PyQt6 import uic
        try:
            w = uic.loadUi(io.StringIO(ed.toPlainText()))
        except Exception as e:  # noqa
            self.output.write("Не удалось открыть форму: %s" % e, "Сборка", C["error"])
            self.show_dock(self.d_output)
            return
        w.setParent(self, Qt.WindowType.Window)
        w.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        w.setStyleSheet("QWidget{background:#F0F0F0;color:#202020;} QLineEdit,QTextEdit{background:white;}")
        w.setWindowTitle("Предпросмотр — " + (w.windowTitle() or os.path.basename(ed.path)))
        w.show()

    def _pick_unity_root(self):
        d = QFileDialog.getExistingDirectory(self, "Папка с версиями Unity (Hub/Editor)",
                                             self.unity.editors_root.text())
        if d:
            self.unity.editors_root.setText(d)
            self.unity._root_changed()
            self.show_dock(self.d_unity)

    def install_requirements(self):
        reqs = [p for p in self.explorer.files if os.path.basename(p) == "requirements.txt"]
        if not reqs:
            self.status("В решении нет requirements.txt")
            return
        self.show_dock(self.d_terminal)
        self.terminal.set_cwd(os.path.dirname(reqs[0]))
        self.terminal.input.setText('"%s" -m pip install --user -r requirements.txt' % self.interp.currentData())
        self.terminal.input.setFocus()
        self.status("Проверьте команду в терминале и нажмите Enter")

    def show_keys(self):
        QMessageBox.information(self, "Горячие клавиши", "\n".join([
            "Ctrl+Пробел — подсказки (список членов)",
            "Ctrl+Shift+Пробел — параметры функции",
            "F12 или Ctrl+клик — перейти к определению",
            "Ctrl+− / Ctrl+Shift+− — назад / вперёд (и боковые кнопки мыши)",
            "Shift+F12 — найти все ссылки",
            "Ctrl+R, Ctrl+R или F2 — переименовать",
            "Наведение мыши — описание",
            "F5 — запуск, Shift+F5 — остановить",
            "Ctrl+Shift+B — проверить решение",
            "Ctrl+S / Ctrl+Shift+S — сохранить / всё",
            "Ctrl+F / Ctrl+H — найти / заменить",
            "Ctrl+G — к строке, Ctrl+P — к файлу",
            "Ctrl+/ — комментарий, Ctrl+D — дублировать строку",
            "Tab / Shift+Tab — отступ",
            "Ctrl+колёсико или Ctrl+Shift+. / Ctrl+Shift+, — размер кода",
            "Ctrl+колёсико над списком подсказок — размер подсказок",
            "Ctrl+Alt+= / Ctrl+Alt+− — крупнее / мельче подсказки",
        ]))

    # ------------------------------------------------------------ тема
    def _fill_theme_menu(self):
        ctl = theme.CONTROLLER
        m = self.theme_menu
        m.clear()
        if ctl is None:
            return
        for mode, label in theme.MODES:
            a = m.addAction(label, lambda md=mode: ctl.set_mode(md))
            a.setCheckable(True)
            a.setChecked(ctl.mode == mode)
        m.addSeparator()
        own = m.addAction("UniPy (как Visual Studio)", ctl.use_unipy)
        own.setCheckable(True)
        own.setChecked(not ctl.uses_character)
        if theme.KIT_AVAILABLE:
            m.addSection("Тема персонажа — общая для Equestria OS")
            for name in ctl.characters():
                a = m.addAction(name, lambda n=name: ctl.use_character(n))
                a.setCheckable(True)
                a.setChecked(ctl.uses_character and ctl.kit_name == name)
        else:
            note = m.addAction("Темы персонажей — с пакетом equestria-qt-kit 1.2+")
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
            self.status("Тема: " + theme.CONTROLLER.title())

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
        QMessageBox.about(self, APP_NAME, "<b>%s</b><br>Редактор кода для Unity (C#) и Python/PyQt.<br><br>"
                          "Подсказки Python: %s<br>PyQt: %s" % (
                              APP_NAME, "jedi " + intellisense.JEDI_VERSION if intellisense.JEDI_VERSION else "по словам",
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
            self.output.write("Скрипты C# выполняет Unity. Открываю проект в Unity…", "Unity", C["warning"])
            self.show_dock(self.d_output)
            self.unity.open_in_unity()
        else:
            self.status("Откройте файл Python или выберите файл запуска на панели")

    def run_file(self, path):
        if self._running():
            if QMessageBox.question(self, APP_NAME, "Программа уже запущена. Остановить и запустить заново?") \
                    != QMessageBox.StandardButton.Yes:
                return
            self.stop_run()
            self.proc.waitForFinished(2000)
        self.save_all()
        self.run_path = path
        self.run_stderr = ""
        self.errors.clear_source("Запуск")
        interp = self.interp.currentData() or sys.executable
        self.output.clear()
        self.output.write("------ Запуск: %s ------" % os.path.relpath(path, self.root or os.path.dirname(path)), "Запуск", C["link"])
        self.output.write("%s %s" % (interp, path), "Запуск", C["dim"])
        self.show_dock(self.d_output)
        self.proc = QProcess(self)
        self.proc.setWorkingDirectory(os.path.dirname(path))
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        env.insert("PYTHONIOENCODING", "utf-8")
        self.proc.setProcessEnvironment(env)
        self.proc.readyReadStandardOutput.connect(self._read_out)
        self.proc.readyReadStandardError.connect(self._read_err)
        self.proc.finished.connect(self._run_finished)
        self.proc.errorOccurred.connect(lambda err: self.output.write("Не удалось запустить: %s" % self.proc.errorString(), "Запуск", C["error"]))
        self.proc.start(interp, ["-u", path])
        self.a_stop.setEnabled(True)
        self.status_label.setText("Выполняется…")

    def _read_out(self):
        self.output.write(bytes(self.proc.readAllStandardOutput()).decode("utf-8", "replace"), "Запуск")

    def _read_err(self):
        text = bytes(self.proc.readAllStandardError()).decode("utf-8", "replace")
        self.run_stderr += text
        self.output.write(text, "Запуск", C["error"])

    def _run_finished(self, code, _status):
        self.a_stop.setEnabled(False)
        self.output.write("Программа завершилась с кодом %d" % code, "Запуск", C["dim"] if code == 0 else C["warning"])
        self.status("Готово" if code == 0 else "Программа завершилась с ошибкой (код %d)" % code, 0)
        if self.run_stderr and "Traceback" in self.run_stderr:
            frames = re.findall(r'File "(.+?)", line (\d+)', self.run_stderr)
            last = self.run_stderr.strip().splitlines()[-1]
            if frames:
                own = [f for f in frames if self.root and os.path.abspath(f[0]).startswith(self.root)] or frames
                path, line = own[-1]
                self.errors.set_group("Запуск", os.path.abspath(path), [(int(line), 1, last, "error")])
                self.show_dock(self.d_errors)

    def stop_run(self):
        if self._running():
            self.proc.kill()
            self.output.write("Остановлено пользователем", "Запуск", C["warning"])

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
                return [(e.lineno or 1, e.offset or 1, "Синтаксическая ошибка: %s" % e.msg, "error")]
            return []
        if path.endswith(".cs"):
            return check_csharp(text)
        return []

    def build_solution(self):
        if not self.root:
            return
        self.errors.clear_source("Сборка")
        self.output.clear()
        self.output.write("------ Проверка начата: решение «%s» ------" % os.path.basename(self.root), "Сборка", C["link"])
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
                self.errors.set_group("Сборка", p, diags)
                for line, col, msg, sev in diags:
                    self.output.write("%s(%d,%d): %s: %s" % (os.path.relpath(p, self.root), line, col,
                                                           "ошибка" if sev == "error" else "предупреждение", msg),
                                      "Сборка", C["error"] if sev == "error" else C["warning"])
        ok = bad = 0
        for proj, (n, e, w) in sorted(per_project.items()):
            self.output.write("%s: проверено файлов %d — ошибок %d, предупреждений %d" % (proj, n, e, w), "Сборка")
            if e:
                bad += 1
            else:
                ok += 1
        self.output.write("========== Проверка: успешно %d, с ошибками %d ==========" % (ok, bad), "Сборка",
                          C["ok"] if not bad else C["error"])
        self.show_dock(self.d_output)
        self.status("Проверка завершена: успешно %d, с ошибками %d" % (ok, bad))

    def build_current(self):
        ed = self.editor()
        if ed:
            ed.run_diagnostics()
            e = sum(1 for d in ed.diagnostics if d[3] == "error")
            self.status("%s: ошибок %d" % (os.path.basename(ed.path), e))
            self.show_dock(self.d_errors)

    # ------------------------------------------------------------ выход
    def closeEvent(self, e):
        if self.screenshot_mode:
            e.accept()
            return
        for ed in self.editors():
            if ed.document().isModified():
                r = QMessageBox.question(self, APP_NAME, "Есть несохранённые файлы. Сохранить перед выходом?",
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
