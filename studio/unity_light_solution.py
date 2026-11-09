"""Облегчённое решение Unity для анализатора C# — как делает Rider: пакеты Unity не компилируются из исходников.

Плагины в Assets через .asmref встраивают свои скрипты в сборки пакетов (URP, ShaderGraph…), и Unity тогда
генерирует эти сборки целиком — сотни файлов из Library/PackageCache. Roslyn собирал их при каждом запуске:
первая подсказка ждала ~4 минуты (замер на World without Time: 225 с → 8 с, ошибки файла 200+ с → 5 с).
Здесь такой проект («почти весь код — в Library/PackageCache») заменяется готовой сборкой, которую Unity уже
собрал в Library/ScriptAssemblies. Свой код (Assets, Packages) остаётся проектами с исходниками.

Решение и .csproj пишутся в кэш программы с абсолютными путями к файлам проекта: файлы Unity не меняются,
временные файлы Roslyn (obj) тоже остаются в кэше.

Скрипты, которые .asmref встроил в сборку пакета (VolumetricFog в URP и т. п.), получают свой маленький проект
«<пакет>.Plugins»: только эти файлы + готовая сборка пакета. Он компилируется быстро и не тянет исходники пакета.
Unity компилирует их внутри пакета, поэтому им доступно его internal — в отдельном проекте Roslyn сообщил бы
ложные CS0122 («недоступен из-за уровня защиты») и CS0436 (тип есть и в готовой сборке): для этих файлов они
скрываются (plugin_files → RoslynServer.filter_diagnostics). Без Qt.
"""
import hashlib
import os
import re

CACHE = os.path.join(os.path.expanduser("~"), ".cache", "unipy-studio", "roslyn")
_PROJECT_LINE = re.compile(r'^Project\("\{[^}]+\}"\)\s*=\s*"[^"]*",\s*"([^"]+\.csproj)"', re.M)
_ITEM_PATH = re.compile(r'(<(?:Compile|None|Content|AdditionalFiles|EditorConfig|Analyzer)\s+Include)="([^"]+)"')
_PROJECT_REF = re.compile(r'<ProjectReference\s+Include="([^"]+)"\s*(?:/>|>.*?</ProjectReference>)', re.S)
_COMPILE = re.compile(r'<Compile\s+Include="([^"]+)"')
_ASSEMBLY_NAME = re.compile(r'<AssemblyName>([^<]+)</AssemblyName>')
_COMPILE_ITEM = re.compile(r'[ \t]*<Compile\s+Include="([^"]+)"\s*/>[ \t]*\r?\n?')


def _read(path):
    with open(path, encoding="utf-8-sig") as f:
        return f.read()


def _write_if_changed(path, text):
    """Писать, только если содержимое другое: иначе Roslyn увидит «изменение» и перезагрузит проект."""
    try:
        if _read(path) == text:
            return False
    except OSError:
        pass
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return True


def is_package_project(text):
    """Больше половины кода — в Library/PackageCache: это пакет Unity, а не свой код."""
    files = _COMPILE.findall(text)
    return bool(files) and sum(f.replace("\\", "/").startswith("Library/PackageCache/") for f in files) * 2 > len(files)


def cache_dir(root):
    return os.path.join(CACHE, hashlib.sha1(os.path.abspath(root).encode("utf-8", "surrogateescape")).hexdigest()[:16])


PLUGIN_HIDDEN_CODES = {"CS0122", "CS0436"}         # ложные для скриптов, встроенных .asmref в пакет


def build(root, solution):
    """Облегчённое решение для solution (.sln проекта Unity): (путь к нему, [изменённые файлы], {скрипты плагинов})
    или (None, [], set()) — облегчать нечего (тогда анализатор открывает исходное решение)."""
    if not solution or not solution.endswith(".sln"):
        return None, [], set()
    try:
        sln_text = _read(solution)
    except OSError:
        return None, [], set()
    base = os.path.dirname(solution)
    texts, packages = {}, {}
    dlls = os.path.join(root, "Library", "ScriptAssemblies")
    for rel in _PROJECT_LINE.findall(sln_text):
        path = os.path.join(base, rel.replace("\\", os.sep))
        try:
            texts[rel] = _read(path)
        except OSError:
            continue
        if is_package_project(texts[rel]):
            m = _ASSEMBLY_NAME.search(texts[rel])
            name = m.group(1).strip() if m else os.path.splitext(os.path.basename(rel))[0]
            dll = os.path.join(dlls, name + ".dll")
            if os.path.isfile(dll):                 # Unity ещё не собрал — оставить исходники
                packages[rel] = (name, dll)
    if not packages:
        return None, [], set()
    out = cache_dir(root)
    os.makedirs(out, exist_ok=True)
    changed = []

    def absolute(m):
        p = m.group(2)
        return m.group(0) if os.path.isabs(p) else '%s="%s"' % (m.group(1), os.path.join(base, p.replace("\\", os.sep)))

    def reference(m):
        rel = m.group(1)
        if rel not in packages:
            return m.group(0)
        name, dll = packages[rel]
        return '<Reference Include="%s"><HintPath>%s</HintPath></Reference>' % (name, dll)

    plugins = {}                                    # проект пакета → проект его встроенных скриптов
    plugin_files = set()
    for rel, text in texts.items():
        if rel in packages:
            own = [f for f in _COMPILE.findall(text) if not f.replace("\\", "/").startswith("Library/PackageCache/")]
            if not own:
                continue
            name, dll = packages[rel]
            text = _COMPILE_ITEM.sub(lambda m: "" if m.group(1).replace("\\", "/").startswith("Library/PackageCache/")
                                     else m.group(0), text)
            text = _ASSEMBLY_NAME.sub("<AssemblyName>%s.Plugins</AssemblyName>" % name, text)
            text = text.replace("</Project>", '  <ItemGroup><Reference Include="%s"><HintPath>%s</HintPath>'
                                              '</Reference></ItemGroup>\n</Project>' % (name, dll), 1)
            rel_out = os.path.splitext(rel)[0] + ".Plugins.csproj"
            plugins[rel] = rel_out
            plugin_files.update(os.path.normpath(os.path.join(base, f.replace("\\", os.sep))) for f in own)
        else:
            rel_out = rel
        text = _PROJECT_REF.sub(reference, _ITEM_PATH.sub(absolute, text))
        target = os.path.join(out, rel_out.replace("\\", os.sep))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if _write_if_changed(target, text):
            changed.append(target)
    lines, skip = [], False
    for line in sln_text.splitlines():
        m = _PROJECT_LINE.match(line)
        if m:
            rel = m.group(1)
            if rel in plugins:                      # тот же GUID и настройки — другой файл проекта
                line = line.replace('"%s"' % rel, '"%s"' % plugins[rel])
                skip = False
            else:
                skip = rel in packages
        if not skip:
            lines.append(line)
        if line.startswith("EndProject"):
            skip = False
    light = os.path.join(out, os.path.basename(solution))
    if _write_if_changed(light, "\n".join(lines) + "\n"):
        changed.append(light)
    return light, changed, plugin_files
