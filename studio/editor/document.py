"""Файл редактора: загрузка, новая версия с диска, сохранение (атомарно, в своей кодировке), правки LSP."""
import os
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import QMessageBox
from ..documents import DiskState, atomic_write
from ..indentation import apply_save_rules
from ..lsp import line_diff_edits
from ..i18n import tr
from .source import ENCODING_NAME, decode_source


class DocumentMixin:
    """Чтение и запись файла вкладки."""

    def load(self):
        with open(self.path, "rb") as f:
            raw = f.read()
        self.disk.remember(raw)
        self.encoding, text = decode_source(raw)
        self.newline = "\r\n" if "\r\n" in text else "\n"
        self.setPlainText(text.replace("\r\n", "\n").replace("\r", "\n"))
        self.document().setModified(False)
        self.folding.recompute()            # +/− сразу, а не через четверть секунды
        self.run_diagnostics()

    def reload_from(self, raw):
        """Новая версия файла с диска (изменили вне редактора). Меняются только отличающиеся строки,
        одним шагом отмены: курсор и прокрутка остаются на месте, Ctrl+Z вернёт прежний текст."""
        self.disk.remember(raw)
        self.missing_on_disk = False
        self.encoding, text = decode_source(raw)
        self.newline = "\r\n" if "\r\n" in text else "\n"
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        bar = self.verticalScrollBar()
        scroll = bar.value()
        edits = line_diff_edits(self.toPlainText(), text)
        if edits:
            self.apply_edits(edits)
        bar.setValue(scroll)
        self.document().setModified(False)
        self.run_diagnostics()

    def set_text_as_edit(self, text):
        """Подменить весь текст как обычную правку (Ctrl+Z вернёт прежний) — для восстановленных копий."""
        edits = line_diff_edits(self.toPlainText(), text)
        if edits:
            self.apply_edits(edits)

    def save(self):
        """Сохраняет в той же кодировке и с теми же переводами строк, что были у файла (как Visual Studio).
        Запись атомарная; если файл успели изменить вне редактора — сначала спрашивает.
        Правила .editorconfig (пробелы в конце строк, перевод строки в конце) применяются и к тексту
        во вкладке — одной правкой, Ctrl+Z её вернёт."""
        text = self.toPlainText()
        ruled = apply_save_rules(text, self.indent)
        if ruled != text:
            self.set_text_as_edit(ruled)
            text = self.toPlainText()
        if self.newline != "\n":
            text = text.replace("\n", self.newline)
        try:
            data = text.encode(self.encoding)
        except UnicodeEncodeError as e:
            bad = text[e.start:e.end]
            r = QMessageBox.question(
                self, tr("editor.encoding"), tr("editor.symbol_cannot_stored_encoding_save") % (bad, tr(ENCODING_NAME.get(self.encoding, self.encoding))),
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel)
            if r != QMessageBox.StandardButton.Save:
                return False
            self.encoding = "utf-8-sig"
            data = text.encode(self.encoding)
        on_disk = self.disk.check()
        if on_disk is not None and on_disk is not DiskState.MISSING:
            r = QMessageBox.question(self, tr("documents.changed_outside_title"),
                                     tr("documents.overwrite_changed") % os.path.basename(self.path),
                                     QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
                                     QMessageBox.StandardButton.Cancel)
            if r != QMessageBox.StandardButton.Save:
                return False
        try:
            atomic_write(self.path, data)
        except OSError as e:
            QMessageBox.warning(self, tr("documents.save_failed_title"),
                                tr("documents.save_failed") % (self.path, e.strerror or e))
            return False
        self.disk.remember(data)
        self.missing_on_disk = False
        self.document().setModified(False)
        self.saved.emit(self.path)
        self.run_diagnostics()
        return True

    def encoding_label(self):
        return tr(ENCODING_NAME.get(self.encoding, self.encoding.upper())) + ("  CRLF" if self.newline == "\r\n" else "")

    def apply_edits(self, edits, join_undo=False):
        """Правки LSP (стр, стлб, стр2, стлб2, текст; с 0, столбцы в UTF-16 — как позиции Qt) одним шагом отмены."""
        doc = self.document()

        def pos(line, col):
            block = doc.findBlockByNumber(line)
            if not block.isValid():
                return doc.characterCount() - 1
            return block.position() + min(col, block.length() - 1)
        c = self.textCursor()           # не QTextCursor(doc): Ctrl+Z ставит курсор туда, где был этот, — иначе в начало файла
        if join_undo:
            c.joinPreviousEditBlock()   # тот же шаг отмены, что и предыдущая правка
        else:
            c.beginEditBlock()
        for a, b, l2, c2, text in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
            c.setPosition(pos(a, b))
            c.setPosition(pos(l2, c2), QTextCursor.MoveMode.KeepAnchor)
            c.insertText(text)
        c.endEditBlock()
