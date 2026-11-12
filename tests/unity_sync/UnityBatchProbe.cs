// Copy to Assets/Editor in an isolated Unity project with com.unipy.ide installed.
// Execute with -executeMethod UnityBatchProbe.Run. Never run in a production project.
using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Xml.Linq;
using UniPy.UnityIntegration;
using UnityEngine;

public static class UnityBatchProbe
{
    public static void Run()
    {
        var root = Path.GetDirectoryName(Application.dataPath);
        var editor = new UniPyEditor();
        editor.SyncAll(); // Real Visual Studio package, CompilationPipeline and filesystem.
        var project = Path.Combine(root, "Assembly-CSharp.csproj");
        var xml = XDocument.Load(project);
        if (!xml.Descendants().Any(e => e.Name.LocalName == "Compile" &&
            (string)e.Attribute("Include") == "Assets/Probe.cs"))
            throw new Exception("Generated project does not include Probe.cs");
        if (!xml.Descendants().Any(e => e.Name.LocalName == "HintPath" &&
            e.Value.Contains("UnityEngine.CoreModule.dll") && File.Exists(e.Value)))
            throw new Exception("Missing real Unity reference");
        var solution = Directory.GetFiles(root, "*.sln").Single();
        if (!File.ReadAllText(solution).Contains("Assembly-CSharp.csproj"))
            throw new Exception("Solution does not reference the project");

        var stamp = Path.Combine(root, "Library/UniPy/project-generation.sha256");
        var current = File.ReadAllText(stamp);
        var ensure = typeof(UniPyEditor).GetMethod("EnsureSolutionCurrent", BindingFlags.NonPublic | BindingFlags.Instance);
        var written = File.GetLastWriteTimeUtc(stamp);
        ensure.Invoke(editor, null);
        if (written != File.GetLastWriteTimeUtc(stamp)) throw new Exception("Unchanged solution regenerated");
        File.WriteAllText(stamp, "old Unity/package state");
        ensure.Invoke(editor, null);
        if (File.ReadAllText(stamp) != current) throw new Exception("Stale state was not refreshed");
        File.WriteAllText(Path.Combine(root, "probe-passed.txt"), "Real Unity generation, references and refresh passed");
        Debug.Log("UNIPY_BATCH_PROBE_PASS");
    }
}
