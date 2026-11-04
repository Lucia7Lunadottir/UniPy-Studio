#!/usr/bin/env python3
"""UniPy Studio — редактор кода для Unity (C#) и Python/PyQt.

Запуск:  python3 main.py [папка или файл]
Снимок:  python3 main.py --screenshot shots/now.png
"""
import argparse
import os
import sys


def main():
    parser = argparse.ArgumentParser(description="UniPy Studio")
    parser.add_argument("path", nargs="?", help="папка решения или файл")
    parser.add_argument("--screenshot", metavar="FILE.png", help="нарисовать ~120 кадров, сохранить картинку окна и выйти")
    args = parser.parse_args()
    if args.screenshot and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from studio.app import run
    except ImportError as e:
        sys.stderr.write("Нужен PyQt6: pip install PyQt6 jedi\n(%s)\n" % e)
        return 1
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
