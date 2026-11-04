"""Обновления анализаторов кода (Roslyn для C#, pyright для Python).

Только по кнопке: показываем установленную и последнюю версию; после обновления новая версия
проверяется (запуск + ответ на initialize), а если проверка не прошла — автоматически
возвращается прежняя версия и тоже проверяется. Источники — официальные пакеты Microsoft
на NuGet и npm.
"""
import json
import os
import re
import selectors
import subprocess
import tempfile
import threading
import time
import urllib.request

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
                             QPushButton, QPlainTextEdit, QHeaderView, QAbstractItemView)

from . import lsp
from .theme import C

NET_TIMEOUT = 20


def run(cmd, timeout):
    """Команда целиком: (код возврата, вывод). Ошибка запуска — код 127."""
    env = dict(os.environ, DOTNET_CLI_TELEMETRY_OPTOUT="1", NO_UPDATE_NOTIFIER="1")
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        return p.returncode, (p.stdout + p.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, str(e)


def lsp_self_test(cmd, timeout=90):
    """Запустить языковой сервер и дождаться ответа на initialize — так же, как его запускает редактор."""
    workdir = tempfile.mkdtemp(prefix="unipy-selftest-")
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                cwd=workdir, env=dict(os.environ, DOTNET_CLI_TELEMETRY_OPTOUT="1"))
    except OSError as e:
        return False, "не запускается: %s" % e

    def send(msg):
        body = json.dumps(msg).encode("utf-8")
        proc.stdin.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
        proc.stdin.flush()

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"processId": os.getpid(), "rootUri": lsp.path_to_uri(workdir), "capabilities": {}}})
        sel = selectors.DefaultSelector()
        sel.register(proc.stdout, selectors.EVENT_READ)
        buf, deadline = b"", time.monotonic() + timeout
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return False, "завершился с кодом %s сразу после запуска" % proc.returncode
            if not sel.select(timeout=0.5):
                continue
            chunk = os.read(proc.stdout.fileno(), 65536)
            if not chunk:
                continue
            buf += chunk
            while True:
                head = buf.find(b"\r\n\r\n")
                m = re.search(rb"Content-Length:\s*(\d+)", buf[:head]) if head >= 0 else None
                if not m or len(buf) < head + 4 + int(m.group(1)):
                    break
                body, buf = buf[head + 4:head + 4 + int(m.group(1))], buf[head + 4 + int(m.group(1)):]
                msg = json.loads(body.decode("utf-8"))
                if msg.get("id") == 1 and "method" not in msg:
                    if "error" in msg:
                        return False, "ошибка initialize: %s" % msg["error"].get("message")
                    return True, "отвечает"
        return False, "не ответил за %d с" % timeout
    except (OSError, ValueError) as e:
        return False, "сбой связи: %s" % e
    finally:
        try:
            send({"jsonrpc": "2.0", "id": 2, "method": "shutdown"})
            send({"jsonrpc": "2.0", "method": "exit"})
            proc.wait(3)
        except Exception:
            proc.kill()
        try:
            os.rmdir(workdir)
        except OSError:
            pass


# ====================================================================== анализаторы
class AnalyzerTool:
    """Один анализатор: как узнать версии, как поставить нужную, как запустить для проверки."""
    title = ""
    language = ""       # "csharp" / "python" — какой сервис останавливать на время обновления
    source = ""

    def installed_version(self):
        raise NotImplementedError

    def latest_version(self):
        raise NotImplementedError

    def install_command(self, version):
        raise NotImplementedError

    def server_command(self):
        raise NotImplementedError

    def self_test(self):
        cmd = self.server_command()
        if not cmd:
            return False, "не найден после установки"
        return lsp_self_test(cmd)


class RoslynTool(AnalyzerTool):
    title = "Roslyn — C# (Unity)"
    language = "csharp"
    source = "Microsoft, NuGet: roslyn-language-server"
    package = "roslyn-language-server"

    def installed_version(self):
        code, out = run(["dotnet", "tool", "list", "-g"], 60)
        for line in out.splitlines():
            parts = line.split()
            if code == 0 and len(parts) >= 2 and parts[0].lower() == self.package:
                return parts[1]
        return None

    def latest_version(self):
        url = "https://api.nuget.org/v3-flatcontainer/%s/index.json" % self.package
        with urllib.request.urlopen(url, timeout=NET_TIMEOUT) as r:
            return json.load(r)["versions"][-1]        # NuGet отдаёт версии по возрастанию

    def install_command(self, version):
        if self.installed_version():
            return ["dotnet", "tool", "update", "-g", self.package, "--version", version, "--allow-downgrade"]
        return ["dotnet", "tool", "install", "-g", self.package, "--version", version]

    def server_command(self):
        exe = lsp._find_dotnet_tool(self.package)
        return [exe, "--stdio", "--telemetryLevel", "off"] if exe else None


class PyrightTool(AnalyzerTool):
    title = "pyright — Python и PyQt"
    language = "python"
    source = "Microsoft, npm: pyright"
    prefix = os.path.join(os.path.expanduser("~"), ".local")

    def installed_version(self):
        pkg = os.path.join(self.prefix, "lib", "node_modules", "pyright", "package.json")
        try:
            with open(pkg, encoding="utf-8") as f:
                return json.load(f).get("version")
        except (OSError, ValueError):
            return None

    def latest_version(self):
        with urllib.request.urlopen("https://registry.npmjs.org/pyright/latest", timeout=NET_TIMEOUT) as r:
            return json.load(r)["version"]

    def install_command(self, version):
        return ["npm", "install", "-g", "--prefix", self.prefix, "pyright@" + version]

    def server_command(self):
        exe = lsp.find_pyright()
        return [exe, "--stdio"] if exe else None


TOOLS = (RoslynTool(), PyrightTool())


class UpdateWorker(QObject):
    """Проверка и обновление в фоне; окно получает только сигналы. Живёт столько же, сколько главное окно."""
    versions = pyqtSignal(int, object, object, str)     # № анализатора, установлено, последняя, ошибка
    log = pyqtSignal(str)
    finished = pyqtSignal(int, bool, str)               # № анализатора, успех, итог

    def __init__(self, parent=None):
        super().__init__(parent)
        self.busy = False

    def check(self):
        threading.Thread(target=self._check, daemon=True).start()

    def _check(self):
        for i, tool in enumerate(TOOLS):
            installed = tool.installed_version()
            try:
                latest, err = tool.latest_version(), ""
            except Exception as e:      # нет сети и т.п. — установленное продолжает работать
                latest, err = None, str(e)
            self.versions.emit(i, installed, latest, err)

    def update(self, index, version):
        self.busy = True
        threading.Thread(target=self._update, args=(index, version), daemon=True).start()

    def _update(self, index, version):
        tool = TOOLS[index]
        try:
            old = tool.installed_version()
            self.log.emit("%s: %s → %s" % (tool.title, old or "не установлен", version))
            code, out = run(tool.install_command(version), 900)
            self.log.emit(out[-1500:] if out else "(без вывода)")
            if code != 0:
                ok, msg = tool.self_test()
                self.finished.emit(index, False, "Установка не удалась (код %d). Работает прежняя версия %s: %s." % (
                    code, old or "—", msg))
                return
            self.log.emit("Проверяю новую версию…")
            ok, msg = tool.self_test()
            if ok:
                self.finished.emit(index, True, "Версия %s установлена и проверена: %s." % (version, msg))
                return
            self.log.emit("Новая версия не прошла проверку: %s" % msg)
            if not old:
                self.finished.emit(index, False, "Установленная версия не работает: %s." % msg)
                return
            self.log.emit("Возвращаю прежнюю версию %s…" % old)
            code, out = run(tool.install_command(old), 900)
            self.log.emit(out[-1500:] if out else "(без вывода)")
            ok2, msg2 = tool.self_test()
            self.finished.emit(index, False, "Новая версия не работала (%s). Возвращена %s: %s." % (
                msg, old, msg2 if ok2 else "ВНИМАНИЕ, и она не отвечает — " + msg2))
        finally:
            self.busy = False


class UpdatesDialog(QDialog):
    """«Справка → Обновления анализаторов». Закрыть нельзя, пока идёт обновление."""

    def __init__(self, worker, before_update, after_update, parent=None):
        super().__init__(parent)
        self.worker = worker
        self.before_update = before_update      # функция(язык): остановить анализатор на время обновления
        self.after_update = after_update        # функция(язык): запустить снова
        self.latest = {}
        self.setWindowTitle("Обновления анализаторов")
        self.resize(720, 420)
        lay = QVBoxLayout(self)
        note = QLabel("Ничего не обновляется само. После обновления новая версия проверяется; "
                      "если она не работает, автоматически возвращается прежняя.")
        note.setWordWrap(True)
        lay.addWidget(note)
        self.table = QTableWidget(len(TOOLS), 4)
        self.table.setHorizontalHeaderLabels(["Анализатор", "Установлено", "Последняя", ""])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in (1, 2, 3):
            h.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.buttons = []
        for i, tool in enumerate(TOOLS):
            it = QTableWidgetItem(tool.title)
            it.setToolTip(tool.source)
            self.table.setItem(i, 0, it)
            for col in (1, 2):
                self.table.setItem(i, col, QTableWidgetItem("…"))
            b = QPushButton("Обновить")
            b.setEnabled(False)
            b.clicked.connect(lambda _c=False, n=i: self._update(n))
            self.table.setCellWidget(i, 3, b)
            self.buttons.append(b)
        self.table.verticalHeader().setDefaultSectionSize(30)
        lay.addWidget(self.table)
        self.result = QLabel("")
        self.result.setWordWrap(True)
        lay.addWidget(self.result)
        self.log_view = QPlainTextEdit(readOnly=True)
        self.log_view.setObjectName("plainField")
        lay.addWidget(self.log_view, 1)
        row = QHBoxLayout()
        self.status = QLabel("")
        row.addWidget(self.status, 1)
        self.recheck = QPushButton("Проверить снова")
        self.recheck.clicked.connect(self.check)
        row.addWidget(self.recheck)
        self.close_btn = QPushButton("Закрыть")
        self.close_btn.clicked.connect(self.reject)
        row.addWidget(self.close_btn)
        lay.addLayout(row)
        worker.versions.connect(self._on_versions)
        worker.log.connect(self.log_view.appendPlainText)
        worker.finished.connect(self._on_finished)

    def check(self):
        if self.worker.busy:
            return
        for i in range(len(TOOLS)):
            self.table.item(i, 1).setText("…")
            self.table.item(i, 2).setText("…")
            self.buttons[i].setEnabled(False)
        self.status.setText("Проверяю версии…")
        self.worker.check()

    def _on_versions(self, i, installed, latest, err):
        self.latest[i] = latest
        self.table.item(i, 1).setText(installed or "не установлен")
        self.table.item(i, 2).setText(latest or "нет связи")
        if err:
            self.table.item(i, 2).setToolTip(err)
        b = self.buttons[i]
        if latest and installed == latest:
            b.setText("Актуально")
            b.setEnabled(False)
        else:
            b.setText("Установить" if not installed else "Обновить")
            b.setEnabled(bool(latest) and not self.worker.busy)
        self.status.setText("")

    def _update(self, i):
        version = self.latest.get(i)
        if not version or self.worker.busy:
            return
        for b in self.buttons + [self.recheck, self.close_btn]:
            b.setEnabled(False)
        self.status.setText("Обновление… окно закроется только после проверки")
        self.before_update(TOOLS[i].language)
        self.worker.update(i, version)

    def _on_finished(self, i, ok, message):
        self.after_update(TOOLS[i].language)
        self.log_view.appendPlainText(("✔ " if ok else "✖ ") + message)
        self.result.setText(("✔ " if ok else "✖ ") + message)
        self.result.setStyleSheet("color:%s" % (C["text"] if ok else C["warning"]))
        self.recheck.setEnabled(True)
        self.close_btn.setEnabled(True)
        self.check()

    def reject(self):
        if not self.worker.busy:        # во время обновления не закрываемся
            super().reject()

    def closeEvent(self, e):
        if self.worker.busy:
            e.ignore()
        else:
            super().closeEvent(e)
