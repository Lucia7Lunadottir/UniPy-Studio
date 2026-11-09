"""Система контроля версий — общий интерфейс. Окно знает только Repository; Git — одна из реализаций
(git.py). Другая система (Mercurial, Fossil…) — подкласс Repository и строка в vcs/__init__.py.

Методы синхронные и могут быть небыстрыми — VcsController зовёт их в фоновом потоке,
кроме тех, что нужны по кнопке (stage, commit…). Ошибки — исключение VcsError с текстом команды."""


class VcsError(Exception):
    pass


# Состояния файла: как показывает «Изменения Git» в Visual Studio
MODIFIED, ADDED, DELETED, RENAMED, UNTRACKED, CONFLICT, IGNORED = (
    "modified", "added", "deleted", "renamed", "untracked", "conflict", "ignored")


class FileStatus:
    """staged — в индексе (будет в коммите), unstaged — правки в рабочей папке. Каждое — состояние или None."""
    __slots__ = ("path", "staged", "unstaged", "original")

    def __init__(self, path, staged=None, unstaged=None, original=None):
        self.path, self.staged, self.unstaged, self.original = path, staged, unstaged, original

    def main(self):
        """Что показать одним значком: конфликт важнее всего, потом правки в папке, потом индекс."""
        if CONFLICT in (self.staged, self.unstaged):
            return CONFLICT
        return self.unstaged or self.staged

    def __repr__(self):
        return "FileStatus(%r, staged=%s, unstaged=%s)" % (self.path, self.staged, self.unstaged)


class Repository:
    name = ""                   # «Git»
    root = ""

    @classmethod
    def find(cls, path):
        """Repository для папки (или её родителя) или None."""
        return None

    def branch(self):
        """Текущая ветка (или короткий хеш, если HEAD отсоединён)."""
        raise NotImplementedError

    def status(self):
        """{абсолютный путь: FileStatus} — только изменённые, новые, конфликты."""
        raise NotImplementedError

    def base_text(self, path):
        """Текст файла в последнем коммите (HEAD) или None — файла там нет (новый)."""
        raise NotImplementedError

    def stage(self, paths):
        raise NotImplementedError

    def unstage(self, paths):
        raise NotImplementedError

    def discard(self, paths):
        """Отменить правки в рабочей папке (вернуть как в индексе/HEAD). Новые файлы не удаляются."""
        raise NotImplementedError

    def commit(self, message, amend=False):
        raise NotImplementedError

    def branches(self):
        """[(имя, текущая ли)] — локальные ветки."""
        raise NotImplementedError

    def checkout(self, branch):
        raise NotImplementedError

    def remote_command(self, action):
        """(программа, [аргументы], папка) для fetch / pull / push — выполняется задачей сборки с выводом."""
        raise NotImplementedError

    def watch_paths(self):
        """Файлы, изменение которых значит «статус поменялся» (индекс, HEAD) — для QFileSystemWatcher."""
        return []
