"""Сохранность документов в окне: слежение за открытыми файлами и автовосстановление.
Механизмы — в documents.py; здесь только связь с вкладками, параметрами и строкой состояния."""
import os

from PyQt6.QtCore import QTimer

from . import documents
from .i18n import tr
from .theme import C


class DocumentSafety:
    def __init__(self, window):
        self.win = window
        self.watcher = documents.DiskWatcher(window)
        self.watcher.enabled = window.options["documents/detect_external"] and not window.screenshot_mode
        self.external = documents.ExternalChanges(window, self.editor_for_path,
                                                  lambda: window.options["documents/reload_unmodified"])
        self.watcher.changed.connect(self.external.on_changed)
        self.external.reloaded.connect(lambda ed: window.status(tr("documents.reloaded") % os.path.basename(ed.path)))
        self.external.deleted.connect(self._deleted)
        self.recovery = documents.RecoveryStore()
        self.timer = QTimer(window, timeout=self.snapshot)
        self.apply_options()

    # --- вкладки
    def track(self, ed):
        """Новая вкладка: следить за её файлом, копию убирать после сохранения."""
        ed.saved.connect(self.recovery.discard)
        self.watcher.watch(ed.path)

    def forget(self, ed):
        """Вкладку закрыли: правки сохранены или от них сознательно отказались."""
        self.watcher.unwatch(ed.path)
        self.recovery.discard(ed.path)

    def renamed(self, old, new):
        self.watcher.unwatch(old)
        self.recovery.discard(old)
        self.watcher.watch(new)

    def shutdown(self):
        """Обычный выход: копии на случай падения больше не нужны."""
        self.timer.stop()
        self.recovery.discard_mine()

    def editor_for_path(self, path):
        return next((ed for ed in self.win.editors() if ed.path == path), None)

    def _deleted(self, ed):
        msg = tr("documents.deleted_outside") % os.path.basename(ed.path)
        self.win.status(msg, 12000)
        self.win.output.write(msg, "common.build", C["warning"])

    # --- параметры
    def recovery_on(self):
        return self.win.options["recovery/enabled"] and not self.win.screenshot_mode

    def apply_options(self):
        self.watcher.enabled = self.win.options["documents/detect_external"] and not self.win.screenshot_mode
        if self.recovery_on():
            self.timer.start(self.win.options["recovery/interval_sec"] * 1000)
        else:
            self.timer.stop()
            self.recovery.discard_mine()

    def option_changed(self, key):
        if key == "documents/detect_external":
            self.apply_options()
            if self.watcher.enabled:
                self.watcher.check_all()
        elif key.startswith("recovery/"):
            self.apply_options()

    # --- копии
    def snapshot(self):
        self.recovery.snapshot(self.win.editors())

    def offer_recovery(self):
        """После падения: копии несохранённых правок файлов этого проекта — восстановить?"""
        win = self.win
        if not win.root or not self.recovery_on():
            return
        drafts = self.recovery.orphans(win.root, win.options["recovery/keep_days"])
        if not drafts:
            return
        dialog = documents.RecoveryDialog(win, drafts, win.root)
        result = dialog.exec()
        if result == documents.DISCARD:
            for draft in drafts:
                self.recovery.discard(draft.path)
            return
        if result != documents.RESTORE:
            return                          # «Позже» — копии остаются до следующего открытия
        restored = 0
        for draft in dialog.chosen():
            if not os.path.exists(draft.path):
                try:
                    os.makedirs(os.path.dirname(draft.path), exist_ok=True)
                    documents.atomic_write(draft.path, b"")
                except OSError as e:
                    win.output.write(tr("documents.recovery_failed") % (draft.path, e), "common.build", C["error"])
                    continue
            ed = win.open_path(draft.path)
            if ed is None:
                continue
            ed.set_text_as_edit(draft.text)     # Ctrl+Z вернёт версию с диска
            ed.document().setModified(True)
            self.recovery.discard(draft.path)
            self.recovery.save(ed)              # копия теперь принадлежит этому окну
            restored += 1
        win.status(tr("documents.recovered") % restored, 10000)
