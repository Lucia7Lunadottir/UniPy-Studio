"""Чтение решений и проектов: .sln/.slnx, .csproj (SDK-стиль и старый формат Unity), проекты Python.

Каждый читатель — класс с `can_read(path)` и `read(path)`; реестр PROJECT_READERS. Новый вид проектов
(например, .vcxproj или Cargo.toml) — класс здесь и строка в реестре. Без Qt.
"""
import glob
import os
import re
from xml.etree import ElementTree

from .model import ANALYZER, ASSEMBLY, FRAMEWORK, PACKAGE, PROJECT, Dependency, Project, Solution

SKIP_DIRS = {"bin", "obj", ".git", ".vs", ".idea", "node_modules", "Library", "Temp", "Logs", "__pycache__",
             ".venv", "venv", "build", "dist", ".mypy_cache", ".pytest_cache"}

# Project("{тип}") = "Имя", "путь\к\проекту.csproj", "{guid}"; папки решения — тип 2150E333…
_SLN_PROJECT = re.compile(r'^Project\("\{(?P<type>[^}]+)\}"\)\s*=\s*"(?P<name>[^"]+)",\s*"(?P<path>[^"]+)"', re.M)
SOLUTION_FOLDER_TYPE = "2150E333-8FDC-42A3-9474-1A3956D46DE8"


def _native(base, rel):
    """Путь из файла MSBuild (с обратными косыми) → настоящий путь."""
    return os.path.normpath(os.path.join(base, rel.replace("\\", os.sep)))


def _local(tag):
    return tag.rsplit("}", 1)[-1]


# ---------------------------------------------------------------- решения
def read_sln(path):
    """Пути проектов из .sln (папки решения пропускаются)."""
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            text = f.read()
    except OSError:
        return []
    base = os.path.dirname(path)
    return [_native(base, m.group("path")) for m in _SLN_PROJECT.finditer(text)
            if m.group("type").upper() != SOLUTION_FOLDER_TYPE]


def read_slnx(path):
    """Пути проектов из .slnx (новый XML-формат решений)."""
    try:
        tree = ElementTree.parse(path)
    except (ElementTree.ParseError, OSError):
        return []
    base = os.path.dirname(path)
    return [_native(base, el.get("Path")) for el in tree.iter() if _local(el.tag) == "Project" and el.get("Path")]


# ---------------------------------------------------------------- проекты
class ProjectReader:
    kind = ""

    def can_read(self, path):
        return False

    def read(self, path):
        raise NotImplementedError


class CsprojReader(ProjectReader):
    """.csproj: SDK-стиль (файлы — вся папка, PackageReference) и старый формат с явным списком файлов
    (так их генерирует Unity: Compile/None Include, сотни Reference с HintPath)."""
    kind = "csharp"
    FILE_ITEMS = ("Compile", "None", "Content", "EmbeddedResource", "AdditionalFiles", "Page")

    def can_read(self, path):
        return path.endswith(".csproj")

    def read(self, path):
        name = os.path.splitext(os.path.basename(path))[0]
        folder = os.path.dirname(path)
        try:
            root = ElementTree.parse(path).getroot()
        except (ElementTree.ParseError, OSError) as e:
            p = Project(name, path, self.kind, reader="csproj")
            p.error = str(e)
            return p
        sdk = bool(root.get("Sdk")) or any(_local(el.tag) == "Sdk" for el in root)
        deps, files = [], []
        for el in root.iter():
            tag = _local(el.tag)
            if tag in ("TargetFramework", "TargetFrameworks", "TargetFrameworkVersion") and el.text:
                for fw in el.text.split(";"):
                    if fw.strip():
                        deps.append(Dependency(FRAMEWORK, fw.strip()))
            elif tag == "PackageReference" and (el.get("Include") or el.get("Update")):
                version = el.get("Version") or _child_text(el, "Version")
                deps.append(Dependency(PACKAGE, el.get("Include") or el.get("Update"), version))
            elif tag == "ProjectReference" and el.get("Include"):
                target = _native(folder, el.get("Include"))
                deps.append(Dependency(PROJECT, _child_text(el, "Name") or os.path.splitext(os.path.basename(target))[0],
                                       path=target))
            elif tag == "Reference" and el.get("Include"):
                hint = _child_text(el, "HintPath")
                deps.append(Dependency(ASSEMBLY, el.get("Include").split(",")[0],
                                       path=_native(folder, hint) if hint else None))
            elif tag == "Analyzer" and el.get("Include"):
                deps.append(Dependency(ANALYZER, os.path.splitext(os.path.basename(el.get("Include")))[0],
                                       path=_native(folder, el.get("Include"))))
            elif tag in self.FILE_ITEMS and el.get("Include") and not sdk:
                files.append(_native(folder, el.get("Include")))
        unity = any(d.kind == ASSEMBLY and d.name == "UnityEngine" for d in deps)
        project = Project(name, path, "unity" if unity else self.kind, folder=folder,
                          files=None if sdk else sorted(set(files)), dependencies=_unique(deps), reader="csproj")
        if not sdk and not files:
            project.files = None          # старый формат без списка — показать папку
        return project


class PythonProjectReader(ProjectReader):
    """Проект Python — папка с pyproject.toml / setup.py / requirements.txt. Имя — из [project] name,
    зависимости — те же, что в панели «Зависимости» (dependencies.python)."""
    kind = "python"
    MARKERS = ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt")

    def can_read(self, path):
        return os.path.basename(path) in self.MARKERS

    def read(self, path):
        folder = os.path.dirname(path)
        name = os.path.basename(folder.rstrip(os.sep))
        pyproject = os.path.join(folder, "pyproject.toml")
        if os.path.isfile(pyproject):
            try:
                import tomllib
                with open(pyproject, "rb") as f:
                    name = (tomllib.load(f).get("project") or {}).get("name") or name
            except (OSError, ValueError):
                pass
        deps = []
        try:
            from ..dependencies.python import PythonDependencies
            for r in PythonDependencies(folder, None).requirements():
                deps.append(Dependency(PACKAGE, r.name + (" [%s]" % r.group if r.group else ""), r.spec, r.source))
        except Exception:     # noqa: BLE001 — сломанный pyproject не должен ломать обозреватель
            pass
        return Project(name, path, self.kind, folder=folder, dependencies=deps, reader="python")


PROJECT_READERS = [CsprojReader(), PythonProjectReader()]


def read_project(path):
    reader = next((r for r in PROJECT_READERS if r.can_read(path)), None)
    return reader.read(path) if reader is not None else None


def _child_text(el, name):
    for ch in el:
        if _local(ch.tag) == name and ch.text:
            return ch.text.strip()
    return ""


def _unique(deps):
    seen, out = set(), []
    for d in deps:
        key = (d.kind, d.name)
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


# ---------------------------------------------------------------- решение папки
def _walk_projects(root, depth=4):
    """Файлы проектов в папке (как find_projects у .NET): .csproj и признаки проекта Python."""
    found = []
    base = root.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        if dirpath.count(os.sep) - base >= depth:
            dirnames[:] = []
        csproj = [f for f in filenames if f.endswith(".csproj")]
        found.extend(os.path.join(dirpath, f) for f in sorted(csproj))
        if not csproj:
            marker = next((m for m in PythonProjectReader.MARKERS if m in filenames), None)
            if marker:
                found.append(os.path.join(dirpath, marker))
                dirnames[:] = []            # вложенные папки — часть этого проекта
    return found


def load_solution(root):
    """Решение папки: .sln/.slnx в корне (как Visual Studio) → его проекты; иначе — проекты, найденные в папке.
    Проектов нет — None (обозреватель покажет просто папку)."""
    if not root or not os.path.isdir(root):
        return None
    root = os.path.abspath(root)
    slns = sorted(glob.glob(os.path.join(glob.escape(root), "*.sln")) +
                  glob.glob(os.path.join(glob.escape(root), "*.slnx")))
    named = [s for s in slns if os.path.splitext(os.path.basename(s))[0] == os.path.basename(root.rstrip(os.sep))]
    # .sln и .slnx с одним именем — свежий (его пересоздаёт Unity); то же правило у анализатора (lsp.pick_solution)
    sln = max(named or slns, key=lambda p: (os.path.getmtime(p), p.endswith(".sln"))) if slns else None
    name = os.path.basename(root.rstrip(os.sep))
    if sln:
        paths = read_slnx(sln) if sln.endswith(".slnx") else read_sln(sln)
        name = os.path.splitext(os.path.basename(sln))[0]
        marker = next((m for m in PythonProjectReader.MARKERS if os.path.isfile(os.path.join(root, m))), None)
        if marker:                                          # Python-проект в корне рядом с решением C#
            paths.append(os.path.join(root, marker))
    else:
        paths = _walk_projects(root)
    projects = []
    for path in paths:
        project = read_project(path) if os.path.exists(path) else None
        if project is not None:
            projects.append(project)
    if not projects:
        return None
    projects.sort(key=lambda p: p.name.lower())
    return Solution(name, sln or root, projects, root=root)
