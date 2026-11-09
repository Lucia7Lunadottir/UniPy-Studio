"""Сборка решения (Ctrl+Shift+B), как в Visual Studio: в фоне, с ошибками в «Списке ошибок».

- C# — `dotnet build` решения (.sln) или проекта (.csproj). Для Unity-проекта всё, что пишет MSBuild
  (bin/obj), уходит в ~/.cache/unipy-studio/build/<проект>: в папке проекта ничего не появляется,
  Unity и git этого не видят. Обычные .NET-проекты собираются как обычно (bin/ и obj/ рядом).
- Python — проверка синтаксиса всех .py в фоновом потоке (окно не замирает на больших проектах).

Задачи сборки (`BuildJob`) не знают об окне: они только сообщают строки вывода, найденные ошибки
и итог. `BuildController` раскладывает это по панелям.
"""
import glob
import hashlib
import os
import re
import shutil
import threading
import time
from xml.etree import ElementTree

from PyQt6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, pyqtSignal

from .build_check import check_text
from .i18n import tr
from .theme import C

CACHE_ROOT = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "unipy-studio", "build")

# path(line,col[,line2,col2]): error CS0103: message [project.csproj]
_CANONICAL = re.compile(
    r"^\s*(?P<file>.+?)\((?P<line>\d+)(?:,(?P<col>\d+))?(?:,\d+,\d+)?\)\s*:\s*"
    r"(?P<sev>error|warning)\s*(?P<code>[A-Za-z]+\d+)?\s*:\s*(?P<msg>.*?)(?:\s+\[(?P<proj>[^\[\]]+)\])?\s*$")
# MSBUILD : error MSB1009: … / CSC : error CS2001: …  (без файла и строки)
_ORIGIN = re.compile(r"^\s*(?P<file>[^:()]+?)\s*:\s*(?P<sev>error|warning)\s*(?P<code>[A-Za-z]+\d+)?\s*:\s*"
                     r"(?P<msg>.*?)(?:\s+\[(?P<proj>[^\[\]]+)\])?\s*$")


class BuildDiagnostic:
    __slots__ = ("path", "line", "col", "severity", "code", "message", "project")

    def __init__(self, path, line, col, severity, code, message, project=""):
        self.path, self.line, self.col = path, line, col
        self.severity, self.code, self.message, self.project = severity, code, message, project

    def key(self):
        return self.path, self.line, self.col, self.code, self.message

    def text(self):
        return "%s: %s" % (self.code, self.message) if self.code else self.message

    def __repr__(self):
        return "BuildDiagnostic(%r, %d, %r, %r)" % (os.path.basename(self.path or ""), self.line, self.code, self.severity)


def parse_msbuild_line(line, base_dir=""):
    """Строка вывода MSBuild → BuildDiagnostic или None. Относительные пути — от base_dir."""
    m = _CANONICAL.match(line)
    if m:
        path = m.group("file").strip()
        if base_dir and not os.path.isabs(path):
            path = os.path.normpath(os.path.join(base_dir, path.replace("\\", os.sep)))
        proj = m.group("proj") or ""
        return BuildDiagnostic(path, int(m.group("line")), int(m.group("col") or 1), m.group("sev"),
                               m.group("code") or "", m.group("msg"), os.path.splitext(os.path.basename(proj))[0])
    m = _ORIGIN.match(line)
    if m and " " not in m.group("file").strip():        # «MSBUILD», «CSC», путь к .csproj
        origin = m.group("file").strip()
        proj = m.group("proj") or (origin if origin.endswith("proj") else "")
        path = proj if proj and os.path.isabs(proj) else ""
        return BuildDiagnostic(path, 1, 1, m.group("sev"), m.group("code") or "", m.group("msg"),
                               os.path.splitext(os.path.basename(proj))[0])
    return None


# ---------------------------------------------------------------- что собирать
def is_unity_folder(folder):
    return os.path.isfile(os.path.join(folder, "ProjectSettings", "ProjectVersion.txt"))


def dotnet_target(root):
    """Что отдать dotnet build для корня решения: .sln/.slnx, иначе единственный .csproj, иначе None.
    Если в корне ничего нет — Unity-проект на уровень-два ниже (корень — папка с несколькими проектами)."""
    from .lsp import pick_solution
    if not root:
        return None
    sln = pick_solution(root)
    if sln:
        return sln
    projects = glob.glob(os.path.join(glob.escape(root), "*.csproj"))
    if len(projects) == 1:
        return projects[0]
    for sub in sorted(glob.glob(os.path.join(glob.escape(root), "*", "ProjectSettings"))):
        sln = pick_solution(os.path.dirname(sub))
        if sln:
            return sln
    return None


def project_for_file(path, folder):
    """.csproj в folder, в который входит файл: Unity перечисляет файлы явно (<Compile Include>),
    SDK-проекты берут всё из своей папки."""
    path = os.path.abspath(path)
    best = None
    for proj in sorted(glob.glob(os.path.join(glob.escape(folder), "*.csproj"))):
        proj_dir = os.path.dirname(proj)
        try:
            tree = ElementTree.parse(proj)
        except (ElementTree.ParseError, OSError):
            continue
        includes = [el.get("Include") for el in tree.iter() if el.tag.endswith("Compile") and el.get("Include")]
        if includes:
            for inc in includes:
                if os.path.normpath(os.path.join(proj_dir, inc.replace("\\", os.sep))) == path:
                    return proj
        elif path.startswith(os.path.join(proj_dir, "")) and best is None:
            best = proj
    return best


def cache_dir_for(target):
    folder = os.path.dirname(os.path.abspath(target))
    digest = hashlib.sha1(folder.encode("utf-8", "surrogateescape")).hexdigest()[:12]
    return os.path.join(CACHE_ROOT, "%s-%s" % (re.sub(r"[^\w.-]", "_", os.path.basename(folder))[:40], digest))


def write_redirect_targets(cache):
    """Файл, который MSBuild подключает перед Microsoft.Common.targets (CustomBeforeMicrosoftCommonTargets):
    bin/obj каждого проекта — в кэш, а не в папку Unity-проекта."""
    os.makedirs(cache, exist_ok=True)
    path = os.path.join(cache, "unipy-redirect.targets")
    text = ("<Project>\n  <PropertyGroup>\n"
            "    <OutputPath>{c}/bin/$(MSBuildProjectName)/</OutputPath>\n"
            "    <BaseIntermediateOutputPath>{c}/obj/$(MSBuildProjectName)/</BaseIntermediateOutputPath>\n"
            "    <IntermediateOutputPath>{c}/obj/$(MSBuildProjectName)/$(Configuration)/</IntermediateOutputPath>\n"
            "  </PropertyGroup>\n</Project>\n").format(c=cache.replace("&", "&amp;").replace("<", "&lt;"))
    try:
        with open(path, encoding="utf-8") as f:
            if f.read() == text:
                return path
    except OSError:
        pass
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


# ---------------------------------------------------------------- задачи сборки
class BuildJob(QObject):
    """Одна сборка. output(текст, роль цвета), diagnostic(BuildDiagnostic), finished(итог: dict)."""
    output = pyqtSignal(str, str)
    diagnostic = pyqtSignal(object)
    finished = pyqtSignal(object)

    title = ""

    def start(self):
        raise NotImplementedError

    def cancel(self):
        raise NotImplementedError


class DotnetBuild(BuildJob):
    VERBS = ("build", "rebuild", "clean")

    def __init__(self, target, verb="build", use_cache=False, shared_compiler=False, parent=None):
        super().__init__(parent)
        assert verb in self.VERBS
        self.target = target
        self.verb = verb
        self.use_cache = use_cache
        self.shared_compiler = shared_compiler
        self.title = os.path.basename(target)
        self.proc = None
        self._buffer = b""
        self._seen = set()
        self._counts = {"error": 0, "warning": 0}
        self._started = 0.0
        self._cancelled = False

    def arguments(self):
        args = ["clean" if self.verb == "clean" else "build", self.target, "-nologo", "-v:q",
                "-clp:NoSummary;ForceNoAlign", "-p:GenerateFullPaths=true", "-nr:false"]
        if self.verb == "rebuild":
            args.append("-t:Rebuild")
        if not self.shared_compiler:
            args.append("-p:UseSharedCompilation=false")    # без VBCSCompiler, который живёт в памяти ещё 10 минут
        if self.use_cache:
            args.append("-p:CustomBeforeMicrosoftCommonTargets=" + write_redirect_targets(cache_dir_for(self.target)))
        return args

    def start(self):
        exe = shutil.which("dotnet")
        if exe is None:
            self.output.emit(tr("build.no_dotnet"), "error")
            self.finished.emit({"ok": False, "errors": 1, "warnings": 0, "seconds": 0, "missing_tool": True})
            return
        self.proc = QProcess(self)
        self.proc.setWorkingDirectory(os.path.dirname(os.path.abspath(self.target)))
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        env = QProcessEnvironment.systemEnvironment()
        for key, value in (("DOTNET_NOLOGO", "1"), ("DOTNET_CLI_TELEMETRY_OPTOUT", "1"),
                           ("MSBUILDDISABLENODEREUSE", "1"), ("DOTNET_SKIP_FIRST_TIME_EXPERIENCE", "1")):
            env.insert(key, value)
        self.proc.setProcessEnvironment(env)
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.finished.connect(self._done)
        self.proc.errorOccurred.connect(self._failed)
        self._started = time.monotonic()
        self.output.emit("dotnet " + " ".join('"%s"' % a if " " in a else a for a in self.arguments()), "dim")
        self.proc.start(exe, self.arguments())

    def _read(self):
        self._buffer += bytes(self.proc.readAllStandardOutput())
        *lines, self._buffer = self._buffer.split(b"\n")
        for raw in lines:
            self._line(raw.decode("utf-8", "replace").rstrip("\r"))

    def _line(self, line):
        if not line.strip():
            return
        diag = parse_msbuild_line(line, os.path.dirname(os.path.abspath(self.target)))
        if diag is None:
            self.output.emit(line, "text")
            return
        if diag.key() in self._seen:            # MSBuild повторяет одно и то же для разных целей
            return
        self._seen.add(diag.key())
        self._counts[diag.severity] += 1
        where = os.path.relpath(diag.path, os.path.dirname(self.target)) if diag.path else ""
        self.output.emit("%s(%d,%d): %s %s" % (where, diag.line, diag.col, diag.severity, diag.text()) if where
                         else "%s %s" % (diag.severity, diag.text()),
                         "error" if diag.severity == "error" else "warning")
        self.diagnostic.emit(diag)

    def _failed(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.output.emit(tr("build.could_not_start") % self.proc.errorString(), "error")
            self.finished.emit({"ok": False, "errors": 1, "warnings": 0, "seconds": 0})

    def _done(self, code, _status):
        if self._buffer:
            self._line(self._buffer.decode("utf-8", "replace"))
            self._buffer = b""
        self.finished.emit({"ok": code == 0 and not self._cancelled, "errors": self._counts["error"],
                            "warnings": self._counts["warning"], "seconds": time.monotonic() - self._started,
                            "cancelled": self._cancelled, "code": code})

    def cancel(self):
        if self.proc is not None and self.proc.state() != QProcess.ProcessState.NotRunning:
            self._cancelled = True
            self.proc.terminate()
            QTimer.singleShot(3000, lambda: self.proc is not None and
                              self.proc.state() != QProcess.ProcessState.NotRunning and self.proc.kill())


class PythonCheck(BuildJob):
    """Проверка синтаксиса .py в фоновом потоке. Тексты открытых вкладок берутся из редакторов (снимок)."""
    _done_signal = pyqtSignal(object)
    _diag_signal = pyqtSignal(object)

    def __init__(self, files, buffers, parent=None):
        super().__init__(parent)
        self.files = [p for p in files if p.endswith(".py")]
        self.buffers = dict(buffers)
        self.title = "Python"
        self._stop = threading.Event()
        self._done_signal.connect(self.finished)        # сигналы из потока приходят в главный поток очередью
        self._diag_signal.connect(self.diagnostic)

    def start(self):
        self.output.emit(tr("build.checking_python") % len(self.files), "dim")
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        started, errors = time.monotonic(), 0
        for path in self.files:
            if self._stop.is_set():
                break
            text = self.buffers.get(path)
            if text is None:
                try:
                    with open(path, encoding="utf-8", errors="replace") as f:
                        text = f.read()
                except OSError:
                    continue
            for line, col, msg, sev in check_text(path, text):
                errors += sev == "error"
                self._diag_signal.emit(BuildDiagnostic(path, line, col, sev, "", msg, "Python"))
        self._done_signal.emit({"ok": not errors and not self._stop.is_set(), "errors": errors, "warnings": 0,
                                "seconds": time.monotonic() - started, "cancelled": self._stop.is_set()})

    def cancel(self):
        self._stop.set()


class CommandSequence(BuildJob):
    """Несколько команд по очереди (создание проекта: dotnet new, dotnet sln add…). Первая ошибка останавливает.
    result — что добавить в итог (например, {"root": …, "open": […]}), когда всё прошло."""

    def __init__(self, title, commands, result=None, parent=None):
        super().__init__(parent)
        self.title = title
        self.commands = list(commands)          # [(программа, [аргументы], папка)]
        self.result = dict(result or {})
        self.proc = None
        self._started = 0.0
        self._cancelled = False

    def start(self):
        self._started = time.monotonic()
        self._next()

    def _next(self):
        if not self.commands:
            self.finished.emit(dict(self.result, ok=True, errors=0, warnings=0,
                                    seconds=time.monotonic() - self._started))
            return
        program, args, cwd = self.commands.pop(0)
        exe = shutil.which(program) or program
        os.makedirs(cwd, exist_ok=True)
        self.output.emit(" ".join('"%s"' % a if " " in a else a for a in [program, *args]), "dim")
        self.proc = QProcess(self)
        self.proc.setWorkingDirectory(cwd)
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        env = QProcessEnvironment.systemEnvironment()
        for key in ("DOTNET_NOLOGO", "DOTNET_CLI_TELEMETRY_OPTOUT", "DOTNET_SKIP_FIRST_TIME_EXPERIENCE"):
            env.insert(key, "1")
        self.proc.setProcessEnvironment(env)
        self.proc.readyReadStandardOutput.connect(
            lambda: [self.output.emit(line, "text") for line in
                     bytes(self.proc.readAllStandardOutput()).decode("utf-8", "replace").splitlines() if line.strip()])
        self.proc.finished.connect(self._step_done)
        self.proc.errorOccurred.connect(lambda e: e == QProcess.ProcessError.FailedToStart and self._fail(
            tr("build.could_not_start") % self.proc.errorString()))
        self.proc.start(exe, args)

    def _step_done(self, code, _status):
        if code != 0 or self._cancelled:
            self._fail(None)
            return
        self._next()

    def _fail(self, message):
        if message:
            self.output.emit(message, "error")
        self.commands = []
        self.finished.emit({"ok": False, "errors": 1, "warnings": 0, "cancelled": self._cancelled,
                            "seconds": time.monotonic() - self._started})

    def cancel(self):
        self._cancelled = True
        if self.proc is not None and self.proc.state() != QProcess.ProcessState.NotRunning:
            self.proc.kill()


class WriteFiles(BuildJob):
    """Создать файлы с текстом (шаблоны проектов без внешних программ). Существующие файлы не перезаписываются."""

    def __init__(self, title, files, result=None, parent=None):
        super().__init__(parent)
        self.title = title
        self.files = dict(files)                # путь → текст
        self.result = dict(result or {})

    def start(self):
        QTimer.singleShot(0, self._write)

    def _write(self):
        started = time.monotonic()
        for path, text in self.files.items():
            if os.path.exists(path):
                self.output.emit(tr("build.file_exists") % path, "error")
                self.finished.emit({"ok": False, "errors": 1, "warnings": 0, "seconds": 0})
                return
        try:
            for path, text in self.files.items():
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
                self.output.emit(path, "text")
        except OSError as e:
            self.output.emit(str(e), "error")
            self.finished.emit({"ok": False, "errors": 1, "warnings": 0, "seconds": 0})
            return
        self.finished.emit(dict(self.result, ok=True, errors=0, warnings=0, seconds=time.monotonic() - started))

    def cancel(self):
        pass


# ---------------------------------------------------------------- в окне
class BuildController(QObject):
    """Сборка из окна: что собирать, вывод в «Вывод → Сборка», ошибки в «Список ошибок», итог в строке состояния.
    Из окна берёт: root, editors(), explorer.files, output, errors, show_dock, status, save_all, options."""
    SOURCE = "common.build"
    started = pyqtSignal()
    stopped = pyqtSignal()

    def __init__(self, window):
        super().__init__(window)
        self.win = window
        self.jobs = []
        self._summaries = []
        self._diags = {}            # путь → [(строка, столбец, текст, вид, проект)]
        self._flush = QTimer(self, singleShot=True, interval=200, timeout=self._push_errors)
        self._custom = False

    def running(self):
        return bool(self.jobs)

    def _toolchains(self):
        from . import toolchains
        ctx = self.win.project_context()
        return ctx, toolchains.active(ctx)

    def build_solution(self, rebuild=False):
        """Ctrl+Shift+B: сборка от каждого набора инструментов, который узнал решение (C#, Python…)."""
        if not self.win.root:
            return
        ctx, active = self._toolchains()
        self._start([job for tc in active for job in tc.build_jobs(ctx, rebuild)])

    def build_current_project(self):
        """Shift+F6: проект, в который входит текущий файл."""
        ed = self.win.editor()
        if ed is None:
            return
        ctx, active = self._toolchains()
        for tc in active:
            jobs = tc.build_jobs_for_file(ctx, ed.path)
            if jobs:
                self._start(jobs)
                return
        self.win.status(tr("build.no_project_for_file"))

    def clean_solution(self):
        ctx, active = self._toolchains()
        jobs = [job for job in (tc.clean(ctx) for tc in active) if job is not None]
        if jobs:
            self._start(jobs)
        else:
            self.win.status(tr("build.cleaned"))

    def run_job(self, job, header, done=None):
        """Не сборка, а другая задача через тот же «Вывод» (создание проекта и т. п.): свой заголовок,
        без сохранения файлов и итоговой строки сборки; done(итог) — когда закончится."""
        if done is not None:
            job.finished.connect(done)
        self._start([job], header=header, save=False)

    def _start(self, jobs, header=None, save=True):
        win = self.win
        if self.running():
            win.status(tr("build.already_running"))
            return
        if not jobs:
            win.status(tr("build.nothing_to_build"))
            return
        if save and win.options["documents/save_before_run"]:
            win.save_all()
        self._custom = header is not None
        win.errors.clear_source(self.SOURCE)
        self._diags.clear()
        self._summaries = []
        win.output.clear()
        win.output.write(header or tr("build.started") % time.strftime("%H:%M:%S"), self.SOURCE, C["link"])
        win.show_dock(win.d_output)
        self.jobs = list(jobs)
        self.started.emit()
        win.status(tr("build.running"), 0)
        for job in jobs:
            job.output.connect(lambda text, role: win.output.write(text, self.SOURCE, C.get(role, C["text"])))
            job.diagnostic.connect(self._diagnostic)
            job.finished.connect(lambda summary, j=job: self._job_done(j, summary))
            job.start()

    def _diagnostic(self, d):
        self._diags.setdefault(d.path, []).append((d.line, d.col, d.text(), d.severity, d.project))
        self._flush.start()

    def _push_errors(self):
        for path, items in self._diags.items():
            if path:
                self.win.errors.set_group(self.SOURCE, path, items)

    def _job_done(self, job, summary):
        if job not in self.jobs:
            return
        self.jobs.remove(job)
        self._summaries.append((job.title, summary))
        if self.jobs:
            return
        self._flush.stop()
        self._push_errors()
        errors = sum(s["errors"] for _t, s in self._summaries)
        warnings = sum(s["warnings"] for _t, s in self._summaries)
        seconds = sum(s["seconds"] for _t, s in self._summaries)
        cancelled = any(s.get("cancelled") for _t, s in self._summaries)
        ok = not cancelled and all(s["ok"] for _t, s in self._summaries)
        win = self.win
        if self._custom:                    # итог сообщит тот, кто запускал (run_job → done)
            self.stopped.emit()
            return
        line = (tr("build.cancelled") if cancelled else tr("build.succeeded") if ok else tr("build.failed")) \
            % (errors, warnings, seconds)
        win.output.write(line, self.SOURCE, C["ok"] if ok else C["warning"] if cancelled else C["error"])
        win.status(line, 15000)
        if errors:
            win.show_dock(win.d_errors)
        self.stopped.emit()

    def cancel(self):
        for job in list(self.jobs):
            job.cancel()
