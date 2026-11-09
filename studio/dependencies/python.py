"""Зависимости Python: pyproject.toml ([project] dependencies, optional-dependencies, [dependency-groups])
и requirements*.txt; что установлено — спрашивается у интерпретатора проекта (importlib.metadata);
сравнение версий — packaging (PEP 440). Системный Python Arch помечен EXTERNALLY-MANAGED: pip в него
не ставит — такие пакеты ставятся из официального репозитория (pacman), остальное — в .venv."""
import glob
import json
import os
import re
import shutil
import subprocess
import sys

from .base import DependencyProvider, MISMATCH, MISSING, OK, Requirement, UNKNOWN

try:
    import tomllib
except ImportError:     # Python < 3.11
    tomllib = None
try:
    from packaging.requirements import InvalidRequirement, Requirement as PepRequirement
    from packaging.specifiers import SpecifierSet
except ImportError:     # без python-packaging — без проверки версий, только «установлен / нет»
    PepRequirement = None

# Имя на PyPI → пакет Arch, где они не совпадают с «python-<имя>»
ARCH_NAMES = {"pyyaml": "python-yaml", "opencv-python": "python-opencv", "opencv-contrib-python": "python-opencv",
              "beautifulsoup4": "python-beautifulsoup4", "pillow": "python-pillow", "pyqt6": "python-pyqt6",
              "pyside6": "pyside6", "scikit-learn": "python-scikit-learn", "attrs": "python-attrs",
              "protobuf": "python-protobuf", "msgpack": "python-msgpack", "pygobject": "python-gobject",
              "pycairo": "python-cairo", "dbus-python": "python-dbus", "pyopengl": "python-opengl"}

# Выполняется интерпретатором проекта. Не version(name): если рядом лежит старая нечитаемая папка
# *.dist-info (остаток обновления), importlib берёт её первой и отдаёт None — перебираем все и берём новую.
_CHECK = r"""
import json, re, sys
from importlib.metadata import distributions
norm = lambda n: re.sub(r"[-_.]+", "-", n).lower()
want = {norm(n): n for n in json.loads(sys.argv[1])}
key = lambda v: [int(x) if x.isdigit() else x for x in re.split(r"[.+-]", v)]
out = {n: None for n in want.values()}
for dist in distributions():
    try:
        name, ver = dist.metadata["Name"], dist.version
    except Exception:
        continue
    if not name or not ver or norm(name) not in want:
        continue
    original = want[norm(name)]
    try:
        newer = out[original] is None or key(ver) > key(out[original])
    except TypeError:
        newer = out[original] is None
    if newer:
        out[original] = ver
print(json.dumps(out))
"""


def normalize(name):
    """PEP 503: PyQt6, pyqt6, PyQt_6 — одно и то же."""
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_line(text, source, line, group=""):
    """Строка требования (PEP 508) → Requirement или None (комментарий, -r, -e, URL…)."""
    text = text.split(" #", 1)[0].strip()
    if not text or text.startswith(("#", "-", "git+", "http:", "https:", "file:")):
        return None
    if PepRequirement is not None:
        try:
            req = PepRequirement(text)
        except InvalidRequirement:
            return None
        if req.marker is not None and not req.marker.evaluate():
            return None                 # «; sys_platform == 'win32'» — не для этой системы
        return Requirement(req.name, str(req.specifier), source, line, group, str(req.marker or ""), text)
    m = re.match(r"([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*([^;]*)", text)
    return Requirement(m.group(1), m.group(3).strip(), source, line, group, "", text) if m else None


def _line_of(lines, needle, start=0):
    for i in range(start, len(lines)):
        if needle in lines[i]:
            return i + 1
    return 1


class PythonDependencies(DependencyProvider):
    name = "Python"

    def __init__(self, root, interpreter):
        self.root = root
        self.interpreter = interpreter

    def is_system(self):
        """Окружение без venv, помеченное системой (PEP 668) — pip в него не ставит."""
        try:
            out = subprocess.run([self.interpreter, "-c", "import sys, sysconfig, os; print(sys.prefix != sys.base_prefix);"
                                  "print(os.path.exists(os.path.join(sysconfig.get_path('stdlib'), 'EXTERNALLY-MANAGED')))"],
                                 capture_output=True, text=True, timeout=15).stdout.split()
        except (OSError, subprocess.SubprocessError):
            return False
        return len(out) == 2 and out[0] == "False" and out[1] == "True"

    def environment(self):
        return self.interpreter, self.is_system()

    # --- требования
    def requirements(self):
        out = []
        pyproject = os.path.join(self.root, "pyproject.toml")
        if os.path.isfile(pyproject) and tomllib is not None:
            out.extend(self._from_pyproject(pyproject))
        for path in sorted(glob.glob(os.path.join(glob.escape(self.root), "requirements*.txt")) +
                           glob.glob(os.path.join(glob.escape(self.root), "requirements", "*.txt"))):
            out.extend(self._from_requirements(path))
        seen, unique = set(), []
        for r in out:                   # одно и то же в pyproject и requirements — одна строка
            key = (normalize(r.name), r.group)
            if key not in seen:
                seen.add(key)
                unique.append(r)
        return unique

    def _from_pyproject(self, path):
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
            with open(path, encoding="utf-8", errors="replace") as f:
                lines = f.read().split("\n")
        except (OSError, ValueError):
            return []
        out = []
        groups = [("", (data.get("project") or {}).get("dependencies") or [])]
        groups += [(name, items) for name, items in ((data.get("project") or {}).get("optional-dependencies") or {}).items()]
        groups += [(name, [i for i in items if isinstance(i, str)])
                   for name, items in (data.get("dependency-groups") or {}).items()]
        for group, items in groups:
            for item in items:
                req = parse_line(item, path, _line_of(lines, item), group)
                if req is not None:
                    out.append(req)
        return out

    def _from_requirements(self, path):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                lines = f.read().split("\n")
        except OSError:
            return []
        group = os.path.basename(path)[len("requirements"):-len(".txt")].strip("-_") or ""
        return [r for i, text in enumerate(lines) for r in [parse_line(text, path, i + 1, group)] if r is not None]

    # --- что установлено
    def check(self, requirements):
        names = sorted({r.name for r in requirements})
        try:
            out = subprocess.run([self.interpreter, "-c", _CHECK, json.dumps(names)], capture_output=True,
                                 text=True, timeout=60).stdout
            installed = json.loads(out.strip().splitlines()[-1]) if out.strip() else {}
        except (OSError, subprocess.SubprocessError, ValueError, IndexError):
            installed = {}
        system = self.is_system()
        for r in requirements:
            r.installed = installed.get(r.name)
            if r.installed is None:
                r.state = MISSING
            elif r.spec and PepRequirement is not None:
                ok = SpecifierSet(r.spec).contains(r.installed, prereleases=True)
                r.state = OK if ok else MISMATCH
            else:
                r.state = OK if r.installed else UNKNOWN
            if system and r.state != OK:
                r.system_package = self.arch_package(r.name)
        return requirements

    @staticmethod
    def arch_package(name):
        """Пакет официального репозитория для имени с PyPI или None (pacman -Ssq без root)."""
        if shutil.which("pacman") is None:
            return None
        n = normalize(name)
        candidates = [ARCH_NAMES.get(n), "python-" + n, "python-" + re.sub(r"^py", "", n)]
        for candidate in [c for c in candidates if c]:
            try:
                found = subprocess.run(["pacman", "-Ssq", "^%s$" % re.escape(candidate)], capture_output=True,
                                       text=True, timeout=15).stdout.split()
            except (OSError, subprocess.SubprocessError):
                return None
            if candidate in found:
                return candidate
        return None

    # --- установка
    def install_commands(self, requirements):
        system = self.is_system()
        if system:
            packages = sorted({r.system_package for r in requirements if r.system_package})
            return [("sudo pacman -S --needed %s" % " ".join(packages), "pacman")] if packages else []
        specs = " ".join('"%s"' % (r.raw or r.name) for r in requirements)
        return [('"%s" -m pip install %s' % (self.interpreter, specs), "pip")] if specs else []

    def create_environment_command(self):
        if os.path.exists(os.path.join(self.root, ".venv")):
            return None
        base = sys.executable if self.is_system() else self.interpreter
        return 'cd "%s" && "%s" -m venv .venv' % (self.root, base)
