"""Устаревшие пути в .csproj от Unity: замена на установленную версию и текущий хэш пакета (unity_references)."""
import os
import tempfile
import unittest
from pathlib import Path

from studio import unity_light_solution as light_mod
from studio.unity_references import ReferenceRetarget


def touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")


class RetargetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.hub = base / "Hub" / "Editor"
        self.root = base / "Game"
        (self.root / "ProjectSettings").mkdir(parents=True)
        (self.root / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 6000.6.7f1\n")
        touch(self.hub / "6000.6.7f1" / "Editor" / "Data" / "Managed" / "UnityEngine" / "UnityEngine.dll")
        touch(self.root / "Library" / "PackageCache" / "com.unity.ugui@newhash" / "Lib" / "X.dll")
        self.retarget = ReferenceRetarget(str(self.root), editor_dirs=[])

    def tearDown(self):
        self.tmp.cleanup()

    def test_removed_editor_version_goes_to_project_version(self):
        old = str(self.hub / "6000.6.4f1" / "Editor" / "Data" / "Managed" / "UnityEngine" / "UnityEngine.dll")
        self.assertEqual(self.retarget.fix(old), old.replace("6000.6.4f1", "6000.6.7f1"))
        self.assertFalse(self.retarget.missing)

    def test_package_with_changed_hash(self):
        old = str(self.root / "Library" / "PackageCache" / "com.unity.ugui@oldhash" / "Lib" / "X.dll")
        self.assertEqual(self.retarget.fix(old), old.replace("oldhash", "newhash"))

    def test_unfixable_is_reported(self):
        gone = str(self.root / "Library" / "PackageCache" / "com.unity.gone@1" / "Y.dll")
        self.assertEqual(self.retarget.fix(gone), gone)
        self.assertEqual(self.retarget.missing, {gone})

    def test_project_text_relative_and_escaped(self):
        text = ('<HintPath>Library/PackageCache/com.unity.ugui@oldhash/Lib/X.dll</HintPath>'
                '<Analyzer Include="%s" />' % (self.hub / "6000.6.4f1/Editor/Data/Managed/UnityEngine/UnityEngine.dll"))
        out = self.retarget.fix_project(text, str(self.root))
        self.assertIn("com.unity.ugui@newhash", out)
        self.assertIn("6000.6.7f1", out)


class LightSolutionReferencesTest(unittest.TestCase):
    """Копия решения лежит в кэше: относительный HintPath оттуда указывал в никуда — Roslyn считал проект
    «с неразрешёнными зависимостями» и молча не показывал ошибки компилятора."""

    def test_relative_hint_paths_become_absolute(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Game"
            (root / "ProjectSettings").mkdir(parents=True)
            (root / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 6000.6.7f1\n")
            touch(root / "Assets" / "Plugins" / "Lib.dll")
            touch(root / "Assets" / "A.cs")
            (root / "Game.sln").write_text('Project("{FAE04EC0-301F-11D3-BF4B-00C04F79EFBC}") = "Assembly-CSharp", '
                                          '"Assembly-CSharp.csproj", "{1}"\nEndProject\n')
            (root / "Assembly-CSharp.csproj").write_text(
                '<Project><ItemGroup><Compile Include="Assets/A.cs" /><Reference Include="Lib">'
                '<HintPath>Assets/Plugins/Lib.dll</HintPath></Reference>'
                '<Reference Include="Old"><HintPath>Library/PackageCache/gone@1/Old.dll</HintPath></Reference>'
                '</ItemGroup></Project>')
            old_cache = light_mod.CACHE
            light_mod.CACHE = str(Path(tmp) / "cache")
            try:
                report = {}
                light, _changed, _plugins = light_mod.build(str(root), str(root / "Game.sln"),
                                                           analyzers=["/x/Microsoft.Unity.Analyzers.dll"], report=report)
            finally:
                light_mod.CACHE = old_cache
            self.assertIsNotNone(light)         # копия нужна ради анализатора — и в ней пути должны жить
            text = (Path(light).parent / "Assembly-CSharp.csproj").read_text()
            self.assertIn("<HintPath>%s</HintPath>" % (root / "Assets" / "Plugins" / "Lib.dll"), text)
            self.assertEqual(report["missing"], [str(root / "Library" / "PackageCache" / "gone@1" / "Old.dll")])


class MissingLibraryMessageTest(unittest.TestCase):
    """Нет папки Library (Unity не открывал проект здесь) — своё понятное сообщение, а не пример «collab-proxy»."""

    def test_message_names_library(self):
        from studio.i18n import tr
        from studio.lsp import CSharpServer
        with tempfile.TemporaryDirectory() as tmp:
            srv = CSharpServer.__new__(CSharpServer)
            srv.root, srv.missing_references = tmp, ["/x/Library/ScriptAssemblies/Unity.TextMeshPro.dll"]
            self.assertEqual(srv.missing_references_text(), tr("lsp.unity_no_library") % (os.path.basename(tmp), 1))
            os.mkdir(os.path.join(tmp, "Library"))
            self.assertIn("Unity.TextMeshPro.dll", srv.missing_references_text())
            srv.missing_references = []
            self.assertEqual(srv.missing_references_text(), "")


class LauncherEditorsTest(unittest.TestCase):
    """Unity Hub закрыт или не установлен: редакторы находятся по настройкам equestria-unity."""

    def test_editor_root_and_hand_located(self):
        from unittest import mock
        from studio import unity_references as refs
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config"
            root = Path(tmp) / "Editors"
            touch(root / "6000.6.7f1" / "Editor" / "Data" / "x")
            elsewhere = Path(tmp) / "custom" / "6000.3.4f1"
            touch(elsewhere / "Editor" / "Data" / "x")
            (config / "equestria-unity").mkdir(parents=True)
            (config / "equestria-unity" / "settings.json").write_text(
                '{"editor_root": "%s", "editors": {"6000.3.4f1": "%s"}}' % (root, elsewhere / "Editor" / "Unity"))
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(config), "HOME": str(Path(tmp) / "home")}):
                found = refs.installed_editors()
            self.assertEqual(found, {"6000.6.7f1": str(root / "6000.6.7f1"), "6000.3.4f1": str(elsewhere)})
