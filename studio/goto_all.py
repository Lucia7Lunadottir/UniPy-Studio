"""«Перейти ко всему» (Ctrl+T), как в Visual Studio: файлы, типы и члены всего решения в одном поиске.

Источники (результаты сливаются по мере прихода):
- имена файлов решения (обозреватель решения);
- символы открытых вкладок (уже есть у редакторов — мгновенно);
- workspace/symbol всех загруженных анализаторов (Roslyn, pyright) — по всему решению.
Фильтры в начале запроса: «f » — файлы, «t » — типы, «m » — члены, «:42» — строка в текущем файле.
"""
import os

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QDialog, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout, QLabel

from . import symbols, theme
from .i18n import tr

FILTERS = {"f": "files", "t": "types", "m": "members"}
MAX_ROWS = 200
ROLE = Qt.ItemDataRole.UserRole


class Result:
    __slots__ = ("name", "kind", "path", "line", "where")

    def __init__(self, name, kind, path, line, where=""):
        self.name, self.kind, self.path, self.line, self.where = name, kind, path, line, where

    def key(self):
        return self.path, self.line, self.name


def parse_query(text):
    """(фильтр | None, строка поиска). «:42» → ("line", "42")."""
    text = text.strip()
    if text.startswith(":"):
        return "line", text[1:].strip()
    head, _, rest = text.partition(" ")
    if head.lower() in FILTERS and rest:
        return FILTERS[head.lower()], rest.strip()
    if head.lower() in FILTERS and text.endswith(" "):
        return FILTERS[head.lower()], ""
    return None, text


def wanted(kind, flt):
    if flt == "types":
        return kind in symbols.TYPE_KINDS
    if flt == "members":
        return kind in symbols.MEMBER_KINDS
    if flt == "files":
        return kind == "path"
    return True


class GoToAll(QDialog):
    """Всплывающее окно поиска. Из окна программы берёт: editors(), editor(), explorer.files, root,
    language_services, jump_to()."""

    def __init__(self, window):
        super().__init__(window, Qt.WindowType.Popup)
        self.win = window
        self.setObjectName("goToAll")
        self.resize(640, 420)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(tr("goto_all.placeholder"))
        self.edit.setClearButtonEnabled(True)
        self.edit.textChanged.connect(lambda: self._timer.start())
        self.edit.installEventFilter(self)
        lay.addWidget(self.edit)
        self.hint = QLabel(tr("goto_all.hint"))
        theme.style(self.hint, "color:@dim@;")
        lay.addWidget(self.hint)
        self.list = QListWidget()
        self.list.itemActivated.connect(self._open)
        lay.addWidget(self.list, 1)
        self._timer = QTimer(self, singleShot=True, interval=140, timeout=self.search)
        self._results = {}
        self._generation = 0

    def popup(self, text=""):
        anchor = self.win.tabs if self.win.tabs.isVisible() else self.win
        top = anchor.mapToGlobal(anchor.rect().topLeft())
        self.move(top.x() + max(0, (anchor.width() - self.width()) // 2), top.y() + 4)
        self.edit.setText(text)
        self.show()
        self.edit.setFocus()
        self.edit.selectAll()
        self.search()

    # --- поиск
    def search(self):
        self._generation += 1
        generation = self._generation
        self._results = {}
        flt, query = parse_query(self.edit.text())
        if flt == "line":
            self._show_line(query)
            return
        self._add(self._file_results(query, flt) + self._open_symbol_results(query, flt), query, flt)
        if flt != "files" and len(query) >= 2:
            for svc in self.win.language_services:
                for srv in list(svc.servers.values()):
                    srv.workspace_symbols(query, lambda found, g=generation, q=query, f=flt:
                                          g == self._generation and found and self._add(
                                              [self._from_symbol(s) for s in found], q, f))

    def _file_results(self, query, flt):
        if flt not in (None, "files") or not query:
            return []
        root = self.win.root or "/"
        out = []
        for path in getattr(self.win.explorer, "files", []):
            name = os.path.basename(path)
            if symbols.matches(query, name) or query.casefold() in os.path.relpath(path, root).casefold():
                out.append(Result(name, "path", path, 0, os.path.relpath(os.path.dirname(path), root)))
                if len(out) >= MAX_ROWS:
                    break
        return out

    def _open_symbol_results(self, query, flt):
        if flt == "files":
            return []
        out = []
        for ed in self.win.editors():
            for _depth, s in symbols.walk(getattr(ed, "symbols", [])):
                if s.kind == "variable" or not symbols.matches(query, s.name):
                    continue
                out.append(Result(s.name, s.kind, ed.path, s.start, os.path.basename(ed.path)))
        return out

    def _from_symbol(self, s):
        where = os.path.relpath(s.path, self.win.root) if self.win.root else s.path
        if s.container:
            where = "%s — %s" % (s.container, where)
        return Result(s.name, s.kind, s.path, s.start, where)

    def _add(self, results, query, flt):
        for r in results:
            if not wanted(r.kind, flt):
                continue
            if r.kind != "path" and not symbols.matches(query, r.name.split("(")[0]):
                continue                        # файлы уже отобраны по имени и пути
            self._results.setdefault(r.key(), r)
        self._render(query)

    def _render(self, query):
        current = self.list.currentItem().data(ROLE).key() if self.list.currentItem() else None
        ranked = sorted(self._results.values(),
                        key=lambda r: (symbols.score(query, r.name.split("(")[0]), r.kind == "path", r.where))[:MAX_ROWS]
        self.list.clear()
        for r in ranked:
            label = "%s    %s" % (r.name, r.where) if r.kind == "path" else "%s    %s:%d" % (r.name, r.where, r.line)
            item = QListWidgetItem(theme.file_icon(r.path) if r.kind == "path" else theme.kind_icon(r.kind), label)
            item.setData(ROLE, r)
            item.setToolTip(r.path)
            self.list.addItem(item)
            if r.key() == current:
                self.list.setCurrentItem(item)
        if self.list.currentRow() < 0 and self.list.count():
            self.list.setCurrentRow(0)
        self.hint.setText(tr("goto_all.hint") if self.list.count() or not query else tr("goto_all.nothing"))

    def _show_line(self, query):
        self.list.clear()
        ed = self.win.editor()
        if ed is None or not query.isdigit():
            self.hint.setText(tr("goto_all.line_hint"))
            return
        line = max(1, min(int(query), ed.blockCount()))
        r = Result(tr("goto_all.line") % line, "path", ed.path, line, os.path.basename(ed.path))
        item = QListWidgetItem(theme.file_icon(ed.path), "%s    %s" % (r.name, r.where))
        item.setData(ROLE, r)
        self.list.addItem(item)
        self.list.setCurrentRow(0)

    # --- выбор
    def _open(self, item):
        r = item.data(ROLE)
        self.hide()
        if r.line:
            self.win.jump_to(r.path, r.line)
        else:
            self.win.open_path(r.path)

    def eventFilter(self, obj, e):
        if obj is self.edit and e.type() == e.Type.KeyPress:
            key = e.key()
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up, Qt.Key.Key_PageDown, Qt.Key.Key_PageUp):
                self.list.keyPressEvent(e)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if self._timer.isActive():          # нажали Enter быстрее, чем прошёл поиск
                    self._timer.stop()
                    self.search()
                if self.list.currentItem():
                    self._open(self.list.currentItem())
                return True
            if key == Qt.Key.Key_Escape:
                self.hide()
                return True
        return super().eventFilter(obj, e)
