"""Текст файла: кодировка (UTF-8 / BOM / cp1251) и язык по имени."""
from .. import languages


ENCODING_NAME = {"utf-8": "UTF-8", "utf-8-sig": "editor.utf_8_with_bom", "utf-16": "UTF-16",
                 "cp1251": "Windows-1251", "latin-1": "Latin-1"}


def decode_source(raw):
    """Кодировка файла: BOM, затем UTF-8, затем Windows-1251 — так файлы с русскими комментариями
    из Visual Studio на русской Windows открываются без «�» и не портятся при сохранении."""
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig", raw[3:].decode("utf-8", errors="replace")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16", raw.decode("utf-16", errors="replace")
    for enc in ("utf-8", "cp1251"):
        try:
            return enc, raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return "latin-1", raw.decode("latin-1")


def lang_for(path):
    return languages.for_path(path).id
