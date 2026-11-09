"""Панели окна — по модулю на панель. Здесь только реэкспорт: `from .panels import SolutionExplorer` и т. п.
работает как раньше, когда всё лежало в одном panels.py."""
from .common import BusyBar, CODE_EXT, CODE_HIDDEN_DIRS, CODE_NAMES, HIDDEN_DIRS, ROLE_LINE, ROLE_PATH, find_unity_projects, is_code_file, is_unity_project, mini_button  # noqa: F401
from .solution_explorer import SolutionExplorer  # noqa: F401
from .outline import OutlinePanel, outline  # noqa: F401
from .output import OutputPanel  # noqa: F401
from .references import ReferencesPanel  # noqa: F401
from .error_list import ErrorList  # noqa: F401
from .unity import UNITY_EDITOR_ROOTS, UnityPanel, find_unity_editors  # noqa: F401
from .pyqt_designer import PREVIEW_STYLE, PyQtDesigner, WIDGETS, build_widget  # noqa: F401
from .new_file import NewFileDialog  # noqa: F401
