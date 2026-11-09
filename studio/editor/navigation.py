"""Переходы и рефакторинг через анализатор: F12, Shift+F12, переименование."""
import re
from ..i18n import tr


class NavigationMixin:
    """Определение, ссылки, переименование."""

    def _request_source_definition(self):
        self.busy_changed.emit(tr("editor.searching_definition_sources"))
        self.services.csharp_index.submit(kind="definition", req=0, editor=id(self),
            code=self.toPlainText(), pos=self._source_offset(), path=self.path)

    def go_to_definition(self):
        srv = self._lsp_server()
        if srv or (self.lang == "python" and self.services.jedi.available):
            word = self._word_at(self.textCursor())
            # переход в класс из сборки (декомпиляция) может занять несколько секунд — показываем, что идёт работа
            self.busy_changed.emit(tr("editor.going_definition") % word if word else tr("editor.going_to_definition"))
        if srv:
            line, col = self._line_col()
            srv.submit("definition", req=0, editor=id(self), code=self.toPlainText(),
                       line=line, col=col, path=self.path)
        elif self.lang == "python" and self.services.jedi.available:
            line, col = self._line_col()
            self.services.jedi.submit("definition", req=0, editor=id(self),
                                      code=self.toPlainText(), line=line, col=col, path=self.path)
        elif self.lang == "csharp":
            self._request_source_definition()
        else:
            word = self._word_at(self.textCursor())
            m = re.search(r"\b(?:class|void|[A-Z]\w*|int|float|bool|string)\s+%s\b" % re.escape(word),
                          self.toPlainText()) if word else None
            if m:
                line = self.toPlainText().count("\n", 0, m.start()) + 1
                self.go_to_line(line)
            else:
                self.services.status(tr("editor.definition_not_found_file") % word)

    def _symbol_request(self, kind, busy_text, extra=None):
        """Запрос к анализатору о слове под курсором. False — анализатора для этого файла нет."""
        line, col = self._line_col()
        srv = self._lsp_server()
        if srv:
            srv.submit(kind, req=0, editor=id(self), code=self.toPlainText(), line=line, col=col,
                       path=self.path, extra=extra)
        elif self.lang == "python" and self.services.jedi.available:
            self.services.jedi.submit(kind, req=0, editor=id(self), code=self.toPlainText(),
                                      line=line, col=col, path=self.path, extra=extra or {})
        else:
            self.services.status(tr("editor.no_code_analyzer_file_solution"))
            return False
        self.busy_changed.emit(busy_text)
        return True

    def symbol_under_cursor(self):
        return self._word_at(self.textCursor())

    def find_references(self):
        word = self.symbol_under_cursor()
        if word:
            self._last_symbol = word
            self._symbol_request("references", tr("editor.finding_references") % word)

    def rename_symbol(self, new_name):
        self._new_name = new_name
        self._symbol_request("rename", tr("editor.renaming") % new_name, {"new_name": new_name})
