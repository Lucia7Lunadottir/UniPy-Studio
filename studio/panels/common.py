"""Общее для панелей: что считать кодом, скрытые папки, роли данных в деревьях, маленькие кнопки, BusyBar,
ряд кнопок с переносом (FlowRow), поиск Unity-проектов."""
import os
from PyQt6.QtCore import Qt, QTimer, QRect, QSize, QPoint
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QWidget, QToolButton, QLayout
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


class FlowRow(QLayout):
    """Ряд кнопок панели, который переносится на следующую строку, когда панели не хватает ширины.
    Обычный QHBoxLayout требует ширину всех кнопок сразу — а вкладки дока не бывают уже самой широкой
    панели группы, и окно переставало сужаться."""

    def __init__(self, parent=None, spacing=4, margins=(0, 0, 0, 0)):
        super().__init__(parent)
        self._items = []
        self._fill = set()                  # виджеты, которые забирают остаток своей строки
        self._height = None                 # высота при текущей ширине — минимум для родителя
        self.setSpacing(spacing)
        self.setContentsMargins(*margins)

    def add(self, widget, fill=False):
        self.addWidget(widget)
        if fill:
            self._fill.add(id(widget))

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        height = self._arrange(rect, apply=True)
        if height != self._height:
            # строк стало больше/меньше — родитель должен выделить другую высоту, иначе
            # перенесённые кнопки наедут на соседние виджеты
            self._height = height
            self.invalidate()

    def _visible(self):
        return [i for i in self._items if not i.isEmpty()]

    def sizeHint(self):
        items = self._visible()
        m = self.contentsMargins()
        width = sum(i.sizeHint().width() for i in items) + self.spacing() * max(0, len(items) - 1)
        height = max((i.sizeHint().height() for i in items), default=0)
        return QSize(width + m.left() + m.right(), height + m.top() + m.bottom())

    def minimumSize(self):
        m = self.contentsMargins()
        width = max((i.minimumSize().width() for i in self._visible()), default=0)
        height = max((i.minimumSize().height() for i in self._visible()), default=0) + m.top() + m.bottom()
        # Минимум — одна строка. Перенесённые строки QVBoxLayout выдаёт по heightForWidth из растягиваемого
        # соседа (списка); как жёсткий минимум они не давали уменьшить по высоте всю нижнюю область дока.
        return QSize(width + m.left() + m.right(), height)

    def _lines(self, width):
        lines, used = [[]], 0
        for item in self._visible():
            w = item.sizeHint().width()
            if lines[-1] and used + self.spacing() + w > width:
                lines.append([])
                used = 0
            used += (self.spacing() if lines[-1] else 0) + w
            lines[-1].append(item)
        return [line for line in lines if line]

    def _arrange(self, rect, apply):
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        y = area.y()
        for line in self._lines(area.width()):
            height = max(i.sizeHint().height() for i in line)
            if apply:
                widths = [min(i.sizeHint().width(), area.width()) for i in line]
                free = area.width() - sum(widths) - self.spacing() * (len(line) - 1)
                fill = [n for n, i in enumerate(line) if i.widget() is not None and id(i.widget()) in self._fill]
                for n in fill:
                    widths[n] += max(0, free) // len(fill)
                x = area.x()
                for item, w in zip(line, widths):
                    item.setGeometry(QRect(QPoint(x, y + (height - item.sizeHint().height()) // 2),
                                           QSize(w, item.sizeHint().height())))
                    x += w + self.spacing()
            y += height + self.spacing()
        return max(0, y - self.spacing() - rect.y()) + m.bottom()


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
