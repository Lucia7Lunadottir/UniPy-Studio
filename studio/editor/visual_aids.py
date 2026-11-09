"""Видимые пробелы и табуляции (Ctrl+R, Ctrl+W) и направляющие отступов — как в Visual Studio.
Рисуются поверх текста в paintEvent; сам текст и его разметка не меняются."""
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen

from ..theme import C


def indent_columns(text, tab_width):
    """Ширина отступа строки в колонках (таб — до следующей позиции табуляции); None — строка пустая."""
    col = 0
    for ch in text:
        if ch == " ":
            col += 1
        elif ch == "\t":
            col += tab_width - col % tab_width
        else:
            return col
    return None


def guide_levels(indents, step):
    """Колонки направляющих для каждой строки. Пустые строки берут меньший из отступов соседей,
    чтобы линии не рвались на пустых строках внутри блока."""
    n = len(indents)
    filled = list(indents)
    prev = [0] * n
    last = 0
    for i, v in enumerate(indents):
        if v is not None:
            last = v
        prev[i] = last
    nxt = 0
    for i in range(n - 1, -1, -1):
        if indents[i] is not None:
            nxt = indents[i]
        elif filled[i] is None:
            filled[i] = min(prev[i], nxt)
    step = max(1, step)
    return [list(range(0, width, step)) if width else [] for width in filled]


class VisualAidsMixin:
    """Что показать поверх текста: точки пробелов, стрелки табов, направляющие отступов."""
    show_whitespace = False
    indent_guides = True

    def set_view_option(self, name, value):
        """Флажок вида редактора (см. MainWindow.EDITOR_VIEW_OPTIONS) — свойство и перерисовка."""
        setattr(self, name, value)
        self.viewport().update()

    def paintEvent(self, e):
        super().paintEvent(e)
        if not (self.show_whitespace or self.indent_guides):
            return
        p = QPainter(self.viewport())
        try:
            blocks = self._visible_blocks(e.rect())
            if self.indent_guides:
                self._paint_indent_guides(p, blocks)
            if self.show_whitespace:
                self._paint_whitespace(p, blocks, e.rect())
        finally:
            p.end()

    def _visible_blocks(self, rect):
        """[(блок, прямоугольник на экране)] для видимых строк, плюс по строке сверху и снизу —
        им нужны соседи, чтобы направляющие на пустых строках не обрывались."""
        out = []
        block = self.firstVisibleBlock()
        offset = self.contentOffset()
        before = block.previous()
        while before.isValid() and not before.isVisible():
            before = before.previous()
        if before.isValid():
            out.append((before, None))
        while block.isValid():
            geo = self.blockBoundingGeometry(block).translated(offset)
            if geo.top() > rect.bottom():
                out.append((block, None))
                break
            if block.isVisible():
                out.append((block, geo))
            block = block.next()
        return out

    def _column_x(self, column):
        fm = self.fontMetrics()
        return self.contentOffset().x() + self.document().documentMargin() + column * fm.horizontalAdvance(" ")

    def _paint_indent_guides(self, p, blocks):
        tab = max(1, self.indent.tab_width)
        indents = [indent_columns(b.text(), tab) for b, _ in blocks]
        levels = guide_levels(indents, self.indent.size if not self.indent.use_tabs else tab)
        color = QColor(C["dim"])
        color.setAlpha(90)
        pen = QPen(color, 1, Qt.PenStyle.DotLine)
        p.setPen(pen)
        cursor_block = self.textCursor().blockNumber()
        for (block, geo), columns in zip(blocks, levels):
            if geo is None:
                continue
            for column in columns:
                x = round(self._column_x(column)) + 0.5
                if x < 0:
                    continue
                # у строки с курсором самая глубокая линия ярче — видно, в каком блоке курсор (как в VS)
                if block.blockNumber() == cursor_block and column == columns[-1]:
                    strong = QColor(C["dim"])
                    strong.setAlpha(170)
                    p.setPen(QPen(strong, 1, Qt.PenStyle.DotLine))
                    p.drawLine(QPointF(x, geo.top()), QPointF(x, geo.bottom()))
                    p.setPen(pen)
                else:
                    p.drawLine(QPointF(x, geo.top()), QPointF(x, geo.bottom()))

    def _paint_whitespace(self, p, blocks, rect):
        color = QColor(C["dim"])
        color.setAlpha(150)
        p.setPen(QPen(color, 1))
        p.setBrush(color)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        left, right = rect.left() - 20, rect.right() + 20
        for block, geo in blocks:
            if geo is None:
                continue
            text = block.text()
            if " " not in text and "\t" not in text:
                continue
            layout = block.layout()
            if layout is None or layout.lineCount() == 0:
                continue
            origin = geo.topLeft()
            for i, ch in enumerate(text):
                if ch not in " \t":
                    continue
                line = layout.lineForTextPosition(i)
                if not line.isValid():
                    continue
                x1 = origin.x() + line.cursorToX(i)[0]
                x2 = origin.x() + line.cursorToX(i + 1)[0]
                if x2 < left or x1 > right:
                    continue
                cy = origin.y() + line.y() + line.height() / 2
                if ch == " ":
                    p.drawEllipse(QPointF((x1 + x2) / 2, cy), 1.1, 1.1)
                else:
                    self._draw_tab_arrow(p, QRectF(x1 + 2, cy - 3, max(6.0, x2 - x1 - 4), 6))

    @staticmethod
    def _draw_tab_arrow(p, r):
        p.drawLine(QPointF(r.left(), r.center().y()), QPointF(r.right(), r.center().y()))
        p.drawLine(QPointF(r.right() - 3, r.top()), QPointF(r.right(), r.center().y()))
        p.drawLine(QPointF(r.right() - 3, r.bottom()), QPointF(r.right(), r.center().y()))
