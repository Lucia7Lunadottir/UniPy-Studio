""".NET (не Unity): консольные, оконные (Avalonia), веб-приложения и библиотеки на C#.

Запуск — `dotnet run --project`, сборка — `dotnet build` (bin/obj рядом с проектом, как в Visual Studio),
новые проекты — шаблоны, установленные в `dotnet new` (список читается у самой dotnet, поэтому
в нём есть и то, что поставлено отдельно, например Avalonia).
"""
import os
import re
import shutil
import threading
from xml.etree import ElementTree

from PyQt6.QtCore import QObject, pyqtSignal

from ..build import CommandSequence, DotnetBuild, dotnet_target, is_unity_folder, project_for_file
from .base import ProjectTemplate, RunCommand, RunTarget, Toolchain

SKIP_DIRS = {"bin", "obj", ".git", "node_modules", "Library", "Temp", ".vs", ".idea"}
ENV = {"DOTNET_NOLOGO": "1", "DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1"}
# Шаблоны только для Windows на Linux не собираются
WINDOWS_ONLY_TAGS = ("WPF", "WinForms", "Windows Forms", "WinUI", "MAUI")
# «at X in /path/File.cs:line 12» — слово «line» переводится вместе с .NET, поэтому любое слово перед номером
_STACK = re.compile(r"(?P<path>/[^\n]+?\.(?:cs|fs|vb)):\w+ (?P<line>\d+)")


def dotnet_ui_language():
    """Язык интерфейса UniPy → язык dotnet (названия шаблонов на том же языке, что и меню)."""
    from .. import i18n
    lang = (i18n.LANG or "en").replace("_", "-")
    return {"zh": "zh-Hans", "zh-TW": "zh-Hant", "pt": "pt-BR"}.get(lang, lang.split("-")[0])


def exception_problems(text):
    """Необработанное исключение .NET: «   at Program.Main() in /path/Program.cs:line 12» → первое место."""
    m = _STACK.search(text)
    if not m:
        return []
    first = next((ln for ln in text.splitlines() if "Exception" in ln and not ln.lstrip().startswith("at ")), "")
    return [(m.group("path"), int(m.group("line")), 1, first.strip() or "Unhandled exception")]


def find_projects(root, depth=4):
    """Все .csproj решения (без bin/obj и т. п.)."""
    out = []
    if not root or not os.path.isdir(root):
        return out
    base = root.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        if dirpath.count(os.sep) - base >= depth:
            dirnames[:] = []
        out.extend(os.path.join(dirpath, f) for f in filenames if f.endswith(".csproj"))
    return sorted(out)


def is_runnable(csproj):
    """Exe/WinExe или веб-проект (Microsoft.NET.Sdk.Web)."""
    try:
        tree = ElementTree.parse(csproj)
    except (ElementTree.ParseError, OSError):
        return False
    root = tree.getroot()
    if (root.get("Sdk") or "").startswith("Microsoft.NET.Sdk.Web"):
        return True
    kinds = [el.text.strip() for el in root.iter() if el.tag.endswith("OutputType") and el.text]
    return any(k in ("Exe", "WinExe") for k in kinds)


def parse_template_table(text):
    """Таблица `dotnet new list` → [(имя, короткое имя, языки, теги)]. Колонки — по строке из дефисов
    (заголовки переведены на язык системы, поэтому по ним не ищем)."""
    lines = text.splitlines()
    dash = next((i for i, ln in enumerate(lines) if re.fullmatch(r"[-\s]+", ln.strip() or "x") and "--" in ln), None)
    if dash is None:
        return []
    spans = [(m.start(), m.end()) for m in re.finditer(r"-+", lines[dash])]
    rows = []
    for ln in lines[dash + 1:]:
        if not ln.strip():
            continue
        cells = [ln[a:(spans[i + 1][0] if i + 1 < len(spans) else None)].strip() for i, (a, _b) in enumerate(spans)]
        if len(cells) >= 4 and cells[1]:
            rows.append((cells[0], cells[1].split(",")[0], cells[2], cells[3]))
    return rows


class _TemplateLoader(QObject):
    """`dotnet new list` в фоне (первый запуск dotnet бывает долгим) — один раз за сеанс."""
    loaded = pyqtSignal(object)

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        import subprocess
        env = {**os.environ, **ENV, "DOTNET_CLI_UI_LANGUAGE": dotnet_ui_language()}
        try:
            out = subprocess.run(["dotnet", "new", "list", "--type", "project", "--language", "C#"],
                                 capture_output=True, text=True, timeout=120, env=env).stdout
        except (OSError, subprocess.SubprocessError):
            out = ""
        self.loaded.emit(parse_template_table(out))


class DotnetToolchain(Toolchain):
    id = "dotnet"
    name = ".NET"
    badge = ("C#", "#68217A", "#FFFFFF")
    file_extensions = (".cs", ".csproj", ".sln", ".slnx", ".props", ".targets", ".razor", ".axaml", ".xaml",
                       ".resx", ".editorconfig")
    file_names = ("global.json", "nuget.config", "Directory.Build.props", "appsettings.json")

    def __init__(self):
        self._templates = None
        self._waiting = []
        self._loader = None

    def available(self):
        return shutil.which("dotnet") is not None, "sudo pacman -S dotnet-sdk"

    def applies(self, ctx):
        target = dotnet_target(ctx.root)
        return bool(target) and not is_unity_folder(os.path.dirname(target))

    # --- запуск
    def run_targets(self, ctx):
        return [RunTarget(p, os.path.splitext(os.path.basename(p))[0], p)
                for p in find_projects(ctx.root) if is_runnable(p)]

    def target_for_file(self, ctx, path):
        if not path.endswith((".cs", ".csproj", ".razor", ".axaml", ".xaml")) or not self.applies(ctx):
            return None
        for proj in find_projects(ctx.root):
            if (path == proj or project_for_file(path, os.path.dirname(proj)) == proj) and is_runnable(proj):
                return RunTarget(proj, os.path.splitext(os.path.basename(proj))[0], proj)
        runnable = self.run_targets(ctx)
        return runnable[0] if len(runnable) == 1 else None     # библиотека в решении с одним приложением

    def run_command(self, ctx, target):
        return RunCommand("dotnet", ["run", "--project", target.path], os.path.dirname(target.path), ENV,
                          title=target.label, problem_matcher=exception_problems)

    # --- сборка
    def build_jobs(self, ctx, rebuild=False):
        target = dotnet_target(ctx.root)
        return [DotnetBuild(target, "rebuild" if rebuild else "build",
                            shared_compiler=ctx.option("build/shared_compiler", False))] if target else []

    def build_jobs_for_file(self, ctx, path):
        if not path.endswith(".cs") or not self.applies(ctx):
            return None
        for proj in find_projects(ctx.root):
            if project_for_file(path, os.path.dirname(proj)) == proj:
                return [DotnetBuild(proj, shared_compiler=ctx.option("build/shared_compiler", False))]
        return None

    def clean(self, ctx):
        target = dotnet_target(ctx.root)
        return DotnetBuild(target, "clean") if target else None

    # --- новые проекты
    def load_templates(self, done):
        if self._templates is not None:
            done(self._templates)
            return
        self._waiting.append(done)
        if self._loader is None:
            if not self.available()[0]:
                self._loaded([])
                return
            self._loader = _TemplateLoader()
            self._loader.loaded.connect(self._loaded)
            self._loader.start()

    def _loaded(self, rows):
        self._templates = [
            ProjectTemplate(short, name, "", "C#", tags, options=("solution",))
            for name, short, _langs, tags in rows if not any(t in tags for t in WINDOWS_ONLY_TAGS)]
        waiting, self._waiting = self._waiting, []
        for done in waiting:
            done(self._templates)

    def create_project(self, template, folder, name, choices):
        """Как «Создать проект» в VS: с решением — Name/Name.sln + Name/Name/Name.csproj; без — Name/Name.csproj."""
        root = os.path.join(folder, name)
        with_solution = choices.get("solution", True)
        project_dir = os.path.join(root, name) if with_solution else root
        commands = [("dotnet", ["new", template.id, "-n", name, "-o", project_dir], folder)]
        if with_solution:
            commands.insert(0, ("dotnet", ["new", "sln", "-n", name, "-o", root], folder))
            commands.append(("dotnet", ["sln", "add", os.path.join(project_dir, name + ".csproj")], root))
        # что открыть после создания — первый существующий (у Avalonia нет Program.cs в привычном виде)
        candidates = [os.path.join(project_dir, n) for n in
                      ("Program.cs", os.path.join("Views", "MainWindow.axaml.cs"), "MainWindow.axaml.cs", "App.axaml.cs",
                       "Class1.cs", "Worker.cs", "UnitTest1.cs")]
        return CommandSequence(name, commands, {"root": root, "open": candidates})
