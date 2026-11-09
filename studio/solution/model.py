"""Модель решения для обозревателя: решение → проекты → зависимости и файлы. Только данные, без Qt."""
import os

# виды зависимостей — как группы в узле «Зависимости» Visual Studio (порядок показа)
FRAMEWORK, PACKAGE, PROJECT, ASSEMBLY, ANALYZER = "framework", "package", "project", "assembly", "analyzer"
DEPENDENCY_KINDS = (FRAMEWORK, PACKAGE, PROJECT, ASSEMBLY, ANALYZER)


class Dependency:
    __slots__ = ("kind", "name", "version", "path")

    def __init__(self, kind, name, version="", path=None):
        self.kind = kind
        self.name = name
        self.version = version or ""
        self.path = path            # файл проекта (для ссылки на проект) или сборки; None — не файл

    def label(self):
        return "%s (%s)" % (self.name, self.version) if self.version else self.name

    def __repr__(self):
        return "Dependency(%r, %r, %r)" % (self.kind, self.name, self.version)


class Project:
    """Проект решения. files — явный список файлов (как в .csproj Unity) или None: тогда в проект входит
    всё, что лежит в его папке (SDK-проекты .NET, Python), — обозреватель покажет папку."""

    def __init__(self, name, path, kind, folder=None, files=None, dependencies=None, reader=""):
        self.name = name
        self.path = path            # файл проекта (.csproj, pyproject.toml…) или папка
        self.kind = kind            # "csharp", "unity", "python" — значок и подпись
        self.folder = folder or (path if os.path.isdir(path) else os.path.dirname(path))
        self.files = files
        self.dependencies = list(dependencies or [])
        self.reader = reader        # кто прочитал (для отладки и тестов)
        self.error = ""             # не удалось прочитать файл проекта — показать, а не молча пропустить

    def dependencies_of(self, kind):
        return [d for d in self.dependencies if d.kind == kind]

    def __repr__(self):
        return "Project(%r, %r)" % (self.name, self.kind)


class Solution:
    def __init__(self, name, path, projects=None, root=None):
        self.name = name
        self.path = path            # .sln/.slnx или папка, если решения как файла нет
        self.root = root or (path if os.path.isdir(path) else os.path.dirname(path))
        self.projects = list(projects or [])

    def project_by_path(self, path):
        real = os.path.realpath(path)
        return next((p for p in self.projects if os.path.realpath(p.path) == real), None)

    def project_of_file(self, path):
        """Проект, которому принадлежит файл (ближайшая папка проекта или явный список)."""
        best = None
        for p in self.projects:
            if p.files is not None:
                if path in p.files:
                    return p
                continue
            if path.startswith(p.folder.rstrip(os.sep) + os.sep) and (best is None or len(p.folder) > len(best.folder)):
                best = p
        return best
