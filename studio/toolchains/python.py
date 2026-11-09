"""Python: запуск файла выбранным интерпретатором, проверка синтаксиса, шаблоны проектов."""
import os
import re
import subprocess
import sys

from .. import templates
from ..build import PythonCheck, WriteFiles
from .base import ProjectTemplate, RunCommand, RunTarget, Toolchain

MAX_SCAN_BYTES = 200_000


def terminal_available():
    from ..terminal.screen import AVAILABLE
    return AVAILABLE


def traceback_problems(text, root=None):
    """Traceback → [(путь, строка, 1, последняя строка ошибки)]: последний кадр из кода проекта
    (а не из библиотек), если такой есть."""
    if "Traceback" not in text:
        return []
    frames = re.findall(r'File "(.+?)", line (\d+)', text)
    if not frames:
        return []
    last = text.strip().splitlines()[-1]
    own = [f for f in frames if root and os.path.abspath(f[0]).startswith(os.path.join(root, ""))] or frames
    path, line = own[-1]
    return [(os.path.abspath(path), int(line), 1, last)]


PYPROJECT = '''[project]
name = "{package}"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = [{dependencies}]
'''

CONSOLE_MAIN = '''"""{title}"""


def main():
    print("Hello, {title}!")


if __name__ == "__main__":
    main()
'''


class PythonToolchain(Toolchain):
    id = "python"
    name = "Python"
    badge = ("py", "#3572A5", "#FFD43B")
    file_extensions = (".py", ".pyw", ".pyi", ".ui", ".qrc")
    file_names = ("pyproject.toml", "requirements.txt", "setup.py", "setup.cfg")

    def applies(self, ctx):
        return any(p.endswith(".py") for p in ctx.files)

    # --- запуск
    def run_targets(self, ctx):
        """main.py — первыми, затем файлы с `if __name__ == "__main__"`."""
        mains, others = [], []
        for p in ctx.files:
            if not p.endswith(".py"):
                continue
            if os.path.basename(p) == "main.py":
                mains.append(p)
                continue
            text = ctx.buffers.get(p)
            if text is None:
                try:
                    if os.path.getsize(p) > MAX_SCAN_BYTES:
                        continue
                    with open(p, encoding="utf-8", errors="ignore") as f:
                        text = f.read()
                except OSError:
                    continue
            if "__main__" in text:
                others.append(p)
        return [RunTarget(p, os.path.relpath(p, ctx.root) if ctx.root else p, p) for p in (mains + others)[:40]]

    def target_for_file(self, ctx, path):
        return RunTarget(path, os.path.basename(path), path) if path.endswith((".py", ".pyw")) else None

    def run_command(self, ctx, target):
        interp = ctx.interpreter or sys.executable
        return RunCommand(interp, ["-u", target.path], os.path.dirname(target.path),
                          {"PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"},
                          title=os.path.basename(target.path),
                          problem_matcher=lambda text: traceback_problems(text, ctx.root))

    # --- отладка (debugpy — адаптер DAP от Microsoft, пакет python-debugpy в официальном репозитории Arch)
    _has_debugpy = {}

    def _debugpy_python(self, ctx):
        """Python, в котором есть debugpy: выбранный интерпретатор (venv), иначе системный."""
        for interp in (ctx.interpreter, sys.executable, "/usr/bin/python3"):
            if not interp:
                continue
            if interp not in self._has_debugpy:
                try:
                    self._has_debugpy[interp] = subprocess.run(
                        [interp, "-c", "import debugpy"], capture_output=True, timeout=10).returncode == 0
                except (OSError, subprocess.SubprocessError):
                    self._has_debugpy[interp] = False
            if self._has_debugpy[interp]:
                return interp
        return None

    def debug_available(self, ctx):
        return self._debugpy_python(ctx) is not None, "sudo pacman -S python-debugpy"

    def debug_config(self, ctx, target):
        from ..debug.session import DebugConfig
        adapter_python = self._debugpy_python(ctx)
        if adapter_python is None:
            return None
        program = ctx.interpreter or sys.executable
        return DebugConfig(adapter_python, ["-m", "debugpy.adapter"], "launch", {
            "name": os.path.basename(target.path), "type": "python", "request": "launch",
            "program": target.path, "cwd": os.path.dirname(target.path), "python": [program],
            # во встроенном терминале работает input(); иначе — вывод в «Вывод» через сам отладчик
            "console": "integratedTerminal" if ctx.option("run/in_terminal", True) and terminal_available()
                       else "internalConsole",
            "redirectOutput": True, "justMyCode": True, "showReturnValue": True,
            "env": {"PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}},
            adapter_id="debugpy", cwd=os.path.dirname(target.path), title=os.path.basename(target.path))

    # --- тесты (pytest или unittest — решает помощник в интерпретаторе проекта)
    def test_provider(self, ctx):
        names = (os.path.basename(p) for p in ctx.files)
        if not any(n.endswith(".py") and (n.startswith("test_") or n.endswith("_test.py")) for n in names):
            return None
        from ..testing.python import PythonTests
        return PythonTests(ctx.root, ctx.interpreter or sys.executable, self._debugpy_python(ctx))

    # --- сборка
    def build_jobs(self, ctx, rebuild=False):
        files = [p for p in ctx.files if p.endswith(".py")]
        return [PythonCheck(files, ctx.buffers)] if files else []

    def build_jobs_for_file(self, ctx, path):
        if not path.endswith(".py"):
            return None
        return [PythonCheck([path], ctx.buffers)]

    # --- новые проекты
    def load_templates(self, done):
        done([
            ProjectTemplate("console", "toolchains.python_console", "toolchains.python_console_about", "Python"),
            ProjectTemplate("pyqt6", "toolchains.pyqt6_app", "toolchains.pyqt6_app_about", "Python", "PyQt6/Desktop"),
        ])

    def create_project(self, template, folder, name, choices):
        root = os.path.join(folder, name)
        package = re.sub(r"[^A-Za-z0-9_.-]", "-", name).strip("-").lower() or "app"
        main = os.path.join(root, "main.py")
        if template.id == "pyqt6":
            cls = re.sub(r"\W", "", name.title()) or "MainWindow"
            code = templates.render(templates.PYQT_WINDOW, cls if cls[0].isalpha() else "App" + cls)
            deps = '"PyQt6"'
        else:
            code = CONSOLE_MAIN.format(title=name)
            deps = ""
        files = {main: code, os.path.join(root, "pyproject.toml"): PYPROJECT.format(package=package, dependencies=deps)}
        return WriteFiles(name, files, {"root": root, "open": [main]})
