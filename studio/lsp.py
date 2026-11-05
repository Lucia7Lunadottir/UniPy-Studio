"""Языковые серверы (LSP) в отдельном процессе: C# через csharp-ls (Roslyn — движок Visual Studio).

Редактор не ждёт сервер: запросы уходят асинхронно, устаревшие ответы отбрасываются,
а пока решение загружается, подсказки C# дают встроенный справочник Unity (intellisense.py).
Результаты приходят в том же виде, что и от JediWorker: {"kind", "req", "editor", "data"}.
"""
import ast
import glob
import html
import json
import os
import re
import shutil
import signal
import time
from urllib.parse import quote, unquote, urlparse

from PyQt6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, pyqtSignal

from . import unity_api
from .intellisense import Item
from .theme import C
from .i18n import tr

METADATA_DIR = os.path.join(os.path.expanduser("~"), ".cache", "unipy-studio", "metadata")


def is_metadata_path(path):
    """Исходник, восстановленный из сборки (наш кэш или MetadataAsSource сервера Roslyn) — только чтение."""
    return path.startswith(METADATA_DIR) or os.sep + "MetadataAsSource" + os.sep in path

# CompletionItemKind из LSP → наш вид подсказки (значок и подпись)
LSP_KIND = {
    1: "word", 2: "method", 3: "function", 4: "method", 5: "field", 6: "variable", 7: "class",
    8: "type", 9: "module", 10: "property", 12: "const", 13: "enum", 14: "keyword", 15: "snippet",
    17: "path", 19: "path", 20: "const", 21: "const", 22: "struct", 23: "event", 25: "type",
}
KIND_LABEL = {
    "method": "метод", "function": "функция", "field": "поле", "variable": "переменная",
    "class": "класс", "type": "тип", "module": "пространство имён", "property": "свойство",
    "const": "значение", "enum": "перечисление", "keyword": "ключевое слово", "snippet": "шаблон",
    "struct": "структура", "event": "событие", "word": "слово", "path": "путь",
}


def path_to_uri(path):
    return "file://" + quote(os.path.abspath(path))


def uri_to_path(uri):
    p = urlparse(uri)
    return unquote(p.path) if p.scheme == "file" else None


# ====================================================================== JSON-RPC
class JsonRpcConnection(QObject):
    """JSON-RPC поверх stdin/stdout процесса. Чтение — по сигналу Qt, без потоков и без ожиданий."""
    notification = pyqtSignal(str, object)          # метод, params
    server_request = pyqtSignal(int, str, object)   # id, метод, params — сервер спрашивает клиента
    stderr = pyqtSignal(str)
    exited = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.proc = QProcess(self)
        self._process_group = None
        self._isolated = hasattr(QProcess, "UnixProcessParameters") and hasattr(os, "killpg")
        if self._isolated:
            parameters = QProcess.UnixProcessParameters()
            parameters.flags = QProcess.UnixProcessFlag.CreateNewSession
            self.proc.setUnixProcessParameters(parameters)
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.readyReadStandardError.connect(
            lambda: self.stderr.emit(bytes(self.proc.readAllStandardError()).decode("utf-8", "replace")))
        self.proc.finished.connect(lambda code, _st: self.exited.emit(code))
        self._buf = b""
        self._next_id = 1
        self._callbacks = {}

    def start(self, program, args, cwd, extra_env=None):
        env = QProcessEnvironment.systemEnvironment()
        env.insert("DOTNET_CLI_TELEMETRY_OPTOUT", "1")
        for k, v in (extra_env or {}).items():
            env.insert(k, v)
        self.proc.setProcessEnvironment(env)
        self.proc.setWorkingDirectory(cwd)
        self.proc.start(program, args)
        started = self.proc.waitForStarted(5000)
        if started and self._isolated:
            pid = int(self.proc.processId())
            try:
                if os.getpgid(pid) == pid:
                    self._process_group = pid
            except ProcessLookupError:
                pass
        return started

    def running(self):
        return self.proc.state() != QProcess.ProcessState.NotRunning

    def request(self, method, params, callback=None):
        rid = self._next_id
        self._next_id += 1
        if callback is not None:
            self._callbacks[rid] = callback
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        return rid

    def cancel(self, rid):
        if rid in self._callbacks:
            self._callbacks.pop(rid, None)  # Late replies are ignored by _dispatch.
            self.notify("$/cancelRequest", {"id": rid})

    def notify(self, method, params):
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def respond(self, rid, result):
        self._send({"jsonrpc": "2.0", "id": rid, "result": result})

    def _send(self, msg):
        if not self.running():
            return
        body = json.dumps(msg, ensure_ascii=False).encode("utf-8")
        self.proc.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)

    def _read(self):
        self._buf += bytes(self.proc.readAllStandardOutput())
        while True:
            head_end = self._buf.find(b"\r\n\r\n")
            if head_end < 0:
                return
            m = re.search(rb"Content-Length:\s*(\d+)", self._buf[:head_end], re.I)
            if not m:                       # мусор в выводе — пропускаем до следующего заголовка
                self._buf = self._buf[head_end + 4:]
                continue
            start, length = head_end + 4, int(m.group(1))
            if len(self._buf) < start + length:
                return
            body, self._buf = self._buf[start:start + length], self._buf[start + length:]
            try:
                msg = json.loads(body.decode("utf-8"))
            except ValueError:
                continue
            self._dispatch(msg)

    def _dispatch(self, msg):
        if "method" in msg:
            if "id" in msg:
                self.server_request.emit(msg["id"], msg["method"], msg.get("params"))
            else:
                self.notification.emit(msg["method"], msg.get("params"))
            return
        cb = self._callbacks.pop(msg.get("id"), None)
        if cb is not None:
            cb(msg.get("result"), msg.get("error"))

    def stop(self):
        if self.running():
            self.request("shutdown", None, lambda *_: self.notify("exit", None))
            if not self.proc.waitForFinished(1500):
                self.proc.kill()
                self.proc.waitForFinished(500)
        # The dotnet-tool launcher may exit before its language-server/build hosts.
        # Only kill the private session we created, never processes by name.
        if self._process_group is not None:
            try:
                os.killpg(self._process_group, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self._process_group = None
        self._callbacks.clear()
        self._buf = b""


# ====================================================================== сервер
class LanguageServer(QObject):
    """Один сеанс LSP для одной папки (решения). Подклассы задают команду запуска и особенности сервера."""
    result = pyqtSignal(object)                 # в формате JediWorker.finished
    diagnostics = pyqtSignal(str, object)       # путь, [(строка, столбец, текст, "error"/"warning")]
    state_changed = pyqtSignal(str)             # "starting" / "loading" / "ready" / "failed"
    log = pyqtSignal(str)

    language_id = "plaintext"
    show_diagnostic_code = True     # «CS0103: …»; у pyright коды — длинные имена правил, их не показываем
    ready_after_initialize = False  # сервер готов сразу (pyright), а не после загрузки решения (Roslyn)

    def __init__(self, root, parent=None):
        super().__init__(parent)
        self.root = root
        self.state = "starting"
        self.conn = JsonRpcConnection(self)
        self.conn.notification.connect(self._on_notification)
        self.conn.server_request.connect(self._on_server_request)
        self.conn.stderr.connect(lambda t: self.log.emit(t.rstrip()))
        self.conn.exited.connect(self._on_exit)
        self._docs = {}                 # uri → (версия, текст)
        self._pending = {}              # (редактор, вид) → id запроса, чтобы отменять устаревшие
        self._progress = set()
        self.pull_diagnostics_ids = []  # группы ошибок, которые сервер отдаёт по запросу (textDocument/diagnostic)
        self.pull_supported = False
        self._diag_parts = {}           # uri → {группа: [ошибки]}
        self._diag_pending = {}         # (uri, группа) → id запроса
        self._warmed = False
        self.last_activity = time.monotonic()     # последняя правка или запрос — для перезапуска в простое

    # ---- то, что задают подклассы
    def command(self):
        raise NotImplementedError

    def initialization_options(self):
        return None

    def environment(self):
        return {}

    def configuration(self, section):
        """Ответ на workspace/configuration для раздела настроек сервера."""
        return None

    def filter_diagnostics(self, path, items):
        """Сырые диагностики LSP → те, что показать (подклассы убирают шум)."""
        return items

    def is_loaded_message(self, text):
        return False

    def is_loaded_notification(self, method):
        return False

    def open_location(self, uri, line, done):
        """Путь к файлу для перехода; для «нефайловых» адресов подкласс может скачать исходник."""
        done(uri_to_path(uri), line)

    def extra_completions(self, line_text, prefix):
        return []

    def wanted_diagnostic_group(self, identifier):
        """Какие из зарегистрированных сервером групп ошибок запрашивать."""
        return True

    # ---- жизненный цикл
    def start(self):
        program, args = self.command()
        if not self.conn.start(program, args, self.root, self.environment()):
            self._set_state("failed")
            self.log.emit(tr("Не удалось запустить %s") % program)
            return
        caps = {
            "textDocument": {
                "synchronization": {"didSave": False, "dynamicRegistration": False},
                "completion": {"completionItem": {"snippetSupport": False,
                                                  "documentationFormat": ["markdown", "plaintext"]},
                               "contextSupport": True},
                "hover": {"contentFormat": ["markdown", "plaintext"]},
                "signatureHelp": {"signatureInformation": {
                    "documentationFormat": ["markdown", "plaintext"],
                    "parameterInformation": {"labelOffsetSupport": True}}},
                "definition": {"linkSupport": True},
                "implementation": {"linkSupport": True},
                "publishDiagnostics": {"relatedInformation": False},
                "diagnostic": {"dynamicRegistration": True, "relatedDocumentSupport": False},
            },
            "workspace": {"configuration": True, "workspaceFolders": True},
            "window": {"workDoneProgress": True},
        }
        params = {"processId": os.getpid(), "rootUri": path_to_uri(self.root), "capabilities": caps,
                  "workspaceFolders": [{"uri": path_to_uri(self.root), "name": os.path.basename(self.root)}],
                  "initializationOptions": self.initialization_options()}
        self.conn.request("initialize", params, self._initialized)

    def _initialized(self, result, error):
        if error:
            self._set_state("failed")
            self.log.emit(tr("Ошибка initialize: %s") % error.get("message"))
            return
        caps = (result or {}).get("capabilities", {})
        self.pull_supported = bool(caps.get("diagnosticProvider"))
        sync = caps.get("textDocumentSync")
        # 2 = Incremental: сервер принимает только изменённый кусок текста (как шлют VS Code и Visual Studio)
        self.incremental_sync = (sync == 2) or (isinstance(sync, dict) and sync.get("change") == 2)
        self.conn.notify("initialized", {})
        self._set_state("loading")
        for uri, (ver, text) in list(self._docs.items()):
            self._did_open(uri, text, ver)
        if self.ready_after_initialize:
            self._set_state("ready")

    def stop(self):
        self.conn.stop()
        self._docs.clear()
        self._pending.clear()
        self._diag_pending.clear()
        self._diag_parts.clear()
        self._progress.clear()
        # Release signal closures before deferred QObject deletion.
        for obj in (self.conn.proc, self.conn, self):
            try:
                obj.disconnect()
            except TypeError:
                pass

    def _on_exit(self, code):
        if self.state != "failed":
            self.log.emit(tr("Сервер завершился (код %s)") % code)
        self._set_state("failed")

    def _set_state(self, state):
        if state != self.state:
            self.state = state
            if state == "ready":
                self._warm_up()
            self.state_changed.emit(state)

    def _warm_up(self):
        # первый запрос подсказок у Roslyn долгий (JIT): делаем его в фоне, а не на первом нажатии «.»
        if self._warmed or not self._docs:
            return
        self._warmed = True
        uri = next(iter(self._docs))
        self.conn.request("textDocument/completion",
                          {"textDocument": {"uri": uri}, "position": {"line": 0, "character": 0}})

    @property
    def ready(self):
        return self.state == "ready"

    # ---- сообщения от сервера
    def _on_server_request(self, rid, method, params):
        if method == "workspace/configuration":
            self.conn.respond(rid, [self.configuration(it.get("section"))
                                    for it in (params or {}).get("items", [])])
        elif method == "workspace/diagnostic/refresh":
            self.conn.respond(rid, None)
            for uri in list(self._docs):        # сервер просит перезапросить ошибки (например, сменились настройки)
                path = uri_to_path(uri)
                if path:
                    self.pull_diagnostics(path)
            return
        elif method == "client/registerCapability":
            for reg in (params or {}).get("registrations", []):
                if reg.get("method") == "textDocument/diagnostic":
                    opts = reg.get("registerOptions") or {}
                    if opts.get("documentSelector"):
                        continue        # чужие языки (razor и т.п.)
                    ident = opts.get("identifier")
                    if ident is None:
                        self.pull_supported = True
                    elif self.wanted_diagnostic_group(ident) and ident not in self.pull_diagnostics_ids:
                        self.pull_diagnostics_ids.append(ident)
            self.conn.respond(rid, None)
        else:   # window/workDoneProgress/create, client/registerCapability и т.п.
            self.conn.respond(rid, None)

    def _on_notification(self, method, params):
        params = params or {}
        if method == "textDocument/publishDiagnostics":
            path = uri_to_path(params.get("uri", ""))
            if path:
                items = self.filter_diagnostics(path, params.get("diagnostics") or [])
                self.diagnostics.emit(path, self._convert_diagnostics(items))
        elif method in ("window/logMessage", "window/showMessage"):
            text = params.get("message", "")
            self.log.emit(text)
            if self.state == "loading" and self.is_loaded_message(text):
                self._set_state("ready")
        elif self.state == "loading" and self.is_loaded_notification(method):
            self._set_state("ready")
        elif method == "$/progress":
            token, value = params.get("token"), params.get("value") or {}
            if value.get("kind") == "begin":
                self._progress.add(token)
                self.log.emit(value.get("title", ""))
            elif value.get("kind") == "end":
                self._progress.discard(token)
                if self.state == "loading" and not self._progress:
                    self._set_state("ready")

    def _convert_diagnostics(self, items):
        out = []
        for d in items:
            sev = d.get("severity", 1)
            if sev not in (1, 2):          # подсказки и «сообщения» не засоряют список ошибок
                continue
            start = d.get("range", {}).get("start", {})
            code = d.get("code")
            msg = d.get("message", "")
            # многострочные пояснения (pyright) — в одну строку для списка ошибок
            msg = " — ".join(part.strip() for part in msg.splitlines() if part.strip())
            if code and self.show_diagnostic_code:
                msg = "%s: %s" % (code, msg)
            out.append((start.get("line", 0) + 1, start.get("character", 0) + 1, msg,
                        "error" if sev == 1 else "warning"))
        return out

    # ---- документы
    def sync(self, path, text):
        """Сообщить серверу текущий текст файла (целиком; для файлов кода это быстро)."""
        uri = path_to_uri(path)
        if uri not in self._docs:
            self._docs[uri] = (1, text)
            if self.state in ("loading", "ready"):
                self._did_open(uri, text, 1)
            return
        ver, old = self._docs[uri]
        if old == text:
            return
        self._docs[uri] = (ver + 1, text)
        self.last_activity = time.monotonic()
        if self.state in ("loading", "ready"):
            # Только изменённый кусок: иначе на каждую клавишу сервер заново строит текст и разбор
            # всего файла (лишний мусор и работа; так шлют VS Code и Visual Studio).
            change = text_change(old, text) if getattr(self, "incremental_sync", False) else {"text": text}
            self.conn.notify("textDocument/didChange", {"textDocument": {"uri": uri, "version": ver + 1},
                                                        "contentChanges": [change]})

    def _did_open(self, uri, text, ver):
        self.conn.notify("textDocument/didOpen", {"textDocument": {
            "uri": uri, "languageId": self.language_id, "version": ver, "text": text}})
        if self.ready:
            self._warm_up()

    def pull_diagnostics(self, path):
        """Запросить ошибки у сервера (у Roslyn они не присылаются сами, а отдаются по группам)."""
        if self.state not in ("loading", "ready"):
            return
        uri = path_to_uri(path)
        groups = self.pull_diagnostics_ids or ([None] if self.pull_supported else [])
        for ident in groups:
            key = (uri, ident)
            old = self._diag_pending.pop(key, None)
            if old is not None:
                self.conn.cancel(old)
            params = {"textDocument": {"uri": uri}}
            if ident is not None:
                params["identifier"] = ident

            def got(result, error, key=key, ident=ident):
                self._diag_pending.pop(key, None)
                if error or not result or result.get("kind") != "full" or uri not in self._docs:
                    return
                parts = self._diag_parts.setdefault(uri, {})
                parts[ident] = self._convert_diagnostics(self.filter_diagnostics(path, result.get("items") or []))
                merged, seen = [], set()
                for items in parts.values():
                    for d in items:
                        if d not in seen:
                            seen.add(d)
                            merged.append(d)
                merged.sort()
                self.diagnostics.emit(path, merged)
            self._diag_pending[key] = self.conn.request("textDocument/diagnostic", params, got)

    def close(self, path):
        uri = path_to_uri(path)
        self._diag_parts.pop(uri, None)
        if self._docs.pop(uri, None) is not None and self.state in ("loading", "ready"):
            self.conn.notify("textDocument/didClose", {"textDocument": {"uri": uri}})

    # ---- запросы (формат как у JediWorker.submit)
    def submit(self, kind, req, editor, code, line, col, path, extra=None):
        self.last_activity = time.monotonic()
        self.sync(path, code)
        key = (editor, kind)
        old = self._pending.pop(key, None)
        if old is not None:
            self.conn.cancel(old)
        pos = {"textDocument": {"uri": path_to_uri(path)}, "position": {"line": line - 1, "character": col}}
        line_text = code.split("\n")[line - 1][:col] if line - 1 < code.count("\n") + 1 else ""

        def reply(data):
            self._pending.pop(key, None)
            self.result.emit({"kind": kind, "req": req, "editor": editor, "data": data})

        if kind == "complete":
            prefix = re.search(r"\w*$", line_text).group(0)
            self._pending[key] = self.conn.request(
                "textDocument/completion", pos,
                lambda r, e: reply(None if e else self._completions(r, line_text, prefix)))
        elif kind == "signatures":
            self._pending[key] = self.conn.request(
                "textDocument/signatureHelp", pos, lambda r, e: reply(None if e else self._signatures(r)))
        elif kind == "hover":
            self._pending[key] = self.conn.request(
                "textDocument/hover", pos, lambda r, e: reply(None if e else self._hover(r)))
        elif kind == "references":
            params = dict(pos, context={"includeDeclaration": True})
            self._pending[key] = self.conn.request(
                "textDocument/references", params, lambda r, e: reply(None if e else self._locations(r)))
        elif kind == "rename":
            params = dict(pos, newName=(extra or {}).get("new_name", ""))
            self._pending[key] = self.conn.request(
                "textDocument/rename", params,
                lambda r, e: reply({"error": e.get("message", "")} if e else self._workspace_edit(r)))
        elif kind in ("definition", "implementation"):
            def located(r, e):
                loc = self._first_location(r) if not e else None
                if not loc:
                    reply(None)
                    return
                self.open_location(loc[0], loc[1], lambda p, ln: reply({"path": p, "line": ln} if p else None))
            self._pending[key] = self.conn.request("textDocument/" + kind, pos, located)

    def _completions(self, result, line_text, prefix):
        raw = result.get("items", []) if isinstance(result, dict) else (result or [])
        low = prefix.lower()
        scored = []
        for it in raw:
            name = it.get("filterText") or it.get("label", "")
            label = it.get("label", name)
            n = name.lower()
            if low and low not in n:
                continue
            rank = 0 if n.startswith(low) else 1
            scored.append((rank, it.get("sortText") or label.lower(), it))
        scored.sort(key=lambda t: (t[0], t[1]))
        items = []
        for _rank, _s, it in scored[:300]:
            kind = LSP_KIND.get(it.get("kind"), "word")
            # откуда подсказка: пространство имён (UnityEngine.UI) — как справа в списке Visual Studio
            source = (it.get("labelDetails") or {}).get("description")
            detail = source or it.get("detail") or tr(KIND_LABEL.get(kind, ""))
            doc = it.get("documentation") or ""
            if isinstance(doc, dict):
                doc = doc.get("value", "")
            items.append(Item(it.get("label", ""), kind, detail, html.escape(doc[:600]), data=it))
        return self.extra_completions(line_text, prefix) + items

    def resolve_completion(self, raw, done):
        """Подробности выбранной подсказки: документация с полным именем и правки,
        которые нужны при выборе (например, «using UnityEngine.UI;»). done(элемент)."""
        if self.state not in ("loading", "ready"):
            done(raw)
            return
        self.conn.request("completionItem/resolve", raw, lambda r, e: done(raw if e or not r else r))

    @staticmethod
    def _signatures(result):
        if not result or not result.get("signatures"):
            return []
        active_param = result.get("activeParameter") or 0
        out = []
        for s in result["signatures"][:6]:
            label = s.get("label", "")
            params = []
            for p in s.get("parameters") or []:
                lab = p.get("label", "")
                params.append(label[lab[0]:lab[1]] if isinstance(lab, list) else lab)
            head = label.split("(", 1)[0].strip()
            name = re.search(r"[\w.<>,]*$", head).group(0) or head    # «void AddForce» → «AddForce»
            idx = s.get("activeParameter", active_param)
            out.append((name, params, idx if idx is not None else 0))
        active = result.get("activeSignature") or 0
        if 0 < active < len(out):
            out.insert(0, out.pop(active))
        return out

    @staticmethod
    def _hover(result):
        if not result:
            return None
        contents = result.get("contents")
        if isinstance(contents, dict):
            text = contents.get("value", "")
        elif isinstance(contents, list):
            text = "\n\n".join(c.get("value", "") if isinstance(c, dict) else c for c in contents)
        else:
            text = contents or ""
        return markdown_to_html(text) or None

    @staticmethod
    def _locations(result):
        out = []
        for loc in result or []:
            path = uri_to_path(loc.get("uri", ""))
            if path and not is_metadata_path(path):
                start = loc.get("range", {}).get("start", {})
                out.append({"path": path, "line": start.get("line", 0) + 1, "col": start.get("character", 0) + 1})
        return out

    @staticmethod
    def _workspace_edit(result):
        """WorkspaceEdit → {"edits": {путь: [(стр, стлб, стр2, стлб2, текст), …]}}; строки/столбцы с 0, столбцы в UTF-16."""
        edits = {}
        changes = dict((result or {}).get("changes") or {})
        for dc in (result or {}).get("documentChanges") or []:
            if "textDocument" in dc:
                changes.setdefault(dc["textDocument"]["uri"], []).extend(dc.get("edits") or [])
        for uri, items in changes.items():
            path = uri_to_path(uri)
            if not path:
                continue
            for e in items:
                r = e["range"]
                edits.setdefault(path, []).append((r["start"]["line"], r["start"]["character"],
                                                   r["end"]["line"], r["end"]["character"], e.get("newText", "")))
        return {"edits": edits}

    @staticmethod
    def _first_location(result):
        if not result:
            return None
        loc = result[0] if isinstance(result, list) else result
        if not loc:
            return None
        uri = loc.get("targetUri") or loc.get("uri")
        rng = loc.get("targetSelectionRange") or loc.get("range") or {}
        return uri, rng.get("start", {}).get("line", 0) + 1


def split_lines(text):
    """Строки с «\\n» на конце. Только «\\n», как в LSP и Qt (splitlines() режет ещё по \\x0c, \\u2028…)."""
    parts = text.split("\n")
    return [p + "\n" for p in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


def _lsp_position(text, index):
    """Индекс в строке Python → позиция LSP (строка с 0, столбец в UTF-16)."""
    line_start = text.rfind("\n", 0, index) + 1
    return {"line": text.count("\n", 0, index), "character": _utf16_len(text[line_start:index])}


def _utf16_len(s):
    return len(s) if s.isascii() else len(s.encode("utf-16-le")) // 2


def text_change(old, new):
    """Одна правка LSP «было → стало»: общее начало и общий конец не отправляются."""
    n = min(len(old), len(new))
    start = 0
    while start < n and old[start] == new[start]:
        start += 1
    end_old, end_new = len(old), len(new)
    while end_old > start and end_new > start and old[end_old - 1] == new[end_new - 1]:
        end_old -= 1
        end_new -= 1
    return {"range": {"start": _lsp_position(old, start), "end": _lsp_position(old, end_old)},
            "text": new[start:end_new]}


def text_offset(text, line, utf16_col):
    """Позиция LSP (строка с 0, столбец в UTF-16) → индекс в строке Python."""
    lines = split_lines(text)
    if line >= len(lines):
        return len(text)
    start = sum(len(l) for l in lines[:line])
    body = lines[line].rstrip("\r\n")
    i = u = 0
    while i < len(body) and u < utf16_col:
        u += 2 if ord(body[i]) > 0xFFFF else 1
        i += 1
    return start + i


def apply_text_edits(text, edits):
    """Применить правки (стр, стлб, стр2, стлб2, текст) к тексту — с конца, чтобы позиции не сдвигались."""
    spans = [(text_offset(text, a, b), text_offset(text, c, d), t) for a, b, c, d, t in edits]
    for start, end, new in sorted(spans, reverse=True):
        text = text[:start] + new + text[end:]
    return text


def line_diff_edits(old, new):
    """Правки по строкам из «было → стало» (для jedi, который отдаёт файл целиком)."""
    import difflib
    a, b = split_lines(old), split_lines(new)
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op != "equal":
            out.append((i1, 0, i2, 0, "".join(b[j1:j2])))
    return out


def markdown_to_html(text):
    """Справка от Roslyn приходит в markdown: блоки кода — моноширинно, остальное — абзацами."""
    out, code, buf = [], False, []
    for line in text.replace("\r", "").split("\n"):
        if line.startswith("```"):
            if code and buf:
                out.append("<code style='color:%s'>%s</code>" % (C["function"], "<br>".join(html.escape(b) for b in buf)))
            code, buf = not code, []
            continue
        if code:
            buf.append(line)
        elif line.strip() and line.strip() != "---":
            s = html.escape(line.strip())
            s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
            s = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
            s = re.sub(r"\\([\\`*_{}\[\]()#+\-.!<>])", r"\1", s)
            out.append("<span style='color:%s'>%s</span>" % (C["tip_doc"], s))
    if code and buf:
        out.append("<code style='color:%s'>%s</code>" % (C["function"], "<br>".join(html.escape(b) for b in buf)))
    return "<br>".join(out[:14])


# ====================================================================== C#
def _find_dotnet_tool(name):
    exe = shutil.which(name)
    if exe:
        return exe
    p = os.path.join(os.path.expanduser("~"), ".dotnet", "tools", name)
    return p if os.path.isfile(p) else None


def find_csharp_server():
    """(класс сервера, путь). Первым — Roslyn от Microsoft (dotnet/roslyn), csharp-ls — запасной."""
    for cls in (RoslynServer, CsharpLsServer):
        exe = _find_dotnet_tool(cls.tool_name)
        if exe:
            return cls, exe
    return None, None


def find_solution_dir(path):
    """Ближайшая вверх папка с .sln/.slnx — так же решение ищут Visual Studio и Rider."""
    d = os.path.dirname(os.path.abspath(path))
    while True:
        if glob.glob(os.path.join(glob.escape(d), "*.sln")) or glob.glob(os.path.join(glob.escape(d), "*.slnx")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def pick_solution(folder):
    slns = sorted(glob.glob(os.path.join(glob.escape(folder), "*.sln")) +
                  glob.glob(os.path.join(glob.escape(folder), "*.slnx")))
    if not slns:
        return None
    named = [s for s in slns if os.path.splitext(os.path.basename(s))[0] == os.path.basename(folder)]
    return (named or slns)[0]


class CSharpServer(LanguageServer):
    """Общее для серверов C#: читают .sln/.csproj, которые создаёт Unity (пакеты ide.rider / ide.visualstudio)."""
    language_id = "csharp"
    tool_name = None

    def __init__(self, root, exe, parent=None):
        super().__init__(root, parent)
        self.exe = exe
        self.solution = pick_solution(root)

    def open_location(self, uri, line, done):
        if not uri.startswith("csharp:"):
            done(uri_to_path(uri), line)
            return

        def got(result, error):
            if error or not result or not result.get("source"):
                done(None, line)
                return
            asm = re.sub(r"[^\w.\-]", "_", result.get("assemblyName") or "metadata")
            sym = re.sub(r"[^\w.\-]", "_", result.get("symbolName") or "Symbol")
            folder = os.path.join(METADATA_DIR, asm)
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, sym + ".cs")
            header = tr("// Только для чтения: восстановлено из сборки %s\n") % (result.get("assemblyName") or "")
            with open(path, "w", encoding="utf-8") as f:
                f.write(header + result["source"])
            done(path, line + 1)
        # декомпилированный исходник типа из UnityEngine.dll и т.п.
        self.conn.request("csharp/metadata", {"textDocument": {"uri": uri}}, got)

    def extra_completions(self, line_text, prefix):
        # сообщения Unity (Update, OnTriggerEnter…) Roslyn не предлагает — это не переопределения
        if not prefix or "." in line_text[-len(prefix) - 1:] or re.search(r"\w\s*\.\s*\w*$", line_text):
            return []
        low = prefix.lower()
        return [Item(name, "event", sig, tr(doc)) for name, sig, doc in unity_api.MESSAGES
                if name.lower().startswith(low)]


class RoslynServer(CSharpServer):
    """Языковой сервер Roslyn от Microsoft (пакет roslyn-language-server) — движок IntelliSense Visual Studio."""
    tool_name = "roslyn-language-server"

    def command(self):
        # единственный .sln в корне сервер находит и загружает сам
        return self.exe, ["--stdio", "--autoLoadProjects", "--telemetryLevel", "off", "--logLevel", "Warning"]

    def environment(self):
        # Only our analyzer inherits these; do not change the user's SDK globally.
        return {
            "MSBUILDDISABLENODEREUSE": "1",
            "DOTNET_CLI_USE_MSBUILD_SERVER": "0",
            "DOTNET_gcServer": os.environ.get("DOTNET_gcServer", "0"),
            # Замер на The Mirror (9 проектов): без этих двух — 10 процессов при загрузке
            # (MSBuild по процессу на ядро, ~1,2 ГБ в пике) и память, растущая за 600 МБ;
            # с ними — 3 процесса, рост останавливается на ~560 МБ, подсказки так же быстрые.
            "DOTNET_PROCESSOR_COUNT": os.environ.get("DOTNET_PROCESSOR_COUNT", "2"),
            "DOTNET_GCConserveMemory": os.environ.get("DOTNET_GCConserveMemory", "7"),
        }

    def configuration(self, section):
        # Roslyn requests individual option names in workspace/configuration.
        # Match VS Code's open-file diagnostic scope, keeping solution navigation.
        options = {
            "csharp|background_analysis.dotnet_analyzer_diagnostics_scope": "openFiles",
            "csharp|background_analysis.dotnet_compiler_diagnostics_scope": "openFiles",
            # подсказки из ещё не подключённых пространств имён, «using» добавляется при выборе
            "csharp|completion.dotnet_show_completion_items_from_unimported_namespaces": True,
        }
        if section == "projects.dotnet_enable_automatic_restore" and self.is_unity_project:
            # У Unity в .csproj нет пакетов NuGet (библиотеки — готовые DLL): восстановление
            # пакетов бесполезно и запускает по процессу MSBuild на ядро (6–8 процессов по ~90 МБ).
            return False
        return options.get(section)

    @property
    def is_unity_project(self):
        return os.path.isfile(os.path.join(self.root, "ProjectSettings", "ProjectVersion.txt"))

    def is_loaded_notification(self, method):
        return method == "workspace/projectInitializationComplete"

    def wanted_diagnostic_group(self, identifier):
        # ошибки и предупреждения самого файла; HotReload/XAML/ENC/по всему решению — не для редактора
        return identifier in ("syntax", "DocumentCompilerSemantic", "DocumentAnalyzerSyntax",
                              "DocumentAnalyzerSemantic")


class CsharpLsServer(CSharpServer):
    """csharp-ls (razzmatazz/csharp-language-server) — тот же Roslyn, сторонняя обвязка."""
    tool_name = "csharp-ls"

    def command(self):
        return self.exe, (["--solution", self.solution] if self.solution else [])

    def is_loaded_message(self, text):
        t = text.lower()
        return "finished loading" in t or "solution loaded" in t or "loaded solution" in t


class LanguageService(QObject):
    """Набор серверов одного языка по корням проектов; редактор спрашивает его так же, как JediWorker."""
    finished = pyqtSignal(object)
    diagnostics = pyqtSignal(str, object)
    status = pyqtSignal(str, str)     # текст, состояние: loading / ready / failed
    log = pyqtSignal(str)
    restarted = pyqtSignal(str)     # анализатор перезапущен (причина) — открытые файлы надо отдать новому

    language_name = ""
    CHECK_EVERY_MS = 30_000
    IDLE_BEFORE_RESTART = 120       # секунд без правок и запросов в этом проекте

    def __init__(self, parent=None):
        super().__init__(parent)
        self.servers = {}           # корень проекта → сервер
        self._metadata_owner = {}   # исходник класса из сборки → сервер, который его выдал
        self.restart_mb = 0         # 0 — не перезапускать сам
        self._memory_timer = QTimer(self, interval=self.CHECK_EVERY_MS, timeout=self._check_memory)
        self._memory_timer.start()

    # ---- память: анализатор, набравший лишнего, перезапускается, когда вы не печатаете
    def memory_mb(self, srv):
        return process_tree_rss_mb(int(srv.conn.proc.processId())) if srv.conn.running() else 0

    def _check_memory(self):
        if not self.restart_mb:
            return
        for root, srv in list(self.servers.items()):
            if not srv.ready or time.monotonic() - srv.last_activity < self.IDLE_BEFORE_RESTART:
                continue
            mb = self.memory_mb(srv)
            if mb > self.restart_mb:
                self.restart(root, tr("занимал %d МБ при пороге %d МБ") % (mb, self.restart_mb))

    def restart(self, root=None, reason=None):
        """Остановить сервер(ы); новый запустится сам при следующей синхронизации открытого файла."""
        if reason is None:
            reason = tr("по запросу")
        roots = [root] if root else list(self.servers)
        for r in roots:
            srv = self.servers.pop(r, None)
            if srv is None:
                continue
            for path, owner in list(self._metadata_owner.items()):
                if owner is srv:
                    del self._metadata_owner[path]
            srv.stop()
            srv.deleteLater()
        if roots:
            self.restarted.emit(reason)

    # ---- то, что задают подклассы
    @property
    def available(self):
        raise NotImplementedError

    def find_root(self, path):
        raise NotImplementedError

    def make_server(self, root):
        raise NotImplementedError

    def handles(self, path):
        return True

    def rediscover(self):
        """Заново найти программу анализатора (её только что установили или обновили)."""

    def display_name(self, srv):
        return os.path.basename(srv.root)

    # ---- общее
    def server_for(self, path, start=True):
        """Сервер для файла; None — файла нет в проекте или анализатора нет."""
        if not self.available or not path or not self.handles(path):
            return None
        if is_metadata_path(path):
            # класс из UnityEngine.dll и т.п.: переходы дальше (к Behaviour, Object…) даёт тот же сервер
            return self._metadata_owner.get(path)
        root = self.find_root(path)
        if not root:
            return None
        srv = self.servers.get(root)
        if srv is None and start:
            srv = self.make_server(root)
            srv.result.connect(lambda res, s=srv: self._on_result(s, res))
            srv.diagnostics.connect(self.diagnostics)
            srv.log.connect(lambda t, s=srv: t and self.log.emit("[%s] %s" % (os.path.basename(s.root), t)))
            srv.state_changed.connect(lambda st, s=srv: self._state(s, st))
            self.servers[root] = srv
            srv.start()
        return srv

    def _on_result(self, srv, res):
        data = res.get("data")
        if res.get("kind") in ("definition", "implementation") and isinstance(data, dict) and \
                data.get("path") and is_metadata_path(data["path"]):
            self._metadata_owner[data["path"]] = srv
        self.finished.emit(res)

    def _state(self, srv, state):
        name = self.display_name(srv)
        text = {"loading": tr("%s: загрузка %s…") % (self.language_name, name),
                "ready": tr("%s: %s загружено") % (self.language_name, name),
                "failed": tr("%s: анализатор для %s остановлен") % (self.language_name, name)}.get(state, "")
        self.status.emit(text, state)

    def ready_for(self, path):
        srv = self.server_for(path)
        return srv if srv is not None and srv.ready else None

    def sync(self, path, text):
        srv = self.server_for(path)
        if srv is not None and srv.state in ("starting", "loading", "ready"):
            srv.sync(path, text)
            srv.pull_diagnostics(path)

    def close(self, path):
        srv = self.server_for(path, start=False)
        if srv is not None:
            srv.close(path)

    def stop(self):
        for srv in self.servers.values():
            srv.stop()
            srv.deleteLater()
        self.servers.clear()
        self._metadata_owner.clear()

    def configuration_changed(self):
        """Настройки (например, выбранный интерпретатор) изменились — серверы перечитают их сами."""
        for srv in self.servers.values():
            if srv.state in ("loading", "ready"):
                srv.conn.notify("workspace/didChangeConfiguration", {"settings": None})


class CSharpService(LanguageService):
    """C#: сервер на каждое решение (.sln)."""
    language_name = "C#"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.server_class, self.exe = find_csharp_server()

    def rediscover(self):
        self.server_class, self.exe = find_csharp_server()

    @property
    def available(self):
        return self.exe is not None

    def find_root(self, path):
        return find_solution_dir(path)

    def make_server(self, root):
        return self.server_class(root, self.exe, self)

    def display_name(self, srv):
        return os.path.basename(srv.solution or srv.root)


# ====================================================================== Python
PROJECT_MARKERS = ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "pyrightconfig.json", ".git")


def find_python_root(path):
    """Корень Python-проекта: ближайшая вверх папка с pyproject.toml, requirements.txt, .git и т.п."""
    d = os.path.dirname(os.path.abspath(path))
    start = d
    while True:
        if any(os.path.exists(os.path.join(d, m)) for m in PROJECT_MARKERS):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return start
        d = parent


class ProjectAttributeIndex:
    """Имена атрибутов, которым где-то в проекте присваивают значение («obj.name = …», setattr(obj, "name", …)).

    Python разрешает задавать атрибуты снаружи (w._color_panel = ColorPanel() в другом модуле),
    pyright этого не видит и считает self._color_panel ошибкой. Если такое присваивание в проекте
    есть — атрибут существует. У опечатки (self.taks) присваивания нигде нет, она остаётся ошибкой."""
    SKIP_DIRS = {".git", "venv", ".venv", "env", "node_modules", "__pycache__", "build", "dist", ".tox"}
    ASSIGN = re.compile(r"\.\s*([A-Za-z_]\w*)\s*(?::[^=\n]+)?=(?!=)|"
                        r"setattr\(\s*[^,()]+,\s*[\"']([A-Za-z_]\w*)[\"']")
    MAX_FILES = 5000
    RESCAN_EVERY = 5.0      # секунд: чаще не проверяем даты изменения файлов

    def __init__(self, root):
        self.root = root
        self._files = {}    # путь → (mtime, множество имён)
        self._checked = 0.0

    @classmethod
    def names_in(cls, text):
        return {a or b for a, b in cls.ASSIGN.findall(text)}

    def _refresh(self):
        now = time.monotonic()
        if now - self._checked < self.RESCAN_EVERY:
            return
        self._checked = now
        seen = set()
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in self.SKIP_DIRS and not d.startswith(".")]
            for name in filenames:
                if not name.endswith((".py", ".pyw")):
                    continue
                path = os.path.join(dirpath, name)
                seen.add(path)
                if len(seen) > self.MAX_FILES:
                    break
                try:
                    mtime = os.path.getmtime(path)
                    if self._files.get(path, (None,))[0] != mtime:
                        with open(path, encoding="utf-8", errors="replace") as f:
                            self._files[path] = (mtime, self.names_in(f.read()))
                except OSError:
                    pass
        for path in set(self._files) - seen:
            del self._files[path]

    def contains(self, name, open_texts=()):
        self._refresh()
        if any(name in names for _m, names in self._files.values()):
            return True
        return any(name in self.names_in(t) for t in open_texts)     # несохранённые правки


def creates_globals_dynamically(text):
    """Модуль сам создаёт свои имена (setattr(sys.modules[__name__], …), globals()[…] = …)."""
    return "sys.modules[__name__]" in text or "globals()" in text


class PyrightServer(LanguageServer):
    """pyright от Microsoft (ядро Pylance — проверки Python в Visual Studio).

    Строгость подобрана под код на PyQt6: красным только то, что почти наверняка ошибка
    (неизвестное имя, нет такого атрибута у своего класса); неверный вызов — жёлтым. «Может быть None»
    и придирки к типам аргументов выключены — у PyQt6 это в основном шум."""
    language_id = "python"
    show_diagnostic_code = False
    ready_after_initialize = True

    SEVERITY = {
        "reportUndefinedVariable": "error",
        "reportAttributeAccessIssue": "error",
        "reportCallIssue": "warning",       # у PyQt6 (QLabel("…", styleSheet=…), *shape[:2]) часто ложное
        "reportMissingImports": "warning",
        "reportMissingModuleSource": "none",
        "reportPossiblyUnbound": "warning",
        "reportOptionalMemberAccess": "none",
        "reportOptionalSubscript": "none",
        "reportOptionalCall": "none",
        "reportOptionalIterable": "none",
        "reportOptionalOperand": "none",
        "reportArgumentType": "none",
        "reportIncompatibleMethodOverride": "none",
        "reportIncompatibleVariableOverride": "none",
        "reportGeneralTypeIssues": "none",
        "reportIndexIssue": "none",
        "reportOperatorIssue": "none",
        "reportReturnType": "none",
        "reportAssignmentType": "none",
        "reportRedeclaration": "none",
        "reportTypedDictNotRequiredAccess": "none",
    }

    def __init__(self, root, exe, python_path, parent=None):
        super().__init__(root, parent)
        self.exe = exe
        self.python_path = python_path      # функция: интерпретатор, выбранный на панели
        self._spans = {}                    # uri → (текст, [(строка1, строка2, имя класса)])
        self.assigned = ProjectAttributeIndex(root)

    def command(self):
        return self.exe, ["--stdio"]

    def environment(self):
        return {"VSCODE_NLS_CONFIG": json.dumps({"locale": "ru"})}     # сообщения pyright по-русски

    def configuration(self, section):
        analysis = {"typeCheckingMode": "off", "diagnosticMode": "openFilesOnly",
                    "autoSearchPaths": True, "useLibraryCodeForTypes": True,
                    "diagnosticSeverityOverrides": self.SEVERITY}
        if section == "python":         # pyright спрашивает раздел целиком: настройки проверок — в "analysis"
            return {"pythonPath": self.python_path() or "", "analysis": analysis}
        if section == "python.analysis":
            return analysis
        if section == "pyright":
            return {}
        return None

    def _class_spans(self, uri):
        text = self._docs.get(uri, (0, ""))[1]
        cached = self._spans.get(uri)
        if cached and cached[0] == text:
            return cached[1]
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            return cached[1] if cached else []      # пока код недописан — прежняя разметка классов
        spans = [(n.lineno, n.end_lineno, n.name) for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
        self._spans[uri] = (text, spans)
        return spans

    def filter_diagnostics(self, path, items):
        uri = path_to_uri(path)
        text = self._docs.get(uri, (0, ""))[1]
        lines = text.split("\n")
        out = []
        for d in items:
            if d.get("code") == "reportUndefinedVariable" and creates_globals_dynamically(text):
                d = dict(d, severity=2)         # имена модуля создаются на ходу — pyright их не видит
            elif d.get("code") == "reportAttributeAccessIssue":
                rng = d.get("range", {})
                start, end = rng.get("start", {}), rng.get("end", {})
                ln, ch = start.get("line", 0), start.get("character", 0)
                line_text = lines[ln] if ln < len(lines) else ""
                before = line_text[:ch]
                attr = line_text[ch:end.get("character", ch)] if end.get("line", ln) == ln else ""
                if re.search(r"\bself\s*\.\s*$", before):
                    inner = [sp for sp in self._class_spans(uri) if sp[0] <= ln + 1 <= sp[1]]
                    cls = min(inner, key=lambda sp: sp[1] - sp[0]) if inner else None
                    if cls and "Mixin" in cls[2]:
                        continue    # примесь: её атрибуты объявлены в классе, куда её подмешивают
                    open_texts = [t for _v, t in self._docs.values()]
                    if attr and self.assigned.contains(attr, open_texts):
                        continue    # атрибут задают снаружи (w._panel = … в другом модуле) — он существует
                elif before.rstrip().endswith("."):
                    # «что-то.атрибут» не у self: pyright часто путается в типах — только предупреждение.
                    # Опечатка в импорте («from … import QPushButon») остаётся ошибкой.
                    d = dict(d, severity=2)
            out.append(d)
        return out


class PythonService(LanguageService):
    """Python: pyright на каждый проект (папку с pyproject.toml / requirements.txt / .git)."""
    language_name = "Python"

    def __init__(self, python_path, parent=None):
        super().__init__(parent)
        self.exe = find_pyright()
        self.python_path = python_path

    def rediscover(self):
        self.exe = find_pyright()

    @property
    def available(self):
        return self.exe is not None

    def handles(self, path):
        return path.endswith((".py", ".pyw", ".pyi"))

    def find_root(self, path):
        return find_python_root(path)

    def make_server(self, root):
        return PyrightServer(root, self.exe, self.python_path, self)


def process_tree_rss_mb(pid):
    """Память процесса и всех его потомков (МБ) — Linux /proc."""
    total, todo, seen = 0, [pid], set()
    while todo:
        p = todo.pop()
        if p in seen:
            continue
        seen.add(p)
        try:
            with open("/proc/%d/status" % p) as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        total += int(line.split()[1])
                        break
            for task in os.listdir("/proc/%d/task" % p):
                with open("/proc/%d/task/%s/children" % (p, task)) as f:
                    todo.extend(int(x) for x in f.read().split())
        except (OSError, ValueError):
            pass
    return total // 1024


def find_pyright():
    exe = shutil.which("pyright-langserver")
    if exe:
        return exe
    p = os.path.join(os.path.expanduser("~"), ".local", "bin", "pyright-langserver")
    return p if os.path.isfile(p) else None
