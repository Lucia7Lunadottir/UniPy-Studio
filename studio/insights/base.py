"""Подсказки в коде одного файла: InsightController и базовый класс Insight.

Insight — одна возможность (семантическая подсветка, подсказки в строке, CodeLens): у кого спросить
(языковой сервер, если он это умеет; для Python — jedi в фоновом потоке), что сделать с ответом
(форматы поверх подсветки — formats(); рисование поверх текста — paint(); щелчок — click()).
Контроллер живёт у основного редактора файла (вторые окна разделённой вкладки берут его же):
просит обновить после правок и прокрутки, раздаёт ответы, перекрашивает изменившиеся строки.
"""
from PyQt6.QtCore import QObject, QTimer


class Insight:
    id = ""                     # ключ в настройках контроллера (settings)
    kinds = ()                  # виды ответов (lsp_features / JediWorker), которые разбирает эта возможность
    follows_scroll = False      # данные только для видимых строк — обновлять и при прокрутке

    def __init__(self, controller):
        self.ctl = controller

    @property
    def enabled(self):
        return self.ctl.settings.get(self.id, False)

    def request(self):
        """Спросить данные (ответ придёт в on_result)."""

    def edited(self):
        """Текст изменился (вызывается сразу, до отложенного refresh)."""

    def on_result(self, res):
        """res — {"kind", "req" (ревизия документа), "data"}."""

    def clear(self):
        """Выключили — убрать всё показанное."""

    def formats(self, line):
        """[(начало UTF-16, длина, change(QTextCharFormat))] для строки line (с 0) — слой поверх подсветки."""
        return ()

    def paint(self, view, painter, blocks):
        """Рисование поверх текста окна view; blocks — [(блок, прямоугольник)] видимых строк."""

    def click(self, view, pos):
        """Щелчок по нарисованному (pos — в координатах viewport). True — обработан."""
        return False

    def hover(self, view, pos):
        """Мышь над нарисованным — True (курсор-рука)."""
        return False


class InsightController(QObject):
    EDIT_DELAY_MS = 700
    SCROLL_DELAY_MS = 250
    RETRY_MS = 400              # jedi уступил очереди — спросить ещё раз

    def __init__(self, editor, insight_classes):
        super().__init__(editor)
        self.editor = editor
        self.settings = {}
        self.insights = [cls(self) for cls in insight_classes]
        self._by_kind = {k: ins for ins in self.insights for k in ins.kinds}
        self._dirty = True
        self._assist = None             # pyright_assist.PyrightAssist — для Python, создаётся по требованию
        self._edit_timer = QTimer(self, singleShot=True, interval=self.EDIT_DELAY_MS, timeout=self.refresh)
        # слоты — методы, не lambda: метод PyQt отключает сам, когда объект удалён, а lambda вызвал бы
        # уже разобранный сборщиком мусора контроллер (падение процесса)
        self._scroll_timer = QTimer(self, singleShot=True, interval=self.SCROLL_DELAY_MS, timeout=self._scrolled)
        self._retry_timer = QTimer(self, singleShot=True, interval=self.RETRY_MS, timeout=self.refresh)
        # только настоящие правки: перекраска строк (rehighlight) тоже шлёт textChanged, но не contentsChange
        editor.document().contentsChange.connect(self._contents_change)
        self.watch_view(editor)
        editor.highlighter.overlay = self.formats

    # --- окна и источники
    def watch_view(self, view):
        view.verticalScrollBar().valueChanged.connect(self._scroll_moved)

    def _scroll_moved(self, _value):
        self._scroll_timer.start()

    def _scrolled(self):
        self.refresh(scroll=True)

    def views(self):
        return [self.editor] + list(self.editor.split_views)

    def server(self, capability):
        """Языковой сервер файла, если он готов и умеет capability (ключ ServerCapabilities)."""
        ed = self.editor
        svc = ed.services.service_for(ed.language)
        srv = svc.ready_for(ed.path) if svc is not None else None
        return srv if srv is not None and srv.supports(capability) else None

    def python_source(self):
        """Чем отвечать на вопросы о Python: ("pyright", сервер) — pyright готов, спрашиваем его (jedi не нужен);
        ("wait", None) — pyright запущен и грузится: ждём, на готовность контроллер спросит снова;
        ("jedi", jedi) — pyright нет; (None, None) — нечем."""
        ed = self.editor
        svc = ed.services.service_for(ed.language)
        if svc is not None and getattr(svc, "available", False):
            srv = svc.ready_for(ed.path)
            if srv is not None and srv.supports("hoverProvider"):
                return "pyright", srv
            if svc.server_for(ed.path, start=False) is not None:
                return "wait", None
        jedi = self.jedi()
        return ("jedi", jedi) if jedi is not None else (None, None)

    def assist(self):
        if self._assist is None:
            from .pyright_assist import PyrightAssist
            self._assist = PyrightAssist(self)
        return self._assist

    def answer(self, kind):
        """Функция для источника, который отвечает не через окно (pyright-помощник): data → on_result."""
        revision = self.revision()
        return lambda data: self.on_result({"kind": kind, "req": revision, "data": data})

    def jedi(self):
        """jedi для Python (запасной источник: pyright подсказок в строке и семантики не даёт)."""
        jedi = getattr(self.editor.services, "jedi", None)
        return jedi if self.editor.lang == "python" and jedi is not None and jedi.available else None

    def revision(self):
        return self.editor.document().revision()

    def visible_lines(self, margin=30):
        """Строки (с 0), которые видны в окнах файла, с запасом сверху и снизу."""
        first, last = None, None
        for view in self.views():
            block = view.firstVisibleBlock()
            top = block.blockNumber()
            height = view.viewport().height()
            offset = view.contentOffset()
            bottom = top
            while block.isValid() and view.blockBoundingGeometry(block).translated(offset).top() <= height:
                bottom = block.blockNumber()
                block = block.next()
            first = top if first is None else min(first, top)
            last = bottom if last is None else max(last, bottom)
        if first is None:
            return 0, 0
        return max(0, first - margin), min(self.editor.blockCount() - 1, last + margin)

    # --- настройки и обновление
    def apply_settings(self, settings):
        before = dict(self.settings)
        self.settings = dict(settings)
        for ins in self.insights:
            if before.get(ins.id) and not ins.enabled:
                ins.clear()
        if self.settings != before:
            self._dirty = True
            self.refresh()

    def _contents_change(self, _pos, removed, added):
        if removed or added:
            self._edit_timer.start()
            for ins in self.insights:
                if ins.enabled:
                    ins.edited()

    def _shown(self):
        return any(v.isVisible() for v in self.views())

    def refresh(self, scroll=False):
        """Спросить данные у всех включённых возможностей (при прокрутке — только у зависящих от неё).
        Скрытая вкладка ничего не спрашивает — запомнит и спросит, когда её откроют (ensure_fresh)."""
        if not self._shown():
            self._dirty = True
            return
        popup = getattr(self.editor, "completer", None)
        if popup is not None and popup.popup().isVisible():
            # открыт список подсказок: анализатор нужен ему, insights подождут конца набора
            self._edit_timer.start()
            return
        self._dirty = False
        for ins in self.insights:
            if ins.enabled and (not scroll or ins.follows_scroll):
                ins.request()

    def ensure_fresh(self):
        if self._dirty:
            self.refresh()

    def retry_later(self):
        self._retry_timer.start()           # несколько просьб подряд — один повтор

    def symbols_changed(self):
        for ins in self.insights:
            if ins.enabled and hasattr(ins, "symbols_changed"):
                ins.symbols_changed()

    # --- ответы
    def handles(self, kind):
        return kind in self._by_kind

    def on_result(self, res):
        ins = self._by_kind.get(res["kind"])
        if ins is not None and ins.enabled:
            ins.on_result(res)

    # --- вид
    def formats(self, line):
        out = []
        for ins in self.insights:
            if ins.enabled:
                out.extend(ins.formats(line))
        return out

    def rehighlight(self, lines):
        """Перекрасить строки. Сигналы документа на это время молчат: перекраска шлёт textChanged, и без этого
        проснулись бы проверка ошибок, структура файла и лампочка, хотя текст не менялся."""
        doc = self.editor.document()
        lines = sorted(set(lines))
        if not lines:
            return
        doc.blockSignals(True)
        try:
            if len(lines) > 400:
                self.editor.highlighter.rehighlight()
            else:
                for n in lines:
                    block = doc.findBlockByNumber(n)
                    if block.isValid():
                        self.editor.highlighter.rehighlightBlock(block)
        finally:
            doc.blockSignals(False)
        self.update_views()

    def update_views(self):
        for view in self.views():
            view.viewport().update()

    def paint(self, view, painter, blocks):
        for ins in self.insights:
            if ins.enabled:
                ins.paint(view, painter, blocks)

    def click(self, view, pos):
        return any(ins.enabled and ins.click(view, pos) for ins in self.insights)

    def hover(self, view, pos):
        return any(ins.enabled and ins.hover(view, pos) for ins in self.insights)
