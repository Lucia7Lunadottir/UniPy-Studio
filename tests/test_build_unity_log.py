import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import time
import unittest
from pathlib import Path

from PyQt6.QtWidgets import QApplication

from studio import build
from studio.unity_log import UnityLogParser, UnityLogWatcher, classify_block, log_path_for, ERROR, WARNING, LOG, INFO

app = QApplication.instance() or QApplication([])


def spin(seconds, until=None):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
        if until and until():
            return True
    return False


class MSBuildLines(unittest.TestCase):
    def test_canonical_and_origin_lines(self):
        d = build.parse_msbuild_line(
            "/p/Assets/A.cs(12,5): error CS0103: The name 'x' does not exist [/p/Assembly-CSharp.csproj]")
        self.assertEqual((d.path, d.line, d.col, d.severity, d.code, d.project),
                         ("/p/Assets/A.cs", 12, 5, "error", "CS0103", "Assembly-CSharp"))
        self.assertEqual(d.text(), "CS0103: The name 'x' does not exist")
        rel = build.parse_msbuild_line(r"Assets\B.cs(3,1,3,9): warning CS0168: unused", "/p")
        self.assertEqual((rel.path, rel.severity), ("/p/Assets/B.cs", "warning"))
        origin = build.parse_msbuild_line("MSBUILD : error MSB1009: Project file does not exist.")
        self.assertEqual((origin.code, origin.severity, origin.path), ("MSB1009", "error", ""))
        self.assertIsNone(build.parse_msbuild_line("  Assembly-CSharp -> /p/bin/Assembly-CSharp.dll"))
        self.assertIsNone(build.parse_msbuild_line("Restore complete (0.4s)"))

    def test_targets_and_project_for_file(self):
        root = Path(tempfile.mkdtemp())
        (root / "ProjectSettings").mkdir()
        (root / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 6000.6.4f1\n")
        (root / "Game.sln").write_text("")
        (root / "Assets").mkdir()
        (root / "Assembly-CSharp.csproj").write_text(
            '<Project><ItemGroup><Compile Include="Assets\\Player.cs" /></ItemGroup></Project>')
        (root / "Editor.csproj").write_text(
            '<Project><ItemGroup><Compile Include="Assets\\Editor\\Tool.cs" /></ItemGroup></Project>')
        self.assertEqual(build.dotnet_target(str(root)), str(root / "Game.sln"))
        self.assertTrue(build.is_unity_folder(str(root)))
        self.assertEqual(build.project_for_file(str(root / "Assets" / "Player.cs"), str(root)),
                         str(root / "Assembly-CSharp.csproj"))
        self.assertEqual(build.project_for_file(str(root / "Assets" / "Editor" / "Tool.cs"), str(root)),
                         str(root / "Editor.csproj"))
        self.assertIsNone(build.project_for_file(str(root / "Assets" / "Other.cs"), str(root)))
        parent = Path(tempfile.mkdtemp())
        (parent / "Game").mkdir()
        os.rename(root, parent / "Game" / "Unity")
        self.assertIsNone(build.dotnet_target(str(parent)))      # глубже одного уровня не ищем

    def test_redirect_targets_go_to_cache(self):
        cache = tempfile.mkdtemp()
        path = build.write_redirect_targets(cache)
        text = Path(path).read_text()
        self.assertIn(cache + "/obj/$(MSBuildProjectName)/", text)
        self.assertEqual(build.write_redirect_targets(cache), path)
        job = build.DotnetBuild("/p/Game.sln", use_cache=True)
        self.assertTrue(any(a.startswith("-p:CustomBeforeMicrosoftCommonTargets=") for a in job.arguments()))
        self.assertIn("-p:UseSharedCompilation=false", job.arguments())
        self.assertIn("-t:Rebuild", build.DotnetBuild("/p/Game.sln", "rebuild").arguments())
        self.assertEqual(build.DotnetBuild("/p/Game.sln", "clean").arguments()[0], "clean")


class PythonCheckJob(unittest.TestCase):
    def test_background_check_uses_open_buffers(self):
        root = Path(tempfile.mkdtemp())
        good, bad = root / "good.py", root / "bad.py"
        good.write_text("x = 1\n")
        bad.write_text("def f(:\n")
        job = build.PythonCheck([str(good), str(bad)], {str(good): "def g(:\n"})    # во вкладке — с ошибкой
        found, done = [], []
        job.diagnostic.connect(found.append)
        job.finished.connect(done.append)
        job.start()
        self.assertTrue(spin(5, lambda: done))
        self.assertEqual(sorted(os.path.basename(d.path) for d in found), ["bad.py", "good.py"])
        self.assertEqual(done[0]["errors"], 2)


UNITY_LOG = """Initialize engine version
[Licensing::Client] Successfully resolved entitlement details
Player started
UnityEngine.Debug:ExtractStackTraceNoAlloc (byte*,int,string)
UnityEngine.StackTraceUtility:ExtractStackTrace () (at /home/bokken/StackTrace.cs:35)
UnityEngine.DebugLogHandler:Internal_Log (UnityEngine.LogType,UnityEngine.LogOption,string,UnityEngine.Object)
UnityEngine.Logger:Log (UnityEngine.LogType,object)
UnityEngine.Debug:Log (object)
Player:Start () (at Assets/Scripts/Player.cs:12)

NullReferenceException: Object reference not set to an instance of an object
Enemy.Update () (at Assets/Scripts/Enemy.cs:40)

[ScriptCompilation] Requested script compilation because: Assets changed
Assets/Scripts/Player.cs(7,9): error CS0103: The name 'speed' does not exist in the current context
Assets/Scripts/Player.cs(9,13): warning CS0168: The variable 'e' is declared but never used

"""


class UnityLog(unittest.TestCase):
    def test_parser_kinds_locations_and_compile(self):
        parser = UnityLogParser()
        events = parser.feed(UNITY_LOG[:200]) + parser.feed(UNITY_LOG[200:]) + parser.flush()
        entries = [e for e in events if e != "compile_started"]
        self.assertIn("compile_started", events)
        log = next(e for e in entries if e.kind == LOG)
        self.assertEqual((log.message, log.path, log.line), ("Player started", "Assets/Scripts/Player.cs", 12))
        self.assertTrue(any(e.kind == INFO and "Licensing" in e.message for e in entries))
        exc = next(e for e in entries if e.kind == ERROR and not e.compile)
        self.assertEqual((exc.path, exc.line), ("Assets/Scripts/Enemy.cs", 40))
        compile_ = [e for e in entries if e.compile]
        self.assertEqual([(e.kind, e.line, e.col, e.code) for e in compile_],
                         [(ERROR, 7, 9, "CS0103"), (WARNING, 9, 13, "CS0168")])

    def test_warning_from_package_cache(self):
        block = ["Disabling TAA", "UnityEngine.Debug:LogWarning (object)",
                 "TemporalAA:Validate () (at ./Library/PackageCache/com.unity.urp@1/Runtime/TAA.cs:55)"]
        entry = classify_block(block)[0]
        self.assertEqual((entry.kind, entry.path, entry.line), (WARNING, "Library/PackageCache/com.unity.urp@1/Runtime/TAA.cs", 55))

    def test_watcher_tail_append_and_rotation(self):
        project = Path(tempfile.mkdtemp())
        (project / "Logs").mkdir()
        log = project / "Logs" / "Editor.log"
        log.write_text("old line\n\n")
        self.assertEqual(log_path_for(str(project)), str(log))
        watcher = UnityLogWatcher()
        got, resets = [], []
        watcher.events.connect(lambda ev: got.extend(ev))
        watcher.reset.connect(lambda: resets.append(1))
        watcher.watch(str(log))
        got.clear()
        with open(log, "a") as f:
            f.write(UNITY_LOG)
        self.assertTrue(spin(5, lambda: any(getattr(e, "compile", False) for e in got)))
        os.rename(log, project / "Logs" / "Editor-prev.log")           # Unity перезапущен
        log.write_text("New session\nUnityEngine.Debug:Log (object)\nA:B () (at Assets/A.cs:1)\n\n")
        self.assertTrue(spin(5, lambda: resets))
        self.assertTrue(spin(5, lambda: any(getattr(e, "message", "") == "New session" for e in got)))
        watcher.stop()


if __name__ == "__main__":
    unittest.main()
