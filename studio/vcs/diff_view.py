"""Сравнение файла с последним коммитом — рядом, как в Visual Studio: слева HEAD, справа рабочая копия.
Строки выровнены (на месте удалённых/добавленных — пустые заполнители), прокрутка общая."""
import os

from PyQt6.QtCore import Qt, QRect, QSize
from PyQt6.QtGui import QColor, QPainter, QTextCursor, QTextFormat
from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QVBoxLayout, QLabel, QPlainTextEdit, QTextEdit, QWidget,
                             QPushButton, QSplitter)

from .. import languages, theme
from ..highlighter import Highlighter, PygmentsHighlighter
from ..i18n import tr
from ..theme import C
from .linediff import hunks

FILLER = None


def align(base, current):
    """Две колонки одинаковой длины: [(номер строки с 1 | None, текст, вид)] — вид: equal/delete/insert/replace/filler."""
    old = (base or "").split("\n") if base is not None else []
    new = current.split("\n")
    left, right = [], []
    for op, i1, i2, j1, j2 in hunks(base, current):
        a, b = list(range(i1, i2)), list(range(j1, j2))
        for k in range(max(len(a), len(b))):
            if k < len(a):
                left.append((a[k] + 1, old[a[k]], op if op != "insert" else "filler"))
            else:
                left.append((FILLER, "", "filler"))
            if k < len(b):
                right.append((b[k] + 1, new[b[k]], op if op != "delete" else "filler"))
            else:
                right.append((FILLER, "", "filler"))
    return left, right


class _Numbers(QWidget):
    def __init__(self, view, rows):
        super().__init__(view)
        self.view, self.rows = view, rows

    def sizeHint(self):
        return QSize(self.view.fontMetrics().horizontalAdvance("9") * 5 + 10, 0)

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(event.rect(), QColor(C["editor"]))
        view = self.view
        block = view.firstVisibleBlock()
        top = round(view.blockBoundingGeometry(block).translated(view.contentOffset()).top())
        h = view.fontMetrics().height()
        while block.isValid() and top <= event.rect().bottom():
            n = block.blockNumber()
            if n < len(self.rows) and self.rows[n][0] is not None:
                p.setPen(QColor(C["gutter_num"]))
                p.drawText(0, top, self.width() - 6, h, Qt.AlignmentFlag.AlignRight, str(self.rows[n][0]))
            top += round(view.blockBoundingRect(block).height())
            block = block.next()
        p.end()


class _Side(QPlainTextEdit):
    COLORS = {"delete": "#E4676B", "insert": "#57AB5A", "replace": "#4C9BE8", "filler": None}

    def __init__(self, rows, path, font):
        super().__init__()
        self.rows = rows
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setFont(font)
        self.setStyleSheet('QPlainTextEdit { font-family: "%s"; font-size: %gpt; background:%s; color:%s; }'
                           % (font.family(), font.pointSizeF(), C["editor"], C["text"]))
        self.setPlainText("\n".join(text for _n, text, _k in rows))
        language = languages.for_path(path)
        if language.builtin_highlighter:
            self._hl = Highlighter(self.document(), language.id)
        else:
            lexer = language.make_lexer(path)
            self._hl = PygmentsHighlighter(self.document(), lexer) if lexer else None
        self.numbers = _Numbers(self, rows)
        self.setViewportMargins(self.numbers.sizeHint().width(), 0, 0, 0)
        self.updateRequest.connect(lambda rect, dy: self.numbers.scroll(0, dy) if dy else self.numbers.update())
        self._mark()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        cr = self.contentsRect()
        self.numbers.setGeometry(QRect(cr.left(), cr.top(), self.numbers.sizeHint().width(), cr.height()))

    def _mark(self):
        sels = []
        doc = self.document()
        for n, (_line, _text, kind) in enumerate(self.rows):
            if kind == "equal":
                continue
            sel = QTextEdit.ExtraSelection()
            if kind == "filler":
                color = QColor(C["gutter_num"])
                color.setAlpha(35)
            else:
                color = QColor(self.COLORS[kind])
                color.setAlpha(55)
            sel.format.setBackground(color)
            sel.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
            sel.cursor = QTextCursor(doc.findBlockByNumber(n))
            sels.append(sel)
        self.setExtraSelections(sels)

    def first_change(self):
        return next((n for n, (_l, _t, k) in enumerate(self.rows) if k != "equal"), 0)


class DiffWindow(QDialog):
    """Окно сравнения. open_file(path, строка) — кнопка «Открыть файл»."""

    def __init__(self, parent, path, base, current, font, open_file):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle(tr("vcs.diff_title") % os.path.basename(path))
        self.resize(1200, 760)
        left_rows, right_rows = align(base, current)
        lay = QVBoxLayout(self)
        head = QHBoxLayout()
        head.addWidget(QLabel(tr("vcs.diff_left") if base is not None else tr("vcs.diff_left_new")), 1)
        head.addWidget(QLabel(tr("vcs.diff_right")), 1)
        lay.addLayout(head)
        split = QSplitter()
        self.left, self.right = _Side(left_rows, path, font), _Side(right_rows, path, font)
        split.addWidget(self.left)
        split.addWidget(self.right)
        lay.addWidget(split, 1)
        for a, b in ((self.left, self.right), (self.right, self.left)):     # общая прокрутка
            a.verticalScrollBar().valueChanged.connect(b.verticalScrollBar().setValue)
            a.horizontalScrollBar().valueChanged.connect(b.horizontalScrollBar().setValue)
        changes = sum(1 for _n, _t, k in right_rows if k in ("insert", "replace")) + \
            sum(1 for _n, _t, k in left_rows if k == "delete")
        bottom = QHBoxLayout()
        summary = QLabel(tr("vcs.diff_summary") % changes if changes else tr("vcs.diff_same"))
        theme.style(summary, "color:@dim@;")
        bottom.addWidget(summary, 1)
        button = QPushButton(tr("vcs.open_file"))
        first = self.right.first_change()
        line = next((right_rows[n][0] for n in range(first, len(right_rows)) if right_rows[n][0]), 1)
        button.clicked.connect(lambda: (open_file(path, line), self.close()))
        bottom.addWidget(button)
        lay.addLayout(bottom)
        self.right.verticalScrollBar().setValue(max(0, first - 5))
