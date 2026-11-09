"""Project search; replacements remain undoable in editor buffers until saved."""
import fnmatch
import os
import re
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPlainTextEdit,
                             QLineEdit, QCheckBox, QPushButton, QTreeWidget, QTreeWidgetItem)
from .editor import decode_source
from .i18n import bind_text, tr

EXCLUDED = {'.git', '.venv', 'venv', '__pycache__', 'node_modules', 'Library',
            'Temp', 'Obj', 'obj', 'bin', 'target', 'Logs', 'Build', 'Builds'}


def pattern(query, case=False, regex=False, words=False):
    if not query:
        raise ValueError(tr('search.enter_text'))
    source = query if regex else re.escape(query)
    if words:
        source = r'(?<!\w)(?:' + source + r')(?!\w)'
    return re.compile(source, re.MULTILINE | (0 if case else re.IGNORECASE))


class SearchWorker(QThread):
    found = pyqtSignal(object)

    def __init__(self, root, buffers, rx, globs):
        super().__init__()
        self.root, self.buffers, self.rx, self.globs = root, buffers, rx, globs

    def run(self):
        results, errors = [], []
        paths = set(self.buffers)
        if self.root:
            for directory, dirs, files in os.walk(self.root):
                if self.isInterruptionRequested():
                    return
                dirs[:] = [d for d in dirs if d not in EXCLUDED and not d.startswith('.')]
                paths.update(os.path.abspath(os.path.join(directory, f)) for f in files)
        for path in sorted(paths):
            if self.isInterruptionRequested():
                return
            if not any(fnmatch.fnmatch(os.path.basename(path), g) for g in self.globs):
                continue
            try:
                if path in self.buffers:
                    text = self.buffers[path]
                else:
                    if Path(path).stat().st_size > 5_000_000:
                        continue
                    raw = Path(path).read_bytes()
                    if b'\0' in raw and not raw.startswith((b'\xff\xfe', b'\xfe\xff')):
                        continue
                    _, text = decode_source(raw)
                    text = text.replace('\r\n', '\n').replace('\r', '\n')
                matches = [(m.start(), m.end()) for m in self.rx.finditer(text)]
                if matches:
                    results.append((path, text, matches))
            except (OSError, UnicodeError) as exc:
                errors.append(f'{path}: {exc}')
        self.found.emit((results, errors))


class ProjectSearch(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.worker = None
        self.results = []
        bind_text(self, 'search.title', 'setWindowTitle')
        self.resize(900, 650)
        layout = QVBoxLayout(self)
        layout.addWidget(bind_text(QLabel(), 'search.find_label'))
        self.query = QPlainTextEdit()
        self.query.setMaximumHeight(85)
        layout.addWidget(self.query)
        layout.addWidget(bind_text(QLabel(), 'search.replace_label'))
        self.replacement = QPlainTextEdit()
        self.replacement.setMaximumHeight(85)
        layout.addWidget(self.replacement)
        row = QHBoxLayout()
        self.case = bind_text(QCheckBox(), 'search.match_case')
        self.words = bind_text(QCheckBox(), 'search.whole_word')
        self.regex = QCheckBox('Regex')
        self.globs = QLineEdit('*.cs;*.py;*.pyi;*.json;*.xml;*.uxml;*.uss;*.md;*.txt;*.yaml;*.yml;*.shader')
        bind_text(self.globs, 'search.globs_tip', 'setToolTip')
        for widget in (self.case, self.words, self.regex, self.globs):
            row.addWidget(widget)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.search = bind_text(QPushButton(), 'search.find_preview')
        self.apply = bind_text(QPushButton(), 'search.replace_checked')
        self.apply.setEnabled(False)
        row.addWidget(self.search)
        row.addWidget(self.apply)
        layout.addLayout(row)
        self.status = QLabel(tr('search.scope_hint'))
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([tr('search.column_file_line'), tr('search.column_match_replacement')])
        self.tree.setColumnWidth(0, 310)
        layout.addWidget(self.tree)
        self.search.clicked.connect(self.start)
        self.apply.clicked.connect(self.replace_all)
        self.tree.itemDoubleClicked.connect(self.navigate)
        for signal in (self.query.textChanged, self.replacement.textChanged,
                       self.case.toggled, self.words.toggled, self.regex.toggled, self.globs.textChanged):
            signal.connect(self.invalidate)

    def invalidate(self, *_):
        self.apply.setEnabled(False)

    def start(self):
        try:
            self.rx = pattern(self.query.toPlainText(), self.case.isChecked(),
                              self.regex.isChecked(), self.words.isChecked())
            self.replace_text = self.replacement.toPlainText()
            self.regex_mode = self.regex.isChecked()
            # Validate backreferences even if there are no matches.
            if self.regex_mode:
                self.rx.sub(self.replace_text, '')
        except (re.error, ValueError, IndexError) as exc:
            self.status.setText(str(exc))
            return
        self.snapshot = self.options()
        self.apply.setEnabled(False)
        self.search.setEnabled(False)
        self.status.setText(tr('search.searching'))
        self.tree.clear()
        buffers = {os.path.abspath(e.path): e.toPlainText() for e in self.win.editors()}
        self.worker = SearchWorker(self.win.root, buffers, self.rx,
                                   [g.strip() for g in self.globs.text().split(';') if g.strip()] or ['*'])
        self.worker.found.connect(self.show_results)
        self.worker.finished.connect(lambda: self.search.setEnabled(True))
        self.worker.start()

    def options(self):
        return (self.query.toPlainText(), self.replacement.toPlainText(), self.case.isChecked(),
                self.words.isChecked(), self.regex.isChecked(), self.globs.text(), self.win.root)

    def show_results(self, payload):
        self.results, errors = payload
        total = 0
        for path, text, matches in self.results:
            parent = QTreeWidgetItem(self.tree, [os.path.relpath(path, self.win.root) if self.win.root else path,
                                                 str(len(matches))])
            parent.setCheckState(0, Qt.CheckState.Checked)
            parent.setData(0, Qt.ItemDataRole.UserRole, (path, 1))
            for start, end in matches[:200]:
                line = text.count('\n', 0, start) + 1
                match = self.rx.match(text, start)
                replacement = match.expand(self.replace_text) if self.regex_mode else self.replace_text
                child = QTreeWidgetItem(parent, [str(line), text[start:end][:100].replace('\n', ' ↵ ') +
                                                ' → ' + replacement[:100].replace('\n', ' ↵ ')])
                child.setData(0, Qt.ItemDataRole.UserRole, (path, line))
            total += len(matches)
        self.status.setText(tr('search.summary') % (total, len(self.results)) +
                            (tr('search.read_errors') % len(errors) if errors else ''))
        self.status.setToolTip('\n'.join(errors))
        self.apply.setEnabled(bool(total) and self.snapshot == self.options())

    def navigate(self, item, _column):
        path, line = item.data(0, Qt.ItemDataRole.UserRole)
        self.win.open_path(path)
        ed = self.win.editor()
        if ed and os.path.abspath(ed.path) == path:
            ed.go_to_line(line)

    def replace_all(self):
        if self.snapshot != self.options():
            self.invalidate()
            return
        count, skipped = 0, []
        for i, (path, original, matches) in enumerate(self.results):
            if self.tree.topLevelItem(i).checkState(0) != Qt.CheckState.Checked:
                continue
            try:
                ed = self.win.open_path(path)
            except (OSError, UnicodeError):
                ed = None
            if not ed or ed.isReadOnly() or os.path.abspath(ed.path) != path or ed.toPlainText() != original:
                skipped.append(path)
                continue
            new, n = self.rx.subn(self.replace_text if self.regex_mode else lambda m: self.replace_text, original)
            cursor = ed.textCursor()
            cursor.beginEditBlock()
            cursor.select(QTextCursor.SelectionType.Document)
            cursor.insertText(new)
            cursor.endEditBlock()
            count += n
        self.apply.setEnabled(False)
        self.status.setText(tr('search.replaced_summary') % (count, len(skipped)))
        self.status.setToolTip('\n'.join(skipped))

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.worker.requestInterruption()
            self.status.setText(tr('common.stopping_search'))
            event.ignore()
            return
        super().closeEvent(event)
