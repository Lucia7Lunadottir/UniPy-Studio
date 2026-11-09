"""Сообщения «Content-Length: N\\r\\n\\r\\n<JSON>» — общая рамка LSP (анализаторы) и DAP (отладчики)."""
import json
import re

_LENGTH = re.compile(rb"Content-Length:\s*(\d+)", re.I)


def frame(message):
    body = json.dumps(message, ensure_ascii=False).encode("utf-8")
    return b"Content-Length: %d\r\n\r\n" % len(body) + body


def split_frames(buffer):
    """(готовые сообщения, остаток буфера). Мусор без заголовка пропускается, битый JSON — тоже."""
    messages = []
    while True:
        head_end = buffer.find(b"\r\n\r\n")
        if head_end < 0:
            return messages, buffer
        m = _LENGTH.search(buffer[:head_end])
        if not m:
            buffer = buffer[head_end + 4:]
            continue
        start, length = head_end + 4, int(m.group(1))
        if len(buffer) < start + length:
            return messages, buffer
        body, buffer = buffer[start:start + length], buffer[start + length:]
        try:
            messages.append(json.loads(body.decode("utf-8")))
        except ValueError:
            continue
