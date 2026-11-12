using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using Unity.CodeEditor;
using UnityEditor;
using UnityEngine;
using Microsoft.Unity.VisualStudio.Editor;

namespace UniPy.UnityIntegration
{
    [InitializeOnLoad]
    public sealed class UniPyEditor : IExternalCodeEditor
    {
        private string executable;
        private ProjectGeneration generator;
        private ProjectGeneration Generator => generator ?? (generator = CreateGenerator());

        private static ProjectGeneration CreateGenerator()
        {
            // Recent VS Editor packages retain the public base class for compatibility,
            // but its header/footer methods are empty. The working implementations are
            // internal. Keep the legacy format used by UniPy's solution reader.
            var legacy = typeof(ProjectGeneration).Assembly.GetType(
                "Microsoft.Unity.VisualStudio.Editor.LegacyStyleProjectGeneration");
            return legacy != null
                ? (ProjectGeneration)Activator.CreateInstance(legacy, nonPublic: true)
                : new ProjectGeneration(); // Older packages implement generation in the base class.
        }

        static UniPyEditor()
        {
            var editor = new UniPyEditor();
            CodeEditor.Register(editor);
            UnityEditor.PackageManager.Events.registeredPackages += _ => editor.QueueSyncCheck();
            editor.QueueSyncCheck();
        }

        private string StatePath => Path.Combine(Directory.GetParent(Application.dataPath).FullName,
            "Library", "UniPy", "project-generation.sha256");

        private string GenerationState()
        {
            var root = Directory.GetParent(Application.dataPath).FullName;
            var state = new StringBuilder("unipy-project-generation-v2\n");
            state.AppendLine(Application.unityVersion).AppendLine(EditorApplication.applicationPath);
            foreach (var name in new[] { "manifest.json", "packages-lock.json" })
            {
                var path = Path.Combine(root, "Packages", name);
                state.AppendLine(name).AppendLine(File.Exists(path) ? File.ReadAllText(path) : "<missing>");
            }
            // Include resolved paths: a Git/local package or its cache can change without
            // changing the version in package.json. Sort to ignore enumeration order.
            foreach (var package in UnityEditor.PackageManager.PackageInfo.GetAllRegisteredPackages()
                .OrderBy(p => p.name, StringComparer.Ordinal))
                state.AppendLine(package.name).AppendLine(package.packageId).AppendLine(package.resolvedPath);
            using (var hash = SHA256.Create())
                return Convert.ToBase64String(hash.ComputeHash(Encoding.UTF8.GetBytes(state.ToString())));
        }

        private void QueueSyncCheck()
        {
            EditorApplication.update -= CheckWhenReady;
            EditorApplication.update += CheckWhenReady;
        }

        private void CheckWhenReady()
        {
            if (EditorApplication.isCompiling || EditorApplication.isUpdating) return;
            EditorApplication.update -= CheckWhenReady;
            if (!ReferenceEquals(CodeEditor.CurrentEditor, this)) return;
            try { EnsureSolutionCurrent(); }
            catch (Exception exception)
            {
                // Leave the old stamp intact so opening a script or the next package event retries.
                Debug.LogWarning("UniPy Studio: could not refresh project files: " + exception.Message);
            }
        }

        private void EnsureSolutionCurrent()
        {
            var state = GenerationState();
            if (!File.Exists(Generator.SolutionFile()) || !File.Exists(StatePath) ||
                File.ReadAllText(StatePath) != state)
                SyncAll();
        }

        public CodeEditor.Installation[] Installations
        {
            get
            {
                var paths = new List<string> { "/usr/bin/unipy-studio", "/usr/local/bin/unipy-studio" };
                var env = Environment.GetEnvironmentVariable("PATH") ?? "";
                paths.AddRange(env.Split(Path.PathSeparator).Where(p => !string.IsNullOrEmpty(p))
                    .Select(p => Path.Combine(p, "unipy-studio")));
                return paths.Distinct().Where(File.Exists).Select(p => new CodeEditor.Installation
                { Name = "UniPy Studio", Path = p }).ToArray();
            }
        }

        public bool TryGetInstallationForPath(string path, out CodeEditor.Installation installation)
        {
            installation = default;
            if (string.IsNullOrEmpty(path) || !File.Exists(path) ||
                Path.GetFileName(path) != "unipy-studio") return false;
            installation = new CodeEditor.Installation { Name = "UniPy Studio", Path = path };
            return true;
        }

        public void Initialize(string path)
        {
            executable = path;
            QueueSyncCheck();
        }

        public void OnGUI()
        {
            EditorGUILayout.HelpBox("UniPy Studio: project, file, line and column are passed automatically.", MessageType.Info);
            if (GUILayout.Button("Regenerate project files")) SyncAll();
        }

        public void SyncAll()
        {
            var state = GenerationState();
            Generator.Sync();
            // Never mark a failed/missing generation as current.
            if (!File.Exists(Generator.SolutionFile())) return;
            Directory.CreateDirectory(Path.GetDirectoryName(StatePath));
            File.WriteAllText(StatePath, state);
        }

        public void SyncIfNeeded(string[] added, string[] deleted, string[] moved, string[] movedFrom, string[] imported)
        {
            Generator.SyncIfNeeded(added.Concat(deleted).Concat(moved).Concat(movedFrom), imported);
            QueueSyncCheck();
        }

        public bool OpenProject(string filePath = "", int line = -1, int column = -1)
        {
            // Let Unity handle scenes, images and other binary assets itself.
            if (!string.IsNullOrEmpty(filePath) && !Generator.IsSupportedFile(filePath)) return false;
            var path = executable ?? CodeEditor.CurrentEditorPath;
            if (!File.Exists(path)) return false;
            try
            {
                EnsureSolutionCurrent();
                var root = Directory.GetParent(Application.dataPath).FullName;
                var args = "--project " + CodeEditor.QuoteForProcessStart(root);
                if (!string.IsNullOrEmpty(filePath))
                    args += " --file " + CodeEditor.QuoteForProcessStart(Path.GetFullPath(filePath));
                args += " --line " + Math.Max(1, line) + " --column " + Math.Max(1, column);
                return CodeEditor.OSOpenFile(path, args);
            }
            catch (Exception exception)
            {
                Debug.LogError("UniPy Studio: " + exception);
                return false;
            }
        }
    }
}
