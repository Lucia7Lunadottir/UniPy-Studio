"""Анализаторы кода в окне: надпись состояния, перезапуск (по команде и по порогу памяти),
обновление Roslyn/pyright из «Справка → Обновления анализаторов»."""
from . import updates
from .i18n import tr
from .options import RESTART_THRESHOLDS
from .theme import C


class AnalyzerControl:
    def __init__(self, window):
        self.win = window
        self.state = None           # (язык, "loading"/"ready"/…) — последнее, что сообщил анализатор

    def restart_all(self):
        for svc in self.win.language_services:
            svc.restart(reason=tr("mainwindow.by_menu_command"))

    def fill_restart_menu(self):
        m = self.win.restart_menu
        m.clear()
        current = self.win.options["analyzers/restart_mb"]
        for mb, label in RESTART_THRESHOLDS:
            a = m.addAction(tr(label), lambda v=mb: self.set_restart_mb(v))
            a.setCheckable(True)
            a.setChecked(mb == current)

    def set_restart_mb(self, mb):
        self.win.options.set("analyzers/restart_mb", mb)
        self.win.status(tr("mainwindow.restarting_analyzer") + tr(dict(RESTART_THRESHOLDS)[mb]) +
                    (tr("mainwindow.idle_2_minutes") if mb else ""))

    def on_restarted(self, key, reason):
        name = {"csharp": "C#", "python": "Python"}.get(key, key)
        self.win.status(tr("mainwindow.analyzer_restarted") % (name, reason), 10000)
        self.win.output.write(tr("mainwindow.analyzer_restarted") % (name, reason), "common.build", C["dim"])
        for ed in self.win.editors():
            if ed.language.service == key:
                ed.lsp_diagnostics = None
                ed.run_diagnostics()        # отдаёт текст новому серверу (он запустится сам)

    def on_status(self, text, state, key):
        if not text:
            return
        self.win.status(text)
        lang = text.split(":", 1)[0]
        self.state = lang, state
        self.refresh_label()
        if state == "ready":
            for ed in self.win.editors():
                if ed.language.service == key:
                    ed.run_diagnostics()
            self.win.refresh_outline()      # структура и панель навигации — теперь от анализатора

    def refresh_label(self):
        if self.state is None:
            text = tr("mainwindow.intellisense_ready") if self.win.jedi.available else tr("mainwindow.intellisense_words")
        else:
            lang, state = self.state
            label = {"loading": "mainwindow.status_loading", "ready": "mainwindow.status_ready"}.get(state, "mainwindow.status_stopped")
            text = "%s: %s" % (lang, tr(label))
        self.win.jedi_label.setText(text)

    def service(self, language):
        return self.win.services_by_key[language]

    def before_update(self, language):
        self.service(language).stop()       # файлы анализатора не должны быть заняты

    def after_update(self, language):
        svc = self.service(language)
        svc.stop()
        svc.rediscover()
        for ed in self.win.editors():
            if ed.language.service == language:
                ed.lsp_diagnostics = None
                ed.run_diagnostics()        # заодно запускает анализатор снова

    def show_updates(self):
        if not hasattr(self, "_updates_worker"):
            # один объект на всё время работы окна: фоновый поток не должен слать сигналы удалённому объекту
            self._updates_worker = updates.UpdateWorker(self.win)
            self._updates_dialog = updates.UpdatesDialog(self._updates_worker, self.before_update,
                                                         self.after_update, self.win)
        self._updates_dialog.check()
        self._updates_dialog.show()
        self._updates_dialog.raise_()
