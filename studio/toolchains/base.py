"""Общий интерфейс набора инструментов (toolchain) — всё, что IDE умеет делать с проектами одной платформы.

Окно не знает, что такое Python, .NET или Unity: оно спрашивает у каждого зарегистрированного набора
(toolchains/__init__.py) — «это твой проект?», «что тут можно запустить?», «как собрать?», «какие есть шаблоны?».
Чтобы добавить язык или платформу (Rust, Go, Godot…), достаточно подкласса Toolchain и строки в реестре.

Набор работает только с ProjectContext — тем, что ему отдаёт окно; про виджеты он ничего не знает.
"""
import os


class ProjectContext:
    """Что окно сообщает наборам инструментов. Всё — значения или функции без аргументов,
    чтобы набор можно было проверить тестом без главного окна."""

    def __init__(self, root=None, files=(), buffers=None, interpreter=None, options=None,
                 open_in_unity=None, status=None):
        self.root = root
        self.files = list(files)                       # файлы решения (как в обозревателе)
        self.buffers = dict(buffers or {})             # путь → текст открытых вкладок (с несохранёнными правками)
        self.interpreter = interpreter                 # выбранный Python (путь) или None
        self.options = options if options is not None else {}
        self.open_in_unity = open_in_unity or (lambda: None)
        self.status = status or (lambda text: None)

    def option(self, key, default=None):
        try:
            return self.options[key]
        except (KeyError, TypeError):
            return default


class RunTarget:
    """Что можно запустить: файл, проект. id уникален внутри набора, label — для списка «Запуск»."""
    __slots__ = ("id", "label", "path", "data")

    def __init__(self, id, label, path=None, data=None):
        self.id, self.label, self.path, self.data = id, label, path, data

    def __repr__(self):
        return "RunTarget(%r)" % self.id


class RunCommand:
    """Как запустить: программа, аргументы, папка, переменные окружения.
    problem_matcher(текст stderr) → [(путь, строка, столбец, сообщение)] — куда вести после падения."""

    def __init__(self, program, args, cwd, env=None, title="", problem_matcher=None):
        self.program, self.args, self.cwd = program, list(args), cwd
        self.env = dict(env or {})
        self.title = title or os.path.basename(program)
        self.problem_matcher = problem_matcher or (lambda text: [])

    def command_line(self):
        return " ".join('"%s"' % a if " " in a else a for a in [self.program, *self.args])


class ProjectTemplate:
    """Шаблон нового проекта. name/description — ключ перевода или готовый текст (шаблоны dotnet приходят
    с уже переведёнными названиями). options — что ещё показать в окне (например, «создать решение»)."""

    def __init__(self, id, name, description="", language="", tags="", options=()):
        self.id, self.name, self.description = id, name, description
        self.language, self.tags, self.options = language, tags, tuple(options)
        self.toolchain = None                          # заполняет реестр

    def __repr__(self):
        return "ProjectTemplate(%r)" % self.id


class Toolchain:
    """Набор инструментов одной платформы. Методы по умолчанию — «не умею»: подкласс переопределяет нужное."""
    id = ""
    name = ""                   # для людей: «Python», «.NET», «Unity»
    badge = ("?", "#555555", "#FFFFFF")     # значок в списке «Запуск»: буквы, фон, цвет
    file_extensions = ()        # файлы платформы — обозреватель решения показывает их в режиме «только код»
    file_names = ()             # то же по имени (pyproject.toml, Cargo.toml…)

    # --- проект
    def available(self) -> "tuple[bool, str]":
        """(есть ли инструменты в системе, подсказка что поставить)."""
        return True, ""

    def applies(self, ctx) -> bool:
        """Это решение — проект этого набора (или содержит его)?"""
        return False

    # --- запуск
    def run_targets(self, ctx) -> "list[RunTarget]":
        """[RunTarget] для списка «Запуск» на панели инструментов."""
        return []

    def target_for_file(self, ctx, path) -> "RunTarget | None":
        """Что запускать, когда выбран «Текущий файл» и открыт path; None — не мой файл."""
        return None

    def run_command(self, ctx, target) -> "RunCommand | None":
        """RunCommand для цели; None — запускать нечего."""
        return None

    def launch(self, ctx, target, runner):
        """Запустить цель. По умолчанию — процесс через окно «Вывод» (runner.start); Unity переопределяет."""
        command = self.run_command(ctx, target)
        if command is not None:
            runner.start(command)

    # --- отладка
    def debug_available(self, ctx) -> "tuple[bool, str]":
        """(можно ли отлаживать, подсказка что поставить). По умолчанию — нельзя: F5 просто запускает."""
        return False, ""

    def debug_config(self, ctx, target) -> object:
        """debug.session.DebugConfig — какой адаптер DAP запустить и что ему сказать; None — не умею."""
        return None

    # --- сборка
    def build_jobs(self, ctx, rebuild=False) -> list:
        """[build.BuildJob] для «Собрать решение»."""
        return []

    def build_jobs_for_file(self, ctx, path) -> "list | None":
        """[BuildJob] для «Собрать текущий проект»; None — файл не мой."""
        return None

    def clean(self, ctx) -> object:
        """BuildJob для «Очистить решение» или None (если очистил сам или нечего)."""
        return None

    # --- новые проекты
    def load_templates(self, done):
        """done([ProjectTemplate]) — сразу или позже (шаблоны dotnet читаются из `dotnet new list`)."""
        done([])

    def create_project(self, template, folder, name, choices):
        """BuildJob, который создаёт проект; итог (finished) — {"ok", "root", "open": [файлы]}."""
        raise NotImplementedError
