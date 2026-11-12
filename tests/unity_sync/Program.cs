// Runs the real integration against a small Unity API double. No Unity or NuGet required.
// dotnet run --project tests/unity_sync
using System;
using System.IO;
using UniPy.UnityIntegration;
using UnityEditor;
using UnityEditor.PackageManager;
using UnityEngine;
using Microsoft.Unity.VisualStudio.Editor;

static class Program
{
    static void Expect(int count, string scenario)
    {
        if (ProjectGeneration.Count != count)
            throw new Exception(scenario + ": expected " + count + ", got " + ProjectGeneration.Count);
    }

    static void Main()
    {
        var root = Path.Combine(Path.GetTempPath(), "unipy-sync-" + Guid.NewGuid());
        Directory.CreateDirectory(Path.Combine(root, "Assets"));
        Directory.CreateDirectory(Path.Combine(root, "Packages"));
        Application.dataPath = Path.Combine(root, "Assets");
        var executable = Path.Combine(root, "unipy-studio");
        File.WriteAllText(executable, "");
        try
        {
            // Initialize the real static registration; the registered instance is the active editor.
            new UniPyEditor();
            var editor = (UniPyEditor)Unity.CodeEditor.CodeEditor.CurrentEditor;
            editor.Initialize(executable);
            EditorApplication.isUpdating = true;
            EditorApplication.Tick(); Expect(0, "wait for import");
            EditorApplication.isUpdating = false;
            EditorApplication.isCompiling = true;
            EditorApplication.Tick(); Expect(0, "wait for compilation");
            EditorApplication.isCompiling = false;
            EditorApplication.Tick(); Expect(1, "first install");
            editor.OpenProject(); editor.Initialize(executable); EditorApplication.Tick();
            Expect(1, "unchanged opens and initialization");

            Application.unityVersion = "6000.6.7f1";
            editor.Initialize(executable); EditorApplication.Tick(); Expect(2, "Unity upgrade");
            EditorApplication.applicationPath = "/new/Unity";
            editor.OpenProject(); Expect(3, "editor relocation before opening");
            File.WriteAllText(Path.Combine(root, "Packages", "manifest.json"), "new manifest");
            Events.Raise(); Events.Raise(); EditorApplication.Tick(); Expect(4, "coalesced manifest change");
            File.WriteAllText(Path.Combine(root, "Packages", "packages-lock.json"), "new lock");
            Events.Raise(); EditorApplication.Tick(); Expect(5, "transitive dependency change");
            PackageInfo.Packages = new[] { new PackageInfo("b", "1", "/cache/b"), new PackageInfo("a", "1", "/cache/a") };
            Events.Raise(); EditorApplication.Tick(); Expect(6, "package addition");
            Array.Reverse(PackageInfo.Packages);
            Events.Raise(); EditorApplication.Tick(); Expect(6, "package order is irrelevant");
            PackageInfo.Packages[0].resolvedPath = "/cache/a-new-hash";
            Events.Raise(); EditorApplication.Tick(); Expect(7, "same version, new cache path");
            PackageInfo.Packages = Array.Empty<PackageInfo>();
            Events.Raise(); EditorApplication.Tick(); Expect(8, "package removal");

            Unity.CodeEditor.CodeEditor.CurrentEditor = null;
            Application.unityVersion = "next";
            Events.Raise(); EditorApplication.Tick(); Expect(8, "inactive integration");
            Unity.CodeEditor.CodeEditor.CurrentEditor = editor;
            editor.Initialize(executable); EditorApplication.Tick(); Expect(9, "select UniPy again");

            var stamp = Path.Combine(root, "Library", "UniPy", "project-generation.sha256");
            var previous = File.ReadAllText(stamp);
            Application.unityVersion = "failed-upgrade";
            ProjectGeneration.Fail = true;
            Events.Raise(); EditorApplication.Tick(); Expect(10, "failed sync attempted");
            if (File.ReadAllText(stamp) != previous) throw new Exception("Failure saved state");
            EditorApplication.Tick(); Expect(10, "no per-frame failure loop");
            ProjectGeneration.Fail = false;
            if (!editor.OpenProject()) throw new Exception("Retry did not open editor");
            Expect(11, "retry on open");

            // A fresh instance has no memory of the previous generation, like a domain reload.
            var reloaded = new UniPyEditor();
            Unity.CodeEditor.CodeEditor.CurrentEditor = reloaded;
            reloaded.Initialize(executable); EditorApplication.Tick(); Expect(11, "persist across reload");
            File.Delete(ProjectGeneration.Solution);
            reloaded.OpenProject(); Expect(12, "deleted solution");
            Console.WriteLine("PASS: Unity solution refresh scenarios");
        }
        finally { Directory.Delete(root, true); }
    }
}

namespace UnityEngine
{
    static class Application { public static string dataPath; public static string unityVersion = "6000.6.4f1"; }
    static class Debug { public static void LogWarning(object value) { } public static void LogError(object value) { } }
    static class GUILayout { public static bool Button(string label) => false; }
}
namespace UnityEditor
{
    class InitializeOnLoadAttribute : Attribute { }
    enum MessageType { Info }
    static class EditorGUILayout { public static void HelpBox(string text, MessageType type) { } }
    static class EditorApplication
    {
        public static string applicationPath = "/old/Unity";
        public static bool isCompiling, isUpdating;
        public static event Action update;
        public static void Tick() => update?.Invoke();
    }
}
namespace UnityEditor.PackageManager
{
    static class Events { public static event Action<object> registeredPackages; public static void Raise() => registeredPackages?.Invoke(null); }
    class PackageInfo
    {
        public string name, packageId, resolvedPath;
        public PackageInfo(string name, string id, string path) { this.name = name; packageId = id; resolvedPath = path; }
        public static PackageInfo[] Packages = Array.Empty<PackageInfo>();
        public static PackageInfo[] GetAllRegisteredPackages() => Packages;
    }
}
namespace Unity.CodeEditor
{
    public interface IExternalCodeEditor { }
    public static class CodeEditor
    {
        public struct Installation { public string Name, Path; }
        public static IExternalCodeEditor CurrentEditor;
        public static string CurrentEditorPath;
        public static void Register(IExternalCodeEditor editor) => CurrentEditor = editor;
        public static string QuoteForProcessStart(string value) => value;
        public static bool OSOpenFile(string path, string args) => true;
    }
}
namespace Microsoft.Unity.VisualStudio.Editor
{
    class ProjectGeneration
    {
        public static int Count;
        public static bool Fail;
        public static string Solution => Path.Combine(Path.GetDirectoryName(Application.dataPath), "Game.sln");
        public string SolutionFile() => Solution;
        public bool IsSupportedFile(string path) => true;
        public void Sync()
        {
            Count++;
            if (Fail) throw new IOException("Simulated write failure");
            File.WriteAllText(Solution, "generated");
        }
        public void SyncIfNeeded(System.Collections.Generic.IEnumerable<string> paths, string[] imported) { }
    }
}
