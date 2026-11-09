"""Общее для панелей: что считать кодом, скрытые папки, роли данных в деревьях, маленькие кнопки, BusyBar,
поиск Unity-проектов."""
import os
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QWidget, QToolButton
from .. import theme, languages
from ..theme import C
from ..i18n import bind_text


HIDDEN_DIRS = {"Library", "Temp", "Logs", "obj", "bin", "__pycache__", ".git", ".venv", "venv",
               ".idea", ".vs", ".vscode", "UserSettings", "Build", "Builds", "node_modules",
               ".mypy_cache", ".pytest_cache"}


# что обозреватель решений показывает по умолчанию — как Visual Studio: код и файлы проекта, без ресурсов
CODE_EXT = {".cs", ".asmdef", ".asmref", ".rsp", ".shader", ".hlsl", ".cginc", ".compute", ".glsl", ".shadergraph",
            ".uxml", ".uss", ".inputactions", ".py", ".pyw", ".pyi", ".ui", ".qrc", ".qss", ".rs", ".js", ".ts",
            ".html", ".css", ".json", ".xml", ".toml", ".cfg", ".ini", ".yml", ".yaml", ".md", ".txt", ".sh",
            ".sln", ".slnx", ".csproj", ".props", ".targets", ".desktop"}


CODE_NAMES = {"PKGBUILD", "Makefile", "CMakeLists.txt", "Dockerfile", "requirements.txt", ".gitignore"}


CODE_EXT |= languages.all_extensions()      # всё, что знает реестр языков, — тоже код


CODE_HIDDEN_DIRS = {"ProjectSettings", "UserSettings"}      # Unity: настройки редактора, не код


ROLE_PATH = Qt.ItemDataRole.UserRole


ROLE_LINE = Qt.ItemDataRole.UserRole + 1


def is_code_file(name):
    """Код или файл проекта: общий список, реестр языков и файлы наборов инструментов (toolchains)."""
    ext = os.path.splitext(name)[1].lower()
    if name in CODE_NAMES or ext in CODE_EXT:
        return True
    from .. import toolchains
    exts, names = toolchains.known_files()
    return ext in exts or name in names


def mini_button(icon_name, tip, slot):
    b = QToolButton()
    b.setIcon(theme.tool_icon(icon_name))
    b.setProperty("tool_icon", icon_name)      # значок перерисуется при смене темы
    bind_text(b, tip, "setToolTip")
    b.setAutoRaise(True)
    b.clicked.connect(slot)
    return b


def is_unity_project(path):
    return os.path.isdir(os.path.join(path, "Assets")) and (
        os.path.isdir(os.path.join(path, "ProjectSettings")) or os.path.isdir(os.path.join(path, "Packages")))


def find_unity_projects(root, depth=2):
    out = []
    if not root or not os.path.isdir(root):
        return out
    if is_unity_project(root):
        return [root]
    try:
        for name in sorted(os.listdir(root)):
            p = os.path.join(root, name)
            if os.path.isdir(p) and name not in HIDDEN_DIRS and not name.startswith("."):
                if is_unity_project(p):
                    out.append(p)
                elif depth > 1:
                    out.extend(find_unity_projects(p, depth - 1))
    except OSError:
        pass
    return out


# ====================================================================== обозреватель решений
class BusyBar(QWidget):
    """Бегущая полоска «идёт работа» для строки состояния. Рисуется сама: стандартная
    бесконечная QProgressBar под глобальным QSS приложения не анимируется."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(110, 6)
        self._x = 0
        self._timer = QTimer(self, interval=30, timeout=self._tick)
        self.hide()

    def start(self):
        self._x = 0
        self._timer.start()
        self.show()

    def stop(self):
        self._timer.stop()
        self.hide()

    def _tick(self):
        self._x = (self._x + 3) % (self.width() + 40)
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(C["border"]))
        p.fillRect(self._x - 40, 0, 40, self.height(), QColor(C["status_fg"]))
        p.end()
