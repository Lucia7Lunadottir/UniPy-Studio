"""Отладка в окне: F5 / F10 / F11 / Shift+F11 / Shift+F5, точки останова (F9), стрелка текущей строки,
панели «Стек вызовов», «Локальные», «Точки останова», значения при наведении.

Как отлаживать цель, знает набор инструментов (toolchains: debug_config). Если набор не умеет или отладчик
не установлен — F5 запускает программу как обычно и подсказывает, что поставить.
Из окна берёт: project_context(), startup, editor(), editors(), open_path, output, show_dock, status, runner.
"""
import os

from PyQt6.QtCore import QObject, pyqtSignal

from .. import toolchains
from ..i18n import tr
from ..theme import C
from .breakpoints import BreakpointAnchors, BreakpointStore
from .panels import BreakpointsPanel, CallStackPanel, VariablesPanel
from .session import DebugSession, ENDED, RUNNING, STOPPED

SOURCE = "debug.output"


class DebugController(QObject):
    state_changed = pyqtSignal(str)     # idle / starting / running / stopped / ended

    def __init__(self, window):
        super().__init__(window)
        self.win = window
        self.store = BreakpointStore(window.settings, self)
        self.session = None
        self.frame = None               # выбранный кадр стека
        self.call_stack = CallStackPanel()
        self.call_stack.frame_chosen.connect(self.select_frame)
        self.variables = VariablesPanel()
        self.breakpoints_panel = BreakpointsPanel(self.store)
        self.breakpoints_panel.goto.connect(lambda path, line: window.jump_to(path, line))
        self._last = None               # (набор, цель) последнего запуска — для «Перезапустить»

    # --- редакторы
    def attach_editor(self, ed):
        ed.breakpoints = self.store
        ed.breakpoint_anchors = BreakpointAnchors(ed, self.store)
        ed.debug_hover = self.hover
        if self.frame is not None and self.frame.path == ed.path:
            ed.set_execution_line(self.frame.line, self.frame is self._top_frame())

    def toggle_breakpoint(self):
        ed = self.win.editor()
        if ed is not None:
            ed.toggle_breakpoint()

    def load_solution(self, root):
        self.store.load(root)

    # --- что отлаживать
    def active(self):
        return self.session is not None and self.session.state not in (ENDED,)

    def start_or_continue(self):
        """F5: остановлены — продолжить; идёт — ничего; иначе начать отладку выбранного в «Запуске»."""
        if self.active():
            if self.session.state == STOPPED:
                self.session.continue_()
            return
        found = self._target()
        if found is None:
            self.win.status(tr("mainwindow.open_file_python_select_boot"))
            return
        tc, target, ctx = found
        ok, hint = tc.debug_available(ctx)
        config = tc.debug_config(ctx, target) if ok else None
        if config is None:
            if hint:
                self.win.status(tr("debug.not_available") % (tc.name, hint), 15000)
            tc.launch(ctx, target, self.win.runner)          # без отладки — как Ctrl+F5
            return
        self._start(tc, target, config)

    def _target(self):
        """(набор, цель, контекст) — то же правило, что у запуска (toolchains.resolve_target)."""
        ctx = self.win.project_context()
        ed = self.win.editor()
        found = toolchains.resolve_target(ctx, self.win.startup.currentData(), ed.path if ed else None)
        return (found[0], found[1], ctx) if found else None

    def start_config(self, config):
        """Отладить готовую конфигурацию (тест из «Обозревателя тестов» и т. п.), а не цель из «Запуска»."""
        if self.active():
            self.win.status(tr("debug.already_running"))
            return
        self._start(None, None, config)

    def _start(self, tc, target, config):
        win = self.win
        if win.options["documents/save_before_run"]:
            win.save_all()
        self._last = (tc, target, config)
        self.store.reset_verified()
        in_terminal = self.win.terminal.available and self.win.options["run/in_terminal"]
        self.session = DebugSession(config, self.store, run_in_terminal=self._run_in_terminal if in_terminal else None,
                                    parent=self)
        self.session.state_changed.connect(self._state)
        self.session.stopped.connect(self._stopped)
        self.session.output.connect(self._output)
        self.session.breakpoints_bound.connect(lambda path, _b: self._repaint(path))
        self.session.ended.connect(self._ended)
        win.output.clear()
        win.output.write(tr("debug.started") % (config.title or (target.label if target else "")), SOURCE, C["link"])
        win.show_dock(win.d_output)
        self.session.start()

    def _run_in_terminal(self, argv, cwd, env, title):
        """runInTerminal от отладчика: программа — во вкладке встроенного терминала, как при обычном запуске."""
        win = self.win
        win.show_dock(win.d_terminal)
        session = win.terminal.run(argv, cwd, env, title or "debug")
        return session.process.pid

    def restart(self):
        if self._last is None:
            return
        tc, target, config = self._last
        if self.active():
            self.session.ended.connect(lambda *_: self._relaunch(tc, target, config))
            self.session.terminate()
        else:
            self._relaunch(tc, target, config)

    def _relaunch(self, tc, target, config):
        """Ctrl+Shift+F5: цель из «Запуска» — конфигурация заново (файлы могли измениться); готовая — та же."""
        if tc is not None:
            config = tc.debug_config(self.win.project_context(), target)
        if config is not None:
            self._start(tc, target, config)

    # --- кнопки
    def stop(self):
        if self.active():
            self.session.terminate()

    def step_over(self):
        if self.active():
            self.session.step_over()

    def step_into(self):
        if self.active():
            self.session.step_into()

    def step_out(self):
        if self.active():
            self.session.step_out()

    def pause(self):
        if self.active():
            self.session.pause()

    # --- события сеанса
    def _state(self, state):
        if state == RUNNING:
            self._clear_execution()
            self.call_stack.clear()
            self.variables.show_frame(None, None)
            self.win.status(tr("debug.running"), 0)
        self.state_changed.emit(state)

    def _top_frame(self):
        return self.session.frames[0] if self.session and self.session.frames else None

    def _stopped(self, _thread, reason, frames):
        top = next((f for f in frames if f.path and os.path.isfile(f.path)), frames[0] if frames else None)
        self.call_stack.show_frames(frames, top)
        self.win.status(tr("debug.stopped") % (tr(REASONS[reason]) if reason in REASONS else reason), 0)
        self.win.show_debug_docks()
        self.select_frame(top)
        self.win.raise_()
        self.win.activateWindow()

    def select_frame(self, frame):
        if frame is None:
            return
        self.frame = frame
        self._clear_execution()
        if frame.path and os.path.isfile(frame.path):
            ed = self.win.open_path(frame.path, frame.line)
            if ed is not None:
                ed.set_execution_line(frame.line, frame is self._top_frame())
        self.variables.show_frame(self.session, frame)

    def _clear_execution(self):
        for ed in self.win.editors():
            if ed.execution_line is not None:
                ed.set_execution_line(None)

    def _output(self, category, text):
        if category == "adapter" and not text.strip():
            return
        color = {"stderr": C["error"], "console": C["dim"], "important": C["warning"],
                 "adapter": C["dim"]}.get(category, C["text"])
        self.win.output.write(text.rstrip("\n"), SOURCE, color)

    def _ended(self, code):
        self._clear_execution()
        self.frame = None
        self.call_stack.clear()
        self.variables.show_frame(None, None)
        line = tr("debug.ended") % ("" if code is None else code)
        self.win.output.write(line, SOURCE, C["dim"])
        self.win.status(line)
        self._repaint("")

    def _repaint(self, path):
        for ed in self.win.editors():
            if not path or ed.path == path:
                ed.gutter.update()
        self.breakpoints_panel.refresh()

    # --- значения при наведении
    def hover(self, expression, show):
        """DataTip: только когда программа остановлена. True — запрос ушёл, обычную подсказку не показывать."""
        if not self.active() or self.session.state != STOPPED or self.frame is None:
            return False

        def got(variable, _message):
            if variable is not None:
                show(variable.value + ("    (%s)" % variable.type if variable.type else ""))
        self.session.evaluate(expression, self.frame.id, "hover", got)
        return True

    def shutdown(self):
        if self.active():
            self.session.terminate()


# причина остановки из DAP → текст; остальные показываются как есть
REASONS = {"breakpoint": "debug.reason_breakpoint", "step": "debug.reason_step", "exception": "debug.reason_exception",
           "pause": "debug.reason_pause", "entry": "debug.reason_entry"}
