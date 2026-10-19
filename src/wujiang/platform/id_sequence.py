"""The existing integer ID sequence, with a non-consuming checkpoint read."""
from __future__ import annotations

from threading import Lock


class IdSequence:
    __slots__ = ("_next_value", "_lock")

    def __init__(self, start: int = 1) -> None:
        self._next_value = start
        self._lock = Lock()

    def __iter__(self):
        return self

    def __next__(self) -> int:
        with self._lock:
            value = self._next_value
            self._next_value += 1
            return value

    def peek(self) -> int:
        with self._lock:
            return self._next_value

    def fork(self) -> "IdSequence":
        return IdSequence(self.peek())
