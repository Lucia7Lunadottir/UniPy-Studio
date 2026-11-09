"""Тесты Python: pytest (если установлен в интерпретаторе проекта) или unittest — через python_helper.py."""
import os

from ..toolchains.base import RunCommand
from .base import TestProvider

HELPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "python_helper.py")
ENV = {"PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}


class PythonTests(TestProvider):
    name = "Python"

    def __init__(self, root, interpreter, debug_python=None):
        self.root, self.interpreter = root, interpreter
        self.debug_python = debug_python              # Python с debugpy — для «Отладить тест»

    def discover_command(self):
        return RunCommand(self.interpreter, [HELPER, "collect", self.root], self.root, ENV, title="tests")

    def run_command(self, ids):
        return RunCommand(self.interpreter, [HELPER, "run", self.root, *ids], self.root, ENV, title="tests")

    def debug_config(self, ids):
        if not self.debug_python:
            return None
        from ..debug.session import DebugConfig
        return DebugConfig(self.debug_python, ["-m", "debugpy.adapter"], "launch", {
            "name": "tests", "type": "python", "request": "launch", "program": HELPER,
            "args": ["run", self.root, *ids], "cwd": self.root, "python": [self.interpreter],
            "console": "internalConsole", "redirectOutput": True, "justMyCode": True, "env": ENV},
            adapter_id="debugpy", cwd=self.root, title="tests")
