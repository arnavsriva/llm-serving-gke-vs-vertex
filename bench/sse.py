"""Incremental parser for ``text/event-stream`` response bodies.

Network reads split events at arbitrary byte offsets, so the parser buffers partial lines and only
emits an event's ``data`` once the blank line that terminates it has arrived.
"""

from __future__ import annotations


class SSEParser:
    def __init__(self) -> None:
        self._buf = bytearray()
        self._data: list[str] = []

    def feed(self, chunk: bytes) -> list[str]:
        """Consume bytes; return the ``data`` payload of every event they completed."""
        self._buf += chunk
        events: list[str] = []
        start = 0
        while (nl := self._buf.find(b"\n", start)) != -1:
            self._line(bytes(self._buf[start:nl]), events)
            start = nl + 1
        del self._buf[:start]
        return events

    def flush(self) -> list[str]:
        """Dispatch whatever is pending at end of stream (tolerates a missing final blank line)."""
        events: list[str] = []
        if self._buf:
            self._line(bytes(self._buf), events)
            self._buf.clear()
        self._dispatch(events)
        return events

    def _line(self, line: bytes, events: list[str]) -> None:
        if line.endswith(b"\r"):
            line = line[:-1]
        if not line:
            self._dispatch(events)
            return
        if line.startswith(b":"):
            return  # comment, e.g. a keep-alive
        field, _, value = line.partition(b":")
        if field == b"data":
            self._data.append(value.removeprefix(b" ").decode("utf-8"))

    def _dispatch(self, events: list[str]) -> None:
        if self._data:
            events.append("\n".join(self._data))
            self._data = []
