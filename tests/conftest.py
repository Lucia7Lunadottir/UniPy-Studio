"""Тесты никогда не трогают настоящие настройки и копии восстановления пользователя.

MainWindow создаёт QSettings("UniPyStudio", "UniPyStudio") — без этого файла тесты писали бы
в ~/.config/UniPyStudio/UniPyStudio.conf (так и было: размер шрифта 15 и подсказок 13 оттуда).
"""
import os
import tempfile

_SANDBOX = tempfile.mkdtemp(prefix="unipy-tests-")
os.environ["XDG_CONFIG_HOME"] = os.path.join(_SANDBOX, "config")
os.environ["XDG_STATE_HOME"] = os.path.join(_SANDBOX, "state")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QSettings  # noqa: E402

for _fmt in (QSettings.Format.NativeFormat, QSettings.Format.IniFormat):
    QSettings.setPath(_fmt, QSettings.Scope.UserScope, os.environ["XDG_CONFIG_HOME"])
