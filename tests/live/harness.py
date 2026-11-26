"""Живые проверки C# для Unity: настоящий Roslyn + настоящий редактор CodeEditor (без окна, offscreen).

Зачем: тесты на придуманных ответах сервера подтверждали только мои предположения — молча ломались
ошибки компилятора (Roslyn выключал их из-за неразрешённых ссылок) и пункты лампочки (вложенные действия).
Здесь каждый сценарий проверяется путём, которым пользуется человек, и в обе стороны: что ДОЛЖНО
появиться и чего НЕ должно.

Проект-образец (fixture/Assets) копируется во временную папку, к нему пишутся .sln/.csproj «как у Unity»
со ссылками на установленный редактор Unity. Ожидания — прямо в коде образца: `// expect: CS0029, CS0168`
на строке; строки без пометки не должны иметь ни ошибок, ни предупреждений.

Нет roslyn-language-server или Unity — тесты пропускаются (skip), а не «проходят».
"""
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication     # noqa: E402

from studio import unity_light_solution     # noqa: E402
from studio.lsp import RoslynServer, find_csharp_server, path_to_uri     # noqa: E402
from studio.unity_references import installed_editors     # noqa: E402

FIXTURE = Path(__file__).with_name("fixture")
EXPECT = re.compile(r"(?://|#)\s*expect:\s*([A-Za-z]+\d*(?:\s*,\s*[A-Za-z]+\d*)*)")   # C#: коды, Python: вид
CODE = re.compile(r"^([A-Z]+\d+):")

CSPROJ = """<?xml version="1.0" encoding="utf-8"?>
<Project ToolsVersion="4.0" DefaultTargets="Build" xmlns="http://schemas.microsoft.com/developer/msbuild/2003">
  <PropertyGroup><LangVersion>9.0</LangVersion></PropertyGroup>
  <PropertyGroup>
    <Configuration Condition=" '$(Configuration)' == '' ">Debug</Configuration>
    <Platform Condition=" '$(Platform)' == '' ">AnyCPU</Platform>
    <ProjectGuid>{F27DB42F-B19D-2CF5-2920-75DF55B189E7}</ProjectGuid>
    <OutputType>Library</OutputType>
    <AssemblyName>Assembly-CSharp</AssemblyName>
    <TargetFrameworkVersion>v4.7.1</TargetFrameworkVersion>
    <BaseDirectory>.</BaseDirectory>
  </PropertyGroup>
  <PropertyGroup>
    <NoWarn>0169;USG0001</NoWarn>
    <DefineConstants>UNITY_EDITOR;UNITY_5_3_OR_NEWER;UNITY_6000_0_OR_NEWER;DEBUG;TRACE;NET_STANDARD_2_1;NETSTANDARD</DefineConstants>
  </PropertyGroup>
  <PropertyGroup Condition=" '$(Configuration)|$(Platform)' == 'Debug|AnyCPU' ">
    <OutputPath>Temp/bin/Debug/</OutputPath>
  </PropertyGroup>
  <PropertyGroup>
    <NoConfig>true</NoConfig>
    <NoStdLib>true</NoStdLib>
    <AddAdditionalExplicitAssemblyReferences>false</AddAdditionalExplicitAssemblyReferences>
    <ImplicitlyExpandNETStandardFacades>false</ImplicitlyExpandNETStandardFacades>
    <ImplicitlyExpandDesignTimeFacades>false</ImplicitlyExpandDesignTimeFacades>
  </PropertyGroup>
  <ItemGroup>
%(compile)s
  </ItemGroup>
  <ItemGroup>
%(references)s
  </ItemGroup>
  <Import Project="$(MSBuildToolsPath)\\Microsoft.CSharp.targets" />
</Project>
"""
SLN = """Microsoft Visual Studio Solution File, Format Version 11.00
Project("{FAE04EC0-301F-11D3-BF4B-00C04F79EFBC}") = "Assembly-CSharp", "Assembly-CSharp.csproj", "{F27DB42F-B19D-2CF5-2920-75DF55B189E7}"
EndProject
"""


def unity_editor():
    """(версия, папка) самого нового установленного Unity — или (None, None). Настройки Unity Hub и
    equestria-unity читаются из настоящей папки (тесты живут в песочнице — conftest.py), только чтение."""
    from unittest import mock
    real = os.environ.get("UNIPY_REAL_CONFIG_HOME")
    with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": real} if real else {}):
        found = installed_editors()
    if os.environ.get("UNIPY_UNITY_EDITOR"):
        folder = os.environ["UNIPY_UNITY_EDITOR"].rstrip("/")
        return os.path.basename(folder), folder
    if not found:
        return None, None
    version = max(found, key=lambda v: [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", v)])
    return version, found[version]


def skip_reason():
    if not find_csharp_server()[1]:
        return "roslyn-language-server не установлен"
    if unity_editor()[0] is None:
        return "Unity не найден (папки Unity Hub пусты; можно указать UNIPY_UNITY_EDITOR)"
    return None


def expectations(text):
    """{строка (с 1): {коды}} из пометок `// expect:` в тексте."""
    out = {}
    for n, line in enumerate(text.splitlines(), 1):
        m = EXPECT.search(line)
        if m:
            out[n] = {c.strip() for c in m.group(1).split(",")}
    return out


class _Services(SimpleNamespace):
    """Окно для редактора: нужное задано явно, остальное — пустые заглушки (журнал вызовов в .calls).
    Настройки, которые редактор читает через getattr(services, имя, по умолчанию), — тоже явно (DATA)."""
    DATA = {"hint_size": 11.0, "completion_alpha": False}

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        if name in self.DATA:
            return self.DATA[name]
        calls = self.__dict__.setdefault("calls", [])
        return lambda *a, **k: calls.append((name, a))


class LiveProject:
    """Временный проект + настоящий анализатор + редакторы. Один на класс тестов (запуск — секунды).
    Подкласс пишет проект (_write_project) и создаёт сервер (_make_server)."""
    language = "csharp"

    def __init__(self):
        self.app = QApplication.instance() or QApplication([])
        # Исключение в слоте Qt без перехватчика обрывает процесс (PyQt6) — в IDE его ловит app.py.
        # Здесь — собираем: тест обязан проверить, что их не было (crashes).
        import sys
        import traceback
        self.crashes = []
        self._old_hook = sys.excepthook
        sys.excepthook = lambda *exc: (self.crashes.append("".join(traceback.format_exception(*exc))),
                                       print(self.crashes[-1], file=sys.stderr))
        self.tmp = tempfile.TemporaryDirectory(prefix="unipy-live-")
        self.root = Path(self.tmp.name) / "LiveProject"
        self._cache_before = unity_light_solution.CACHE
        unity_light_solution.CACHE = str(Path(self.tmp.name) / "cache")    # кэш IDE не трогаем
        self._write_project()
        self.server = self._make_server()
        self.log = []
        self.server.log.connect(self.log.append)
        self.diagnostics = {}               # путь → [(строка, столбец, текст, вид)] — как видит редактор
        self.server.diagnostics.connect(lambda path, items: self.diagnostics.__setitem__(path, items))
        self.editors = []
        self.server.result.connect(self._route_result)

    def _write_project(self):
        raise NotImplementedError

    def _make_server(self):
        raise NotImplementedError

    def _write_unity_project(self):
        shutil.copytree(FIXTURE, self.root)
        (self.root / "ProjectSettings").mkdir(exist_ok=True)
        (self.root / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: %s\n" % self.version)
        managed = Path(self.editor_dir) / "Editor" / "Data"
        dlls = sorted((managed / "Managed" / "UnityEngine").glob("*.dll"))
        dlls.append(managed / "NetStandard" / "ref" / "2.1.0" / "netstandard.dll")
        refs = "\n".join('    <Reference Include="%s"><HintPath>%s</HintPath></Reference>' % (d.stem, d)
                         for d in dlls if d.exists())
        scripts = sorted(p.relative_to(self.root) for p in (self.root / "Assets").rglob("*.cs"))
        compile_items = "\n".join('    <Compile Include="%s" />' % p for p in scripts)
        (self.root / "Assembly-CSharp.csproj").write_text(CSPROJ % {"compile": compile_items, "references": refs})
        (self.root / "LiveGame.sln").write_text(SLN)

    def path(self, rel):
        return str(self.root / rel)

    def read(self, rel):
        return (self.root / rel).read_text(encoding="utf-8")

    # ---- ожидание
    def wait(self, condition, seconds=10.0):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.app.processEvents()
            if condition():
                return True
            time.sleep(0.01)
        self.app.processEvents()
        return condition()

    def start(self):
        self.server.start()
        if not self.wait(lambda: self.server.state in ("ready", "failed"), 180):
            raise RuntimeError("Roslyn не загрузил решение за 180 с")
        if self.server.state != "ready":
            raise RuntimeError("Roslyn не запустился: %s" % "\n".join(self.log[-5:]))

    def stop(self):
        for ed in self.editors:
            ed.close()
            ed.deleteLater()
        self.server.stop()
        self.app.processEvents()
        unity_light_solution.CACHE = self._cache_before
        import sys
        sys.excepthook = self._old_hook
        self.tmp.cleanup()

    # ---- ошибки, как их получает редактор
    def diagnostics_for(self, rel, text=None, settle=0.3):
        """Ошибки файла с текстом text (или с диска) — после того как ответили все группы диагностик."""
        path = self.path(rel)
        text = self.read(rel) if text is None else text
        self.diagnostics.pop(path, None)
        self.server.sync(path, text)
        self.wait(lambda: False, settle)
        self.server.pull_diagnostics(path)
        uri = path_to_uri(path)
        self.wait(lambda: not any(k[0] == uri for k in self.server._diag_pending), 60)
        return self.diagnostics.get(path, [])

    @staticmethod
    def by_line(items):
        """{строка: {коды}} из списка ошибок редактора (C#: «CS0029: …»; без кода — вид: error/warning)."""
        out = {}
        for line, _col, message, kind in items:
            m = CODE.match(message)
            out.setdefault(line, set()).add(m.group(1) if m else kind)
        return out

    # ---- редактор
    def editor(self, rel, text=None):
        from PyQt6.QtGui import QFont
        from studio.editor import CodeEditor
        path = self.path(rel)
        server = self.server
        services = _Services(
            service_for=lambda language: SimpleNamespace(ready_for=lambda p: server if server.ready else None,
                                                         sync=server.sync),
            jedi=SimpleNamespace(available=False),
            csharp=SimpleNamespace(ready_for=lambda p: server, sync=server.sync),
            # свой поиск по исходникам пакетов Unity (дополняет список Roslyn) — здесь не проверяется
            csharp_index=SimpleNamespace(submit=lambda **k: None))
        ed = CodeEditor(path, services, QFont("monospace", 11))
        self.app.processEvents()
        ed.setPlainText(self.read(rel) if text is None else text)
        ed.resize(900, 600)
        ed.show()
        self.app.processEvents()
        self.editors.append(ed)
        return ed

    def _route_result(self, res):
        for ed in self.editors:
            if id(ed) == res.get("editor"):
                ed.on_worker_result(res)


class LiveUnityProject(LiveProject):
    """Unity-проект (fixture/) со ссылками на установленный редактор Unity + Roslyn."""

    def __init__(self):
        self.version, self.editor_dir = unity_editor()
        super().__init__()

    def _write_project(self):
        self._write_unity_project()

    def _make_server(self):
        return RoslynServer(str(self.root), find_csharp_server()[1])


PY_FIXTURE = Path(__file__).with_name("pyfixture")


def python_skip_reason():
    from studio.lsp import find_pyright
    return None if find_pyright() else "pyright не установлен"


class LivePythonProject(LiveProject):
    """Python-проект (pyfixture/) + pyright с настройками UniPy (профиль под PyQt6)."""
    language = "python"

    def _write_project(self):
        shutil.copytree(PY_FIXTURE, self.root)

    def _make_server(self):
        import sys
        from studio.lsp import PyrightServer, find_pyright
        return PyrightServer(str(self.root), find_pyright(), lambda: sys.executable)
