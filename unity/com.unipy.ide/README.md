# UniPy Studio for Unity

Linux / Unity 2021.3+ (including Unity 6).

1. Install the UniPy Studio OS package.
2. In each Unity project: Window → Package Manager → + → Install package from git URL… and paste
   `https://github.com/Lucia7Lunadottir/UniPy-Studio.git?path=/unity/com.unipy.ide`
   (needs `git`; append `#<release tag>` to pin a version). Offline: + → Install package from disk…
   (older versions: Add package from disk…) and this directory's `package.json` — with the Arch
   package it is `/usr/share/unipy-studio/unity/com.unipy.ide/package.json`.
3. Wait for compilation, then choose **UniPy Studio** in Preferences → External Tools → External Script Editor.

The editor is discovered from `/usr/bin`, `/usr/local/bin` and PATH. No Browse is
needed after installing this integration package. The package must be added to
each project (or included in your Unity project template); installing a Linux
.desktop file alone cannot register an IExternalCodeEditor inside Unity.

Double-clicking a script opens the Unity project root and the script. Console
locations also pass line/column. Subsequent opens reuse the project's window;
separate projects have separate windows. Solution and C# project files are
maintained by the official `com.unity.ide.visualstudio` project's public
`ProjectGeneration` API; installing Visual Studio itself is unnecessary.

Without this integration, select `/usr/bin/unipy-studio` using Browse and set
External Script Editor Args to:

```
--project $(ProjectPath) --file $(File) --line $(Line) --column $(Column)
```

Unity quotes these placeholders itself. This fallback opens the correct project
and position, but does not register UniPy in the discovery list or provide
project generation callbacks. The integration package is recommended.
