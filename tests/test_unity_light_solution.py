import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from studio import unity_light_solution as light_mod

SLN = '''Microsoft Visual Studio Solution File, Format Version 11.00
Project("{FAE04EC0-301F-11D3-BF4B-00C04F79EFBC}") = "Assembly-CSharp", "Assembly-CSharp.csproj", "{1}"
EndProject
Project("{FAE04EC0-301F-11D3-BF4B-00C04F79EFBC}") = "Unity.RenderPipelines.Universal.Runtime", "Unity.RenderPipelines.Universal.Runtime.csproj", "{2}"
EndProject
Project("{FAE04EC0-301F-11D3-BF4B-00C04F79EFBC}") = "PG.Menu", "PG.Menu.csproj", "{3}"
EndProject
'''


def csproj(files, refs=()):
    items = "".join('<Compile Include="%s" />' % f for f in files)
    items += "".join('<ProjectReference Include="%s"><Name>%s</Name></ProjectReference>' % (r, r[:-7]) for r in refs)
    return '<Project ToolsVersion="4.0"><ItemGroup>%s</ItemGroup></Project>' % items


class LightSolution(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp()) / "Game"
        (self.root / "Library" / "ScriptAssemblies").mkdir(parents=True)
        (self.root / "Library" / "ScriptAssemblies" / "Unity.RenderPipelines.Universal.Runtime.dll").write_text("")
        (self.root / "Game.sln").write_text(SLN)
        (self.root / "Assembly-CSharp.csproj").write_text(csproj(
            ["Assets/Player.cs"], ["Unity.RenderPipelines.Universal.Runtime.csproj", "PG.Menu.csproj"]))
        # пакет из кэша, в который .asmref из Assets встроил один свой файл
        (self.root / "Unity.RenderPipelines.Universal.Runtime.csproj").write_text(csproj(
            ["Library/PackageCache/com.unity.urp@1/A.cs", "Library/PackageCache/com.unity.urp@1/B.cs",
             "Assets/Plugins/Fog/FogFeature.cs"]))
        (self.root / "PG.Menu.csproj").write_text(csproj(["Packages/com.pg.menu/Menu.cs"]))     # свой пакет
        patcher = mock.patch.object(light_mod, "CACHE", tempfile.mkdtemp())
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_package_projects_become_assemblies(self):
        light, changed = light_mod.build(str(self.root), str(self.root / "Game.sln"))
        self.assertTrue(light.startswith(light_mod.CACHE))               # файлы Unity не трогаются
        sln = Path(light).read_text()
        self.assertNotIn("Universal.Runtime", sln)
        self.assertIn("PG.Menu.csproj", sln)                             # свой код — проектом
        main = (Path(light).parent / "Assembly-CSharp.csproj").read_text()
        self.assertIn(str(self.root / "Library" / "ScriptAssemblies" / "Unity.RenderPipelines.Universal.Runtime.dll"), main)
        self.assertIn('<ProjectReference Include="PG.Menu.csproj">', main)
        self.assertIn('Include="%s"' % (self.root / "Assets" / "Player.cs"), main)     # пути — абсолютные
        self.assertEqual(len(changed), 3)
        self.assertEqual(light_mod.build(str(self.root), str(self.root / "Game.sln"))[1], [])   # без перезаписи

    def test_nothing_to_lighten_or_dll_missing(self):
        os.remove(self.root / "Library" / "ScriptAssemblies" / "Unity.RenderPipelines.Universal.Runtime.dll")
        self.assertEqual(light_mod.build(str(self.root), str(self.root / "Game.sln")), (None, []))  # Unity не собрал


if __name__ == "__main__":
    unittest.main()
