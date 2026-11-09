"""Экран терминала: текст, цвета, курсор, история прокрутки. Разбор управляющих последовательностей — pyte
(пакет python-pyte); здесь только то, что нужно виджету. Без Qt-виджетов."""
try:                        # python-pyte — зависимость пакета; без него IDE работает, а терминал подсказывает, что поставить
    import pyte
except ImportError:
    pyte = None

AVAILABLE = pyte is not None

HISTORY = 5000
APP_CURSOR = 1 << 5                 # DECCKM (?1h): стрелки в «режиме приложения» (vim, less)
BRACKETED_PASTE = 2004 << 5         # ?2004h: вставка обрамляется, чтобы оболочка не выполняла её построчно


class TerminalScreen:
    def __init__(self, cols, rows, respond=None):
        self.screen = pyte.HistoryScreen(cols, rows, history=HISTORY, ratio=0.5)
        self.screen.write_process_input = respond or (lambda data: None)   # ответы на запросы (позиция курсора…)
        self.title = ""
        self.bells = 0
        screen = self.screen
        original_title = screen.set_title

        def set_title(param):
            original_title(param)
            self.title = param
        screen.set_title = set_title
        screen.bell = lambda *a: setattr(self, "bells", self.bells + 1)
        self.stream = pyte.ByteStream(screen)      # после подмены: поток запоминает обработчики при создании

    @property
    def cols(self):
        return self.screen.columns

    @property
    def rows(self):
        return self.screen.lines

    def feed(self, data):
        self.stream.feed(data)

    def resize(self, cols, rows):
        if (cols, rows) != (self.cols, self.rows):
            self.screen.resize(rows, cols)

    def history_size(self):
        return len(self.screen.history.top)

    def total_lines(self):
        return self.history_size() + self.rows

    def line(self, index):
        """Строка index (0 — самая старая в истории): {столбец: pyte Char} — пустые клетки отсутствуют."""
        history = self.screen.history.top
        if index < len(history):
            return history[index]
        return self.screen.buffer[index - len(history)]

    def text(self, index, start=0, end=None):
        line = self.line(index)
        end = self.cols if end is None else end
        return "".join(line[x].data for x in range(start, end)).rstrip()

    def cursor(self):
        """(столбец, строка в общем счёте, виден ли)."""
        c = self.screen.cursor
        return c.x, self.history_size() + c.y, not c.hidden

    def app_cursor(self):
        return APP_CURSOR in self.screen.mode

    def bracketed_paste(self):
        return BRACKETED_PASTE in self.screen.mode
