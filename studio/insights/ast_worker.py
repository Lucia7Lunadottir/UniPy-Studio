"""Лёгкий фоновый поток для разбора ast (семантика и места подсказок Python), без jedi.

Разбор файла в 1500 строк — около 0,1 с: в потоке интерфейса это заметная задержка. jedi для этого не нужен
(он тянет модули PyQt6 и свой подпроцесс — сотни МБ), поэтому поток свой и один на программу.
Задачи с одним ключом склеиваются — выполняется самая свежая; ответ приходит в поток интерфейса.
"""
import threading

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication


class AstWorker(QObject):
    _done = pyqtSignal(object, object)          # (ключ, номер задачи), результат

    def __init__(self):
        super().__init__()
        self._cv = threading.Condition()
        self._jobs = {}                         # ключ → (номер, функция, аргументы)
        self._callbacks = {}                    # ключ → (номер, callback)
        self._serial = 0
        self._stopped = False
        self._thread = None
        self._done.connect(self._deliver)

    def submit(self, key, fn, args, callback):
        """fn(*args) в фоне, потом callback(результат) в потоке интерфейса. Исключение в fn — результат None.
        Новая задача с тем же ключом отменяет прежнюю (её callback не вызовется)."""
        with self._cv:
            if self._stopped:
                return
            self._serial += 1
            self._jobs[key] = (self._serial, fn, args)
            self._callbacks[key] = (self._serial, callback)
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, daemon=True, name="unipy-ast")
                self._thread.start()
            self._cv.notify()

    def stop(self):
        with self._cv:
            self._stopped = True
            self._jobs.clear()
            self._callbacks.clear()
            self._cv.notify()

    def _run(self):
        while True:
            with self._cv:
                while not self._jobs and not self._stopped:
                    self._cv.wait()
                if self._stopped:
                    return
                key, (serial, fn, args) = self._jobs.popitem()
            try:
                result = fn(*args)
            except Exception:                   # noqa: BLE001 — недописанный код и т. п.: просто нет данных
                result = None
            with self._cv:
                if self._stopped:
                    return
                self._done.emit((key, serial), result)

    def _deliver(self, ident, result):
        key, serial = ident
        entry = self._callbacks.get(key)
        if entry is None or entry[0] != serial:
            return                              # уже есть задача свежее
        del self._callbacks[key]
        entry[1](result)


_worker = None


def worker():
    """Общий поток разбора; останавливается при выходе из программы."""
    global _worker
    if _worker is None:
        _worker = AstWorker()
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(_worker.stop)
    return _worker
