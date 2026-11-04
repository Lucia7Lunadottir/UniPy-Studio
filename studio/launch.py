"""Resolve editor launch requests without depending on Qt."""
from pathlib import Path


def resolve_request(args):
    target = args.file or args.path
    file = str(Path(target).resolve()) if target and Path(target).is_file() else None
    explicit = args.project
    if explicit:
        project = Path(explicit).resolve()
        if project.is_file():
            project = project.parent
        if not project.is_dir():
            raise ValueError("Проект не найден: " + str(project))
        root = str(project)
    elif target:
        path = Path(target).resolve()
        base = path if path.is_dir() else path.parent
        root = str(base)
        # Prefer the Unity root, even when .sln files have not been generated yet.
        for parent in (base, *base.parents):
            if (parent / "Assets").is_dir() and (parent / "ProjectSettings").is_dir():
                root = str(parent)
                break
        else:
            for parent in (base, *base.parents):
                if any(parent.glob("*.sln")) or any(parent.glob("*.slnx")):
                    root = str(parent)
                    break
    else:
        root = None
    if file and Path(file).suffix.lower() in (".sln", ".slnx", ".csproj"):
        file = None
    return dict(root=root, file=file, line=max(1, args.line), column=max(1, args.column))


def apply_request(win, request):
    root = request["root"]
    if root and root != win.root:
        if not win.set_root(root):
            return
    if request["file"]:
        win.open_path(request["file"], request["line"], request["column"])
    win.showNormal()
    win.raise_()
    win.activateWindow()
