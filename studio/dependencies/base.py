"""Зависимости — общий интерфейс. Поставщик читает требования из файлов проекта, узнаёт установленные
версии в окружении и предлагает команды установки (их вписывают в терминал — человек видит и жмёт Enter)."""

OK, MISSING, MISMATCH, UNKNOWN = "ok", "missing", "mismatch", "unknown"


class Requirement:
    """Одна строка требований: имя, условие версии, откуда (файл и строка), группа (dev, test…)."""
    __slots__ = ("name", "spec", "source", "line", "group", "marker", "raw", "installed", "state", "system_package")

    def __init__(self, name, spec="", source="", line=1, group="", marker="", raw=""):
        self.name, self.spec, self.source, self.line = name, spec, source, line
        self.group, self.marker, self.raw = group, marker, raw
        self.installed = None           # версия в окружении или None
        self.state = UNKNOWN
        self.system_package = None      # имя пакета в репозитории системы (python-requests) или None


class DependencyProvider:
    name = ""

    def environment(self):
        """(название окружения для людей, системное ли оно — ставится через пакетный менеджер)."""
        raise NotImplementedError

    def requirements(self):
        """[Requirement] из файлов проекта (быстро, без запуска процессов)."""
        raise NotImplementedError

    def check(self, requirements):
        """Заполнить installed/state (и system_package для системного окружения). Медленно — в фоновом потоке."""
        raise NotImplementedError

    def install_commands(self, requirements):
        """[(команда для терминала, пояснение)] чтобы поставить эти требования."""
        raise NotImplementedError

    def create_environment_command(self):
        """Команда создания своего окружения (.venv) или None."""
        return None
