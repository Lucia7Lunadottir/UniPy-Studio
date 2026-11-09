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
    parser.add_argument("path", nargs="?", help=tr("main.solution_folder_file"))
    parser.add_argument("--screenshot", metavar="FILE.png", help=tr("main.render_about_120_frames_save"))
    parser.add_argument("--project", help=tr("main.unity_project_root_solution_file"))
    parser.add_argument("--file", help=tr("main.file_open_project"))
    parser.add_argument("--line", type=int, default=1, help=tr("main.line_number_starting_at_1"))
    parser.add_argument("--column", type=int, default=1, help=tr("main.column_number_starting_at_1"))
    return parser.parse_args(argv)


def main():
    args = parse_args()
    if args.screenshot and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from studio.app import run
    except ImportError as e:
        sys.stderr.write(tr("main.pyqt6_required_pip_install_pyqt6") % e)
        return 1
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
