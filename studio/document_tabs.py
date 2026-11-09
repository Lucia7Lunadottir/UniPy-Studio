"""Вкладки документов: страница вкладки (основной редактор + второе окно при «Окно → Разделить»),
закреплённые вкладки (всегда слева, со значком булавки, переживают «Закрыть остальные»), как в Visual Studio.

Окно программы работает с вкладками только через DocumentTabs: editors() — основные редакторы файлов,
views() — все окна редакторов (для ответов анализаторов), current_view() — окно, где сейчас курсор.
Вопросы о сохранении и закрытие файла — забота окна программы (MainWindow.close_tab).
"""
from PyQt6.QtCore import QObject, QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import QApplication, QSplitter, QTabBar, QToolButton

from . import theme
from .editor.split_view import SplitView
from .i18n import bind_text


class DocumentPage(QSplitter):
    """Содержимое вкладки. Без разделения — один редактор; с разделением сверху второе окно того же файла."""

    def __init__(self, editor):
        super().__init__(Qt.Orientation.Vertical)
        self.setChildrenCollapsible(False)
        self.editor = editor
        self.view = None                # второе окно (SplitView) или None
        self.active = editor            # окно, где курсор был последним
        self.pinned = False
        self.addWidget(editor)

    def views(self):
        return [self.editor] + ([self.view] if self.view is not None else [])

    def split(self):
        """Второе окно над основным, на том же месте файла (Окно → Разделить)."""
        if self.view is not None:
            return self.view
        view = SplitView(self.editor)
        self.insertWidget(0, view)
        half = max(80, self.height() // 2)
        self.setSizes([half, max(80, self.height() - half)])
        view.setTextCursor(self.editor.textCursor())
        view.verticalScrollBar().setValue(self.editor.verticalScrollBar().value())
        self.view = view
        return view

    def unsplit(self):
        if self.view is None:
            return
        view, self.view = self.view, None
        if self.active is view:
            self.active = self.editor
        view.detach()
        view.hide()
        view.setParent(None)
        view.deleteLater()

    def dispose(self):
        """Вкладку закрыли: сначала второе окно (у него чужой документ), потом основной редактор."""
        self.unsplit()
        self.editor.calltip.hide()
        self.editor.deleteLater()
        self.deleteLater()


class DocumentTabs(QObject):
    current_changed = pyqtSignal()          # другая вкладка или другое окно внутри вкладки
    view_created = pyqtSignal(object)       # второе окно разделённой вкладки — окну программы подключить сигналы
    pinned_changed = pyqtSignal()

    def __init__(self, tab_widget, parent=None):
        super().__init__(parent)
        self.tabs = tab_widget
        self._normalizing = False
        self.tabs.currentChanged.connect(lambda _i: self.current_changed.emit())
        self.tabs.tabBar().tabMoved.connect(lambda *_: self._normalize())
        QApplication.instance().focusChanged.connect(self._focus_changed)

    # --- чтение
    def count(self):
        return self.tabs.count()

    def pages(self):
        return [self.tabs.widget(i) for i in range(self.tabs.count())]

    def page(self, i):
        w = self.tabs.widget(i)
        return w if isinstance(w, DocumentPage) else None

    def editors(self):
        """Основные редакторы файлов (по одному на вкладку)."""
        return [p.editor for p in self.pages()]

    def views(self):
        """Все окна редакторов, включая вторые окна разделённых вкладок."""
        return [v for p in self.pages() for v in p.views()]

    def current_page(self):
        w = self.tabs.currentWidget()
        return w if isinstance(w, DocumentPage) else None

    def current_view(self):
        page = self.current_page()
        return page.active if page is not None else None

    def page_of(self, editor):
        """Страница основного редактора или его второго окна."""
        primary = getattr(editor, "primary", editor)
        return next((p for p in self.pages() if p.editor is primary), None)

    def index_of(self, editor):
        page = self.page_of(editor)
        return self.tabs.indexOf(page) if page is not None else -1

    # --- вкладки
    def add(self, editor, icon, title, tip):
        page = DocumentPage(editor)
        i = self.tabs.addTab(page, icon, title)
        self.tabs.setTabToolTip(i, tip)
        return i

    def set_current(self, i):
        self.tabs.setCurrentIndex(i)

    def set_title(self, editor, title, tip=None):
        i = self.index_of(editor)
        if i >= 0:
            self.tabs.setTabText(i, title)
            if tip is not None:
                self.tabs.setTabToolTip(i, tip)

    def remove(self, i):
        page = self.page(i)
        self.tabs.removeTab(i)
        if page is not None:
            page.dispose()

    # --- разделение
    def toggle_split(self, page=None):
        page = page or self.current_page()
        if page is None:
            return None
        if page.view is None:
            view = page.split()
            self.view_created.emit(view)
            view.setFocus()
            return view
        page.unsplit()
        page.editor.setFocus()
        return None

    # --- закрепление
    def is_pinned(self, i):
        page = self.page(i)
        return page is not None and page.pinned

    def pinned_count(self):
        return sum(1 for p in self.pages() if p.pinned)

    def set_pinned(self, i, pinned):
        """Закреплённые — в начале ряда, со значком булавки (щелчок по нему открепляет)."""
        page = self.page(i)
        if page is None or page.pinned == pinned:
            return
        page.pinned = pinned
        bar = self.tabs.tabBar()
        if pinned:
            button = QToolButton()
            button.setAutoRaise(True)
            button.setIcon(theme.tool_icon("pin"))
            button.setProperty("tool_icon", "pin")      # перерисуется при смене темы
            button.setIconSize(QSize(12, 12))
            bind_text(button, "tabs.unpin", "setToolTip")
            button.clicked.connect(lambda _c=False, p=page: self.set_pinned(self.tabs.indexOf(p), False))
            bar.setTabButton(i, QTabBar.ButtonPosition.LeftSide, button)
        else:
            old = bar.tabButton(i, QTabBar.ButtonPosition.LeftSide)
            bar.setTabButton(i, QTabBar.ButtonPosition.LeftSide, None)
            if old is not None:
                old.deleteLater()
        self._normalize()
        self.pinned_changed.emit()

    def _normalize(self):
        """Закреплённые вкладки всегда левее остальных (и после перетаскивания мышью)."""
        if self._normalizing:
            return
        pages = self.pages()
        wanted = [p for p in pages if p.pinned] + [p for p in pages if not p.pinned]
        if wanted == pages:
            return
        self._normalizing = True
        try:
            current = self.tabs.currentWidget()
            bar = self.tabs.tabBar()
            for target, page in enumerate(wanted):
                bar.moveTab(self.tabs.indexOf(page), target)
            self.tabs.setCurrentWidget(current)
        finally:
            self._normalizing = False

    # --- какое окно активно
    def _focus_changed(self, _old, new):
        page = self.current_page()
        if page is None or new is None:
            return
        for view in page.views():
            if new is view or view.isAncestorOf(new):
                if page.active is not view:
                    page.active = view
                    self.current_changed.emit()
                return
