"""Конструктор PyQt: форма из виджетов, предпросмотр, генерация кода окна."""
import re
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton, QLabel, QComboBox, QListWidget, QListWidgetItem, QFrame, QGroupBox, QFormLayout, QCheckBox, QRadioButton, QSpinBox, QSlider, QProgressBar, QTextEdit, QGridLayout, QScrollArea
from .. import theme
from ..i18n import bind_text, tr
from .common import ROLE_PATH


# ====================================================================== конструктор PyQt
WIDGETS = [
    ("QPushButton", "panels.widget_button", "common.click_me"),
    ("QLabel", "panels.widget_label", "common.text"),
    ("QLineEdit", "panels.widget_line_edit", "panels.enter_text"),
    ("QTextEdit", "panels.multi_line_field", ""),
    ("QCheckBox", "panels.checkbox", "panels.enable"),
    ("QRadioButton", "panels.radio_button", "panels.radio_option"),
    ("QComboBox", "panels.drop_down_list", "panels.first_one_second_one_third"),
    ("QSpinBox", "panels.spin_box", "0"),
    ("QSlider", "panels.slider", "50"),
    ("QProgressBar", "panels.indicator", "40"),
    ("QListWidget", "panels.list", "panels.element_1_element_2"),
]


def build_widget(cls, text):
    if cls == "QPushButton":
        return QPushButton(text)
    if cls == "QLabel":
        return QLabel(text)
    if cls == "QLineEdit":
        w = QLineEdit()
        w.setPlaceholderText(text)
        return w
    if cls == "QTextEdit":
        w = QTextEdit()
        w.setPlainText(text)
        w.setMaximumHeight(60)
        return w
    if cls == "QCheckBox":
        return QCheckBox(text)
    if cls == "QRadioButton":
        return QRadioButton(text)
    if cls == "QComboBox":
        w = QComboBox()
        w.addItems([t for t in text.split(";") if t])
        return w
    if cls == "QSpinBox":
        w = QSpinBox()
        w.setValue(int(text) if text.strip().lstrip("-").isdigit() else 0)
        return w
    if cls == "QSlider":
        w = QSlider(Qt.Orientation.Horizontal)
        w.setValue(int(text) if text.strip().isdigit() else 50)
        return w
    if cls == "QProgressBar":
        w = QProgressBar()
        w.setValue(int(text) if text.strip().isdigit() else 0)
        return w
    if cls == "QListWidget":
        w = QListWidget()
        w.addItems([t for t in text.split(";") if t])
        w.setMaximumHeight(70)
        return w
    return QLabel(text)


PREVIEW_STYLE = """
QWidget { background: #F0F0F0; color: #202020; font-size: 9pt; }
QLineEdit, QTextEdit, QSpinBox, QComboBox, QListWidget { background: white; border: 1px solid #ADADAD; padding: 2px; }
QPushButton { background: #E1E1E1; border: 1px solid #ADADAD; padding: 4px 10px; }
QLabel#formTitle { background: #FFFFFF; border-bottom: 1px solid #D0D0D0; padding: 4px 8px; }
QProgressBar { border: 1px solid #BCBCBC; background: #E6E6E6; text-align: center; }
QProgressBar::chunk { background: #06B025; }
"""


class PyQtDesigner(QWidget):
    generated = pyqtSignal(str, str)      # имя файла, код
    new_template = pyqtSignal(int)
    preview_ui = pyqtSignal()

    def __init__(self):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(8, 6, 8, 8)
        scroll.setWidget(body)
        outer.addWidget(scroll)

        g = bind_text(QGroupBox(), "panels.new_file_pyqt", "setTitle")
        gl = QGridLayout(g)
        for n, (label, idx) in enumerate([("panels.window", 0), ("panels.dialog", 1), ("panels.widget", 2), ("panels.form_ui", 3)]):
            b = bind_text(QPushButton(), label)
            b.clicked.connect(lambda _=False, i=idx: self.new_template.emit(i))
            gl.addWidget(b, n // 2, n % 2)
        b = bind_text(QPushButton(), "panels.preview_ui_form_from_editor")
        b.clicked.connect(self.preview_ui.emit)
        gl.addWidget(b, 2, 0, 1, 2)
        lay.addWidget(g)

        g = bind_text(QGroupBox(), "panels.form_designer", "setTitle")
        gl = QVBoxLayout(g)
        form = QFormLayout()
        self.title = QLineEdit(tr("panels.my_form"))
        self.title.textChanged.connect(self.refresh_preview)
        self.cls_name = QLineEdit("MyForm")
        self.layout_kind = QComboBox()
        self.layout_kind.addItems([tr("panels.vertically"), tr("panels.horizontal"), tr("panels.two_column_grid")])
        self.layout_kind.currentIndexChanged.connect(self.refresh_preview)
        self.qt_version = QComboBox()
        self.qt_version.addItems(["PyQt6", "PyQt5"])
        form.addRow(bind_text(QLabel(), "panels.title"), self.title)
        form.addRow(bind_text(QLabel(), "panels.class"), self.cls_name)
        form.addRow(bind_text(QLabel(), "panels.layout"), self.layout_kind)
        form.addRow(bind_text(QLabel(), "panels.library"), self.qt_version)
        gl.addLayout(form)
        row = QHBoxLayout()
        self.add_kind = QComboBox()
        self.add_kind.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.add_kind.setMinimumContentsLength(12)
        for cls, ru, _ in WIDGETS:
            self.add_kind.addItem("%s (%s)" % (tr(ru), cls), cls)
        row.addWidget(self.add_kind, 1)
        add = bind_text(QPushButton(), "common.add")
        add.clicked.connect(self.add_widget)
        row.addWidget(add)
        gl.addLayout(row)
        self.items = QListWidget()
        self.items.setMinimumHeight(120)
        self.items.itemChanged.connect(self.refresh_preview)
        bind_text(self.items, "panels.double_click_change_text", "setToolTip")
        gl.addWidget(self.items)
        row = QHBoxLayout()
        for text, slot in (("↑", lambda: self.move(-1)), ("↓", lambda: self.move(1)), ("panels.delete", self.remove)):
            b = bind_text(QPushButton(), text)
            b.clicked.connect(slot)
            row.addWidget(b)
        gl.addLayout(row)
        lbl = bind_text(QLabel(), "panels.preview")
        lbl.setObjectName("hint")
        gl.addWidget(lbl)
        self.preview = QFrame()
        self.preview.setObjectName("formPreview")
        self.preview.setStyleSheet(PREVIEW_STYLE)
        self.preview.setMinimumHeight(160)
        gl.addWidget(self.preview)
        gen = bind_text(QPushButton(), "panels.create_form_code")
        gen.setObjectName("primary")
        gen.clicked.connect(self.generate)
        gl.addWidget(gen)
        lay.addWidget(g)
        lay.addStretch(1)
        self.counter = {}
        for cls, text in (("QLabel", tr("panels.your_name")), ("QLineEdit", tr("panels.enter_name")), ("QPushButton", tr("panels.say_hello"))):
            self._add(cls, text)
        self.refresh_preview()

    def retranslate(self):
        for i, key in enumerate(("panels.vertically", "panels.horizontal", "panels.two_column_grid")):
            self.layout_kind.setItemText(i, tr(key))
        for i, (cls, label, _) in enumerate(WIDGETS):
            self.add_kind.setItemText(i, "%s (%s)" % (tr(label), cls))

    def _add(self, cls, text):
        self.counter[cls] = self.counter.get(cls, 0) + 1
        base = {"QPushButton": "button", "QLabel": "label", "QLineEdit": "edit", "QTextEdit": "text",
                "QCheckBox": "check", "QRadioButton": "radio", "QComboBox": "combo", "QSpinBox": "spin",
                "QSlider": "slider", "QProgressBar": "progress", "QListWidget": "list"}.get(cls, "widget")
        obj = "%s%d" % (base, self.counter[cls])
        it = QListWidgetItem("%s" % text)
        it.setIcon(theme.kind_icon("class"))
        it.setData(ROLE_PATH, (cls, obj))
        it.setToolTip("%s — self.%s" % (cls, obj))
        it.setFlags(it.flags() | Qt.ItemFlag.ItemIsEditable)
        self.items.blockSignals(True)
        self.items.addItem(it)
        self.items.blockSignals(False)

    def add_widget(self):
        cls = self.add_kind.currentData()
        default = next(w[2] for w in WIDGETS if w[0] == cls)
        self._add(cls, tr(default))
        self.refresh_preview()

    def move(self, d):
        r = self.items.currentRow()
        if r < 0 or not 0 <= r + d < self.items.count():
            return
        it = self.items.takeItem(r)
        self.items.insertItem(r + d, it)
        self.items.setCurrentRow(r + d)
        self.refresh_preview()

    def remove(self):
        r = self.items.currentRow()
        if r >= 0:
            self.items.takeItem(r)
            self.refresh_preview()

    def spec(self):
        out = []
        for i in range(self.items.count()):
            it = self.items.item(i)
            cls, obj = it.data(ROLE_PATH)
            out.append((cls, obj, it.text()))
        return out

    def refresh_preview(self, *_):
        old = self.preview.layout()
        if old is not None:
            while old.count():
                w = old.takeAt(0).widget()
                if w:
                    w.deleteLater()
            QWidget().setLayout(old)
        outer = QVBoxLayout(self.preview)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        title = QLabel("■  " + (self.title.text() or tr("panels.form")))
        title.setObjectName("formTitle")
        outer.addWidget(title)
        inner = QWidget()
        kind = self.layout_kind.currentIndex()
        lay = QVBoxLayout(inner) if kind == 0 else QHBoxLayout(inner) if kind == 1 else QGridLayout(inner)
        lay.setContentsMargins(10, 8, 10, 10)
        for n, (cls, obj, text) in enumerate(self.spec()):
            w = build_widget(cls, text)
            if kind == 2:
                lay.addWidget(w, n // 2, n % 2)
            else:
                lay.addWidget(w)
        outer.addWidget(inner)
        outer.addStretch(1)

    def generate(self):
        lib = self.qt_version.currentText()
        cls_name = re.sub(r"\W", "", self.cls_name.text()) or "MyForm"
        spec = self.spec()
        used = sorted({c for c, _, _ in spec} | {"QApplication", "QWidget"})
        kind = self.layout_kind.currentIndex()
        layout_cls = ["QVBoxLayout", "QHBoxLayout", "QGridLayout"][kind]
        used.append(layout_cls)
        needs_qt = any(c == "QSlider" for c, _, _ in spec)
        lines = ["import sys", ""]
        if needs_qt:
            lines.append("from %s.QtCore import Qt" % lib)
        lines.append("from %s.QtWidgets import (%s)" % (lib, ", ".join(sorted(set(used)))))
        lines += ["", "", "class %s(QWidget):" % cls_name,
                  "    def __init__(self, parent=None):",
                  "        super().__init__(parent)",
                  "        self.setWindowTitle(%r)" % (self.title.text() or tr("panels.form")),
                  "        layout = %s(self)" % layout_cls, ""]
        handlers = []
        for n, (cls, obj, text) in enumerate(spec):
            t = repr(text)
            if cls in ("QPushButton", "QLabel", "QCheckBox", "QRadioButton"):
                lines.append("        self.%s = %s(%s)" % (obj, cls, t))
            elif cls == "QLineEdit":
                lines.append("        self.%s = QLineEdit()" % obj)
                lines.append("        self.%s.setPlaceholderText(%s)" % (obj, t))
            elif cls in ("QComboBox", "QListWidget"):
                lines.append("        self.%s = %s()" % (obj, cls))
                lines.append("        self.%s.addItems(%r)" % (obj, [x for x in text.split(";") if x]))
            elif cls == "QSlider":
                lines.append("        self.%s = QSlider(Qt.Orientation.Horizontal)" % obj)
                lines.append("        self.%s.setValue(%d)" % (obj, int(text) if text.isdigit() else 50))
            elif cls in ("QSpinBox", "QProgressBar"):
                lines.append("        self.%s = %s()" % (obj, cls))
                lines.append("        self.%s.setValue(%d)" % (obj, int(text) if text.lstrip("-").isdigit() else 0))
            elif cls == "QTextEdit":
                lines.append("        self.%s = QTextEdit()" % obj)
                if text:
                    lines.append("        self.%s.setPlainText(%s)" % (obj, t))
            else:
                lines.append("        self.%s = %s()" % (obj, cls))
            if kind == 2:
                lines.append("        layout.addWidget(self.%s, %d, %d)" % (obj, n // 2, n % 2))
            else:
                lines.append("        layout.addWidget(self.%s)" % obj)
            if cls == "QPushButton":
                lines.append("        self.%s.clicked.connect(self.on_%s_clicked)" % (obj, obj))
                handlers.append(obj)
        for obj in handlers:
            lines += ["", "    def on_%s_clicked(self):" % obj,
                      "        print(%r)" % (tr("panels.pressed_button") % obj)]
        lines += ["", "", 'if __name__ == "__main__":',
                  "    app = QApplication(sys.argv)",
                  "    window = %s()" % cls_name,
                  "    window.show()",
                  "    sys.exit(app.exec())", ""]
        fname = re.sub(r"(?<!^)(?=[A-Z])", "_", cls_name).lower() + ".py"
        self.generated.emit(fname, "\n".join(lines))
