import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from pathlib import Path

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication

from studio.panels.common import ROLE_LINE, ROLE_PATH
from studio.solution import load_solution, read_project, read_sln, read_slnx
from studio.theme import C

app = QApplication.instance() or QApplication([])

SLN = '''Microsoft Visual Studio Solution File, Format Version 12.00
Project("{2150E333-8FDC-42A3-9474-1A3956D46DE8}") = "src", "src", "{11111111-1111-1111-1111-111111111111}"
EndProject
Project("{FAE04EC0-301F-11D3-BF4B-00C04F79EFBC}") = "App", "App\\App.csproj", "{22222222-2222-2222-2222-222222222222}"
EndProject
Project("{FAE04EC0-301F-11D3-BF4B-00C04F79EFBC}") = "Lib", "Lib\\Lib.csproj", "{33333333-3333-3333-3333-333333333333}"
EndProject
'''

SDK = '''<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup><OutputType>Exe</OutputType><TargetFrameworks>net8.0;net9.0</TargetFrameworks></PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Newtonsoft.Json" Version="13.0.3" />
    <PackageReference Include="Serilog"><Version>4.0.0</Version></PackageReference>
    <ProjectReference Include="..\\Lib\\Lib.csproj" />
  </ItemGroup>
</Project>'''

UNITY = '''<?xml version="1.0" encoding="utf-8"?>
<Project ToolsVersion="4.0" xmlns="http://schemas.microsoft.com/developer/msbuild/2003">
  <PropertyGroup><TargetFrameworkVersion>v4.7.1</TargetFrameworkVersion></PropertyGroup>
  <ItemGroup>
    <Compile Include="Assets/Scripts/Player.cs" />
    <Compile Include="Assets/Scripts/UI/Menu.cs" />
    <None Include="Assets/Shaders/Water.shader" />
    <Reference Include="UnityEngine"><HintPath>/opt/Unity/UnityEngine.dll</HintPath></Reference>
    <Analyzer Include="/opt/Unity/Unity.SourceGenerators.dll" />
    <ProjectReference Include="Assembly-CSharp-firstpass.csproj"><Name>Assembly-CSharp-firstpass</Name></ProjectReference>
  </ItemGroup>
</Project>'''


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


class Readers(unittest.TestCase):
    def test_sln_slnx(self):
        root = Path(tempfile.mkdtemp())
        sln = write(root / "All.sln", SLN)
        self.assertEqual(read_sln(str(sln)), [str(root / "App" / "App.csproj"), str(root / "Lib" / "Lib.csproj")])
        slnx = write(root / "All.slnx", '<Solution><Folder Name="/src/"><Project Path="App/App.csproj" />'
                                         '</Folder></Solution>')
        self.assertEqual(read_slnx(str(slnx)), [str(root / "App" / "App.csproj")])

    def test_sdk_project(self):
        root = Path(tempfile.mkdtemp())
        p = read_project(str(write(root / "App" / "App.csproj", SDK)))
        self.assertEqual((p.name, p.kind, p.files), ("App", "csharp", None))         # файлы — вся папка
        self.assertEqual([d.label() for d in p.dependencies_of("framework")], ["net8.0", "net9.0"])
        self.assertEqual([d.label() for d in p.dependencies_of("package")],
                         ["Newtonsoft.Json (13.0.3)", "Serilog (4.0.0)"])
        self.assertEqual([d.path for d in p.dependencies_of("project")], [str(root / "Lib" / "Lib.csproj")])

    def test_unity_project_lists_files(self):
        root = Path(tempfile.mkdtemp())
        p = read_project(str(write(root / "Assembly-CSharp.csproj", UNITY)))
        self.assertEqual(p.kind, "unity")
        self.assertEqual(p.files, sorted(str(root / f) for f in (
            "Assets/Scripts/Player.cs", "Assets/Scripts/UI/Menu.cs", "Assets/Shaders/Water.shader")))
        self.assertEqual([d.name for d in p.dependencies_of("assembly")], ["UnityEngine"])
        self.assertEqual([d.name for d in p.dependencies_of("analyzer")], ["Unity.SourceGenerators"])
        self.assertEqual([d.name for d in p.dependencies_of("project")], ["Assembly-CSharp-firstpass"])
        broken = read_project(str(write(root / "Broken.csproj", "<Project>")))
        self.assertTrue(broken.error)                                   # сломанный — виден, а не пропал

    def test_python_project(self):
        root = Path(tempfile.mkdtemp())
        write(root / "pyproject.toml", '[project]\nname = "demo-app"\ndependencies = ["requests>=2"]\n')
        p = read_project(str(root / "pyproject.toml"))
        self.assertEqual((p.name, p.kind, p.files), ("demo-app", "python", None))
        self.assertEqual([d.label() for d in p.dependencies_of("package")], ["requests (>=2)"])


class Discovery(unittest.TestCase):
    def test_solution_file_wins_and_python_next_to_it(self):
        root = Path(tempfile.mkdtemp())
        write(root / "All.sln", SLN)
        write(root / "App" / "App.csproj", SDK)
        write(root / "Lib" / "Lib.csproj", '<Project Sdk="Microsoft.NET.Sdk" />')
        write(root / "Other" / "Other.csproj", '<Project Sdk="Microsoft.NET.Sdk" />')     # не в решении
        write(root / "tools" / "requirements.txt", "jedi\n")
        s = load_solution(str(root))
        self.assertEqual(s.name, "All")
        self.assertEqual([p.name for p in s.projects], ["App", "Lib"])     # только из .sln
        write(root / "requirements.txt", "jedi\n")
        self.assertIn("python", [p.kind for p in load_solution(str(root)).projects])
        self.assertEqual(s.project_of_file(str(root / "App" / "Program.cs")).name, "App")

    def test_without_solution(self):
        root = Path(tempfile.mkdtemp())
        self.assertIsNone(load_solution(str(root)))                     # пусто — обозреватель покажет папку
        write(root / "App" / "App.csproj", SDK)
        write(root / "scripts" / "pyproject.toml", '[project]\nname = "scripts"\n')
        write(root / "scripts" / "pkg" / "setup.py", "")                 # внутри проекта — не отдельный
        self.assertEqual(sorted(p.name for p in load_solution(str(root)).projects), ["App", "scripts"])


class Explorer(unittest.TestCase):
    def test_tree_projects_dependencies_and_files(self):
        from studio.mainwindow import MainWindow
        root = Path(tempfile.mkdtemp())
        write(root / "Game.sln", 'Project("{FAE04EC0-301F-11D3-BF4B-00C04F79EFBC}") = "Assembly-CSharp", '
                                 '"Assembly-CSharp.csproj", "{1}"\nEndProject\n'
                                 'Project("{FAE04EC0-301F-11D3-BF4B-00C04F79EFBC}") = "Assembly-CSharp-firstpass", '
                                 '"Assembly-CSharp-firstpass.csproj", "{2}"\nEndProject\n')
        write(root / "Assembly-CSharp.csproj", UNITY)
        write(root / "Assembly-CSharp-firstpass.csproj", UNITY.replace("Player", "Plugin"))
        write(root / "Assets" / "Scripts" / "Player.cs", "class Player {}")
        write(root / "Assets" / "Scripts" / "UI" / "Menu.cs", "class Menu {}")
        win = MainWindow(screenshot=True)
        self.addCleanup(lambda: (win.jedi.stop(), win.csharp_index.stop(), win.close()))
        win.set_root(str(root))
        ex = win.explorer
        top = ex.tree.topLevelItem(0)
        self.assertIn("Game", top.text(0))
        main = top.child(0)
        self.assertEqual((main.text(0), main.data(0, ROLE_LINE)), ("Assembly-CSharp", "project"))
        deps = main.child(0)
        self.assertEqual(deps.data(0, ROLE_LINE), "deps")
        groups = [deps.child(i).text(0) for i in range(deps.childCount())]
        self.assertEqual(len(groups), 4)                                # платформа, проекты, сборки, анализаторы
        self.assertIn(str(root / "Assets" / "Scripts" / "UI" / "Menu.cs"), ex.files)     # Ctrl+P видит файлы
        self.assertEqual(len(ex.files), len(set(ex.files)))             # общий файл двух проектов — один раз
        water = str(root / "Assets" / "Shaders" / "Water.shader")       # в проекте есть, на диске нет — помечен
        self.assertIn(water, ex.files)
        found = []

        def walk(it):
            if it.data(0, ROLE_PATH) == water:
                found.append(it)
            for i in range(it.childCount()):
                walk(it.child(i))
        walk(top)
        self.assertEqual(found[0].foreground(0).color().name(), QColor(C["warning"]).name())
        project_dep = next(deps.child(i) for i in range(deps.childCount()) if deps.child(i).data(0, ROLE_PATH).endswith("::project")).child(0)
        ex._activated(project_dep, 0)                                   # двойной щелчок по ссылке на проект
        self.assertEqual(ex.tree.currentItem().text(0), "Assembly-CSharp-firstpass")
        ex.show_all.setChecked(True)                                    # «Показать все файлы» — папка
        ex.refresh()
        kinds = {ex.tree.topLevelItem(0).child(i).data(0, ROLE_LINE) for i in range(ex.tree.topLevelItem(0).childCount())}
        self.assertNotIn("project", kinds)

    def test_startup_project_from_explorer(self):
        from studio.mainwindow import MainWindow
        root = Path(tempfile.mkdtemp())
        write(root / "Apps.sln", SLN)
        write(root / "App" / "App.csproj", SDK)
        write(root / "Lib" / "Lib.csproj", '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe'
                                           '</OutputType></PropertyGroup></Project>')
        win = MainWindow(screenshot=True)
        self.addCleanup(lambda: (win.jedi.stop(), win.csharp_index.stop(), win.close()))
        win.set_root(str(root))
        lib = str(root / "Lib" / "Lib.csproj")
        self.assertTrue(win.explorer.can_start(lib))
        win.explorer.set_startup.emit(lib)
        self.assertTrue(win.startup.currentData().endswith(lib))


if __name__ == "__main__":
    unittest.main()
