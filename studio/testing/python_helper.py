"""Помощник обозревателя тестов: запускается интерпретатором ПРОЕКТА (не IDE), находит и запускает тесты
pytest (если установлен) или unittest и сообщает о них строками «@@UNIPY-TEST@@ {json}».
Остальной вывод — как есть (print в тестах). Совместим с Python 3.8+: проекты бывают со старым venv.

    python python_helper.py collect <корень>
    python python_helper.py run <корень> <id> [<id> …]      (без id — все)
"""
import inspect
import json
import os
import sys
import time
import traceback
import unittest

MARK = "@@UNIPY-TEST@@ "


def emit(**event):
    sys.stdout.write("\n" + MARK + json.dumps(event, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def has_pytest():
    try:
        import pytest  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------- pytest
class PytestPlugin:
    def __init__(self, root):
        self.root = root
        self.started = {}

    @staticmethod
    def _path(item):
        path = getattr(item, "path", None) or getattr(item, "fspath", "")
        return str(path)

    def pytest_collection_finish(self, session):
        seen = set()
        for item in session.items:
            parts = item.nodeid.split("::")
            for depth in range(1, len(parts)):
                node = "::".join(parts[:depth])
                if node not in seen:
                    seen.add(node)
                    emit(event="item", id=node, name=parts[depth - 1].split("/")[-1],
                         kind="file" if depth == 1 else "group", parent="::".join(parts[:depth - 1]) or None,
                         path=self._path(item), line=None)
            line = item.location[1] + 1 if item.location[1] is not None else None
            emit(event="item", id=item.nodeid, name=parts[-1], kind="test", parent="::".join(parts[:-1]) or None,
                 path=self._path(item), line=line)

    def pytest_runtest_logstart(self, nodeid, location):
        self.started[nodeid] = time.time()
        emit(event="start", id=nodeid)

    def pytest_runtest_logreport(self, report):
        if report.when != "call" and report.outcome == "passed":
            return                      # setup/teardown без ошибок — не результат
        if report.when == "teardown" and report.outcome == "skipped":
            return
        crash = getattr(report.longrepr, "reprcrash", None)
        message, path, line = "", None, None
        if report.outcome == "skipped" and isinstance(report.longrepr, tuple):
            path, line, message = report.longrepr[0], report.longrepr[1], report.longrepr[2]
        elif crash is not None:
            message, path, line = crash.message, crash.path, crash.lineno
        outcome = report.outcome
        if report.when != "call" and outcome == "failed":
            outcome = "error"           # упал не тест, а подготовка (fixture)
        # pytest сам перехватывает print из теста — отдаём то, что он поймал
        output = (getattr(report, "capstdout", "") or "") + (getattr(report, "capstderr", "") or "")
        emit(event="result", id=report.nodeid, outcome=outcome, duration=round(report.duration, 4),
             message=message, details=report.longreprtext if outcome != "passed" else "",
             path=path, line=line, output=output)

    def pytest_collectreport(self, report):
        """Файл с тестами не импортировался — показать его как ошибку, а не молча потерять."""
        if report.failed:
            path = str(getattr(report, "fspath", "") or report.nodeid)
            emit(event="item", id=report.nodeid, name=report.nodeid.split("/")[-1], kind="test", parent=None,
                 path=path, line=1, broken=True)
            emit(event="result", id=report.nodeid, outcome="error", duration=0,
                 message=(report.longreprtext.strip().splitlines() or ["collection error"])[-1],
                 details=report.longreprtext, path=path, line=None, output="")


def pytest_main(root, args):
    import pytest
    plugin = PytestPlugin(root)
    code = pytest.main(["-p", "no:cacheprovider", "-q", "--rootdir", root] + args, plugins=[plugin])
    return int(code)


# ---------------------------------------------------------------- unittest
def _flatten(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            for t in _flatten(test):
                yield t
        else:
            yield test


def _location(test):
    cls = type(test)
    method = getattr(cls, getattr(test, "_testMethodName", ""), None)
    try:
        path = inspect.getsourcefile(cls)
        line = inspect.getsourcelines(method)[1] if method else inspect.getsourcelines(cls)[1]
    except (TypeError, OSError):
        return None, None
    return path, line


def unittest_collect(root):
    suite = unittest.TestLoader().discover(root, top_level_dir=root)
    seen = set()
    for test in _flatten(suite):
        test_id = test.id()
        if type(test).__name__ == "_FailedTest":             # модуль не импортировался
            emit(event="item", id=test_id, name=test_id.split(".")[-1], kind="test", parent=None,
                 path=None, line=None, broken=True)
            continue
        parts = test_id.split(".")
        module, cls = ".".join(parts[:-2]), ".".join(parts[:-1])
        path, line = _location(test)
        for node, name, kind, parent in ((module, parts[-3] if len(parts) > 2 else module, "file", None),
                                         (cls, parts[-2], "group", module)):
            if node not in seen:
                seen.add(node)
                emit(event="item", id=node, name=name, kind=kind, parent=parent, path=path, line=None)
        emit(event="item", id=test_id, name=parts[-1], kind="test", parent=cls, path=path, line=line)


class _Result(unittest.TestResult):
    def startTest(self, test):
        super(_Result, self).startTest(test)
        self._t = time.time()
        emit(event="start", id=test.id())

    def _report(self, test, outcome, err=None, reason=""):
        message, details, path, line = reason, "", None, None
        if err is not None:
            details = "".join(traceback.format_exception(*err))
            message = "".join(traceback.format_exception_only(err[0], err[1])).strip()
            frames = traceback.extract_tb(err[2])
            own = [f for f in frames if f.filename == _location(test)[0]] or frames
            if own:
                path, line = own[-1].filename, own[-1].lineno
        emit(event="result", id=test.id(), outcome=outcome, duration=round(time.time() - getattr(self, "_t", time.time()), 4),
             message=message, details=details, path=path, line=line)

    # super() — чтобы wasSuccessful() знал о падениях (иначе итог прогона был бы «всё прошло»)
    def addSuccess(self, test):
        super(_Result, self).addSuccess(test)
        self._report(test, "passed")

    def addFailure(self, test, err):
        super(_Result, self).addFailure(test, err)
        self._report(test, "failed", err)

    def addError(self, test, err):
        super(_Result, self).addError(test, err)
        self._report(test, "error", err)

    def addSkip(self, test, reason):
        super(_Result, self).addSkip(test, reason)
        self._report(test, "skipped", reason=reason)

    def addExpectedFailure(self, test, err):
        super(_Result, self).addExpectedFailure(test, err)
        self._report(test, "passed")

    def addUnexpectedSuccess(self, test):
        super(_Result, self).addUnexpectedSuccess(test)
        self._report(test, "failed", reason="unexpected success")


def unittest_run(root, ids):
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromNames(ids) if ids else loader.discover(root, top_level_dir=root)
    result = _Result()
    suite.run(result)
    return 0 if result.wasSuccessful() else 1


def main(argv):
    mode, root, ids = argv[1], os.path.abspath(argv[2]), argv[3:]
    os.chdir(root)
    if root not in sys.path:
        sys.path.insert(0, root)
    framework = "pytest" if has_pytest() else "unittest"
    emit(event="framework", name=framework, python=sys.version.split()[0])
    try:
        if framework == "pytest":
            code = pytest_main(root, (["--collect-only"] if mode == "collect" else []) + (ids or [root]))
        elif mode == "collect":
            unittest_collect(root)
            code = 0
        else:
            code = unittest_run(root, ids)
    except Exception:                    # noqa: BLE001 — IDE должна увидеть, что сломалось
        emit(event="error", message=traceback.format_exc())
        code = 3
    emit(event="done", code=code)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
