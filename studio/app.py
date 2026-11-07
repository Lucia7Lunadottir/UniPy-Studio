"""Запуск приложения и режим --screenshot."""
import os
import sys
import traceback

from PyQt6.QtCore import QEvent, QTimer, QPoint
from PyQt6.QtGui import QPainter, QTextCursor
from PyQt6.QtWidgets import QApplication

from . import theme
from .mainwindow import MainWindow, APP_NAME
from .i18n import install_qt_translation, tr


class ScreenshotRunner:
    """Рисует ~120 кадров, показывает подсказки кода и сохраняет картинку окна."""

    def __init__(self, app, win, path):
        self.app, self.win, self.path = app, win, path
        self.frame = 0
        self.demo_started = False
        self.timer = QTimer(interval=16, timeout=self.tick)
        self.timer.start()

    def tick(self):
        self.frame += 1
        self.win.update()
        if self.frame == 45 and not self.demo_started:
            self.demo_started = True
            self.start_demo()
        if self.frame in (200, 300):
            ed = self.win.editor()
            if ed is not None and not ed.completer.popup().isVisible():
                ed.setFocus()
                ed.request_completion()
        if self.frame >= 120:
            ed = self.win.editor()
            popup_ready = ed is not None and ed.completer.popup().isVisible()
            if popup_ready or self.frame >= 420:
                self.save()

    def start_demo(self):
        """Показывает подсказки так, как их увидит человек: печатает начало вызова в main_window.py."""
        ed = self.win.editor()
        if not ed or not ed.path.endswith("main_window.py"):
            return
        doc = ed.document()
        block = doc.findBlockByNumber(doc.blockCount() - 1)
        while block.isValid() and not block.text().strip():
            block = block.previous()
        c = QTextCursor(block)
        c.movePosition(QTextCursor.MoveOperation.EndOfBlock)
        c.insertText("\n        self.tasks.setCur")
        ed.setTextCursor(c)
        ed.centerCursor()
        ed.setFocus()
        ed.request_completion()

    def save(self):
        self.timer.stop()
        pix = self.win.grab()
        ed = self.win.editor()
        if ed is not None:
            p = QPainter(pix)
            for w in (ed.completer.popup(), ed.doc_tip, ed.calltip):
                if w.isVisible():
                    pos = self.win.mapFromGlobal(w.mapToGlobal(QPoint(0, 0)))
                    p.drawPixmap(pos, w.grab())
            p.end()
            ed.document().setModified(False)
        d = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(d, exist_ok=True)
        ok = pix.save(self.path, "PNG")
        if os.environ.get("UNIPY_DEBUG"):
            print(tr("кадров:"), self.frame, tr("подсказки:"), ed is not None and ed.completer.popup().isVisible())
        print((tr("Снимок сохранён: %s") if ok else tr("Не удалось сохранить снимок: %s")) % self.path)
        self.app.exit(0 if ok else 1)


def _log_exception(kind, value, tb):
    # без этого PyQt6 при исключении в обработчике сигнала завершает процесс целиком
    traceback.print_exception(kind, value, tb)


def run(args):
    sys.excepthook = _log_exception
    app = QApplication(sys.argv[:1])
    install_qt_translation(app)
    app.setApplicationName("UniPyStudio")
    app.setDesktopFileName("unipy-studio")     # Wayland: окно ↔ ярлык unipy-studio.desktop (значок на панели)
    app.setOrganizationName("UniPyStudio")
    app.setApplicationDisplayName(APP_NAME)
    from .launch import resolve_request, apply_request
    try:
        request = resolve_request(args)
    except ValueError as exc:
        sys.stderr.write(str(exc) + "\n")
        return 2
    # One process per explicit project; repeated Unity opens preserve unsaved tabs.
    from .instance import ProjectInstance
    instance = ProjectInstance(request["root"]) if request["root"] and not args.screenshot else None
    try:
        if instance and instance.forward(request):
            return 0
    except RuntimeError as exc:
        sys.stderr.write(str(exc) + "\n")
        return 1
    theme.apply(app)
    win = MainWindow(screenshot=args.screenshot)
    if not args.screenshot:
        win.restore_layout()
    win.show()
    if request["root"]:
        apply_request(win, request)
    else:
        win.start(args.path)
    if instance:
        instance.listen(lambda message: apply_request(win, message))
    runner = ScreenshotRunner(app, win, args.screenshot) if args.screenshot else None  # noqa
    code = app.exec()
    win.jedi.stop()
    win.csharp_index.stop()
    for svc in win.language_services:
        svc.stop()
    # окно удаляет сам Qt, пока QApplication жив: иначе при уничтожении приложения Qt
    # может вызвать Python-обработчик (eventFilter и т.п.) уже удалённого объекта
    win.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    app.processEvents()
    del runner, win
    return code
