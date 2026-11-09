"""Git через командную строку `git` (пакет git; своих библиотек не нужно)."""
import os
import shutil
import subprocess

from .base import (ADDED, CONFLICT, DELETED, FileStatus, MODIFIED, RENAMED, Repository, UNTRACKED, VcsError)

ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"}     # без вопросов в терминал
_CODE = {"M": MODIFIED, "T": MODIFIED, "A": ADDED, "D": DELETED, "R": RENAMED, "C": ADDED}


def parse_status_v2(data, root):
    """Вывод `git status --porcelain=v2 -z` → {путь: FileStatus}."""
    out = {}
    entries = data.split("\0")
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if not entry or entry.startswith("#"):
            continue
        kind = entry[0]
        if kind == "?":
            path = os.path.normpath(os.path.join(root, entry[2:]))     # папка приходит с «/» на конце
            out[path] = FileStatus(path, unstaged=UNTRACKED)
        elif kind in "12":
            fields = entry.split(" ", 8 if kind == "1" else 9)
            xy, rel = fields[1], fields[-1]
            original = None
            if kind == "2":                     # переименование: следом — старое имя
                original = os.path.join(root, entries[i])
                i += 1
            path = os.path.join(root, rel)
            out[path] = FileStatus(path, _CODE.get(xy[0]), _CODE.get(xy[1]), original)
        elif kind == "u":
            rel = entry.split(" ", 10)[-1]
            path = os.path.join(root, rel)
            out[path] = FileStatus(path, CONFLICT, CONFLICT)
    return out


class GitRepository(Repository):
    name = "Git"

    def __init__(self, root, git_dir):
        self.root = root
        self.git_dir = git_dir

    @classmethod
    def find(cls, path):
        if shutil.which("git") is None or not path:
            return None
        if not os.path.isdir(path):             # файл — спрашиваем про его папку
            path = os.path.dirname(path)
        try:
            top = cls._run_in(path, ["rev-parse", "--show-toplevel", "--absolute-git-dir"]).splitlines()
        except VcsError:
            return None
        return cls(top[0], top[1]) if len(top) == 2 else None

    @staticmethod
    def _run_in(cwd, args, input_text=None, check=True):
        try:
            proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, timeout=60,
                                  input=input_text.encode("utf-8") if input_text is not None else None,
                                  env={**os.environ, **ENV})
        except (OSError, subprocess.SubprocessError) as e:
            raise VcsError(str(e)) from e
        if check and proc.returncode != 0:
            raise VcsError((proc.stderr or proc.stdout).decode("utf-8", "replace").strip() or "git %s" % args[0])
        return proc.stdout.decode("utf-8", "surrogateescape")

    def _run(self, args, **kw):
        return self._run_in(self.root, args, **kw)

    def _rel(self, paths):
        return [os.path.relpath(p, self.root) for p in paths]

    def has_commits(self):
        try:
            self._run(["rev-parse", "--verify", "-q", "HEAD"])
            return True
        except VcsError:
            return False

    # --- чтение
    def branch(self):
        name = self._run(["rev-parse", "--abbrev-ref", "HEAD"], check=False).strip()
        if name == "HEAD":
            name = self._run(["rev-parse", "--short", "HEAD"], check=False).strip()
        if not name:                            # репозиторий без коммитов
            name = self._run(["symbolic-ref", "--short", "HEAD"], check=False).strip()
        return name

    def status(self):
        return parse_status_v2(self._run(["status", "--porcelain=v2", "-z", "--untracked-files=normal"]), self.root)

    def base_text(self, path):
        """Текст в HEAD — в кодировке файла (cp1251 у старых скриптов Unity) с теми же правилами, что редактор."""
        from ..editor import decode_source
        rel = os.path.relpath(path, self.root).replace(os.sep, "/")
        try:
            proc = subprocess.run(["git", "show", "HEAD:" + rel], cwd=self.root, capture_output=True, timeout=30,
                                  env={**os.environ, **ENV})
        except (OSError, subprocess.SubprocessError):
            return None
        if proc.returncode != 0:
            return None
        return decode_source(proc.stdout)[1].replace("\r\n", "\n")

    def branches(self):
        out = []
        for line in self._run(["branch", "--format=%(HEAD)%09%(refname:short)"]).splitlines():
            head, _, name = line.partition("\t")
            if name:
                out.append((name, head.strip() == "*"))
        return out

    def watch_paths(self):
        paths = [os.path.join(self.git_dir, "index"), os.path.join(self.git_dir, "HEAD")]
        return [p for p in paths if os.path.exists(p)]

    # --- изменения
    def stage(self, paths):
        self._run(["add", "--", *self._rel(paths)])

    def unstage(self, paths):
        if self.has_commits():
            self._run(["restore", "--staged", "--", *self._rel(paths)])
        else:
            self._run(["rm", "--cached", "-q", "--", *self._rel(paths)])

    def discard(self, paths):
        self._run(["restore", "--", *self._rel(paths)])

    def commit(self, message, amend=False):
        args = ["commit", "-F", "-"] + (["--amend"] if amend else [])
        return self._run(args, input_text=message)

    def checkout(self, branch):
        self._run(["switch", branch])

    def remote_command(self, action):
        if action == "push":
            upstream = self._run(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], check=False).strip()
            remotes = self._run(["remote"], check=False).split()
            if not upstream and remotes:        # первая отправка ветки — как в VS: сразу с привязкой
                remote = "origin" if "origin" in remotes else remotes[0]
                return "git", ["push", "-u", remote, self.branch()], self.root
        return "git", [action], self.root
