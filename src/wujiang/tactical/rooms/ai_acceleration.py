"""Exact, decision-local memoization and isolated CPU precomputation.

Only proven read-only scalar queries and effect baselines are retained.
Full serialized state and RNG state are the cache key, not a partial combat
signature. Worker transfer comparison ignores only pickle text interning;
mutable graph sharing and iteration order remain exact. Query purity uses
byte-exact local snapshots.
"""
from __future__ import annotations

import atexit
from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
import multiprocessing
import math
import os
import pickle
import pickletools
import random
import struct
import weakref
from threading import Lock
from time import monotonic

from wujiang.platform.id_sequence import IdSequence


_enabled = ContextVar("ai_exact_acceleration_enabled", default=True)
_memo = ContextVar("ai_exact_decision_memo", default=None)
_last_statistics = ContextVar("ai_exact_last_statistics", default=None)
_in_worker = False
_pool = None
_pool_lock = Lock()
_worker_disabled = False
_WORKER_BATCH_TIMEOUT_SECONDS = 2.0


@dataclass
class _State:
    size: int
    values: dict = field(default_factory=dict)
    prepared: set = field(default_factory=set)
    baselines: dict = field(default_factory=dict)


class _Memo:
    def __init__(self):
        self.states = OrderedDict()
        self.bytes = 0
        self.unsupported_battles = set()
        self.worker_transfer_rejected = False
        self.statistics = dict(hits=0, misses=0, impure=0, unsupported=0,
                               move_hits=0, move_misses=0,
                               snapshots=0, baseline_hits=0, baseline_misses=0,
                               baseline_impure=0, baseline_units_reused=0,
                               worker_jobs=0, worker_values=0, worker_failures=0,
                               worker_roundtrip_rejected=0, worker_impure=0, worker_errors=0)

    def state(self, battle):
        if id(battle) in self.unsupported_battles:
            return None, None
        key = _snapshot(battle)
        if key is None:
            self.statistics["unsupported"] += 1
            self.unsupported_battles.add(id(battle))
            return None, None
        state = self.states.get(key)
        if state is not None:
            self.states.move_to_end(key)
            return key, state
        size = len(key[0]) + 8192
        while self.states and (len(self.states) >= 32 or self.bytes + size > 16 * 1024 * 1024):
            _, old = self.states.popitem(last=False)
            self.bytes -= old.size
        state = _State(size)
        self.states[key] = state
        self.bytes += size
        return key, state


def _snapshot(battle):
    try:
        memo = _memo.get() if _enabled.get() and not _in_worker else None
        if memo is not None:
            memo.statistics["snapshots"] += 1
        # Unit.__getstate__ omits weakrefs. Encode absent bindings explicitly;
        # foreign-battle bindings cannot safely be transferred to workers.
        bindings = []
        for unit in [*battle.all_units(), *battle.destroyed_units]:
            reference = getattr(unit, "_battle_ref", None)
            if reference is not None and reference() is not battle:
                return None
            bindings.append((unit.unit_id, reference is not None))
        data = pickle.dumps(battle, protocol=pickle.HIGHEST_PROTOCOL)
        if len(data) > 1024 * 1024:
            return None
        return data, random.getstate(), tuple(bindings), tuple(battle.__dict__)
    except Exception:
        # Closures and unsupported checkpoint objects keep the original path.
        return None


def _id_state():
    from wujiang.tactical.engine import core
    from wujiang.tactical.heroes import base
    if not isinstance(core._id_counter, IdSequence) or not isinstance(base._unit_counter, IdSequence):
        return None
    return core._id_counter.peek(), base._unit_counter.peek()


def _cacheable_scalar(value):
    if not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


@contextmanager
def acceleration_disabled():
    token = _enabled.set(False)
    try:
        yield
    finally:
        _enabled.reset(token)


@contextmanager
def decision_memo_scope():
    if not _enabled.get() or _in_worker or _memo.get() is not None:
        yield
        return
    memo = _Memo()
    token = _memo.set(memo)
    try:
        yield
    finally:
        _last_statistics.set(dict(memo.statistics))
        _memo.reset(token)


def last_statistics():
    return dict(_last_statistics.get() or {}) if _enabled.get() else {}


def cached_position_value(function):
    @wraps(function)
    def wrapped(battle, actor, unit):
        memo = _memo.get() if _enabled.get() and not _in_worker else None
        ids = _id_state() if memo is not None else None
        if memo is None or ids is None:
            return function(battle, actor, unit)
        key, state = memo.state(battle)
        if state is None:
            return function(battle, actor, unit)
        item = actor.unit_id, unit.unit_id
        if item in state.values:
            value = state.values[item]
            if value is not None:
                memo.statistics["hits"] += 1
                return value
            return function(battle, actor, unit)
        memo.statistics["misses"] += 1
        value = function(battle, actor, unit)
        if _cacheable_scalar(value) and _id_state() == ids and _snapshot(battle) == key:
            state.values[item] = value
        else:
            state.values[item] = None
            memo.statistics["impure"] += 1
        return value
    return wrapped


def _freeze_effect_baseline(result, unit_ids, include_pending):
    """Retain only exact immutable rows; never share a caller's mutable dict."""
    from wujiang.tactical.engine.core import Position

    if (type(result) is not dict or any(type(unit_id) is not str for unit_id in unit_ids)
            or tuple(result) != tuple(dict.fromkeys(unit_ids))):
        return None
    for row in result.values():
        if type(row) is not tuple or len(row) != (8 if include_pending else 7):
            return None
        if row[5] is not None:
            if type(row[5]) is not Position or type(row[5].x) is not int or type(row[5].y) is not int:
                return None
        for index, value in enumerate(row):
            if index != 5 and (type(value) not in (int, float, bool) or not _cacheable_scalar(value)):
                return None
    return tuple(result.items())


def cached_effect_baseline(function):
    """Cache the original ordered baseline comprehension as one pure query."""
    @wraps(function)
    def wrapped(battle, actor, units, *, include_pending=False):
        memo = _memo.get() if _enabled.get() and not _in_worker else None
        if memo is None or len(units) < 12 or len(units) > 256:
            return function(battle, actor, units, include_pending=include_pending)
        ids = _id_state()
        if (ids is None or battle.units.get(actor.unit_id) is not actor
                or any(battle.units.get(unit.unit_id) is not unit for unit in units)):
            return function(battle, actor, units, include_pending=include_pending)
        unit_ids = tuple(unit.unit_id for unit in units)
        key, state = memo.state(battle)
        if state is None:
            return function(battle, actor, units, include_pending=include_pending)
        item = function, actor.unit_id, unit_ids, include_pending
        if item in state.baselines:
            frozen = state.baselines[item]
            if frozen is not None:
                memo.statistics["baseline_hits"] += 1
                memo.statistics["baseline_units_reused"] += len(frozen)
                return dict(frozen)
            return function(battle, actor, units, include_pending=include_pending)
        if len(state.baselines) >= 64:
            return function(battle, actor, units, include_pending=include_pending)
        memo.statistics["baseline_misses"] += 1
        result = function(battle, actor, units, include_pending=include_pending)
        frozen = _freeze_effect_baseline(result, unit_ids, include_pending)
        if (frozen is None or _id_state() != ids or _snapshot(battle) != key
                or tuple(unit.unit_id for unit in units) != unit_ids
                or any(battle.units.get(unit.unit_id) is not unit for unit in units)):
            state.baselines[item] = None
            memo.statistics["baseline_impure"] += 1
            return result
        # Include the new rows in the existing state budget. Stop retaining
        # rows when full; eviction and all calculations remain unchanged.
        try:
            size = len(pickle.dumps(frozen, protocol=pickle.HIGHEST_PROTOCOL)) + 256
        except Exception:
            return result
        if memo.states.get(key) is state and memo.bytes + size <= 16 * 1024 * 1024:
            state.baselines[item] = frozen
            state.size += size
            memo.bytes += size
        return result
    return wrapped


def cached_move_score(function):
    """Retain finite, pure movement scores under the same full-state boundary."""
    @wraps(function)
    def wrapped(battle, actor, destination, role, profile):
        memo = _memo.get() if _enabled.get() and not _in_worker else None
        # Small battles have too little repeated work to justify whole-state
        # serialization for every movement candidate.
        if memo is None or len(battle.units) < 12:
            return function(battle, actor, destination, role, profile)
        ids = _id_state()
        if ids is None or battle.units.get(actor.unit_id) is not actor:
            return function(battle, actor, destination, role, profile)
        try:
            arguments = pickle.dumps((destination, role, profile), protocol=pickle.HIGHEST_PROTOCOL)
        except Exception:
            return function(battle, actor, destination, role, profile)
        if len(arguments) > 8192:
            return function(battle, actor, destination, role, profile)
        key, state = memo.state(battle)
        if state is None:
            return function(battle, actor, destination, role, profile)
        item = function, actor.unit_id, arguments
        if item not in state.values and len(state.values) >= 2048:
            return function(battle, actor, destination, role, profile)
        if item in state.values:
            value = state.values[item]
            if value is not None:
                memo.statistics["move_hits"] += 1
                return value
            return function(battle, actor, destination, role, profile)
        memo.statistics["move_misses"] += 1
        value = function(battle, actor, destination, role, profile)
        try:
            unchanged_arguments = arguments == pickle.dumps((destination, role, profile), protocol=pickle.HIGHEST_PROTOCOL)
        except Exception:
            unchanged_arguments = False
        if (_cacheable_scalar(value) and unchanged_arguments
                and _id_state() == ids and _snapshot(battle) == key):
            state.values[item] = value
        else:
            state.values[item] = None
            memo.statistics["impure"] += 1
        return value
    return wrapped


def _worker_init():
    global _in_worker
    _in_worker = True


def _worker_count():
    try:
        requested = int(os.environ.get("WUJIANG_AI_WORKERS", "4"))
    except ValueError:
        requested = 4
    return max(0, min(requested, 4, (os.cpu_count() or 1) - 1))


def _executor():
    global _pool
    if _in_worker or _worker_disabled or _worker_count() < 2:
        return None
    with _pool_lock:
        if _pool is None:
            _pool = ProcessPoolExecutor(max_workers=_worker_count(),
                                        mp_context=multiprocessing.get_context("spawn"), initializer=_worker_init)
        return _pool


@atexit.register
def shutdown_workers():
    global _pool
    with _pool_lock:
        pool, _pool = _pool, None
    if pool is not None:
        pool.shutdown(wait=False, cancel_futures=True)


def _restore_worker_battle(snapshot):
    battle = pickle.loads(snapshot[0])
    # Checkpoint loading intentionally upgrades old saves and rebinds every
    # unit. A speculative query must instead reproduce this exact live state.
    # Remove only defaults introduced by __setstate__; never invent values.
    original_fields = set(snapshot[3])
    for name in tuple(battle.__dict__):
        if name not in original_fields:
            del battle.__dict__[name]
    bindings = dict(snapshot[2])
    for unit in [*battle.all_units(), *battle.destroyed_units]:
        if bindings[unit.unit_id]:
            unit._battle_ref = weakref.ref(battle)
        else:
            unit.__dict__.pop("_battle_ref", None)
    return battle


def _transfer_fingerprint(data):
    """Normalize pickle text interning, retaining every other graph operation.

    Unpickling may reuse a class/module string already interned by Python.
    Thus equal text can acquire different pickle memo references on re-save.
    Inline text memo references and renumber the remaining memo entries. Do
    not sort sets/dicts, merge mutable objects, or normalize numeric values.
    These bytes are a comparison witness only; transport uses the original
    pickle, preserving its actual object graph.
    """
    operations = list(pickletools.genops(data))
    memo = {}
    next_memo = 0
    last_text = None
    output = bytearray()
    for index, (opcode, argument, offset) in enumerate(operations):
        end = operations[index + 1][2] if index + 1 < len(operations) else len(data)
        raw = data[offset:end]
        name = opcode.name
        if name == "FRAME":
            continue
        if name == "MEMOIZE":
            if last_text is not None:
                memo[len(memo)] = last_text
            else:
                memo[len(memo)] = next_memo
                next_memo += 1
                output.extend(raw)
            continue
        if name in ("BINGET", "LONG_BINGET"):
            entry = memo[argument]
            last_text = entry if isinstance(entry, bytes) else None
            output.extend(entry if last_text is not None else b"j" + struct.pack("<I", entry))
        elif name in ("SHORT_BINUNICODE", "BINUNICODE", "BINUNICODE8"):
            last_text = raw
            output.extend(raw)
        else:
            if name in ("PUT", "BINPUT", "LONG_BINPUT", "GET", "STRING", "UNICODE"):
                raise ValueError("unsupported legacy transfer pickle")
            last_text = None
            output.extend(raw)
        if len(output) > 4 * 1024 * 1024:
            raise ValueError("transfer comparison exceeds budget")
    return bytes(output)


def _position_batch(request, diagnostics=False):
    """Workers return scalar values only, never a replacement live battle."""
    from wujiang.tactical.engine import core
    from wujiang.tactical.heroes import base
    from wujiang.tactical.rooms import ai
    snapshot, actor_id, unit_ids, ids = request
    results = []
    counts = dict(worker_roundtrip_rejected=0, worker_impure=0, worker_errors=0)
    try:
        source_fingerprint = _transfer_fingerprint(snapshot[0])
    except Exception:
        counts["worker_errors"] = len(unit_ids)
        results = [(unit_id, None) for unit_id in unit_ids]
        return (results, counts) if diagnostics else results
    for index, unit_id in enumerate(unit_ids):
        try:
            battle = _restore_worker_battle(snapshot)
            random.setstate(snapshot[1])
            core._id_counter, base._unit_counter = IdSequence(ids[0]), IdSequence(ids[1])
            local_snapshot = _snapshot(battle)
            if (local_snapshot is None or local_snapshot[1:] != snapshot[1:]
                    or _transfer_fingerprint(local_snapshot[0]) != source_fingerprint):
                # Every item in the chunk has the same transfer snapshot.
                # Repeating the identical rejected load cannot make it valid.
                remaining = unit_ids[index:]
                counts["worker_roundtrip_rejected"] += len(remaining)
                results.extend((remaining_id, None) for remaining_id in remaining)
                break
            value = ai.reviewed_r17_position_value.__wrapped__(battle, battle.get_unit(actor_id), battle.get_unit(unit_id))
            # Purity remains byte-exact within this process. Transfer text
            # normalization must never hide a change made by the query itself.
            pure = _id_state() == ids and _snapshot(battle) == local_snapshot
            if not pure or not _cacheable_scalar(value):
                counts["worker_impure"] += 1
            results.append((unit_id, value if pure and _cacheable_scalar(value) else None))
        except Exception:
            counts["worker_errors"] += 1
            results.append((unit_id, None))
    return (results, counts) if diagnostics else results


def prepare_position_values(battle, actor, units):
    """Parallelize independent pure queries; impure/failed values run normally."""
    memo = _memo.get() if _enabled.get() and not _in_worker else None
    # Chess already runs each match in a background thread. Sending its whole
    # battle to extra processes can hold the room lock while workers replay
    # every unit query; the ordinary exact memo computes the same values here.
    if (memo is None or memo.worker_transfer_rejected or len(units) < 12
            or getattr(battle, "fast_ai_simulation", False)):
        return
    ids = _id_state()
    if ids is None:
        return
    key, state = memo.state(battle)
    if state is None or actor.unit_id in state.prepared:
        return
    state.prepared.add(actor.unit_id)
    missing = [unit.unit_id for unit in units if (actor.unit_id, unit.unit_id) not in state.values]
    if len(missing) < 4:
        return
    try:
        executor = _executor()
        if executor is None:
            return
        workers = _worker_count()
        chunks = [missing[index::workers] for index in range(workers)]
        futures = [executor.submit(_position_batch, (key, actor.unit_id, chunk, ids), True) for chunk in chunks if chunk]
        memo.statistics["worker_jobs"] += len(futures)
        deadline = monotonic() + _WORKER_BATCH_TIMEOUT_SECONDS
        batches = [future.result(timeout=max(0.0, deadline - monotonic())) for future in futures]
        # Another thread may have advanced global RNG while workers ran. Such
        # results are never installed under a different live state.
        if _snapshot(battle) != key:
            return
        for batch, counts in batches:
            for name, count in counts.items():
                memo.statistics[name] += count
            if counts["worker_roundtrip_rejected"]:
                # Retain serial memoization but avoid repeatedly transferring
                # a state representation this decision cannot reproduce.
                memo.worker_transfer_rejected = True
            for unit_id, value in batch:
                if value is not None:
                    state.values[(actor.unit_id, unit_id)] = value
                    memo.statistics["worker_values"] += 1
                else:
                    memo.statistics["impure"] += 1
    except Exception:
        global _worker_disabled
        _worker_disabled = True
        memo.statistics["worker_failures"] += 1
        # Failure changes performance only. The caller still evaluates every
        # original item, in the original order, using the original function.
        shutdown_workers()
