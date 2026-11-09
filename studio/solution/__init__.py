"""Решение для «Обозревателя решений»: какие проекты, их зависимости и файлы (model.py, readers.py; без Qt).
Новый вид проектов — читатель в readers.PROJECT_READERS."""
from .model import (ANALYZER, ASSEMBLY, DEPENDENCY_KINDS, FRAMEWORK, PACKAGE, PROJECT,  # noqa: F401
                    Dependency, Project, Solution)
from .readers import PROJECT_READERS, load_solution, read_project, read_sln, read_slnx  # noqa: F401
