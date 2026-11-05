#!/usr/bin/env python3
"""UniPy Studio — редактор кода для Unity (C#) и Python/PyQt.

Запуск:  python3 main.py [папка или файл]
Снимок:  python3 main.py --screenshot shots/now.png
"""
import argparse
import os
import sys

from studio.i18n import tr


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="UniPy Studio")
    parser.add_argument("path", nargs="?", help=tr("папка решения или файл"))
    parser.add_argument("--screenshot", metavar="FILE.png", help=tr("нарисовать ~120 кадров, сохранить картинку окна и выйти"))
    parser.add_argument("--project", help=tr("корень проекта Unity или файл решения"))
    parser.add_argument("--file", help=tr("файл для открытия внутри проекта"))
    parser.add_argument("--line", type=int, default=1, help=tr("строка, начиная с 1"))
    parser.add_argument("--column", type=int, default=1, help=tr("столбец, начиная с 1"))
    return parser.parse_args(argv)


def main():
    args = parse_args()
    if args.screenshot and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from studio.app import run
    except ImportError as e:
        sys.stderr.write(tr("Нужен PyQt6: pip install PyQt6 jedi\n(%s)\n") % e)
        return 1
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
