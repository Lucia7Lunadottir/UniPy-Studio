"""Bound retained log text, including output without newlines."""
from PyQt6.QtGui import QTextCursor

LOG_CHARS = 1_000_000
LOG_BLOCKS = 4000
LOG_LINE_CHARS = 8192
STDERR_CHARS = 256_000


def configure_log(view):
    view.setUndoRedoEnabled(False)
    view.setMaximumBlockCount(LOG_BLOCKS)


def clip_log(text):
    return "\n".join(line[-LOG_LINE_CHARS:] for line in text[-LOG_CHARS:].split("\n"))


def trim_log(view):
    block = view.document().lastBlock()
    if block.length() - 1 > LOG_LINE_CHARS:
        cursor = QTextCursor(block)
        cursor.setPosition(block.position() + block.length() - 1 - LOG_LINE_CHARS,
                           QTextCursor.MoveMode.KeepAnchor)
        cursor.removeSelectedText()
    excess = view.document().characterCount() - 1 - LOG_CHARS
    if excess > 0:
        cursor = QTextCursor(view.document())
        cursor.setPosition(0)
        cursor.setPosition(excess, QTextCursor.MoveMode.KeepAnchor)
        cursor.removeSelectedText()
