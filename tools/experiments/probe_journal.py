"""Archived failed experiment: NEVER imported by the game.

Mutable containers retain the old eager-copy semantics, including alias
splitting. Ordinary object namespaces are captured on their first write (or
explicit namespace access); untouched scalar-only objects need no restore.
Slots and objects with observable access/copy hooks keep the full checkpoint.
"""
from abc import ABCMeta
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
import random
import sys


_active = ContextVar("battle_probe_journal", default=None)
_statistics = ContextVar("battle_probe_journal_statistics", default=None)
_SCALARS = frozenset((type(None), bool, int, float, str, bytes))
_setters = []


def supported_runtime():
    # The namespace-alias guard relies on conventional CPython reference
    # ownership. New interpreter versions and free-threaded builds stay on
    # the full checkpoint until that ownership model has been validated.
    gil_enabled = getattr(sys, "_is_gil_enabled", None)
    return (sys.implementation.name == "cpython" and (3, 10) <= sys.version_info[:2] <= (3, 13)
            and (gil_enabled is None or gil_enabled()))


class _NamespaceOwner:
    pass


_native_namespace = _NamespaceOwner.__dict__["__dict__"]


def before_write(obj):
    frame = _active.get()
    while frame is not None:
        entry = frame.entries.get(id(obj))
        if entry is not None and entry.obj is obj:
            entry.touch()
        frame = frame.parent


def tracked_setter(function):
    _setters.append(function)
    return function


class JournalObject(_NamespaceOwner):
    __slots__ = ()

    @property
    def __dict__(self):
        # vars(), direct dict.update/pop/clear and pickle's explicit namespace
        # access must capture the old scalar fields before exposing the dict.
        before_write(self)
        return _native_namespace.__get__(self)

    @__dict__.setter
    def __dict__(self, value):
        before_write(self)
        _native_namespace.__set__(self, value)

    @__dict__.deleter
    def __dict__(self):
        before_write(self)
        _native_namespace.__delete__(self)

    @tracked_setter
    def __setattr__(self, name, value):
        before_write(self)
        object.__setattr__(self, name, value)

    def __delattr__(self, name):
        before_write(self)
        object.__delattr__(self, name)


_namespace_property = JournalObject.__dict__["__dict__"]


@lru_cache(maxsize=256)
def _candidate_type(cls):
    namespaces = tuple(base.__dict__ for base in cls.__mro__)
    def method(name):
        return next((ns[name] for ns in namespaces if name in ns), None)
    return (method("__dict__") is _namespace_property
            and method("__getattribute__") is object.__getattribute__
            and not any("__getattr__" in ns for ns in namespaces)
            and not any("__class__" in base.__dict__ for base in cls.__mro__ if base is not object)
            and any(method("__setattr__") is setter for setter in _setters)
            and method("__delattr__") is JournalObject.__delattr__)


class _Unsupported(Exception):
    pass


def _plain_copy(value, opaque_plan, budget, depth=0):
    cls = type(value)
    # Guard before hashing a class or touching custom metaclass attributes.
    if type(cls) is not type and type(cls) is not ABCMeta:
        raise _Unsupported
    if cls in _SCALARS:
        return value
    budget[0] -= 1
    if budget[0] < 0 or depth > 64:
        raise _Unsupported
    if cls is dict:
        copied = {}
        for key, item in value.items():
            key_type = type(key)
            if (type(key_type) is not type or key_type not in _SCALARS):
                raise _Unsupported
            copied[key] = _plain_copy(item, opaque_plan, budget, depth + 1)
        return copied
    if cls is list:
        return [_plain_copy(item, opaque_plan, budget, depth + 1) for item in value]
    if cls is tuple:
        return tuple(_plain_copy(item, opaque_plan, budget, depth + 1) for item in value)
    if cls is deque:
        return deque(_plain_copy(item, opaque_plan, budget, depth + 1) for item in value)
    if cls is set:
        return set(value)  # Deliberately shallow, just like the old checkpoint.
    if opaque_plan(cls)[0] is None:
        return value
    raise _Unsupported


@contextmanager
def statistics_scope(statistics):
    token = _statistics.set(statistics)
    try:
        yield
    finally:
        _statistics.reset(token)


class _Entry:
    __slots__ = ("obj", "containers", "original")

    def __init__(self, obj, containers):
        self.obj = obj
        self.containers = containers
        self.original = None

    def touch(self):
        if self.original is None:
            self.original = _native_namespace.__get__(self.obj).copy()
            self.original.update(self.containers)

    def snapshot(self):
        if self.original is not None:
            return self.original
        if not self.containers:
            return None
        # The scalars are unchanged. Rebuild using a C-level shallow copy,
        # then attach each independently copied container from entry time.
        result = _native_namespace.__get__(self.obj).copy()
        result.update(self.containers)
        return result


class _Frame:
    def __init__(self, opaque_plan):
        self.parent = _active.get()
        self.entries = {}
        self.opaque_plan = opaque_plan
        self.counts = dict(journal_frames=1, journal_objects=0, journal_full_objects=0,
                           journal_touched_objects=0, journal_untouched_objects=0,
                           journal_container_fields=0, journal_skipped_objects=0)

    def capture(self, obj):
        cls = type(obj)
        if type(cls) is not type and type(cls) is not ABCMeta:
            return None
        if not _candidate_type(cls):
            return None
        namespace = _native_namespace.__get__(obj)
        if type(namespace) is not dict:
            return None
        # A dict alias obtained BEFORE this frame can mutate scalar fields
        # without going through the namespace property. CPython has one owner
        # reference, this local, and getrefcount's argument. Any other holder
        # forces the full old snapshot; other interpreters do so as well.
        if sys.getrefcount(namespace) != 3:
            return None
        containers, budget = {}, [8192]
        try:
            for name, value in namespace.items():
                if type(name) is not str:
                    raise _Unsupported
                value_type = type(value)
                if (type(value_type) is type or type(value_type) is ABCMeta) and value_type in _SCALARS:
                    continue
                copied = _plain_copy(value, self.opaque_plan, budget)
                if copied is not value:
                    containers[name] = copied
        except _Unsupported:
            return None
        entry = _Entry(obj, containers)
        self.entries[id(obj)] = entry
        self.counts["journal_objects"] += 1
        self.counts["journal_container_fields"] += len(containers)
        return entry


@contextmanager
def rollback_objects(objects, state, restore, *, enabled, opaque_plan):
    frame = _Frame(opaque_plan) if enabled and supported_runtime() else None
    token = _active.set(frame) if frame is not None else None
    try:
        saved = []
        for obj in objects:
            entry = frame.capture(obj) if frame is not None else None
            if entry is None:
                saved.append((obj, state(obj)))
                if frame is not None:
                    frame.counts["journal_full_objects"] += 1
            else:
                saved.append((obj, entry))
        rng = random.getstate()
        try:
            yield
        finally:
            # The parent keeps observing restores where appropriate. The
            # current frame must not capture its own restoration as a write.
            if token is not None:
                _active.reset(token)
                token = None
            for obj, snapshot in reversed(saved):
                if type(snapshot) is _Entry:
                    frame.counts["journal_touched_objects" if snapshot.original is not None
                                 else "journal_untouched_objects"] += 1
                    snapshot = snapshot.snapshot()
                    if snapshot is None:
                        frame.counts["journal_skipped_objects"] += 1
                        continue
                restore(obj, snapshot)
            random.setstate(rng)
    finally:
        if token is not None:
            _active.reset(token)
        stats = _statistics.get()
        if frame is not None and stats is not None:
            for name, amount in frame.counts.items():
                stats[name] = stats.get(name, 0) + amount
