import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import time
import unittest
from pathlib import Path

from PyQt6.QtWidgets import QApplication

from studio import toolchains
from studio.build import WriteFiles
from studio.toolchains import ProjectContext, ProjectTemplate, RunCommand, RunTarget, Toolchain
from studio.toolchains.dotnet import (DotnetToolchain, exception_problems, find_projects, is_runnable,
                                      parse_template_table)
from studio.toolchains.python import PythonToolchain, traceback_problems
from studio.toolchains.unity import UnityToolchain

app = QApplication.instance() or QApplication([])


def spin(seconds, until):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
        if until():
            return True
    return False


class Registry(unittest.TestCase):
    def test_order_and_duplicates(self):
        self.assertEqual([t.id for t in toolchains.TOOLCHAINS[:3]], ["unity", "python", "dotnet"])
        with self.assertRaises(ValueError):
            toolchains.register(PythonToolchain())


class PythonTests(unittest.TestCase):
    def test_targets_command_and_traceback(self):
        root = Path(tempfile.mkdtemp())
        (root / "main.py").write_text("print(1)\n")
        (root / "tool.py").write_text("if __name__ == '__main__':\n    pass\n")
        (root / "lib.py").write_text("x = 1\n")
        ctx = ProjectContext(str(root), [str(p) for p in root.iterdir()], interpreter="/usr/bin/python3")
        tc = PythonToolchain()
        self.assertTrue(tc.applies(ctx))
        self.assertEqual([t.label for t in tc.run_targets(ctx)], ["main.py", "tool.py"])
        cmd = tc.run_command(ctx, tc.target_for_file(ctx, str(root / "lib.py")))
        self.assertEqual((cmd.program, cmd.args[-1], cmd.cwd), ("/usr/bin/python3", str(root / "lib.py"), str(root)))
        tb = ('Traceback (most recent call last):\n  File "%s", line 3, in <module>\n'
              '  File "/usr/lib/python3/x.py", line 9, in f\nValueError: bad\n') % (root / "main.py")
        self.assertEqual(cmd.problem_matcher(tb), [(str(root / "main.py"), 3, 1, "ValueError: bad")])
        self.assertEqual(traceback_problems("no error"), [])

    def test_templates_create_files(self):
        found = []
        PythonToolchain().load_templates(found.extend)
        self.assertEqual([t.id for t in found], ["console", "pyqt6"])
        folder = tempfile.mkdtemp()
        job = PythonToolchain().create_project(found[1], folder, "Demo", {})
        done = []
        job.finished.connect(done.append)
        job.start()
        self.assertTrue(spin(3, lambda: done))
        self.assertTrue(done[0]["ok"])
        main = Path(folder, "Demo", "main.py").read_text()
        self.assertIn("class Demo(QMainWindow)", main)
        compile(main, "main.py", "exec")
        self.assertIn('"PyQt6"', Path(folder, "Demo", "pyproject.toml").read_text())


TABLE_RU = """Эти шаблоны соответствуют входным данным: --language='C#', --type='project'.

Имя шаблона                                                    Короткое имя         Язык      Теги
-------------------------------------------------------------  -------------------  --------  -----------------------------------------
Avalonia .NET App                                              avalonia.app         C#,F#     Desktop/Xaml/Avalonia/Windows/Linux/macOS
Консольное приложение                                          console              C#,F#,VB  Common/Console
Приложение WPF                                                 wpf                  C#,VB     Common/WPF
Веб-приложение ASP.NET Core (Razor Pages)                      webapp,razor         C#        Web/MVC/Razor Pages
"""


class DotnetTests(unittest.TestCase):
    def test_template_table_and_windows_only_filter(self):
        rows = parse_template_table(TABLE_RU)
        self.assertEqual([r[1] for r in rows], ["avalonia.app", "console", "wpf", "webapp"])
        self.assertEqual(rows[1][0], "Консольное приложение")
        tc = DotnetToolchain()
        got = []
        tc._waiting.append(got.extend)
        tc._loaded(rows)
        self.assertEqual([t.id for t in got], ["avalonia.app", "console", "webapp"])     # WPF — только Windows

    def test_projects_targets_and_exceptions(self):
        root = Path(tempfile.mkdtemp())
        (root / "App.sln").write_text("")
        (root / "App").mkdir()
        (root / "App" / "App.csproj").write_text(
            '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType></PropertyGroup></Project>')
        (root / "App" / "Program.cs").write_text("")
        (root / "Lib").mkdir()
        (root / "Lib" / "Lib.csproj").write_text('<Project Sdk="Microsoft.NET.Sdk"></Project>')
        (root / "Lib" / "Util.cs").write_text("")
        (root / "App" / "bin").mkdir()
        (root / "App" / "bin" / "Stale.csproj").write_text("<Project/>")
        self.assertEqual([os.path.basename(p) for p in find_projects(str(root))], ["App.csproj", "Lib.csproj"])
        self.assertTrue(is_runnable(str(root / "App" / "App.csproj")))
        self.assertFalse(is_runnable(str(root / "Lib" / "Lib.csproj")))
        tc, ctx = DotnetToolchain(), ProjectContext(str(root))
        self.assertTrue(tc.applies(ctx))
        self.assertEqual([t.label for t in tc.run_targets(ctx)], ["App"])
        # файл библиотеки — запускается единственное приложение решения
        self.assertEqual(tc.target_for_file(ctx, str(root / "Lib" / "Util.cs")).label, "App")
        cmd = tc.run_command(ctx, tc.target_for_file(ctx, str(root / "App" / "Program.cs")))
        self.assertEqual(cmd.args, ["run", "--project", str(root / "App" / "App.csproj")])
        self.assertEqual(exception_problems("Unhandled exception. System.Exception: boom\n"
                                            "   at Program.<Main>$(String[] args) in /x/Program.cs:line 5"),
                         [("/x/Program.cs", 5, 1, "Unhandled exception. System.Exception: boom")])
        job = tc.create_project(ProjectTemplate("console", "Console"), "/tmp/x", "Hello", {"solution": True})
        self.assertEqual([c[1][:2] for c in job.commands], [["new", "sln"], ["new", "console"], ["sln", "add"]])


class UnityTests(unittest.TestCase):
    def test_unity_project_opens_unity_and_wins_over_dotnet(self):
        root = Path(tempfile.mkdtemp())
        (root / "Assets").mkdir()
        (root / "ProjectSettings").mkdir()
        (root / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 6000.6.4f1\n")
        (root / "Game.sln").write_text("")
        opened = []
        ctx = ProjectContext(str(root), open_in_unity=lambda: opened.append(1))
        self.assertTrue(UnityToolchain().applies(ctx))
        self.assertFalse(DotnetToolchain().applies(ctx))            # Unity-решение — не обычный .NET
        script = str(root / "Assets" / "Player.cs")
        tc = next(t for t in toolchains.TOOLCHAINS if t.target_for_file(ctx, script))
        self.assertEqual(tc.id, "unity")
        tc.launch(ctx, tc.target_for_file(ctx, script), runner=None)
        self.assertEqual(opened, [1])


class EchoToolchain(Toolchain):
    """«Новый язык» для проверки модульности: файлы .echo «запускаются» программой echo."""
    id = "echo-test"
    name = "Echo"
    badge = ("E", "#000000", "#FFFFFF")
    file_extensions = (".echo",)

    def applies(self, ctx):
        return any(p.endswith(".echo") for p in ctx.files)

    def run_targets(self, ctx):
        return [RunTarget(p, os.path.basename(p), p) for p in ctx.files if p.endswith(".echo")]

    def target_for_file(self, ctx, path):
        return RunTarget(path, os.path.basename(path), path) if path.endswith(".echo") else None

    def run_command(self, ctx, target):
        return RunCommand("echo", ["echo-ran", os.path.basename(target.path)], os.path.dirname(target.path))

    def load_templates(self, done):
        done([ProjectTemplate("hello", "Echo hello", language="Echo")])

    def create_project(self, template, folder, name, choices):
        path = os.path.join(folder, name, "hello.echo")
        return WriteFiles(name, {path: "hi\n"}, {"root": os.path.join(folder, name), "open": [path]})


class SlowTemplates(Toolchain):
    id = "slow-test"
    name = "Slow"

    def __init__(self):
        self.callbacks = []

    def load_templates(self, done):
        self.callbacks.append(done)             # ответит позже, как dotnet new list


class NewProjectLoading(unittest.TestCase):
    def test_loading_note_stays_until_slow_toolchain_answers(self):
        """Регрессия: быстрые наборы (Python, Unity) обнуляли счётчик раньше, чем медленный начинал грузить."""
        from studio.new_project import NewProjectDialog
        slow = SlowTemplates()
        dialog = NewProjectDialog(None, [UnityToolchain(), PythonToolchain(), slow], tempfile.mkdtemp())
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog._pending, 1)
        self.assertTrue(dialog.loading.text())
        slow.callbacks[0]([ProjectTemplate("t", "Slow template", language="Slow")])
        self.assertEqual(dialog._pending, 0)
        self.assertIn("Slow template", [dialog.list.item(i).text() for i in range(dialog.list.count())])


class Extensibility(unittest.TestCase):
    """Новый набор — одна регистрация: окно само показывает его цели, запускает и создаёт проекты."""

    def test_new_toolchain_plugs_into_window(self):
        from studio.mainwindow import MainWindow
        from studio.new_project import NewProjectDialog
        echo = toolchains.register(EchoToolchain())
        win = MainWindow(screenshot=True)
        try:
            root = Path(tempfile.mkdtemp())
            (root / "a.echo").write_text("x")
            win.set_root(str(root))
            spin(2, lambda: win.startup.count() > 1)
            labels = [win.startup.itemText(i) for i in range(win.startup.count())]
            self.assertIn("a.echo", labels)
            win.startup.setCurrentIndex(labels.index("a.echo"))
            win.a_run.trigger()
            # программы идут во встроенный терминал — вкладка «▶ a.echo»
            self.assertTrue(spin(5, lambda: win.terminal.current() is not None
                                 and b"echo-ran a.echo" in win.terminal.current()._captured))
            self.assertEqual(win.terminal.current().title, "▶ echo")      # RunCommand без title — по имени программы
            dialog = NewProjectDialog(win, toolchains.TOOLCHAINS, str(root))
            self.assertIn("Echo hello", [dialog.list.item(i).text() for i in range(dialog.list.count())])
            dialog.deleteLater()
        finally:
            toolchains.unregister(echo)
            win.jedi.stop()
            win.csharp_index.stop()
            for svc in win.language_services:
                svc.stop()
            win.close()


if __name__ == "__main__":
    unittest.main()
