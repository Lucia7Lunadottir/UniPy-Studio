"""Реестр наборов инструментов (запуск, сборка, шаблоны проектов) — см. base.Toolchain.

Добавить платформу: написать подкласс Toolchain в своём модуле и вызвать register(…) ниже.
Порядок важен: при «Текущий файл» побеждает первый набор, который узнал файл (Unity раньше .NET —
скрипт Unity-проекта открывает Unity, а не `dotnet run`)."""
from .base import ProjectContext, ProjectTemplate, RunCommand, RunTarget, Toolchain
from .dotnet import DotnetToolchain
from .python import PythonToolchain
from .unity import UnityToolchain

TOOLCHAINS = []
_files_cache = None


def register(toolchain):
    global _files_cache
    if any(t.id == toolchain.id for t in TOOLCHAINS):
        raise ValueError("toolchain already registered: %s" % toolchain.id)
    TOOLCHAINS.append(toolchain)
    _files_cache = None
    return toolchain


def unregister(toolchain):
    global _files_cache
    if toolchain in TOOLCHAINS:
        TOOLCHAINS.remove(toolchain)
        _files_cache = None


def known_files():
    """(расширения, имена) файлов всех наборов — для обозревателя решения."""
    global _files_cache
    if _files_cache is None:
        _files_cache = ({e.lower() for t in TOOLCHAINS for e in t.file_extensions},
                        {n for t in TOOLCHAINS for n in t.file_names})
    return _files_cache


def by_id(toolchain_id):
    return next((t for t in TOOLCHAINS if t.id == toolchain_id), None)


def active(ctx):
    """Наборы, которые узнали своё решение."""
    return [t for t in TOOLCHAINS if t.applies(ctx)]


def resolve_target(ctx, choice, current_path):
    """Что запускать или отлаживать: (набор, цель) или None.
    choice — выбранное в списке «Запуск» («набор::цель») или None для «Текущий файл»;
    тогда цель даёт первый набор, узнавший current_path (порядок регистрации важен)."""
    if choice:
        tc_id, _, target_id = choice.partition("::")
        tc = by_id(tc_id)
        target = next((t for t in tc.run_targets(ctx) if t.id == target_id), None) if tc else None
        if target is not None:
            return tc, target
    if current_path:
        for tc in TOOLCHAINS:
            target = tc.target_for_file(ctx, current_path)
            if target is not None:
                return tc, target
    return None


register(UnityToolchain())
register(PythonToolchain())
register(DotnetToolchain())

__all__ = ["TOOLCHAINS", "register", "unregister", "known_files", "by_id", "active", "resolve_target", "Toolchain", "ProjectContext", "ProjectTemplate",
           "RunCommand", "RunTarget"]
