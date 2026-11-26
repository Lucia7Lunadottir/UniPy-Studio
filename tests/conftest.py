"""Тесты никогда не трогают настоящие настройки и копии восстановления пользователя.

MainWindow создаёт QSettings("UniPyStudio", "UniPyStudio") — без этого файла тесты писали бы
в ~/.config/UniPyStudio/UniPyStudio.conf (так и было: размер шрифта 15 и подсказок 13 оттуда).
"""
import os
import tempfile

_SANDBOX = tempfile.mkdtemp(prefix="unipy-tests-")
# настоящая папка настроек — только для ЧТЕНИЯ живыми тестами (где установлен Unity: Hub, equestria-unity)
os.environ.setdefault("UNIPY_REAL_CONFIG_HOME",
                      os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config"))
os.environ["XDG_CONFIG_HOME"] = os.path.join(_SANDBOX, "config")
os.environ["XDG_STATE_HOME"] = os.path.join(_SANDBOX, "state")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.makedirs(os.path.join(_SANDBOX, "tmp"))
tempfile.tempdir = os.path.join(_SANDBOX, "tmp")      # временные файлы тестов — тоже в песочнице, не в /tmp

from PyQt6.QtCore import QSettings  # noqa: E402

for _fmt in (QSettings.Format.NativeFormat, QSettings.Format.IniFormat):
    QSettings.setPath(_fmt, QSettings.Scope.UserScope, os.environ["XDG_CONFIG_HOME"])
