"""Unity: «запуск» — открыть проект в редакторе Unity (игру запускает сам Unity), сборка — проверка всего
решения через dotnet build в кэш UniPy (папка проекта не меняется), очистка — удалить этот кэш.
Новые Unity-проекты создаёт Unity Hub, поэтому шаблонов здесь нет."""
import os
import shutil

from ..build import DotnetBuild, cache_dir_for, dotnet_target, is_unity_folder, project_for_file
from .base import RunTarget, Toolchain


def unity_projects(ctx):
    from ..panels import find_unity_projects
    return find_unity_projects(ctx.root) if ctx.root else []


class UnityToolchain(Toolchain):
    id = "unity"
    name = "Unity"
    badge = ("U", "#222222", "#FFFFFF")
    file_extensions = (".cs", ".asmdef", ".asmref", ".rsp", ".shader", ".hlsl", ".cginc", ".compute",
                       ".shadergraph", ".uxml", ".uss", ".inputactions")

    def applies(self, ctx):
        return bool(unity_projects(ctx))

    def _solution(self, ctx):
        target = dotnet_target(ctx.root)
        return target if target and is_unity_folder(os.path.dirname(target)) else None

    # --- запуск
    def run_targets(self, ctx):
        return [RunTarget(p, "Unity: " + os.path.basename(p), p) for p in unity_projects(ctx)]

    def target_for_file(self, ctx, path):
        for project in unity_projects(ctx):
            if path.startswith(os.path.join(project, "")):
                return RunTarget(project, "Unity: " + os.path.basename(project), project)
        return None

    def launch(self, ctx, target, runner):
        """C#-скрипты Unity запускает сам Unity — открыть проект в нём (как «Присоединиться к Unity» в VS)."""
        ctx.open_in_unity()

    # --- сборка
    def _job(self, ctx, target, verb="build"):
        return DotnetBuild(target, verb, use_cache=ctx.option("build/unity_cache", True),
                           shared_compiler=ctx.option("build/shared_compiler", False))

    def build_jobs(self, ctx, rebuild=False):
        target = self._solution(ctx)
        return [self._job(ctx, target, "rebuild" if rebuild else "build")] if target else []

    def build_jobs_for_file(self, ctx, path):
        target = self._solution(ctx)
        if not target or not path.endswith(".cs"):
            return None
        proj = project_for_file(path, os.path.dirname(target))
        return [self._job(ctx, proj)] if proj else None

    def clean(self, ctx):
        target = self._solution(ctx)
        if not target:
            return None
        if ctx.option("build/unity_cache", True):
            shutil.rmtree(cache_dir_for(target), ignore_errors=True)
            return None
        return self._job(ctx, target, "clean")
