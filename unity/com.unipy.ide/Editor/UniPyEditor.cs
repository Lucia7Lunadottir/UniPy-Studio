using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
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
        private ProjectGeneration Generator => generator ?? (generator = new ProjectGeneration());

        static UniPyEditor() { CodeEditor.Register(new UniPyEditor()); }

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

        public void Initialize(string path) { executable = path; }

        public void OnGUI()
        {
            EditorGUILayout.HelpBox("UniPy Studio: project, file, line and column are passed automatically.", MessageType.Info);
            if (GUILayout.Button("Regenerate project files")) SyncAll();
        }

        public void SyncAll() { Generator.Sync(); }

        public void SyncIfNeeded(string[] added, string[] deleted, string[] moved, string[] movedFrom, string[] imported)
        {
            Generator.SyncIfNeeded(added.Concat(deleted).Concat(moved).Concat(movedFrom), imported);
        }

        public bool OpenProject(string filePath = "", int line = -1, int column = -1)
        {
            // Let Unity handle scenes, images and other binary assets itself.
            if (!string.IsNullOrEmpty(filePath) && !Generator.IsSupportedFile(filePath)) return false;
            var path = executable ?? CodeEditor.CurrentEditorPath;
            if (!File.Exists(path)) return false;
            try
            {
                // Like Visual Studio: generate the solution only when it's missing. Unity keeps
                // it up to date through SyncIfNeeded; regenerating everything on each double click
                // was slow and made the analyzer reload every project.
                if (!File.Exists(Generator.SolutionFile())) SyncAll();
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
