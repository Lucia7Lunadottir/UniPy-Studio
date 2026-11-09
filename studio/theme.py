"""Темы UniPy Studio и нарисованные значки.

Цвета берутся по ролям из словаря C (фон редактора, панель, текст, выделение, подсветка кода…).
Источники ролей:
  • тема персонажа из equestria-qt-kit (её переключатель «Тема персонажа»): тёмная или светлая;
  • свои темы UniPy — тёмная и светлая в духе Visual Studio (без kit — только они).
Светлая или тёмная: как в KDE (по умолчанию) или выбранная вручную в «Вид → Тема».
"""
import os
import re
import sys
import tempfile

from PyQt6.QtCore import Qt, QRectF, QPointF, QObject, QSettings, pyqtSignal
from PyQt6.QtGui import (QColor, QPalette, QPixmap, QPainter, QIcon, QFont,
                         QPolygonF, QPen, QFontDatabase)
from .i18n import tr

try:    # необязательно: без equestria-qt-kit работают только свои темы UniPy
    from equestria_kit import themes as kit_themes, qt as kit_qt
    if not hasattr(kit_qt, "light_variant"):
        raise ImportError("equestria-qt-kit старше 1.2")
except Exception:   # noqa: BLE001 — любая ошибка kit не должна мешать запуску
    kit_themes = kit_qt = None

KIT_AVAILABLE = kit_themes is not None


def _kit_system_name():
    """Тема «Как в системе» — персонаж из переключателя. kit ≥ 1.3 отдаёт его напрямую;
    в старом kit current_name() читал и общий theme.json, который переписывали меню других приложений."""
    return (getattr(kit_themes, "system_name", None) or kit_themes.current_name)()

# ---------------------------------------------------------------- свои темы (как Visual Studio)
VS_DARK = {
    "editor": "#1E1E1E", "panel": "#252526", "chrome": "#2D2D30", "menu": "#1B1B1C", "menu_border": "#333337",
    "hover": "#3E3E40", "hover_soft": "#2A2D2E", "border": "#3F3F46", "alt_row": "#2A2A2B",
    "text": "#D4D4D4", "bright": "#F1F1F1", "dim": "#858585", "hint": "#9D9D9D", "tab_text": "#C8C8C8",
    "disabled": "#656565", "accent": "#007ACC", "on_accent": "#FFFFFF",
    "primary": "#0E639C", "primary_hover": "#1177BB", "status_bg": "#007ACC", "status_fg": "#FFFFFF",
    "select": "#264F78", "tree_select": "#094771", "tree_select_text": "#FFFFFF", "completion_sel": "#04395E",
    "current_line": "#282828", "input_bg": "#333337", "button": "#3E3E42", "button_border": "#555558",
    "button_hover": "#4A4A4F", "scroll": "#424242", "scroll_hover": "#4F4F4F", "tooltip_border": "#454545",
    "group_title": "#9CDCFE", "link": "#4FA6FF", "error": "#F14C4C", "warning": "#CCA700", "ok": "#89D185",
    "find_bg": "#613214", "bracket_bg": "#3B514D", "gutter_num": "#858585", "gutter_current": "#C6C6C6",
    "icon": "#C5C5C5", "icon_accent": "#75BEFF", "icon_green": "#89D185", "icon_red": "#F48771",
    "tip_doc": "#C8C8C8",
}
VS_LIGHT = {
    "editor": "#FFFFFF", "panel": "#F5F5F5", "chrome": "#EEEEF2", "menu": "#F6F6F6", "menu_border": "#CCCEDB",
    "hover": "#C9DEF5", "hover_soft": "#E8EFF7", "border": "#CCCEDB", "alt_row": "#F0F0F0",
    "text": "#1E1E1E", "bright": "#000000", "dim": "#717171", "hint": "#6D6D6D", "tab_text": "#444444",
    "disabled": "#A2A4A5", "accent": "#007ACC", "on_accent": "#FFFFFF",
    "primary": "#007ACC", "primary_hover": "#1C97EA", "status_bg": "#007ACC", "status_fg": "#FFFFFF",
    "select": "#ADD6FF", "tree_select": "#CCE8FF", "tree_select_text": "#000000", "completion_sel": "#CCE8FF",
    "current_line": "#F2F2F2", "input_bg": "#FFFFFF", "button": "#ECECF0", "button_border": "#CCCEDB",
    "button_hover": "#E0E3E6", "scroll": "#C2C3C9", "scroll_hover": "#A6A6A6", "tooltip_border": "#CCCEDB",
    "group_title": "#1F6FB2", "link": "#006AB1", "error": "#E51400", "warning": "#BF8803", "ok": "#388A34",
    "find_bg": "#F6D48B", "bracket_bg": "#DBDBDB", "gutter_num": "#237893", "gutter_current": "#0B216F",
    "icon": "#424242", "icon_accent": "#1F6FB2", "icon_green": "#388A34", "icon_red": "#C72E0F",
    "tip_doc": "#3B3B3B",
}
# подсветка кода: цвета Visual Studio (Dark+ / Light+), подстраиваются под контраст фона
SYNTAX_DARK = {"keyword": "#569CD6", "control": "#C586C0", "type": "#4EC9B0", "string": "#D69D85",
               "comment": "#57A64A", "number": "#B5CEA8", "function": "#DCDCAA", "param": "#9CDCFE",
               "decorator": "#4EC9B0", "tag": "#569CD6", "attr": "#9CDCFE", "preproc": "#9B9B9B"}
SYNTAX_LIGHT = {"keyword": "#0000FF", "control": "#8F08C4", "type": "#2B91AF", "string": "#A31515",
                "comment": "#008000", "number": "#098658", "function": "#795E26", "param": "#001080",
                "decorator": "#2B91AF", "tag": "#800000", "attr": "#E50000", "preproc": "#808080"}

C = dict(VS_DARK, **SYNTAX_DARK)    # текущие роли; меняются на месте при смене темы

MODES = (("auto", "theme.follow_kde"), ("light", "theme.light"), ("dark", "theme.dark"))
UNIPY_SOURCE = "unipy"
SYSTEM_SOURCE = "system"


def _mix(a, b, k):
    ca, cb = QColor(a), QColor(b)
    return QColor(round(ca.red() + (cb.red() - ca.red()) * k), round(ca.green() + (cb.green() - ca.green()) * k),
                  round(ca.blue() + (cb.blue() - ca.blue()) * k)).name()


def _contrast(a, b):
    def lum(h):
        c = QColor(h)
        ch = [x / 255 for x in (c.red(), c.green(), c.blue())]
        ch = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in ch]
        return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _readable(color, background, target=4.5):
    """Тот же оттенок, осветлённый (на тёмном фоне) или затемнённый (на светлом) до контраста ≥ target."""
    if _contrast(color, background) >= target:
        return color
    h, s, l, _a = QColor(color).getHslF()
    dark_bg = QColor(background).lightnessF() < .5
    cand = color
    for _ in range(40):
        l = min(1.0, l + .025) if dark_bg else max(0.0, l - .025)
        cand = QColor.fromHslF(max(h, 0.0), s, l).name()
        if _contrast(cand, background) >= target:
            break
    return cand


def syntax_for(light, editor_bg):
    base = SYNTAX_LIGHT if light else SYNTAX_DARK
    return {k: _readable(v, editor_bg, 4.5) for k, v in base.items()}


def roles_from_character(k, light):
    """Роли UniPy из палитры персонажа kit (k — 12 ролей kit + производные)."""
    R, m = _readable, _mix
    base, text, dim = k["base"], k["text"], k["subtext"]
    acc, acc2 = k["accent"], k.get("accent2", k["accent"])
    if light:
        editor, panel, chrome = m(base, "#ffffff", .75), base, k["mantle"]
        menu = editor
    else:
        editor, panel, chrome = k["mantle"], base, m(base, k["surface0"], .55)
        menu = m(k["mantle"], k["crust"], .5)
    on_accent = kit_qt.derived_colors(k)["on_accent"]
    button = k["surface0"] if light else k["surface1"]
    r = {
        "editor": editor, "panel": panel, "chrome": chrome, "menu": menu,
        "menu_border": m(menu, k["surface2"], .6), "hover": k["surface1"], "hover_soft": m(panel, k["surface1"], .5),
        "border": m(panel, k["surface2"], .55), "alt_row": m(panel, text, .025),
        "text": text, "bright": m(text, "#000000" if light else "#ffffff", .5), "dim": dim, "hint": dim,
        "tab_text": m(text, panel, .15), "disabled": m(dim, panel, .45),
        "accent": acc, "on_accent": on_accent, "primary": acc,
        "primary_hover": (QColor(acc).darker(110) if light else QColor(acc).lighter(112)).name(),
        "status_bg": acc, "status_fg": on_accent,
        "select": m(editor, acc, .22 if light else .28), "tree_select": m(panel, acc, .25 if light else .38),
        "tree_select_text": m(text, "#000000" if light else "#ffffff", .5), "completion_sel": m(menu, acc, .25 if light else .40),
        "current_line": m(editor, text, .045), "input_bg": editor if light else k["surface0"],
        "button": button, "button_border": k["surface2"], "button_hover": m(button, acc, .15),
        "scroll": k["surface1"], "scroll_hover": k["surface2"], "tooltip_border": k["surface2"],
        "group_title": R(acc2, panel), "link": R(acc2, editor), "error": R(k["red"], editor),
        "warning": R(k["yellow"], editor), "ok": R(k["green"], editor),
        "find_bg": m(editor, k["yellow"], .30), "bracket_bg": m(editor, k["green"], .25),
        "gutter_num": m(dim, editor, .25), "gutter_current": text,
        "icon": m(text, panel, .15), "icon_accent": R(acc2, panel, 3), "icon_green": R(k["green"], panel, 3),
        "icon_red": R(k["red"], panel, 3), "tip_doc": m(text, panel, .12),
    }
    r.update(syntax_for(light, editor))
    return r


def render(template):
    """@роль@ → текущий цвет роли (QSS-шаблоны UniPy)."""
    return re.sub(r"@([a-z_0-9]+)@", lambda mm: C.get(mm.group(1), mm.group(0)), template)


_STYLE_PROP = "_unipy_style_template"


def style(widget, template):
    """Стиль виджета по ролям — перерисуется сам при смене темы (вместо setStyleSheet с цветами)."""
    widget.setProperty(_STYLE_PROP, template)
    widget.setStyleSheet(render(template))


def role_of(color):
    """Какой роли принадлежит цвет (чтобы уже написанный текст перекрасить при смене темы)."""
    low = (color or "").lower()
    return next((k for k, v in C.items() if v.lower() == low), None)


STYLE = """
QWidget { background: @panel@; color: @text@; font-size: 10pt; }
QMainWindow::separator { background: @chrome@; width: 4px; height: 4px; }
QMenuBar { background: @chrome@; border: none; padding: 2px; }
QMenuBar::item { background: transparent; padding: 4px 9px; }
QMenuBar::item:selected { background: @hover@; }
QMenu { background: @menu@; border: 1px solid @menu_border@; padding: 3px; }
QMenu::item { padding: 5px 28px 5px 24px; }
QMenu::item:selected { background: @hover@; }
QMenu::item:disabled { color: @disabled@; }
QMenu::separator { height: 1px; background: @menu_border@; margin: 3px 6px; }
QToolBar { background: @chrome@; border: none; border-bottom: 1px solid @editor@; spacing: 3px; padding: 3px; }
QToolBar QLabel { background: transparent; color: @dim@; padding: 0 3px; }
QToolButton { background: transparent; border: 1px solid transparent; border-radius: 2px; padding: 3px 5px; }
QToolButton:hover { background: @hover@; border-color: @hover@; }
QToolButton:pressed { background: @select@; border-color: @accent@; }
QToolButton:checked { background: @hover_soft@; border-color: @border@; }
QToolButton:disabled { color: @disabled@; }
QToolButton#runButton { color: @bright@; padding: 3px 8px; }
QDockWidget { color: @text@; }
QFrame#dockTitle { background: @chrome@; border-bottom: 1px solid @border@; }
QLabel#dockTitleText { background: transparent; color: @text@; }
QFrame#dockTitle QToolButton { padding: 1px; }
QDockWidget::title { background: @chrome@; padding: 5px 8px; font-weight: normal; text-align: left; border-bottom: 1px solid @border@; }
QTabWidget::pane { border: none; background: @editor@; }
QTabBar { background: @chrome@; }
QTabBar::tab { background: @chrome@; color: @tab_text@; padding: 6px 12px; border: none; margin-right: 1px; }
QTabBar::tab:selected { background: @editor@; color: @bright@; border-top: 2px solid @accent@; }
QTabBar::tab:hover:!selected { background: @hover@; }
QTabBar#dockTabs::tab { padding: 4px 10px; }
QTabBar#dockTabs::tab:selected { background: @panel@; color: @link@; border-top: none; border-bottom: 2px solid @accent@; }
QTreeWidget, QTreeView, QListWidget, QListView, QTableWidget {
    background: @panel@; border: none; outline: 0; alternate-background-color: @alt_row@; }
QTreeWidget::item, QListWidget::item { padding: 2px 0; }
QTreeWidget::item:hover, QListWidget::item:hover, QTableWidget::item:hover { background: @hover_soft@; }
QTreeWidget::item:selected, QListWidget::item:selected, QTableWidget::item:selected { background: @tree_select@; color: @tree_select_text@; }
QHeaderView::section { background: @chrome@; color: @tab_text@; border: none; border-right: 1px solid @border@; padding: 4px 6px; }
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit#plainField, QTextEdit {
    background: @input_bg@; border: 1px solid @border@; padding: 3px 5px; selection-background-color: @select@; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: @accent@; }
QComboBox::drop-down { border: none; width: 18px; }
QComboBox QAbstractItemView { background: @menu@; border: 1px solid @menu_border@; selection-background-color: @hover@; }
QPushButton { background: @button@; border: 1px solid @button_border@; padding: 4px 12px; border-radius: 2px; }
QPushButton:hover { background: @button_hover@; border-color: @accent@; }
QPushButton:pressed { background: @select@; }
QPushButton:disabled { color: @disabled@; }
QPushButton#primary { background: @primary@; border-color: @primary_hover@; color: @on_accent@; }
QPushButton#primary:hover { background: @primary_hover@; }
QCheckBox, QRadioButton { background: transparent; }
QStatusBar { background: @status_bg@; color: @status_fg@; }
QStatusBar::item { border: none; }
QStatusBar QLabel { background: transparent; color: @status_fg@; padding: 0 8px; }
QScrollBar:vertical { background: @editor@; width: 13px; margin: 0; }
QScrollBar::handle:vertical { background: @scroll@; min-height: 24px; margin: 2px 3px; }
QScrollBar::handle:vertical:hover { background: @scroll_hover@; }
QScrollBar:horizontal { background: @editor@; height: 13px; margin: 0; }
QScrollBar::handle:horizontal { background: @scroll@; min-width: 24px; margin: 3px 2px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }
QPlainTextEdit { background: @editor@; border: none; selection-background-color: @select@; }
QToolTip { background: @panel@; color: @text@; border: 1px solid @tooltip_border@; padding: 4px; }
QSplitter::handle { background: @chrome@; }
QGroupBox { border: 1px solid @border@; margin-top: 14px; padding: 8px 6px 6px 6px; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; color: @group_title@; }
QLabel#hint { color: @hint@; background: transparent; }
QLabel#sectionTitle { color: @bright@; font-weight: bold; background: transparent; }
QFrame#formPreview { background: #F0F0F0; border: 1px solid @border@; }
QFrame#findBar { background: @panel@; border-bottom: 1px solid @border@; }
"""


class ThemeController(QObject):
    """Какая тема сейчас: источник цветов (как в системе / персонаж из kit / своя UniPy) ×
    режим (как в KDE / светлая / тёмная). Это два независимых выбора: смена одного не трогает другой.
    Следит за переключателем персонажей и за светлой/тёмной схемой KDE, перекрашивает на лету."""
    changed = pyqtSignal()

    def __init__(self, app, settings=None):
        super().__init__(app)
        self.app = app
        self.settings = settings or QSettings("UniPyStudio", "UniPyStudio")
        self.mode = str(self.settings.value("theme/mode", "auto"))
        self.source = str(self.settings.value("theme/source", SYSTEM_SOURCE))
        self._system_name = ""
        self.kit_name = ""
        local_name = self.settings.value("theme/character", "", type=str)
        if self.source == "character" and not local_name:
            self.source = SYSTEM_SOURCE
        self.error = ""
        if KIT_AVAILABLE:
            try:
                self._system_name = _kit_system_name()
                self.kit_name = (kit_themes.registry().resolve(local_name)
                                 if self.source == "character" else self._system_name)
                kit_themes.follow(self._on_character_changed, lambda: self._system_name)
                self._scheme = kit_qt.SystemSchemeWatcher(lambda _dark: self.mode == "auto" and self.apply())
            except Exception as e:   # noqa: BLE001
                self.error = "equestria-qt-kit: %s" % e
        self.apply()

    # ---- выбор
    @property
    def uses_character(self):
        return KIT_AVAILABLE and self.source != UNIPY_SOURCE and bool(self.kit_name)

    def is_light(self):
        """Только из режима — от источника цветов не зависит."""
        if self.mode in ("light", "dark"):
            return self.mode == "light"
        dark = kit_qt.system_prefers_dark() if KIT_AVAILABLE else _kde_prefers_dark()
        return dark is False

    def characters(self):
        return list(kit_themes.THEMES) if KIT_AVAILABLE else []

    def set_mode(self, mode):
        self.mode = mode
        self.settings.setValue("theme/mode", mode)
        self.apply()

    def use_system(self):
        self.source = SYSTEM_SOURCE
        self.settings.setValue("theme/source", self.source)
        if KIT_AVAILABLE:
            self._system_name = self.kit_name = _kit_system_name()
        self.apply()

    def use_unipy(self):
        self.source = UNIPY_SOURCE
        self.settings.setValue("theme/source", UNIPY_SOURCE)
        self.apply()

    def use_character(self, name):
        """Выбирает палитру персонажа для UniPy Studio."""
        self.source = "character"
        self.settings.setValue("theme/source", "character")
        self.kit_name = kit_themes.registry().resolve(name)
        self.settings.setValue("theme/character", self.kit_name)
        self.apply()

    def _on_character_changed(self, name):
        self._system_name = kit_themes.registry().resolve(name)
        if self.source == SYSTEM_SOURCE:
            self.kit_name = self._system_name
            self.apply()
        return self._system_name

    # ---- расчёт и применение
    def roles(self):
        light = self.is_light()
        if self.uses_character:
            try:
                k = dict(kit_themes.THEMES[self.kit_name])
                if light and _contrast(k["base"], "#000000") < _contrast(k["base"], "#ffffff"):
                    k = kit_qt.light_variant(k)   # светлый вариант тёмной палитры персонажа
                k.update({kk: v for kk, v in kit_qt.derived_colors(k).items() if kk not in k})
                return roles_from_character(k, light)
            except Exception as e:   # noqa: BLE001 — сбой в палитре персонажа: своя тема, а не поломка
                self.error = tr("theme.character_theme") % e
                print(tr("theme.unipy_studio_using_built_theme") % self.error, file=sys.stderr)
        own = dict(VS_LIGHT if light else VS_DARK)
        own.update(syntax_for(light, own["editor"]))
        return own

    def title(self):
        variant = tr("theme.variant_light") if self.is_light() else tr("theme.variant_dark")
        name = self.kit_name if self.uses_character else tr("common.unipy_vs_theme")
        return "%s, %s" % (name, variant)

    def apply(self):
        C.clear()
        C.update(self.roles())
        _cache.clear()
        p = QPalette()
        for role, key in (("Window", "panel"), ("WindowText", "text"), ("Base", "editor"), ("AlternateBase", "alt_row"),
                          ("Text", "text"), ("Button", "button"), ("ButtonText", "text"), ("Highlight", "select"),
                          ("HighlightedText", "bright"), ("ToolTipBase", "panel"), ("ToolTipText", "text"),
                          ("PlaceholderText", "dim"), ("Link", "link")):
            p.setColor(getattr(QPalette.ColorRole, role), QColor(C[key]))
        self.app.setPalette(p)
        close_png = _write_close_icon()
        extra = _checkbox_style()
        if close_png:
            extra += ("QTabBar::close-button { image: url(%s); subcontrol-position: right; border: none;"
                      " padding: 1px; margin-left: 4px; }\n"
                      "QTabBar::close-button:hover { background: %s; border-radius: 2px; }\n" % (close_png, C["hover"]))
        self.app.setStyleSheet(render(STYLE) + extra)
        for w in self.app.allWidgets():
            tpl = w.property(_STYLE_PROP)
            if isinstance(tpl, str):
                w.setStyleSheet(render(tpl))
        self.changed.emit()


def _kde_prefers_dark():
    """Без kit: тот же признак, что у kit — фон окон в ~/.config/kdeglobals."""
    path = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "kdeglobals")
    section = ""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line.startswith("["):
                    section = line
                elif section == "[Colors:Window]" and line.startswith("BackgroundNormal="):
                    r, g, b = (int(x) for x in line.split("=", 1)[1].split(",")[:3])
                    return QColor(r, g, b).lightnessF() < .5
    except (OSError, ValueError):
        pass
    return None


CONTROLLER = None


def apply(app, settings=None):
    """Шрифты интерфейса и тема. Возвращает ThemeController (он же theme.CONTROLLER)."""
    global CONTROLLER
    app.setStyle("Fusion")
    for family in ("Segoe UI", "Noto Sans", "Cantarell", "DejaVu Sans"):
        if family in QFontDatabase.families():
            app.setFont(QFont(family, 10))
            for cls in ("QDockWidget", "QDockWidgetTitleButton", "QMenuBar", "QMenu", "QTabBar",
                        "QToolTip", "QHeaderView", "QLabel", "QComboBox"):
                app.setFont(QFont(family, 10), cls)
            break
    CONTROLLER = ThemeController(app, settings)
    return CONTROLLER


def _write_svg(name, svg):
    folder = os.path.join(tempfile.gettempdir(), "unipy-studio-theme-%d" % os.getuid())
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(svg)
    except OSError:
        return None
    return path.replace("\\", "/")


def _checkbox_style():
    """Флажки, заметные и пустыми: рамка, отмеченный — акцентная заливка с галочкой (как в VS).
    Fusion с глобальной таблицей стилей рисовал пустой флажок почти невидимым."""
    mark = C["on_accent"]
    check = _write_svg("check-%s.svg" % mark.lstrip("#"),
                       '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12">'
                       '<path d="M2.2 6.3 L4.9 9 L9.9 3.4" fill="none" stroke="%s" stroke-width="1.9" '
                       'stroke-linecap="round" stroke-linejoin="round"/></svg>' % mark)
    dash = _write_svg("dash-%s.svg" % mark.lstrip("#"),
                      '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12">'
                      '<path d="M3 6 H9" stroke="%s" stroke-width="2" stroke-linecap="round"/></svg>' % mark)
    sel = "QCheckBox::indicator, QTreeView::indicator, QListView::indicator, QTableView::indicator, QGroupBox::indicator"
    css = ("%s { width: 13px; height: 13px; border: 1px solid %s; border-radius: 3px; background: %s; }\n"
           % (sel, C["dim"], C["input_bg"]))
    css += "%s { border-color: %s; }\n" % (sel.replace("::indicator", "::indicator:hover"), C["accent"])
    css += "%s { background: %s; border-color: %s;%s }\n" % (
        sel.replace("::indicator", "::indicator:checked"), C["accent"], C["accent"],
        " image: url(%s);" % check if check else "")
    css += "%s { background: %s; border-color: %s;%s }\n" % (
        sel.replace("::indicator", "::indicator:indeterminate"), C["accent"], C["accent"],
        " image: url(%s);" % dash if dash else "")
    css += "%s { border-color: %s; background: %s; }\n" % (
        sel.replace("::indicator", "::indicator:disabled"), C["border"], C["panel"])
    return css


def _write_close_icon():
    path = os.path.join(tempfile.gettempdir(), "unipy-studio-close-%s.png" % C["icon"].lstrip("#"))
    pm = QPixmap(28, 28)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(QColor(C["icon"]), 2.6))
    p.drawLine(QPointF(8, 8), QPointF(20, 20))
    p.drawLine(QPointF(20, 8), QPointF(8, 20))
    p.end()
    return path.replace("\\", "/") if pm.save(path, "PNG") else None


def code_font(size=11):
    for family in ("JetBrains Mono", "Cascadia Code", "Fira Code", "Source Code Pro",
                   "DejaVu Sans Mono", "Liberation Mono", "Noto Sans Mono"):
        if family in QFontDatabase.families():
            f = QFont(family, size)
            break
    else:
        f = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        f.setPointSize(size)
    f.setStyleHint(QFont.StyleHint.Monospace)
    f.setFixedPitch(True)
    return f


# ---------------------------------------------------------------- значки

_cache = {}


def _pixmap(size=16):
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.GlobalColor.transparent)
    return pm


def badge(text, bg, fg="#FFFFFF", rounded=3, size=16):
    key = ("badge", text, bg, fg, size)
    if key in _cache:
        return _cache[key]
    pm = _pixmap(size)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(bg))
    p.drawRoundedRect(QRectF(1, 2, size - 2, size - 4), rounded, rounded)
    f = QFont()
    f.setPixelSize(8 if len(text) > 1 else 10)
    f.setBold(True)
    p.setFont(f)
    p.setPen(QColor(fg))
    p.drawText(QRectF(0, 1, size, size - 2), Qt.AlignmentFlag.AlignCenter, text)
    p.end()
    icon = QIcon(pm)
    _cache[key] = icon
    return icon


def folder_icon(open_=False):
    key = ("folder", open_)
    if key in _cache:
        return _cache[key]
    pm = _pixmap()
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#C09553"))
    p.drawRoundedRect(QRectF(1, 3, 6, 3), 1, 1)
    p.setBrush(QColor("#DCB67A"))
    p.drawRoundedRect(QRectF(1, 4.5, 14, 9.5), 1.5, 1.5)
    p.end()
    icon = QIcon(pm)
    _cache[key] = icon
    return icon


def file_icon(name):
    low = name.lower()
    if low.endswith((".py", ".pyw")):
        return badge("py", "#3572A5", "#FFD43B")
    if low.endswith(".cs"):
        return badge("C#", "#68217A")
    if low.endswith(".ui"):
        return badge("ui", "#2F9E44")
    if low.endswith(".json"):
        return badge("{}", "#5C5C2E", "#F1E05A")
    if low.endswith((".md", ".txt", ".rst")):
        return badge("≡", "#4D4D4D", "#D4D4D4")
    if low.endswith((".unity", ".prefab", ".asset", ".mat")):
        return badge("◆", "#333333", "#E0E0E0")
    if low.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg")):
        return badge("▣", "#7A4D8C")
    if low.endswith((".shader", ".hlsl", ".compute")):
        return badge("S", "#5B6E2A")
    if low.endswith((".xml", ".uxml", ".uss", ".qss", ".css")):
        return badge("<>", "#8A5A00")
    return badge("·", "#4D4D4D", "#D4D4D4")


KIND_ICON = {
    # тип подсказки: (буква, фон)
    "function": ("f", "#7B4BA8"), "method": ("m", "#7B4BA8"),
    "class": ("C", "#B5730F"), "struct": ("S", "#B5730F"), "enum": ("E", "#B5730F"),
    "instance": ("v", "#1F6FB2"), "statement": ("v", "#1F6FB2"), "param": ("a", "#1F6FB2"),
    "variable": ("v", "#1F6FB2"), "property": ("p", "#2B7A78"), "field": ("p", "#2B7A78"),
    "module": ("M", "#555555"), "keyword": ("k", "#4D4D4D"), "path": ("/", "#555555"),
    "event": ("e", "#A8571F"), "const": ("c", "#B5730F"), "snippet": ("…", "#4D4D4D"),
    "type": ("T", "#2B7A78"), "word": ("w", "#4D4D4D"),
    "interface": ("I", "#2B7A78"), "namespace": ("N", "#555555"),
}


def kind_icon(kind):
    letter, bg = KIND_ICON.get(kind, ("•", "#4D4D4D"))
    return badge(letter, bg)


def tool_icon(name):
    key = ("tool", name)
    if key in _cache:
        return _cache[key]
    pm = _pixmap()
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if name == "run":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(C["icon_green"]))
        p.drawPolygon(QPolygonF([QPointF(4, 2.5), QPointF(13.5, 8), QPointF(4, 13.5)]))
    elif name == "stop":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(C["icon_red"]))
        p.drawRect(QRectF(4, 4, 8, 8))
    elif name == "new":
        p.setPen(QPen(QColor(C["icon"]), 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolygon(QPolygonF([QPointF(3.5, 1.5), QPointF(9.5, 1.5), QPointF(12.5, 4.5),
                                 QPointF(12.5, 14.5), QPointF(3.5, 14.5)]))
        p.setPen(QPen(QColor(C["icon_green"]), 2))
        p.drawLine(QPointF(11, 9), QPointF(11, 15))
        p.drawLine(QPointF(8, 12), QPointF(14, 12))
    elif name == "open":
        p.end()
        return folder_icon()
    elif name in ("save", "saveall"):
        p.setPen(Qt.PenStyle.NoPen)
        if name == "saveall":
            p.setBrush(QColor(C["icon_accent"]).darker(135))
            p.drawRoundedRect(QRectF(4.5, 0.5, 11, 11), 1, 1)
        p.setBrush(QColor(C["icon_accent"]))
        p.drawRoundedRect(QRectF(1.5, 3.5, 11, 11), 1, 1)
        p.setBrush(QColor(C["editor"]))
        p.drawRect(QRectF(4, 4.5, 6, 3.5))
        p.drawRect(QRectF(4.5, 10, 5, 4))
    elif name == "build":
        p.setPen(QPen(QColor(C["icon"]), 2.2, cap=Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(4, 13), QPointF(10, 7))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(C["icon"]))
        p.drawRoundedRect(QRectF(8, 2, 7, 5), 1, 1)
    elif name == "allfiles":
        # два листа друг на друге — «Показать все файлы», как в Visual Studio
        p.setPen(QPen(QColor(C["icon"]), 1.1))
        p.setBrush(QColor(C["chrome"]))
        p.drawRect(QRectF(5.5, 1.5, 8, 10))
        p.drawRect(QRectF(2.5, 4.5, 8, 10))
        p.setPen(QPen(QColor(C["icon_accent"]), 1.1))
        for y in (8, 10.5):
            p.drawLine(QPointF(4.5, y), QPointF(8.5, y))
    elif name in ("back", "forward"):
        # круг со стрелкой, как «Назад/Вперёд» на панели Visual Studio
        p.setPen(QPen(QColor(C["icon_accent"]), 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(1.5, 1.5, 13, 13))
        p.setPen(QPen(QColor(C["icon_accent"]), 1.6, cap=Qt.PenCapStyle.RoundCap, join=Qt.PenJoinStyle.RoundJoin))
        k = -1 if name == "back" else 1
        p.drawLine(QPointF(8 - 3.5 * k, 8), QPointF(8 + 3.5 * k, 8))
        p.drawPolyline(QPolygonF([QPointF(8 - 0.5 * k, 5), QPointF(8 + 3 * k, 8), QPointF(8 - 0.5 * k, 11)]))
    elif name == "unity":
        p.setPen(QPen(QColor(C["icon"]), 1.3))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolygon(QPolygonF([QPointF(8, 1.5), QPointF(14, 5), QPointF(14, 11.5),
                                 QPointF(8, 15), QPointF(2, 11.5), QPointF(2, 5)]))
        p.drawLine(QPointF(8, 8.2), QPointF(8, 15))
        p.drawLine(QPointF(8, 8.2), QPointF(2, 5))
        p.drawLine(QPointF(8, 8.2), QPointF(14, 5))
    elif name == "refresh":
        p.setPen(QPen(QColor(C["icon"]), 1.6))
        p.drawArc(QRectF(3, 3, 10, 10), 30 * 16, 290 * 16)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(C["icon"]))
        p.drawPolygon(QPolygonF([QPointF(10, 1), QPointF(14, 4.5), QPointF(9.5, 6.5)]))
    elif name == "collapse":
        p.setPen(QPen(QColor(C["icon"]), 1.2))
        p.drawRect(QRectF(3.5, 3.5, 9, 9))
        p.drawLine(QPointF(5.5, 8), QPointF(10.5, 8))
    elif name == "clear":
        p.setPen(QPen(QColor(C["icon"]), 1.6))
        p.drawLine(QPointF(4, 4), QPointF(12, 12))
        p.drawLine(QPointF(12, 4), QPointF(4, 12))
    elif name == "qt":
        p.end()
        return badge("Qt", "#2F9E44")
    elif name == "find":
        p.setPen(QPen(QColor(C["icon"]), 1.6))
        p.drawEllipse(QRectF(2.5, 2.5, 8, 8))
        p.drawLine(QPointF(9.5, 9.5), QPointF(14, 14))
    elif name == "terminal":                    # окно с подсказкой «>_»
        p.setPen(QPen(QColor(C["icon"]), 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(1.5, 2.5, 13, 11), 1.5, 1.5)
        p.drawPolyline(QPolygonF([QPointF(4, 6), QPointF(6.5, 8), QPointF(4, 10)]))
        p.drawLine(QPointF(8, 10.5), QPointF(11.5, 10.5))
    p.end()
    icon = QIcon(pm)
    _cache[key] = icon
    return icon


def app_icon():
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#68217A"))
    p.drawRoundedRect(QRectF(2, 2, 60, 60), 12, 12)
    p.setBrush(QColor("#FFFFFF"))
    p.drawPolygon(QPolygonF([QPointF(16, 18), QPointF(30, 32), QPointF(16, 46), QPointF(12, 42),
                             QPointF(22, 32), QPointF(12, 22)]))
    p.drawRect(QRectF(32, 42, 20, 5))
    p.end()
    return QIcon(pm)
