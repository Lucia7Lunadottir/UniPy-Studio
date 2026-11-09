"""Позиции в строке: ast считает в байтах UTF-8, jedi — в символах, Qt и LSP — в единицах UTF-16. Без Qt."""


def utf16_len(text):
    return len(text) if text.isascii() else len(text.encode("utf-16-le")) // 2


class Lines:
    """Строки файла и перевод столбцов между системами счёта."""

    def __init__(self, code):
        self.lines = code.split("\n")
        self._bytes = {}

    def text(self, line0):
        return self.lines[line0] if 0 <= line0 < len(self.lines) else ""

    def _encoded(self, line0):
        b = self._bytes.get(line0)
        if b is None:
            b = self._bytes[line0] = self.text(line0).encode("utf-8")
        return b

    def chars_from_bytes(self, line0, byte_col):
        """Столбец ast (байты) → столбец в символах (jedi)."""
        text = self.text(line0)
        if text.isascii():
            return byte_col
        return len(self._encoded(line0)[:byte_col].decode("utf-8", errors="ignore"))

    def utf16_from_chars(self, line0, char_col):
        return utf16_len(self.text(line0)[:char_col])

    def utf16_from_bytes(self, line0, byte_col):
        return self.utf16_from_chars(line0, self.chars_from_bytes(line0, byte_col))
