"""Сверка UniPy с Unity на настоящем проекте — только чтение (кэш анализатора во временной папке).

    python3 -m tests.live.check_real_project "/путь/к/Unity-проекту" [--folder Assets/Scripts]

Открывает каждый скрипт папки тем же анализатором и с теми же правилами, что IDE, и печатает красные ошибки.
Если Unity компилирует проект без ошибок, любая из них — ложная (баг UniPy). Перед этим — предупреждение
о ненайденных библиотеках, если оно есть (тогда ложные ошибки ожидаемы и причина названа).
Контроль в обратную сторону: --plant «файл:что:на что» — заменить в тексте (в памяти) и убедиться, что
ошибка появилась."""
import argparse
import collections
import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication     # noqa: E402

from studio import unity_light_solution     # noqa: E402
from studio.editor.source import decode_source     # noqa: E402
from studio.lsp import RoslynServer, find_csharp_server, path_to_uri     # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project")
    ap.add_argument("--folder", default="Assets")
    ap.add_argument("--plant", action="append", default=[], help="Assets/X.cs:старый текст:новый текст")
    a = ap.parse_args()
    app = QApplication([])
    with tempfile.TemporaryDirectory(prefix="unipy-check-") as tmp:
        unity_light_solution.CACHE = tmp
        srv = RoslynServer(a.project, find_csharp_server()[1])
        diags = {}
        srv.diagnostics.connect(lambda p, items: diags.__setitem__(p, items))

        def wait(cond, seconds):
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                app.processEvents()
                if cond():
                    return True
                time.sleep(0.005)
            return cond()

        def check(path, text):
            diags.pop(path, None)
            srv.sync(path, text)
            srv.pull_diagnostics(path)
            uri = path_to_uri(path)
            wait(lambda: not any(k[0] == uri for k in srv._diag_pending), 120)
            return diags.get(path, [])

        srv.start()
        wait(lambda: srv.state in ("ready", "failed"), 300)
        print("анализатор:", srv.state)
        if srv.missing_references_text():
            print("ПРЕДУПРЕЖДЕНИЕ:", srv.missing_references_text())
        files = sorted(os.path.join(d, f) for d, _, fs in os.walk(os.path.join(a.project, a.folder))
                       for f in fs if f.endswith(".cs"))
        codes, shown = collections.Counter(), 0
        for path in files:
            with open(path, "rb") as fh:
                text = decode_source(fh.read())[1]
            for line, _col, msg, kind in check(path, text):
                if kind == "error":
                    codes[msg.split(":")[0]] += 1
                    if shown < 30:
                        print("  %s:%d %s" % (os.path.relpath(path, a.project), line, msg[:140]))
                        shown += 1
            srv.close(path)
        print("файлов: %d, красных ошибок: %d %s" % (len(files), sum(codes.values()), dict(codes.most_common(8))))
        for plant in a.plant:
            rel, old, new = plant.split(":", 2)
            path = os.path.join(a.project, rel)
            with open(path, "rb") as fh:
                text = decode_source(fh.read())[1]
            if old not in text:
                print("контроль: «%s» не найдено в %s" % (old, rel))
                continue
            got = [(l, m[:100]) for l, _c, m, k in check(path, text.replace(old, new, 1)) if k == "error"]
            print("контроль %s: %s" % (rel, got or "ОШИБКА НЕ ПОЯВИЛАСЬ"))
        srv.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
