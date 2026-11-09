"""Подсказки в коде (studio/insights) в окне редактора: рисование поверх текста, щелчок по CodeLens,
курсор-рука над ним. Данные и запросы — у контроллера основного редактора (editor.insights)."""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter


class InsightsMixin:
    insights = None             # InsightController — ставит конструктор CodeEditor

    def paintEvent(self, e):
        super().paintEvent(e)
        ctl = getattr(self, "insights", None)
        if ctl is None:
            return
        p = QPainter(self.viewport())
        try:
            ctl.paint(self, p, [(b, g) for b, g in self._visible_blocks(e.rect()) if g is not None])
        finally:
            p.end()

    def mousePressEvent(self, e):
        if (e.button() == Qt.MouseButton.LeftButton and not e.modifiers()
                and self.insights is not None and self.insights.click(self, e.position())):
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        super().mouseMoveEvent(e)
        if self.insights is not None and not e.buttons() and self.insights.hover(self, e.position()):
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
