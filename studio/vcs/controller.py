"""Контроль версий в окне: статус в обозревателе и в панели «Изменения Git», полоски изменённых строк
в редакторе, ветка в строке состояния, коммит, получение и отправка, сравнение с последним коммитом.

Статус читается в фоновом потоке и обновляется: после сохранения, при возвращении в окно, когда Git
меняет индекс или HEAD (коммит из терминала, переключение ветки) и по кнопке.
Из окна берёт: root, editors(), editor(), explorer, open_path, status, builder (вывод fetch/pull/push), font.
"""
import threading

from PyQt6.QtCore import QFileSystemWatcher, QObject, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QCursor, QGuiApplication
from PyQt6.QtWidgets import QMenu, QMessageBox

from .. import vcs
from ..build import CommandSequence
from ..i18n import tr
from .base import VcsError
from .diff_view import DiffWindow
from .linediff import line_markers
from .panel import GitChangesPanel


class VcsController(QObject):
    _status_ready = pyqtSignal(object, object, object, int)     # repo, ветка, статус, поколение
    _base_ready = pyqtSignal(str, object, int)                   # путь, текст HEAD, поколение

    def __init__(self, window):
        super().__init__(window)
        self.win = window
        self.repo = None
        self.branch = ""
        self.status = {}
        self._generation = 0
        self._base = {}                  # путь → текст в HEAD (None — файла нет в HEAD)
        self.panel = GitChangesPanel()
        self.panel.stage.connect(lambda paths: self._do(self.repo.stage, paths))
        self.panel.unstage.connect(lambda paths: self._do(self.repo.unstage, paths))
        self.panel.discard.connect(self._discard)
        self.panel.commit.connect(self._commit)
        self.panel.open_diff.connect(self.show_diff)
        self.panel.open_file.connect(lambda p: window.open_path(p))
        self.panel.remote.connect(self.remote)
        self.panel.refresh_requested.connect(self.refresh)
        self.panel.switch_branch.connect(self.branch_menu)
        self._status_ready.connect(self._apply_status)
        self._base_ready.connect(self._apply_base)
        self._timer = QTimer(self, singleShot=True, interval=300, timeout=self._refresh_now)
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(lambda *_: self.refresh(head_moved=True))
        app = QGuiApplication.instance()
        if app is not None:
            app.applicationStateChanged.connect(
                lambda st: st == Qt.ApplicationState.ApplicationActive and self.refresh(head_moved=True))

    # --- репозиторий
    def set_root(self, root):
        self.repo = vcs.detect(root) if root else None
        self._base.clear()
        self._generation += 1
        if self._watcher.files():
            self._watcher.removePaths(self._watcher.files())
        if self.repo is not None:
            paths = self.repo.watch_paths()
            if paths:
                self._watcher.addPaths(paths)
        self.status, self.branch = {}, ""
        self._apply_status(self.repo, "", {}, self._generation)
        self.refresh()

    def refresh(self, head_moved=False):
        if head_moved:
            self._base.clear()           # другой коммит — другие «исходные» тексты для полосок
            for ed in self.win.editors():
                self._load_base(ed)
        self._timer.start()

    def _refresh_now(self):
        repo, generation = self.repo, self._generation
        if repo is None:
            return

        def work():
            try:
                branch, status = repo.branch(), repo.status()
            except VcsError:
                branch, status = "", {}
            try:
                self._status_ready.emit(repo, branch, status, generation)
            except RuntimeError:        # окно закрыли, пока git считал статус, — ответ больше некому отдавать
                pass
        threading.Thread(target=work, daemon=True).start()

    def _apply_status(self, repo, branch, status, generation):
        if generation != self._generation:
            return
        self.branch, self.status = branch, status
        self.panel.show_repository(repo.root if repo else None, branch, status)
        self.win.explorer.set_vcs_status({p: s.main() for p, s in status.items()})
        self.win.set_branch_label(repo.name if repo else "", branch)
        for ed in self.win.editors():
            self._repaint(ed)

    # --- полоски в редакторе
    def attach_editor(self, ed):
        ed.vcs_timer = QTimer(ed, singleShot=True, interval=300, timeout=lambda e=ed: self._repaint(e))
        ed.textChanged.connect(ed.vcs_timer.start)
        ed.saved.connect(lambda *_: self.refresh())
        self._load_base(ed)

    def _load_base(self, ed):
        repo, generation, path = self.repo, self._generation, ed.path
        if repo is None or not path.startswith(repo.root):
            ed.set_vcs_markers({})
            return
        if path in self._base:
            self._repaint(ed)
            return

        def work():
            self._base_ready.emit(path, repo.base_text(path), generation)
        threading.Thread(target=work, daemon=True).start()

    def _apply_base(self, path, text, generation):
        if generation != self._generation:
            return
        self._base[path] = text
        for ed in self.win.editors():
            if ed.path == path:
                self._repaint(ed)

    def _repaint(self, ed):
        if self.repo is None or ed.path not in self._base:
            return
        st = self.status.get(ed.path)
        base = self._base[ed.path]
        if base is None and st is None:         # нет в HEAD и нет в статусе — игнорируется (.gitignore)
            ed.set_vcs_markers({})
            return
        ed.set_vcs_markers(line_markers(base, ed.toPlainText()))

    # --- действия
    def _do(self, action, *args):
        try:
            action(*args)
        except VcsError as e:
            QMessageBox.warning(self.win, "Git", str(e))
        self.refresh()

    def _discard(self, paths):
        self._do(self.repo.discard, paths)
        for ed in self.win.editors():                       # вкладки подхватят файл с диска (documents.py)
            if ed.path in paths:
                self.win.safety.watcher.check_all()
                break

    def _commit(self, message, amend, everything):
        if self.repo is None:
            return
        try:
            if everything:
                changed = [p for p, s in self.status.items() if s.unstaged]
                if not changed and not amend:
                    self.win.status(tr("vcs.nothing_to_commit"))
                    return
                self.repo.stage(changed)
            out = self.repo.commit(message, amend)
        except VcsError as e:
            QMessageBox.warning(self.win, "Git", str(e))
            self.refresh()
            return
        self.panel.commit_done()
        first = (out.strip().splitlines() or [""])[0]
        self.win.status(tr("vcs.committed") % first, 10000)
        self.refresh(head_moved=True)

    def remote(self, action):
        if self.repo is None:
            return
        try:
            program, args, cwd = self.repo.remote_command(action)
        except VcsError as e:
            QMessageBox.warning(self.win, "Git", str(e))
            return
        job = CommandSequence("git " + action, [(program, args, cwd)])
        self.win.builder.run_job(job, tr("vcs.remote_started") % ("git " + " ".join(args)),
                                 lambda result: self._remote_done(action, result))

    def _remote_done(self, action, result):
        self.win.status(tr("vcs.remote_ok" if result.get("ok") else "vcs.remote_failed") % action, 10000)
        self.refresh(head_moved=True)

    def branch_menu(self):
        if self.repo is None:
            return
        menu = QMenu(self.win)
        try:
            branches = self.repo.branches()
        except VcsError as e:
            QMessageBox.warning(self.win, "Git", str(e))
            return
        for name, current in branches:
            a = menu.addAction(name, lambda n=name: self._checkout(n))
            a.setCheckable(True)
            a.setChecked(current)
            a.setEnabled(not current)
        menu.addSeparator()
        menu.addAction(tr("vcs.open_changes"), lambda: self.win.show_dock(self.win.d_git))
        menu.exec(QCursor.pos())
        menu.deleteLater()

    def _checkout(self, name):
        if any(ed.document().isModified() for ed in self.win.editors()):
            if not self.win.save_all():
                return
        self._do(self.repo.checkout, name)
        self.refresh(head_moved=True)
        self.win.safety.watcher.check_all()                 # файлы ветки — во вкладки

    def show_diff(self, path=None):
        """Сравнить файл с последним коммитом (из панели или для текущей вкладки)."""
        if path is None:
            ed = self.win.editor()
            path = ed.path if ed else None
        if not path or self.repo is None:
            return
        base = self.repo.base_text(path)
        current = next((ed.toPlainText() for ed in self.win.editors() if ed.path == path), None)
        if current is None:
            try:
                from ..editor import decode_source
                with open(path, "rb") as f:
                    current = decode_source(f.read())[1].replace("\r\n", "\n")
            except OSError:
                current = ""
        window = DiffWindow(self.win, path, base, current, self.win.font, lambda p, line: self.win.open_path(p, line))
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        window.show()
