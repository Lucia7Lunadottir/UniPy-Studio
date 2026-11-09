"""Внешний вид: шрифт кода, ширина табуляции, стиль отступов, подсветка синтаксиса, тема, размер подсказок."""
from PyQt6.QtCore import QEvent
from PyQt6.QtGui import QColor, QPalette, QFontMetrics
from ..highlighter import Highlighter, PygmentsHighlighter
from ..theme import C
from .completion import DEFAULT_HINT_PT


class AppearanceMixin:
    """Шрифт, тема, отступы, размер подсказок."""

    def set_code_font(self, font):
        """Шрифт кода. Через собственный стиль редактора: глобальный QSS (font-size: 10pt)
        иначе перекрывает setFont() при каждом показе вкладки."""
        self.setStyleSheet('CodeEditor { font-family: "%s"; font-size: %gpt; }' % (font.family(), font.pointSizeF()))
        self.setFont(font)
        self._update_tab_stops(font)
        if hasattr(self, "gutter"):
            self._update_gutter_width()

    def _update_tab_stops(self, font):
        width = getattr(self, "indent", None).tab_width if hasattr(self, "indent") else 4
        self.setTabStopDistance(QFontMetrics(font).horizontalAdvance(" ") * width)

    def set_indent_style(self, style):
        """Стиль отступа файла (indentation.resolve). Ширина таба на экране — из него же."""
        self.indent = style
        self._update_tab_stops(self.font())

    def _make_highlighter(self):
        """Своя подсветка для основных языков, Pygments — для остальных (если он знает язык)."""
        if self.language.builtin_highlighter:
            return Highlighter(self.document(), self.lang)
        lexer = self.language.make_lexer(self.path)
        if lexer is not None:
            return PygmentsHighlighter(self.document(), lexer)
        return Highlighter(self.document(), "plain")

    def _apply_palette(self):
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Text, QColor(C["text"]))
        pal.setColor(QPalette.ColorRole.Base, QColor(C["editor"]))
        self.setPalette(pal)

    def changeEvent(self, e):
        # шрифт кода задаётся стилем и применяется при показе — пересчитать поле номеров строк,
        # иначе оно наезжает на первые символы строк
        if e.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._update_gutter_width()
            self._update_tab_stops(self.font())
        super().changeEvent(e)

    def apply_theme(self):
        """Тема сменилась: фон и текст, подсветка кода, подчёркивания ошибок, номера строк."""
        self._apply_palette()
        self.highlighter.rebuild()
        self.update_selections()
        self.gutter.update()
        self.viewport().update()
        if self._footer is not None:
            self._footer.restyle()

    def hint_pt(self):
        return float(getattr(self.services, "hint_size", DEFAULT_HINT_PT))

    def apply_hint_size(self):
        pt = self.hint_pt()
        self.calltip.set_size(pt)
        self.doc_tip.set_size(pt)
        popup = self.completer.popup()
        popup.doItemsLayout()
        if popup.isVisible():
            self.show_completions_geometry()

    def _tip_html(self, html_):
        return "<div style='font-size:%gpt'>%s</div>" % (self.hint_pt(), html_)
