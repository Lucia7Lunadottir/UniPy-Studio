"""Лампочка (Ctrl+.): быстрые исправления и рефакторинги анализатора и самой программы."""
from PyQt6.QtWidgets import QMenu
from .. import refactor, refactor_python
from ..i18n import tr


class QuickActionsMixin:
    """Лампочка быстрых действий."""

    def _own_actions(self):
        """Рефакторинги самой программы (работают и без анализатора)."""
        if self.isReadOnly():
            return []
        text = self.toPlainText()
        if self.lang == "python":
            if refactor_python.can_move(text, self.textCursor().blockNumber() + 1):
                return [{"title": tr("common.move_type_own_file"), "source": "own",
                         "run": self.services.move_type_to_file}]
            return []
        if self.lang != "csharp":
            return []
        pos = len(text.encode("utf-16-le")[:self.textCursor().position() * 2].decode("utf-16-le", "ignore"))
        try:
            types = refactor.parse(text)[0]
        except Exception:       # noqa: BLE001 — разбор не должен мешать набору текста
            return []
        if len(types) > 1 and any(t.begin <= pos <= t.end for t in types):
            return [{"title": tr("common.move_type_own_file"), "source": "own",
                     "run": self.services.move_type_to_file}]
        return []

    def _refresh_bulb(self):
        if not self.isVisible() or self.completer.popup().isVisible():
            return
        self._bulb_req += 1
        req = self._bulb_req
        line, col = self._line_col()
        line -= 1
        own = self._own_actions()
        self._bulb_actions = own
        self._bulb_line = line if own else None
        self.gutter.update()
        srv = self._lsp_server()
        if srv is not None and hasattr(srv, "code_actions"):
            srv.code_actions(self.path, line, col,
                             lambda items, req=req, line=line, own=own: self._bulb_result(req, line, own, items),
                             explicit=self._bulb_open_after)
        elif self._bulb_open_after:
            self._bulb_open_after = False
            self._open_bulb_menu()

    def _bulb_result(self, req, line, own, items):
        if req != self._bulb_req or line != self.textCursor().blockNumber():
            return                  # пока сервер думал, курсор уже ушёл
        self._bulb_actions = [{"title": it["title"], "source": "lsp", "item": it} for it in items] + own
        self._bulb_line = line if self._bulb_actions else None
        self.gutter.update()
        if self._bulb_open_after:
            self._bulb_open_after = False
            self._open_bulb_menu()

    def show_bulb_menu(self):
        """Ctrl+. или клик по лампочке: список быстрых действий."""
        if self._bulb_line == self.textCursor().blockNumber() and self._bulb_actions:
            self._open_bulb_menu()
            return
        self._bulb_open_after = True        # данных для этой строки ещё нет — спросим и откроем
        self._refresh_bulb()

    FIX_ALL_SCOPE_KEYS = {"Document": "editor.fix_all_document", "ContainingMember": "editor.fix_all_member",
                          "ContainingType": "editor.fix_all_type", "Project": "editor.fix_all_project",
                          "Solution": "editor.fix_all_solution"}

    def _open_bulb_menu(self):
        if not self._bulb_actions:
            self.services.status(tr("editor.no_quick_actions_here"))
            return
        menu = QMenu(self)
        srv = self._lsp_server()
        for a in self._bulb_actions:
            self._add_bulb_entry(menu, a, srv)
        menu.exec(self.viewport().mapToGlobal(self.cursorRect().bottomLeft()))
        menu.deleteLater()

    def _add_bulb_entry(self, menu, action, srv):
        """Пункт меню; вложенные действия анализатора — подменю вариантов, «Исправить все» — подменю областей
        (как в Visual Studio)."""
        item = action.get("item")
        if action["source"] == "lsp" and srv is not None and item is not None:
            children = srv.code_action_children(item)
            if children:
                sub = menu.addMenu(action["title"])
                for child in children:
                    self._add_bulb_entry(sub, {"title": child["title"], "source": "lsp", "item": child}, srv)
                return
            scopes = srv.fix_all_scopes(item)
            if scopes:
                sub = menu.addMenu(action["title"])
                for scope in scopes:
                    key = self.FIX_ALL_SCOPE_KEYS.get(scope)
                    sub.addAction(tr(key) if key else scope).triggered.connect(
                        lambda _c=False, a=action, sc=scope: self._run_bulb_action(a, sc))
                return
        menu.addAction(action["title"]).triggered.connect(lambda _c=False, a=action: self._run_bulb_action(a))

    def _run_bulb_action(self, action, scope=None):
        if action["source"] == "own":
            action["run"]()
            return
        srv = self._lsp_server()
        if srv is None:
            return
        title = action["title"]
        srv.resolve_code_action(action["item"], lambda edit, title=title: self.services.apply_code_action(edit, title),
                                scope=scope)
