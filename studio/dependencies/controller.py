"""Зависимости в окне: поставщик — от набора инструментов (Toolchain.dependency_provider), проверка — в фоне,
установка — команда во встроенный терминал (человек видит её и жмёт Enter; sudo спросит пароль там же).
Из окна берёт: project_context(), root, terminal, show_dock, d_terminal, d_dependencies, status."""
import threading

from PyQt6.QtCore import QObject, pyqtSignal

from .. import toolchains
from ..i18n import tr
from .base import MISMATCH, MISSING
from .panel import DependenciesPanel


class DependenciesController(QObject):
    _checked = pyqtSignal(object, object, bool, int)        # поставщик, требования, системное, поколение

    def __init__(self, window):
        super().__init__(window)
        self.win = window
        self.panel = DependenciesPanel()
        self.panel.refresh.connect(self.check)
        self.panel.install.connect(self.install_missing)
        self.panel.create_venv.connect(self.create_venv)
        self.panel.open_location.connect(lambda p, l: window.jump_to(p, l))
        self._checked.connect(self._show)
        self.provider = None
        self.requirements = []
        self._generation = 0

    def _provider(self):
        ctx = self.win.project_context()
        for tc in toolchains.active(ctx):
            p = tc.dependency_provider(ctx)
            if p is not None:
                return p
        return None

    def check(self):
        self._generation += 1
        generation = self._generation
        provider = self._provider()
        self.provider = provider
        if provider is None:
            self.panel.show_requirements(None, False, [], False)
            return
        requirements = provider.requirements()
        self.panel.show_requirements(provider.interpreter, False, requirements, checking=True, root=self.win.root)

        def work():
            system = provider.environment()[1]
            self._checked.emit(provider, provider.check(requirements), system, generation)
        threading.Thread(target=work, daemon=True).start()

    def _show(self, provider, requirements, system, generation):
        if generation != self._generation:
            return
        self.requirements = requirements
        self.panel.show_requirements(provider.interpreter, system, requirements, checking=False, root=self.win.root,
                                     can_venv=provider.create_environment_command() is not None)

    def install_missing(self):
        if self.provider is None:
            return
        missing = [r for r in self.requirements if r.state in (MISSING, MISMATCH)]
        commands = self.provider.install_commands(missing)
        if not commands:
            self.win.status(tr("dependencies.need_venv"), 15000)
            return
        self.win.show_dock(self.win.d_terminal)
        self.win.terminal.type_text(" && ".join(c for c, _kind in commands))
        self.win.status(tr("mainwindow.check_command_terminal_press_enter"), 15000)

    def create_venv(self):
        command = self.provider.create_environment_command() if self.provider else None
        if command:
            self.win.show_dock(self.win.d_terminal)
            self.win.terminal.type_text(command)
            self.win.status(tr("dependencies.venv_hint"), 20000)
