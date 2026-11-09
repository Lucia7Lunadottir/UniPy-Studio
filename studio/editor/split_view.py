"""Второе окно того же файла (Окно → Разделить, как в Visual Studio): общий документ, свои курсор и прокрутка.

Всё, что относится к файлу, а не к окну, — у основного редактора (primary): путь, кодировка, состояние на диске,
сохранение, ошибки, точки останова и закладки, полоски Git, символы. Эти поля здесь — ссылки на его поля
(SHARED), а сохранение и проверка ошибок передаются ему. Подсказки, переходы, лампочка, поиск работают
в этом окне как в обычном редакторе: окно программы ищет ответ по редактору среди всех видов (views()).
"""
from .code_editor import CodeEditor

# поля «про файл»: читаются и пишутся у основного редактора
SHARED = ("path", "encoding", "newline", "disk", "missing_on_disk", "indent", "lsp_diagnostics", "diagnostics",
          "faded", "vcs_markers", "symbols", "breakpoints", "bookmarks", "execution_line", "execution_is_top",
          "debug_hover")


def _shared(name):
    """Поле основного редактора. Пока конструктор CodeEditor заполняет свои поля (основной ещё не привязан),
    значения складываются в черновик и потом не используются."""
    def get(self):
        primary = self.__dict__.get("_primary")
        return getattr(primary, name) if primary is not None else self.__dict__.get("_draft_" + name)

    def put(self, value):
        primary = self.__dict__.get("_primary")
        if primary is not None:
            setattr(primary, name, value)
        else:
            self.__dict__["_draft_" + name] = value
    return property(get, put)


class SplitView(CodeEditor):
    def __init__(self, primary):
        super().__init__(primary.path, primary.services, primary.font(), shared=primary)
        self._primary = primary
        primary.split_views.append(self)
        # ошибки считает основной редактор: правка в любом окне меняет общий документ, а его textChanged
        # срабатывает и у основного
        self.textChanged.disconnect(self._diag_timer.start)
        self.setLineWrapMode(primary.lineWrapMode())
        self.set_indent_style(primary.indent)
        for name in ("show_whitespace", "indent_guides"):
            self.set_view_option(name, getattr(primary, name))
        self.set_navigation_bar(not primary.navbar.isHidden())
        for store in (primary.breakpoints, primary.bookmarks):
            if store is not None:
                store.changed.connect(self._store_changed)
        self.mirror_primary()

    @property
    def primary(self):
        return self._primary

    def detach(self):
        """Окно закрывают — отвязаться от основного (до deleteLater)."""
        if self in self._primary.split_views:
            self._primary.split_views.remove(self)
        for store in (self.breakpoints, self.bookmarks):
            if store is not None:
                try:
                    store.changed.disconnect(self._store_changed)
                except TypeError:
                    pass

    def _store_changed(self, _path):
        self.gutter.update()

    def mirror_primary(self):
        """Основной редактор сменил общее состояние — перерисовать подчёркивания, поле, панель навигации."""
        if not self.navbar.isHidden():
            self.navbar.set_symbols(self.symbols)
        self.update_selections()
        self.gutter.update()

    # --- то, что делает файл, а не окно
    def save(self):
        return self._primary.save()

    def load(self):
        pass                                # текст уже в общем документе

    def reload_from(self, raw):
        self._primary.reload_from(raw)

    def run_diagnostics(self, *, sync_server=True):
        self._primary.run_diagnostics(sync_server=sync_server)

    def set_lsp_diagnostics(self, diags):
        self._primary.set_lsp_diagnostics(diags)

    def refresh_views(self):
        self._primary.refresh_views()


for _name in SHARED:
    setattr(SplitView, _name, _shared(_name))
