"""The existing integer ID sequence, with a non-consuming checkpoint read."""
from __future__ import annotations

from threading import Lock
from contextlib import contextmanager
from contextvars import ContextVar
import random

_prediction_ids = ContextVar("full_ai_private_ids", default=None)


@contextmanager
def isolated_ids():
    current = _prediction_ids.get()
    token = _prediction_ids.set(dict(current) if current is not None else {})
    try:
        yield
    finally:
        _prediction_ids.reset(token)


class IdSequence:
    __slots__ = ("_next_value", "_lock")

    def __init__(self, start: int = 1) -> None:
        self._next_value = start
        self._lock = Lock()

    def __iter__(self):
        return self

    def __next__(self) -> int:
        private = _prediction_ids.get()
        if private is not None:
            value = self.peek()
            private[self] = value + 1
            return value
        with self._lock:
            value = self._next_value
            self._next_value += 1
            return value

    def peek(self) -> int:
        private = _prediction_ids.get()
        if private is not None:
            if self not in private:
                with self._lock:
                    private[self] = self._next_value
            return private[self]
        with self._lock:
            return self._next_value

    def fork(self) -> "IdSequence":
        return IdSequence(self.peek())


def advance_pair_if_unchanged(first, second, expected, end, rng):
    """Reserve exactly the IDs consumed by an accepted isolated calculation.

    Never replace live sequences or rewind them. Both comparisons precede
    either write; ordinary next()/peek() use the same sequence locks.
    RNG is a read-only acceptance guard, never overwritten by a worker.
    """
    if (type(first) is not IdSequence or type(second) is not IdSequence or first is second
            or type(expected) is not tuple or type(end) is not tuple
            or len(expected) != 2 or len(end) != 2
            or any(type(value) is not int for value in (*expected, *end))
            or any(after < before for before, after in zip(expected, end))):
        return False
    with first._lock, second._lock:
        if (first._next_value, second._next_value) != expected or random.getstate() != rng:
            return False
        first._next_value, second._next_value = end
        return True
