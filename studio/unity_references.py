"""Устаревшие ссылки в .csproj от Unity — без Qt.

Unity пишет в .csproj абсолютные пути к своим DLL (…/Hub/Editor/<версия>/Editor/Data/…) и к пакетам
(Library/PackageCache/<пакет>@<хэш>/…). После обновления Unity или пакетов эти пути исчезают, а .csproj
перепишет только Unity, и то лишь по просьбе IDE. Что видит человек (проверено, tests/live):
- пропали ВСЕ ссылки (The Mirror: .csproj от 6000.6.4f1, Unity уже 6000.6.7f1, 367 ссылок мимо) — Roslyn
  молча оставляет только синтаксические ошибки: на `int x = "text";` в редакторе пусто;
- пропала часть (DLL пакетов/плагинов) — ложные «тип не найден» (CS0246, CS0012) на рабочем коде.
Строка журнала Roslyn «имеются неразрешённые зависимости» с этим НЕ связана — она бывает и при всех ссылках.

Rider в таком случае просит Unity перегенерировать проекты. Мы чиним копию решения для анализатора
(unity_light_solution): старая версия редактора → версия из ProjectSettings/ProjectVersion.txt, если она
установлена; пакет с прежним хэшем → та же папка пакета с новым хэшем. Чего найти не удалось —
сообщается человеку (missing), а не замалчивается.
"""
import glob
import html
import json
import os
import re

_VERSION_LINE = re.compile(r"^m_EditorVersion:\s*(\S+)", re.M)
# …/<папка редакторов>/<версия>/Editor/Data/… — Unity Hub ставит редакторы так на Linux
_EDITOR_PATH = re.compile(r"^(?P<hub>.*)/(?P<version>\d+\.\d+\.\d+[abfpx]\d+)/Editor/Data/(?P<rest>.*)$")
_PACKAGE_PATH = re.compile(r"^(?P<cache>.*/Library/PackageCache)/(?P<name>[^/@]+)@(?P<hash>[^/]+)/(?P<rest>.*)$")
_PATH_ATTR = re.compile(r'(<HintPath>)([^<]+)(</HintPath>)|(<Analyzer\s+Include=")([^"]+)(")')


def project_editor_version(root):
    """Версия Unity, на которой сейчас открывается проект (ProjectVersion.txt), или None."""
    try:
        with open(os.path.join(root, "ProjectSettings", "ProjectVersion.txt"), encoding="utf-8-sig") as f:
            m = _VERSION_LINE.search(f.read())
    except OSError:
        return None
    return m.group(1) if m else None


def editor_folders():
    """Папки, где Unity Hub держит редакторы (кроме тех, что видны по путям в самих .csproj)."""
    home = os.path.expanduser("~")
    out = [os.path.join(home, "Unity", "Hub", "Editor")]
    # «Installs location» из настроек Unity Hub — строка JSON с путём (папка у Hub на Linux — строчными)
    for hub in ("unityhub", "UnityHub"):
        try:
            with open(os.path.join(home, ".config", hub, "secondaryInstallPath.json"), encoding="utf-8") as f:
                extra = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(extra, str) and extra.strip():
            out.insert(0, os.path.normpath(extra.strip()))
    return out


def installed_editors():
    """{версия: папка редактора} — что установлено в папках Unity Hub."""
    out = {}
    for folder in editor_folders():
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for name in names:
            if re.fullmatch(r"\d+\.\d+\.\d+[abfpx]\d+", name) and os.path.isdir(os.path.join(folder, name, "Editor", "Data")):
                out.setdefault(name, os.path.join(folder, name))
    return out


class ReferenceRetarget:
    """Подставляет живые пути вместо исчезнувших. Один объект — на одно построение решения (кэширует поиски)."""

    def __init__(self, root, editor_dirs=None):
        self.root = os.path.normpath(root)
        self.version = project_editor_version(root)
        self.editor_dirs = list(editor_dirs if editor_dirs is not None else editor_folders())
        self._editor_cache = {}             # папка редакторов из пути → папка нужной версии (или None)
        self._package_cache = {}            # (папка кэша, пакет) → папка пакета с текущим хэшем (или None)
        self.retargeted = 0                 # сколько путей заменено
        self.missing = set()                # пути, для которых замены нет

    def fix(self, path):
        """Живой путь для path (тот же, если файл есть или замены нет)."""
        if os.path.exists(path):
            return path
        new = self._fix_editor(path) or self._fix_package(path)
        if new:
            self.retargeted += 1
            return new
        self.missing.add(path)
        return path

    def _fix_editor(self, path):
        m = _EDITOR_PATH.match(path)
        if not m or not self.version or m.group("version") == self.version:
            return None
        hub = m.group("hub")
        if hub not in self._editor_cache:
            found = None
            for folder in [hub] + self.editor_dirs:
                if os.path.isdir(os.path.join(folder, self.version, "Editor", "Data")):
                    found = os.path.join(folder, self.version)
                    break
            self._editor_cache[hub] = found
        base = self._editor_cache[hub]
        if base is None:
            return None
        new = os.path.join(base, "Editor", "Data", m.group("rest"))
        return new if os.path.exists(new) else None

    def _fix_package(self, path):
        m = _PACKAGE_PATH.match(path)
        if not m:
            return None
        key = (m.group("cache"), m.group("name"))
        if key not in self._package_cache:
            found = sorted(glob.glob(os.path.join(glob.escape(key[0]), glob.escape(key[1]) + "@*")))
            self._package_cache[key] = found[0] if len(found) == 1 else None     # двух версий быть не должно
        base = self._package_cache[key]
        if base is None:
            return None
        new = os.path.join(base, m.group("rest"))
        return new if os.path.exists(new) else None

    def fix_project(self, text, project_dir):
        """Текст .csproj с живыми путями в HintPath и Analyzer (относительные пути — от project_dir)."""
        def repl(m):
            if m.group(1):
                head, value, tail = m.group(1), m.group(2), m.group(3)
            else:
                head, value, tail = m.group(4), m.group(5), m.group(6)
            raw = html.unescape(value).replace("\\", "/")
            path = raw if os.path.isabs(raw) else os.path.normpath(os.path.join(project_dir, raw))
            new = self.fix(path)
            return m.group(0) if new == path else head + html.escape(new, quote=True) + tail
        return _PATH_ATTR.sub(repl, text)

    def needs_fix(self, text, project_dir):
        """Есть ли в проекте исчезнувшие пути, которые можно заменить (тогда нужна копия решения)."""
        before = self.retargeted
        self.fix_project(text, project_dir)
        return self.retargeted > before

    def summary(self):
        """Кратко для журнала: какие версии/пакеты не найдены (не больше пяти примеров)."""
        return sorted(self.missing)[:5]
