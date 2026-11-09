"""Поле слева от кода: номера строк, точки останова, стрелка отладки, полоски Git, лампочка, +/− сворачивания."""
from PyQt6.QtCore import Qt, QRect, QSize, QPoint, QPointF, QRectF
from PyQt6.QtGui import QColor, QPainter, QPen, QPolygonF
from PyQt6.QtWidgets import QWidget
from ..theme import C


BREAKPOINT_COLUMN = 16      # слева на поле номеров строк: точки останова и стрелка текущей строки отладки


class LineNumberArea(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self):
        return QSize(self.editor.gutter_width(), 0)

    def paintEvent(self, event):
        self.editor.paint_gutter(event)

    def mousePressEvent(self, e):
        ed = self.editor
        if e.button() == Qt.MouseButton.LeftButton:
            block = ed.cursorForPosition(QPoint(1, int(e.position().y()))).block()
            x = e.position().x()
            fold = ed.folding.COLUMN if ed.folding.enabled else 0
            if x < BREAKPOINT_COLUMN and ed.breakpoints is not None:     # слева — точки останова, как в VS
                ed.toggle_breakpoint(block.blockNumber() + 1)
                return
            if fold and x >= self.width() - fold:          # +/− — свернуть или развернуть блок
                if ed.folding.range_at(block.blockNumber()) is not None:
                    ed.folding.toggle(block.blockNumber())
                    return
            elif ed._bulb_line is not None and x >= self.width() - fold - 18 \
                    and block.blockNumber() == ed._bulb_line:
                ed.show_bulb_menu()
                return
        super().mousePressEvent(e)


class GutterMixin:
    """Размер и рисование поля номеров строк."""

    def gutter_width(self):
        digits = max(3, len(str(self.blockCount())))
        fold = self.folding.COLUMN if getattr(self, "folding", None) is not None and self.folding.enabled else 0
        return 22 + BREAKPOINT_COLUMN + fold + self.fontMetrics().horizontalAdvance("9") * digits

    def _top_margin(self):
        bar = getattr(self, "navbar", None)
        return bar.bar_height() if bar is not None and not bar.isHidden() else 0

    def _update_gutter_width(self, *_):
        self.setViewportMargins(self.gutter_width(), self._top_margin(), 0, 0)
        self._place_side_widgets()

    def _place_side_widgets(self):
        if not hasattr(self, "gutter"):         # смена шрифта/стиля ещё в конструкторе
            return
        cr = self.contentsRect()
        top = self._top_margin()
        self.gutter.setGeometry(QRect(cr.left(), cr.top() + top, self.gutter_width(), cr.height() - top))
        if top:
            self.navbar.setGeometry(QRect(cr.left(), cr.top(), cr.width(), top))

    def _update_gutter(self, rect, dy):
        if dy:
            self.gutter.scroll(0, dy)
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_side_widgets()

    def paint_gutter(self, event):
        p = QPainter(self.gutter)
        p.fillRect(event.rect(), QColor(C["editor"]))
        block = self.firstVisibleBlock()
        num = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        current = self.textCursor().blockNumber()
        err_lines = {d[0] - 1: d[3] for d in self.diagnostics}
        breakpoints = {b.line: b for b in self.breakpoints.for_path(self.path)} if self.breakpoints is not None else {}
        fold = self.folding.COLUMN if self.folding.enabled else 0
        w = self.gutter.width() - fold          # справа — колонка +/− сворачивания
        h = self.fontMetrics().height()
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                p.setPen(QColor(C["gutter_current"] if num == current else C["gutter_num"]))
                p.drawText(BREAKPOINT_COLUMN, top, w - 14 - BREAKPOINT_COLUMN, h, Qt.AlignmentFlag.AlignRight, str(num + 1))
                if num == self._bulb_line:
                    self._draw_bulb(p, w - 15, top + h / 2 - 7)
                if fold:
                    self.folding.paint_marker(p, num, w, top, h, C["gutter_num"])
                if num in err_lines:
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QColor(C["error"] if err_lines[num] == "error" else C["warning"]))
                    p.drawEllipse(QRectF(BREAKPOINT_COLUMN + 2, top + h / 2 - 3, 6, 6))
                change = self.vcs_markers.get(num + 1)
                if change:
                    self._draw_change(p, change, w - 3, top, h)
                if num + 1 in breakpoints:
                    self._draw_breakpoint(p, breakpoints[num + 1], top + h / 2)
                if num + 1 == self.execution_line:
                    self._draw_execution_arrow(p, top + h / 2, self.execution_is_top)
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            num += 1
        p.end()

    @staticmethod
    def _draw_change(p, kind, x, top, h):
        """Полоска справа от номеров строк — как в Visual Studio и VS Code."""
        if kind == "deleted":
            p.save()
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#E4676B"))
            y = top + h
            p.drawPolygon(QPolygonF([QPointF(x - 2, y - 4), QPointF(x + 3, y), QPointF(x - 2, y + 4)]))
            p.restore()
            return
        p.fillRect(QRectF(x, top, 3, h), QColor("#57AB5A" if kind == "added" else "#4C9BE8"))

    @staticmethod
    def _draw_breakpoint(p, bp, cy):
        """Красный круг; выключенная — пустой круг; не привязалась (нет кода на строке) — круг с дыркой."""
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor("#E51400")
        rect = QRectF(BREAKPOINT_COLUMN / 2 - 5.5, cy - 5.5, 11, 11)
        if not bp.enabled:
            p.setPen(QPen(color, 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(rect.adjusted(0.75, 0.75, -0.75, -0.75))
        else:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawEllipse(rect)
            if bp.condition:                            # условная — белый «+», как в VS
                p.setPen(QPen(QColor("#FFFFFF"), 1.6))
                c = rect.center()
                p.drawLine(QPointF(c.x() - 3, c.y()), QPointF(c.x() + 3, c.y()))
                p.drawLine(QPointF(c.x(), c.y() - 3), QPointF(c.x(), c.y() + 3))
            elif bp.verified is False:
                p.setBrush(QColor(C["editor"]))
                p.drawEllipse(rect.adjusted(3, 3, -3, -3))
        p.restore()

    @staticmethod
    def _draw_execution_arrow(p, cy, top):
        """Жёлтая стрелка — где остановилась программа; зелёная — выбранный кадр стека выше (как в VS)."""
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor("#5A4A00" if top else "#1F4D1F"), 1))
        p.setBrush(QColor("#FFE000" if top else "#7BC27B"))
        x = 2
        p.drawPolygon(QPolygonF([QPointF(x, cy - 3), QPointF(x + 6, cy - 3), QPointF(x + 6, cy - 6.5),
                                 QPointF(x + 12.5, cy), QPointF(x + 6, cy + 6.5), QPointF(x + 6, cy + 3),
                                 QPointF(x, cy + 3)]))
        p.restore()

    @staticmethod
    def _draw_bulb(p, x, y):
        """Лампочка быстрых действий (жёлтая колба и серый цоколь)."""
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#F5C518"))
        p.drawEllipse(QRectF(x + 1.5, y, 9, 9))
        p.setBrush(QColor("#9A9A9A"))
        p.drawRoundedRect(QRectF(x + 3.5, y + 9, 5, 2.5), 1, 1)
        p.restore()
