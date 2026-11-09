"""Сохранность файлов: атомарная запись, изменения на диске вне редактора, автовосстановление.

Три независимые части, каждая знает только своё:
- `atomic_write` — запись, после которой на диске либо старый файл целиком, либо новый целиком;
- `DiskState` + `DiskWatcher` — что сейчас на диске и сообщение, когда это изменил кто-то другой
  (Unity, git, другая программа);
- `RecoveryStore` — копии несохранённых правок, чтобы после падения ничего не пропало.
Решения «перезагрузить или спросить» принимает окно (`ExternalChanges`), а не эти классы.
"""
import hashlib
import json
import os
import stat
import tempfile
import time

from PyQt6.QtCore import QObject, QFileSystemWatcher, QTimer, pyqtSignal, Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QLabel, QListWidget, QListWidgetItem, QDialogButtonBox,
                             QMessageBox)

from .i18n import tr

TEMP_SUFFIX = ".unipy-tmp"


# ---------------------------------------------------------------- запись
def atomic_write(path, data):
    """Пишет байты во временный файл рядом и подменяет им оригинал одной операцией (rename).
    Если программа упадёт или кончится место посередине — на диске останется прежний файл.
    Ссылка (symlink) сохраняется: пишется файл, на который она указывает. Права файла сохраняются.
    Если в папку нельзя писать (а в сам файл можно) — пишет прямо в файл, как раньше."""
    target = os.path.realpath(path)
    folder = os.path.dirname(target) or "."
    try:
        # точка в начале: Unity и обозреватель решения скрытые файлы не показывают
        fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(target) + ".", suffix=TEMP_SUFFIX, dir=folder)
    except PermissionError:
        with open(target, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        return
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        try:
            mode = stat.S_IMODE(os.stat(target).st_mode)
        except FileNotFoundError:
            umask = os.umask(0)
            os.umask(umask)
            mode = 0o666 & ~umask
        os.chmod(tmp, mode)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:        # чтобы и само переименование пережило внезапное выключение
        dir_fd = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass


def digest(data):
    return hashlib.sha1(data).hexdigest()


# ---------------------------------------------------------------- что на диске
class DiskState:
    """Какой файл был на диске, когда редактор его открыл или сохранил.
    Сравнивается сначала время и размер (дёшево), при расхождении — содержимое:
    Unity часто перезаписывает .csproj и скрипты теми же байтами, это не изменение."""
    MISSING = object()

    def __init__(self, path):
        self.path = path
        self.stamp = None
        self.sha = None

    @staticmethod
    def _stamp(path):
        try:
            st = os.stat(path)
        except OSError:
            return None
        return st.st_mtime_ns, st.st_size

    def remember(self, data):
        """Вызывается после чтения и после записи: эти байты теперь «наши»."""
        self.stamp = self._stamp(self.path)
        self.sha = digest(data)

    def check(self):
        """None — на диске то же, что мы помним; MISSING — файла нет; иначе — новые байты с диска."""
        stamp = self._stamp(self.path)
        if stamp is None:
            return self.MISSING
        if stamp == self.stamp:
            return None
        try:
            with open(self.path, "rb") as f:
                data = f.read()
        except OSError:
            return None             # файл прямо сейчас пишут — посмотрим при следующем сигнале
        if digest(data) == self.sha:
            self.stamp = stamp      # тронули, но не изменили
            return None
        return data

    def accept(self, data):
        """Пользователь решил оставить свою версию: то, что сейчас на диске, больше не новость."""
        self.remember(data)


class DiskWatcher(QObject):
    """Следит за файлами открытых вкладок. Сообщает `changed(path)` — дальше решает окно.

    QFileSystemWatcher теряет файл, когда его подменяют переименованием (так сохраняют Unity, git
    и мы сами), поэтому путь добавляется заново после каждой проверки. Сигналы склеиваются:
    Unity пишет файл в несколько приёмов. При возвращении в окно проверяются все файлы — на случай,
    если сигнал потерялся (сетевой диск, кончился лимит inotify)."""
    changed = pyqtSignal(str)

    DEBOUNCE_MS = 300

    def __init__(self, parent=None):
        super().__init__(parent)
        self._paths = set()
        self._pending = set()
        self._fs = QFileSystemWatcher(self)
        self._fs.fileChanged.connect(self._touched)
        self._timer = QTimer(self, singleShot=True, interval=self.DEBOUNCE_MS, timeout=self._flush)
        self.enabled = True
        app = QGuiApplication.instance()
        if app is not None:
            app.applicationStateChanged.connect(self._app_state)

    def watch(self, path):
        self._paths.add(path)
        self._readd(path)

    def unwatch(self, path):
        self._paths.discard(path)
        self._pending.discard(path)
        if path in self._fs.files():
            self._fs.removePath(path)

    def _readd(self, path):
        if path in self._paths and os.path.exists(path) and path not in self._fs.files():
            self._fs.addPath(path)

    def _touched(self, path):
        if path in self._paths:
            self._pending.add(path)
            self._timer.start()

    def _flush(self):
        pending, self._pending = self._pending, set()
        for path in sorted(pending):
            self._readd(path)
            if self.enabled and path in self._paths:
                self.changed.emit(path)

    def _app_state(self, state):
        if state == Qt.ApplicationState.ApplicationActive:
            self.check_all()

    def check_all(self):
        self._pending |= self._paths
        self._timer.start()


class ExternalChanges(QObject):
    """Что делать, когда открытый файл изменили вне редактора (как в Visual Studio).

    Нет несохранённых правок и включено «загружать сами» — тихо загружает новую версию
    (одним шагом отмены: Ctrl+Z вернёт прежний текст). Есть правки — спрашивает, но только когда
    окно активно: пока вы в Unity, вопросы копятся и задаются при возвращении. Файл удалили —
    вкладка помечается несохранённой, и при сохранении файл будет создан заново."""
    reloaded = pyqtSignal(object)        # редактор
    deleted = pyqtSignal(object)

    def __init__(self, window, editor_for, reload_unmodified):
        super().__init__(window)
        self.window = window
        self.editor_for = editor_for                    # путь → редактор или None
        self.reload_unmodified = reload_unmodified      # () → bool, из параметров
        self._waiting = []                              # пути с вопросом, который ещё не задан
        self._asking = False
        app = QGuiApplication.instance()
        if app is not None:
            app.applicationStateChanged.connect(lambda s: s == Qt.ApplicationState.ApplicationActive
                                                and QTimer.singleShot(0, self._ask_waiting))

    def on_changed(self, path):
        ed = self.editor_for(path)
        if ed is None:
            return
        data = ed.disk.check()
        if data is None:
            return
        if data is DiskState.MISSING:
            if not getattr(ed, "missing_on_disk", False):
                ed.missing_on_disk = True
                ed.document().setModified(True)
                self.deleted.emit(ed)
            return
        ed.missing_on_disk = False
        if not ed.document().isModified() and self.reload_unmodified():
            ed.reload_from(data)
            self.reloaded.emit(ed)
            return
        if path not in self._waiting:
            self._waiting.append(path)
        self._ask_waiting()

    def _ask_waiting(self):
        if self._asking or not self._waiting:
            return
        if QGuiApplication.applicationState() != Qt.ApplicationState.ApplicationActive:
            return
        self._asking = True
        try:
            while self._waiting:
                path = self._waiting.pop(0)
                ed = self.editor_for(path)
                if ed is None:
                    continue
                data = ed.disk.check()          # пока ждали, могло измениться ещё раз или вернуться
                if data is None or data is DiskState.MISSING:
                    continue
                if not ed.document().isModified() and self.reload_unmodified():
                    ed.reload_from(data)
                    self.reloaded.emit(ed)
                    continue
                self._ask(ed, data)
        finally:
            self._asking = False

    def _ask(self, ed, data):
        box = QMessageBox(QMessageBox.Icon.Question, tr("documents.changed_outside_title"),
                          tr("documents.changed_outside") % os.path.basename(ed.path), parent=self.window)
        box.setInformativeText(tr("documents.changed_outside_detail") if ed.document().isModified()
                               else tr("documents.changed_outside_detail_clean"))
        load = box.addButton(tr("documents.load_from_disk"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(tr("documents.keep_mine"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(load)
        box.exec()
        if box.clickedButton() is load:
            ed.reload_from(data)
            self.reloaded.emit(ed)
        else:
            ed.disk.accept(data)
            ed.document().setModified(True)     # текст в редакторе теперь отличается от диска


# ---------------------------------------------------------------- автовосстановление
def _state_dir():
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(base, "unipy-studio", "recovery")


def _process_id(pid):
    """pid + время запуска процесса: номер pid может достаться другой программе после падения."""
    try:
        with open("/proc/%d/stat" % pid, encoding="ascii", errors="replace") as f:
            fields = f.read().rsplit(")", 1)[1].split()
        return "%d:%s" % (pid, fields[19])
    except (OSError, IndexError):
        return None


class Draft:
    """Копия несохранённого файла."""
    def __init__(self, store_path, data):
        self.store_path = store_path
        self.path = data["path"]
        self.text = data["text"]
        self.encoding = data.get("encoding", "utf-8")
        self.newline = data.get("newline", "\n")
        self.disk_sha = data.get("disk_sha")
        self.saved_at = float(data.get("saved_at", 0))
        self.owner = data.get("owner")

    def owner_alive(self):
        if not self.owner:
            return False
        pid = int(self.owner.split(":")[0])
        return _process_id(pid) == self.owner

    def disk_changed(self):
        """Файл на диске менялся после того, как сделана копия (или его уже нет)."""
        try:
            with open(self.path, "rb") as f:
                return digest(f.read()) != self.disk_sha
        except OSError:
            return True


class RecoveryStore:
    """Хранит копии несохранённых правок в ~/.local/state/unipy-studio/recovery (по файлу на документ).
    Копия удаляется, когда файл сохранён, правки отменены или программа закрыта как обычно.
    Копии от упавшего процесса предлагаются при следующем открытии проекта; копии другого
    работающего окна UniPy Studio не трогаются."""

    def __init__(self, folder=None):
        self.folder = folder or _state_dir()
        self.owner = _process_id(os.getpid())
        self._written = {}          # путь → ревизия документа, уже записанная в копию

    def _file_for(self, path):
        return os.path.join(self.folder, hashlib.sha1(path.encode("utf-8", "surrogateescape")).hexdigest() + ".json")

    def save(self, ed):
        """Записывает копию, если текст изменился с прошлого раза. Возвращает True, если записал."""
        rev = ed.document().revision()
        if self._written.get(ed.path) == rev:
            return False
        os.makedirs(self.folder, mode=0o700, exist_ok=True)
        payload = {"path": ed.path, "text": ed.toPlainText(), "encoding": ed.encoding, "newline": ed.newline,
                   "disk_sha": ed.disk.sha, "saved_at": time.time(), "owner": self.owner}
        atomic_write(self._file_for(ed.path), json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        self._written[ed.path] = rev
        return True

    def discard(self, path):
        self._written.pop(path, None)
        try:
            os.unlink(self._file_for(path))
        except OSError:
            pass

    def snapshot(self, editors):
        """Раз в N секунд: копии для изменённых вкладок, удаление копий для сохранённых."""
        for ed in editors:
            if ed.document().isModified():
                try:
                    self.save(ed)
                except OSError:
                    pass                # нет места или прав — редактор от этого не должен падать
            elif ed.path in self._written:
                self.discard(ed.path)

    def discard_mine(self):
        """Обычный выход: все копии этого процесса больше не нужны."""
        for draft in self._drafts():
            if draft.owner == self.owner:
                self.discard(draft.path)

    def _drafts(self):
        try:
            names = os.listdir(self.folder)
        except OSError:
            return []
        out = []
        for name in names:
            if not name.endswith(".json"):
                continue
            full = os.path.join(self.folder, name)
            try:
                with open(full, encoding="utf-8") as f:
                    out.append(Draft(full, json.load(f)))
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return out

    def orphans(self, root, keep_days):
        """Копии от упавших сеансов для файлов внутри root. Старше keep_days — удаляются."""
        limit = time.time() - keep_days * 86400
        root = os.path.join(os.path.abspath(root), "")
        found = []
        for draft in self._drafts():
            if draft.owner_alive():
                continue
            if draft.saved_at < limit:
                self.discard(draft.path)
                continue
            if draft.path.startswith(root):
                found.append(draft)
        return sorted(found, key=lambda d: d.path)


class RecoveryDialog(QDialog):
    """«UniPy Studio закрылась, не сохранив эти файлы» — выбрать, что восстановить."""

    def __init__(self, parent, drafts, root):
        super().__init__(parent)
        self.setWindowTitle(tr("documents.recovery_title"))
        self.drafts = drafts
        lay = QVBoxLayout(self)
        info = QLabel(tr("documents.recovery_intro"))
        info.setWordWrap(True)
        lay.addWidget(info)
        self.list = QListWidget()
        for draft in drafts:
            when = time.strftime("%d.%m %H:%M", time.localtime(draft.saved_at))
            text = "%s   (%s)" % (os.path.relpath(draft.path, root), when)
            if not os.path.exists(draft.path):
                text += "   " + tr("documents.recovery_file_gone")
            elif draft.disk_changed():
                text += "   " + tr("documents.recovery_disk_changed")
            item = QListWidgetItem(text)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.list.addItem(item)
        lay.addWidget(self.list, 1)
        buttons = QDialogButtonBox()
        restore = buttons.addButton(tr("documents.recovery_restore"), QDialogButtonBox.ButtonRole.AcceptRole)
        self.discard_button = buttons.addButton(tr("documents.recovery_discard"), QDialogButtonBox.ButtonRole.DestructiveRole)
        later = buttons.addButton(tr("documents.recovery_later"), QDialogButtonBox.ButtonRole.RejectRole)
        restore.clicked.connect(lambda: self.done(1))
        self.discard_button.clicked.connect(self._confirm_discard)
        later.clicked.connect(self.reject)
        lay.addWidget(buttons)
        self.resize(620, 340)

    def _confirm_discard(self):
        if QMessageBox.question(self, self.windowTitle(), tr("documents.recovery_discard_confirm")) \
                == QMessageBox.StandardButton.Yes:
            self.done(2)

    def chosen(self):
        return [d for i, d in enumerate(self.drafts)
                if self.list.item(i).checkState() == Qt.CheckState.Checked]


RESTORE, DISCARD = 1, 2
