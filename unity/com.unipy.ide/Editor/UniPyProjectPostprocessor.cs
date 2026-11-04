using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security;
using System.Text;
using Unity.CodeEditor;
using UnityEditor;
using UnityEditor.Compilation;

namespace UniPy.UnityIntegration
{
    // The Visual Studio Editor package adds Unity's analyzers and source generators
    // (Unity.SourceGenerators, Unity.Analyzers…, Project Auditor rules) and the C# version
    // Unity compiles with only when Visual Studio itself is the external editor. With UniPy
    // Studio it leaves them out — Roslyn would then report false errors for source-generated
    // code and accept C# that Unity's compiler rejects. Put them back from Unity's own
    // compiler options, the same values the package uses.
    public sealed class UniPyProjectPostprocessor : AssetPostprocessor
    {
        static string OnGeneratedCSProject(string path, string content)
        {
            if (!(CodeEditor.CurrentEditor is UniPyEditor))
                return content;
            try
            {
                var assembly = FindAssembly(Path.GetFileNameWithoutExtension(path));
                return assembly == null ? content : Complete(content, assembly);
            }
            catch (Exception exception)
            {
                UnityEngine.Debug.LogWarning("UniPy Studio: could not add analyzers to " + path + ": " + exception.Message);
                return content;
            }
        }

        static Assembly FindAssembly(string name)
        {
            return CompilationPipeline.GetAssemblies(AssembliesType.Editor).FirstOrDefault(a => a.name == name);
        }

        static string Complete(string content, Assembly assembly)
        {
            var options = assembly.compilerOptions;
            var projectDir = Directory.GetParent(UnityEngine.Application.dataPath).FullName;

            var version = options.LanguageVersion;
            if (!string.IsNullOrEmpty(version))
                content = content.Replace("<LangVersion>latest</LangVersion>", "<LangVersion>" + version + "</LangVersion>");

            var items = new StringBuilder();
            foreach (var analyzer in options.RoslynAnalyzerDllPaths ?? Array.Empty<string>())
                AddItem(items, content, "Analyzer", Full(projectDir, analyzer));
#if UNITY_2022_2_OR_NEWER
            foreach (var file in options.RoslynAdditionalFilePaths ?? Array.Empty<string>())
                AddItem(items, content, "AdditionalFiles", Full(projectDir, file));
            if (!string.IsNullOrEmpty(options.AnalyzerConfigPath))
                AddItem(items, content, "EditorConfigFiles", Full(projectDir, options.AnalyzerConfigPath));
#endif
            var properties = new StringBuilder();
            var ruleset = options.RoslynAnalyzerRulesetPath;
            if (!string.IsNullOrEmpty(ruleset) && !content.Contains("<CodeAnalysisRuleSet>"))
                properties.Append("    <CodeAnalysisRuleSet>").Append(SecurityElement.Escape(Full(projectDir, ruleset)))
                          .Append("</CodeAnalysisRuleSet>\r\n");

            var insert = new StringBuilder();
            if (properties.Length > 0)
                insert.Append("  <PropertyGroup>\r\n").Append(properties).Append("  </PropertyGroup>\r\n");
            if (items.Length > 0)
                insert.Append("  <ItemGroup>\r\n").Append(items).Append("  </ItemGroup>\r\n");
            var end = content.LastIndexOf("</Project>", StringComparison.Ordinal);
            return insert.Length == 0 || end < 0 ? content : content.Insert(end, insert.ToString());
        }

        static void AddItem(StringBuilder items, string content, string kind, string path)
        {
            var escaped = SecurityElement.Escape(path);
            if (content.Contains("Include=\"" + escaped + "\""))
                return;     // already there (e.g. from csc.rsp)
            items.Append("    <").Append(kind).Append(" Include=\"").Append(escaped).Append("\" />\r\n");
        }

        static string Full(string projectDir, string path)
        {
            return Path.GetFullPath(Path.IsPathRooted(path) ? path : Path.Combine(projectDir, path)).Replace('\\', '/');
        }
    }
}
