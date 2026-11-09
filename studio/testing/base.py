"""Тесты — общий интерфейс. Набор инструментов (toolchains) отдаёт TestProvider для своего решения
(Toolchain.test_provider). Поставщик говорит, как найти и запустить тесты, а его процесс сообщает о них
строками «@@UNIPY-TEST@@ {json}» — для другого фреймворка достаточно переопределить parse()."""
import json

MARK = "@@UNIPY-TEST@@ "
NOT_RUN, RUNNING, PASSED, FAILED, ERROR, SKIPPED = "notrun", "running", "passed", "failed", "error", "skipped"


class TestNode:
    """Файл, группа (класс) или тест. status — для теста; у файла и группы — сводный (см. aggregate)."""
    __slots__ = ("id", "name", "kind", "parent", "path", "line", "status", "duration", "message", "details",
                 "fail_path", "fail_line", "output", "children")

    def __init__(self, id, name, kind, parent=None, path=None, line=None):
        self.id, self.name, self.kind, self.parent = id, name, kind, parent
        self.path, self.line = path, line
        self.status, self.duration = NOT_RUN, 0.0
        self.message = self.details = self.output = ""
        self.fail_path = self.fail_line = None
        self.children = []

    def tests(self):
        """Все тесты внутри (или сам тест)."""
        if self.kind == "test":
            return [self]
        return [t for ch in self.children for t in ch.tests()]


def aggregate(statuses):
    """Сводное состояние группы, как в VS: упавший важнее идущего, идущий — пройденного."""
    statuses = set(statuses)
    for s in (FAILED, ERROR, RUNNING, NOT_RUN):
        if s in statuses:
            return FAILED if s == ERROR else s
    if statuses == {SKIPPED}:
        return SKIPPED
    return PASSED if statuses else NOT_RUN


class TestProvider:
    """Как найти, запустить и отладить тесты одного решения."""
    name = ""

    def discover_command(self):
        """RunCommand: найти тесты (события item)."""
        raise NotImplementedError

    def run_command(self, ids):
        """RunCommand: запустить тесты (все, если ids пуст) — события start/result."""
        raise NotImplementedError

    def debug_config(self, ids):
        """debug.session.DebugConfig — те же тесты под отладчиком; None — не умею."""
        return None

    def parse(self, line):
        """Строка вывода → событие (dict) или None — обычный вывод теста."""
        if not line.startswith(MARK):
            return None
        try:
            return json.loads(line[len(MARK):])
        except ValueError:
            return None
