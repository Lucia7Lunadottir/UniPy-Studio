"""Тесты в окне: поиск, запуск, отладка, результаты в «Обозревателе тестов» и упавшие — в «Списке ошибок».

Поставщика даёт набор инструментов (Toolchain.test_provider); процесс поставщика сообщает события
(testing.base.MARK) — строки без маркера считаются выводом текущего теста.
Из окна берёт: project_context(), root, show_dock, d_tests, errors, status, debugger, save_all, options.
"""
import os

from PyQt6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer

from .. import toolchains
from ..i18n import tr
from .base import ERROR, FAILED, NOT_RUN, RUNNING, TestNode
from .panel import TestExplorer

SOURCE = "testing.errors"


class TestController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.win = window
        self.panel = TestExplorer()
        self.panel.run_all.connect(lambda: self.run([]))
        self.panel.run_failed.connect(self.run_failed)
        self.panel.run_ids.connect(self.run)
        self.panel.debug_ids.connect(self.debug)
        self.panel.refresh.connect(self.discover)
        self.panel.open_location.connect(lambda p, l: window.jump_to(p, l))
        self.nodes = {}
        self.proc = None
        self._buffer = b""
        self._current = None             # тест, чей вывод сейчас идёт
        self._mode = None                # "discover" / "run"
        self._run_ids = None
        self._after = None
        self._found = {}
        self._framework = ""

    def provider(self):
        ctx = self.win.project_context()
        for tc in toolchains.active(ctx):
            p = tc.test_provider(ctx)
            if p is not None:
                return p
        return None

    def running(self):
        return self.proc is not None and self.proc.state() != QProcess.ProcessState.NotRunning

    # --- поиск
    def discover(self, then=None):
        provider = self.provider()
        if provider is None:
            self.nodes = {}
            self.panel.set_nodes({})
            self.panel.set_message(tr("testing.no_tests"))
            return
        self._after = then
        self._start(provider, provider.discover_command(), "discover")
        self.panel.set_message(tr("testing.discovering"))

    # --- запуск
    def run(self, ids):
        """ids пуст — все тесты. Если тесты ещё не найдены — сначала поиск."""
        if self.running():
            return
        if not self.nodes:
            self.discover(then=lambda: self.run(ids))
            return
        provider = self.provider()
        if provider is None:
            return
        if self.win.options["documents/save_before_run"]:
            self.win.save_all()
        targets = self._expand(ids)
        run_ids = [n.id for n in targets] if ids else []
        for node in targets:
            node.status, node.message, node.details, node.output = NOT_RUN, "", "", ""
        self.win.errors.clear_source(SOURCE)
        self.panel.update_statuses()
        self._run_ids = {n.id for n in targets}
        self._start(provider, provider.run_command(self._top_ids(ids)), "run")

    def at_cursor(self):
        """Тест (или класс тестов) под курсором — ближайший сверху в этом файле."""
        ed = self.win.editor()
        if ed is None:
            return None
        line = ed.textCursor().blockNumber() + 1
        candidates = [n for n in self.nodes.values() if n.kind == "test" and n.path == ed.path and (n.line or 0) <= line]
        return max(candidates, key=lambda n: n.line or 0) if candidates else None

    def run_at_cursor(self, debug=False):
        if not self.nodes:
            self.discover(then=lambda: self.run_at_cursor(debug))
            return
        node = self.at_cursor()
        if node is None:
            self.win.status(tr("testing.no_test_here"))
            return
        (self.debug if debug else self.run)([node.id])

    def file_saved(self, path):
        """Сохранили файл с тестами — список тестов мог измениться."""
        name = os.path.basename(path)
        if self.nodes and name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py")) \
                and not self.running():
            QTimer.singleShot(300, self.discover)

    def run_failed(self):
        failed = [n.id for n in self.nodes.values() if n.kind == "test" and n.status in (FAILED, ERROR)]
        if failed:
            self.run(failed)
        else:
            self.win.status(tr("testing.nothing_failed"))

    def debug(self, ids):
        provider = self.provider()
        config = provider.debug_config(self._top_ids(ids)) if provider else None
        if config is None:
            self.win.status(tr("testing.debug_unavailable"), 12000)
            return
        self.win.debugger.start_config(config)

    def _expand(self, ids):
        if not ids:
            return [n for n in self.nodes.values() if n.kind == "test"]
        out = []
        for i in ids:
            node = self.nodes.get(i)
            if node is not None:
                out.extend(t for t in node.tests() if t not in out)
        return out

    def _top_ids(self, ids):
        """Самые общие из выбранных: файл, а не каждый его тест (так короче команда и быстрее запуск)."""
        chosen = [i for i in ids if i in self.nodes]
        return [i for i in chosen if not any(self._is_inside(i, j) for j in chosen if j != i)]

    def _is_inside(self, node_id, other_id):
        node = self.nodes.get(node_id)
        while node is not None and node.parent:
            if node.parent == other_id:
                return True
            node = self.nodes.get(node.parent)
        return False

    # --- процесс
    def _start(self, provider, command, mode):
        self._provider, self._mode = provider, mode
        self._buffer = b""
        self._current = None
        self._found = {}
        if self.proc is not None:
            self.proc.deleteLater()
        self.proc = QProcess(self)
        self.proc.setWorkingDirectory(command.cwd)
        env = QProcessEnvironment.systemEnvironment()
        for k, v in command.env.items():
            env.insert(k, v)
        self.proc.setProcessEnvironment(env)
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.finished.connect(self._finished)
        self.proc.errorOccurred.connect(lambda e: e == QProcess.ProcessError.FailedToStart and
                                        self.panel.set_message(self.proc.errorString()))
        self.panel.set_running(True)
        self.win.show_dock(self.win.d_tests)
        self.proc.start(command.program, command.args)

    def _read(self):
        self._buffer += bytes(self.proc.readAllStandardOutput())
        *lines, self._buffer = self._buffer.split(b"\n")
        changed = []
        for raw in lines:
            line = raw.decode("utf-8", "replace").rstrip("\r")
            event = self._provider.parse(line)
            if event is None:
                if self._current is not None and line:
                    self._current.output = (self._current.output + line + "\n")[-20000:]
                continue
            node = self._event(event)
            if node is not None:
                changed.append(node)
        if changed and self._mode == "run":
            parents = {self.nodes.get(n.parent) for n in changed} | set(changed)
            self.panel.update_statuses(only=[p for p in parents if p is not None] + self._ancestors(changed))

    def _ancestors(self, nodes):
        out = []
        for n in nodes:
            p = self.nodes.get(n.parent)
            while p is not None:
                out.append(p)
                p = self.nodes.get(p.parent)
        return out

    def _event(self, e):
        kind = e.get("event")
        if kind == "item":
            node = TestNode(e["id"], e.get("name") or e["id"], e.get("kind", "test"), e.get("parent"),
                            e.get("path"), e.get("line"))
            self._found[node.id] = node
            if self._mode == "run" and node.id not in self.nodes:     # ошибка сбора во время прогона
                self.nodes[node.id] = node
                self.panel.set_nodes(self.nodes)
            return None
        if kind == "framework":
            self._framework = e.get("name", "")
            return None
        if kind == "error":
            self.win.output.write(e.get("message", ""), SOURCE)
            return None
        node = self.nodes.get(e.get("id"))
        if node is None:
            return None
        if kind == "start":
            node.status = RUNNING
            self._current = node
        elif kind == "result":
            node.status = e.get("outcome", ERROR)
            node.duration = float(e.get("duration") or 0)
            node.message, node.details = e.get("message") or "", e.get("details") or ""
            if e.get("output"):                        # pytest перехватывает вывод сам и отдаёт его в результате
                node.output = e["output"]
            node.fail_path, node.fail_line = e.get("path"), e.get("line")
            if node.fail_path and not os.path.isabs(node.fail_path):
                node.fail_path = os.path.join(self.win.root or "", node.fail_path)
            if node.status in (FAILED, ERROR):
                self._report_failure(node)
            self._current = None
        return node

    def _report_failure(self, node):
        path = node.fail_path if node.fail_path and os.path.isfile(node.fail_path) else node.path
        if path and os.path.isfile(path):
            line = node.fail_line if path == node.fail_path else (node.line or 1)
            text = "%s: %s" % (node.name, (node.message or "").splitlines()[0] if node.message else node.status)
            items = [d for d in self.win.errors.groups.get((SOURCE, path), [])] + [(line or 1, 1, text, "error", "tests")]
            self.win.errors.set_group(SOURCE, path, items)

    def _finished(self, code, _status):
        self.panel.set_running(False)
        if self._mode == "discover":
            self._build_tree()
            after, self._after = self._after, None
            if after is not None:
                QTimer.singleShot(0, after)
            return
        for node in self.nodes.values():               # не дошли (процесс упал) — снова «не запускался»
            if node.kind == "test" and node.status == RUNNING:
                node.status = ERROR
            if node.kind == "test" and node.id in (self._run_ids or ()) and node.status == NOT_RUN and code not in (0, 1):
                node.message = tr("testing.process_failed") % code
        self.panel.update_statuses()
        failed = sum(1 for n in self.nodes.values() if n.kind == "test" and n.status in (FAILED, ERROR))
        self.win.status(tr("testing.done_failed") % failed if failed else tr("testing.done_ok"), 12000)
        if failed:
            self.win.show_dock(self.win.d_errors)
            self.win.show_dock(self.win.d_tests)

    def _build_tree(self):
        old = self.nodes
        nodes = self._found
        for node in nodes.values():
            node.children = []
            prev = old.get(node.id)
            if prev is not None and node.kind == "test":     # результаты прошлого прогона не теряются
                node.status, node.duration, node.message, node.details = prev.status, prev.duration, prev.message, prev.details
                node.fail_path, node.fail_line, node.output = prev.fail_path, prev.fail_line, prev.output
        for node in nodes.values():
            parent = nodes.get(node.parent)
            if parent is not None:
                parent.children.append(node)
        self.nodes = nodes
        self.panel.set_nodes(nodes)
        if not any(n.kind == "test" for n in nodes.values()):
            self.panel.set_message(tr("testing.none_found"))

    def shutdown(self):
        if self.running():
            self.proc.kill()
            self.proc.waitForFinished(1000)
