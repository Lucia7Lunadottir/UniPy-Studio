"""Системы контроля версий. Окно спрашивает detect(root) и дальше работает с base.Repository.
Добавить систему: подкласс Repository (find, status, base_text…) и строка в PROVIDERS."""
from .base import FileStatus, Repository, VcsError
from .git import GitRepository

PROVIDERS = [GitRepository]


def detect(path):
    """Репозиторий, в котором лежит path, или None."""
    for provider in PROVIDERS:
        repo = provider.find(path)
        if repo is not None:
            return repo
    return None


__all__ = ["PROVIDERS", "detect", "FileStatus", "Repository", "VcsError"]
