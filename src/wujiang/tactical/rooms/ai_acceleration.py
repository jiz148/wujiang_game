"""Exact, decision-local memoization and isolated CPU precomputation.

Only proven read-only scalar queries and effect baselines are retained.
Full serialized state and RNG state are the cache key, not a partial combat
signature. Worker transfer comparison ignores only pickle text interning;
mutable graph sharing and iteration order remain exact. Query purity uses
byte-exact local snapshots.
"""
from __future__ import annotations

import atexit
from abc import ABCMeta
from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from io import BytesIO
import multiprocessing
import math
import os
import pickle
import pickletools
import random
import struct
import weakref
from threading import Lock, Timer
from time import monotonic
from types import FunctionType

from wujiang.platform.id_sequence import IdSequence
from wujiang.platform.ai_native import load_rng_mask


_enabled = ContextVar("ai_exact_acceleration_enabled", default=True)
_memo = ContextVar("ai_exact_decision_memo", default=None)
_last_statistics = ContextVar("ai_exact_last_statistics", default=None)
_in_worker = False
_pool = None
_pool_lock = Lock()
_worker_disabled = False
_move_batch_active = ContextVar("ai_exact_move_batch_active", default=False)
_baseline_batch_active = ContextVar("ai_exact_baseline_batch_active", default=False)
_baseline_diagnostic_sink = ContextVar("ai_exact_baseline_diagnostic_sink", default=None)
_position_diagnostic_sink = ContextVar("ai_exact_position_diagnostic_sink", default=None)
_MOVE_BATCHES_ENABLED = os.environ.get("WUJIANG_AI_MOVE_BATCHES", "1") != "0"
_SMALL_BASELINES_ENABLED = os.environ.get("WUJIANG_AI_SMALL_BASELINES", "1") != "0"
_BASELINE_BATCHES_ENABLED = os.environ.get("WUJIANG_AI_BASELINE_BATCHES", "1") != "0"
_PARALLEL_BASELINES_ENABLED = os.environ.get("WUJIANG_AI_PARALLEL_BASELINES", "1") != "0"
# Opt in during acceptance first; the production default awaits measured gain.
_RNG_INDEPENDENT_ROWS_ENABLED = os.environ.get("WUJIANG_AI_RNG_INDEPENDENT_ROWS", "0") != "0"
_ADAPTIVE_ROWS_ENABLED = os.environ.get("WUJIANG_AI_ADAPTIVE_ROWS", "1") != "0"
_TACTICAL_QUERIES_ENABLED = os.environ.get("WUJIANG_AI_TACTICAL_QUERIES", "0") != "0"
_NATIVE_ROW_KEYS_ENABLED = os.environ.get("WUJIANG_AI_NATIVE_ROW_KEYS", "0") != "0"
_INDEPENDENT_RNG_MARKER = "battle-local-rng"
_baseline_costs = OrderedDict()
_baseline_cost_lock = Lock()
_PARALLEL_BASELINE_MIN_SECONDS = 0.025
_WORKER_BATCH_TIMEOUT_SECONDS = 2.0


@dataclass
class _State:
    size: int
    values: dict = field(default_factory=dict)
    prepared: set = field(default_factory=set)
    baselines: dict = field(default_factory=dict)
    batches: dict = field(default_factory=dict)
    independent_rows: dict = field(default_factory=dict)
    tactical_queries: dict = field(default_factory=dict)


class _Memo:
    def __init__(self):
        self.states = OrderedDict()
        self.bytes = 0
        self.unsupported_battles = set()
        self.worker_transfer_rejected = False
        self.random_baseline_battles = set()
        self.independent_row_bypass = set()
        self.independent_row_trials = {}
        self.independent_row_disabled = set()
        self.tactical_query_bypass = set()
        self.statistics = dict(hits=0, misses=0, impure=0, unsupported=0,
                               move_hits=0, move_misses=0,
                               snapshots=0, snapshot_seconds=0.0, baseline_hits=0, baseline_misses=0,
                               baseline_impure=0, baseline_units_reused=0,
                               baseline_batch_runs=0, parallel_baseline_jobs=0,
                               parallel_baseline_hits=0, parallel_baseline_rejected=0,
                               move_batch_hits=0, move_batch_misses=0, move_batch_impure=0, move_batch_queries_reused=0,
                               worker_jobs=0, worker_values=0, worker_failures=0,
                               worker_roundtrip_rejected=0, worker_impure=0, worker_errors=0)
        self.statistics.update(independent_row_hits=0, independent_row_misses=0,
                               independent_row_dependent=0, independent_row_impure=0,
                               independent_row_unsupported=0, independent_row_snapshots=0,
                               independent_row_bypasses=0, independent_row_fallbacks=0)
        self.statistics.update(independent_row_snapshot_seconds=0.0, independent_row_native_snapshots=0,
                               independent_row_native_visits=0, independent_row_native_fallbacks=0)
        self.statistics.update(tactical_query_hits=0, tactical_query_misses=0,
                               tactical_query_impure=0, tactical_query_bypasses=0,
                               tactical_query_unsupported=0, tactical_query_cells_reused=0)

    def state(self, battle):
        if id(battle) in self.unsupported_battles:
            return None, None
        key = _snapshot(battle)
        return self.state_from_snapshot(battle, key)

    def state_from_snapshot(self, battle, key):
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


def _snapshot(battle, *, request=None, strict=False):
    snapshot_started = None
    try:
        memo = _memo.get() if _enabled.get() and not _in_worker else None
        if memo is not None:
            snapshot_started = monotonic()
            memo.statistics["snapshots"] += 1
        # Unit.__getstate__ omits weakrefs. Encode absent bindings explicitly;
        # foreign-battle bindings cannot safely be transferred to workers.
        bindings = []
        for unit in [*battle.all_units(), *battle.destroyed_units]:
            reference = getattr(unit, "_battle_ref", None)
            if strict and reference is not None and type(reference) is not weakref.ReferenceType:
                return None
            if reference is not None and reference() is not battle:
                return None
            bindings.append((unit.unit_id, reference is not None))
        if request is None and not strict:
            data = pickle.dumps(battle, protocol=pickle.HIGHEST_PROTOCOL)
        else:
            stream = BytesIO()
            writer = (_TacticalQueryPickler if strict else pickle.Pickler)(stream, protocol=pickle.HIGHEST_PROTOCOL)
            writer.dump(battle)
            data = stream.getvalue()
        if len(data) > 1024 * 1024:
            return None
        from wujiang.tactical.prediction import scoring_random_state
        key = data, scoring_random_state(), tuple(bindings), tuple(battle.__dict__)
        if request is None:
            return key
        writer.dump(request)
        # One memo preserves argument-to-battle aliasing, not just equal values.
        return key, stream.getvalue()[len(data):]
    except Exception:
        # Closures and unsupported checkpoint objects keep the original path.
        return None
    finally:
        if snapshot_started is not None:
            memo.statistics["snapshot_seconds"] += monotonic() - snapshot_started


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


class _TacticalQueryPickler(pickle.Pickler):
    """Serialize full RNG/state, rejecting foreign custom state protocols."""
    def reducer_override(self, obj):
        cls = type(obj)
        module = (type.__getattribute__(obj, "__module__") if cls is type or cls is ABCMeta
                  else obj.__module__ if cls is FunctionType else type.__getattribute__(cls, "__module__"))
        if (type(module) is not str or module not in {"builtins", "collections", "random", "wujiang.tactical.prediction"}
                and not module.startswith("wujiang.tactical.")):
            raise ValueError("Untracked tactical query state protocol")
        return NotImplemented


def _tactical_query_result(value):
    if type(value) in (int, float, bool) and _cacheable_scalar(value):
        return True
    return (type(value) is tuple and len(value) <= 4096
            and all(type(cell) is tuple and len(cell) == 2
                    and type(cell[0]) is int and type(cell[1]) is int for cell in value))


def tactical_queries_active():
    from wujiang.tactical.prediction import forecasting_active
    if forecasting_active():
        return False  # Legacy query entries can represent a stochastic leaf.
    return _TACTICAL_QUERIES_ENABLED and _enabled.get() and not _in_worker and _memo.get() is not None


def cached_tactical_query(function):
    """Retain a complete, pure query batch, never a declaration/effect score."""
    code = function.__code__
    @wraps(function)
    def wrapped(battle, unit, *args, **kwargs):
        if not tactical_queries_active():
            return function(battle, unit, *args, **kwargs)
        from wujiang.tactical.engine import core
        from wujiang.tactical.rooms import ai
        memo = _memo.get()
        if (type(battle) is not core.Battle or battle.units.get(unit.unit_id) is not unit
                or core.Battle.chess_rng is not core._CANONICAL_CHESS_RNG_DESCRIPTOR
                or function.__module__ != ai.__name__ or ai.__dict__.get(function.__name__) is not wrapped
                or wrapped not in ai._TACTICAL_QUERY_ROOTS
                or function.__code__ is not code or wrapped.__code__ is not wrapper_code
                or wrapped.__wrapped__ is not function
                or any(getattr(owner, name) is not original or original.__code__ is not original_code
                       for owner, name, original, original_code in ai._TACTICAL_QUERY_BINDINGS)
                or any(name in battle.__dict__ for owner, name, _, _ in ai._TACTICAL_QUERY_BINDINGS if owner is core.Battle)
                or any(ai.__dict__.get(name) is not original or original.__code__ is not original_code
                       or getattr(original, "__wrapped__", None) is not inner
                       or inner is not None and inner.__code__ is not inner_code
                       for name, original, original_code, inner, inner_code in ai._TACTICAL_GLOBAL_BINDINGS)):
            return function(battle, unit, *args, **kwargs)
        bypass = id(battle), id(unit), function
        if bypass in memo.tactical_query_bypass:
            memo.statistics["tactical_query_bypasses"] += 1
            return function(battle, unit, *args, **kwargs)
        ids = _id_state()
        if ids is None:
            return function(battle, unit, *args, **kwargs)
        request = unit, args, kwargs
        joint = _snapshot(battle, request=request, strict=True)
        if joint is None or len(joint[1]) > 16384:
            memo.statistics["tactical_query_unsupported"] += 1
            if len(memo.tactical_query_bypass) < 4096:
                memo.tactical_query_bypass.add(bypass)
            return function(battle, unit, *args, **kwargs)
        key, state = memo.state_from_snapshot(battle, joint[0])
        item = function, id(battle), id(unit), joint[1]
        if item in state.tactical_queries:
            value, dependency, saved_battle, saved_unit = state.tactical_queries[item]
            if saved_battle is battle and saved_unit is unit:
                # Preserve enclosing RNG-dependency witnesses even when this
                # full-RNG-keyed query read and restored its local RNG.
                if dependency:
                    core._note_battle_rng_access()
                memo.statistics["tactical_query_hits"] += 1
                if type(value) is tuple:
                    memo.statistics["tactical_query_cells_reused"] += len(value)
                return value
        if len(state.tactical_queries) >= 256:
            return function(battle, unit, *args, **kwargs)
        memo.statistics["tactical_query_misses"] += 1
        with core.battle_rng_dependencies() as dependency:
            value = function(battle, unit, *args, **kwargs)
        pure = (_tactical_query_result(value) and _id_state() == ids
                and _snapshot(battle, request=request, strict=True) == joint
                and battle.units.get(unit.unit_id) is unit)
        if not pure:
            memo.statistics["tactical_query_impure"] += 1
            if len(memo.tactical_query_bypass) < 4096:
                memo.tactical_query_bypass.add(bypass)
            return value
        size = len(joint[1]) + 256 + (len(value) * 128 if type(value) is tuple else 0)
        if memo.states.get(key) is state and memo.bytes + size <= 16 * 1024 * 1024:
            state.tactical_queries[item] = value, dependency[0], battle, unit
            state.size += size
            memo.bytes += size
        return value
    wrapper_code = wrapped.__code__
    return wrapped


@contextmanager
def position_value_diagnostic_scope(sink):
    """Offline ordered scalar witnesses; never enabled by the live picker."""
    token = _position_diagnostic_sink.set(sink)
    try:
        yield
    finally:
        _position_diagnostic_sink.reset(token)


class _IndependentRowPickler(pickle.Pickler):
    """Mask one opaque RNG, rejecting aliases and foreign query protocols.

    All other state, graph sharing and ordering are still serialized exactly.
    Never install the masked graph on a battle or send it to a worker.
    """
    def __init__(self, stream, rng):
        super().__init__(stream, protocol=pickle.HIGHEST_PROTOCOL)
        self.rng = rng
        self.rng_references = 0

    def persistent_id(self, obj):
        if obj is self.rng:
            self.rng_references += 1
            if self.rng_references != 1:
                raise ValueError("An RNG alias can bypass Battle.chess_rng dependency tracking")
            return _INDEPENDENT_RNG_MARKER
        return None

    def reducer_override(self, obj):
        # User protocols may inspect the RNG through a raw dictionary or a
        # hidden closure. Only the shipped tactical graph is eligible.
        cls = type(obj)
        module = (type.__getattribute__(obj, "__module__") if cls is type or cls is ABCMeta
                  else obj.__module__ if cls is FunctionType else type.__getattribute__(cls, "__module__"))
        if (type(module) is not str or module not in {"builtins", "collections", "random"}
                and not module.startswith("wujiang.tactical.")):
            raise ValueError("Untracked custom state protocol")
        return NotImplemented


_INDEPENDENT_PID_BINDING = (_IndependentRowPickler.persistent_id, _IndependentRowPickler.persistent_id.__code__)


class _NativeIndependentRowPickler(_IndependentRowPickler):
    def __init__(self, stream, rng, mask):
        # Install before Pickler.__init__ resolves persistent_id. Keep the
        # exact same reducer_override and pickle memo/order/protocol.
        self.persistent_id = mask.persistent_id
        super().__init__(stream, rng)


def _independent_row_snapshot(battle, rng, memo):
    started = monotonic()
    mask = None
    try:
        memo.statistics["independent_row_snapshots"] += 1
        stream = BytesIO()
        canonical_pid, canonical_code = _INDEPENDENT_PID_BINDING
        factory = (load_rng_mask() if _NATIVE_ROW_KEYS_ENABLED
                   and _IndependentRowPickler.persistent_id is canonical_pid
                   and canonical_pid.__code__ is canonical_code else None)
        if factory is not None:
            mask = factory(rng, _INDEPENDENT_RNG_MARKER)
            writer = _NativeIndependentRowPickler(stream, rng, mask)
            memo.statistics["independent_row_native_snapshots"] += 1
        else:
            if _NATIVE_ROW_KEYS_ENABLED:
                memo.statistics["independent_row_native_fallbacks"] += 1
            writer = _IndependentRowPickler(stream, rng)
        writer.dump(battle)
        data = stream.getvalue()
        references = mask.references if mask is not None else writer.rng_references
        if references != 1 or len(data) > 1024 * 1024:
            return None
        bindings = []
        for unit in [*battle.all_units(), *battle.destroyed_units]:
            reference = getattr(unit, "_battle_ref", None)
            if reference is not None and type(reference) is not weakref.ReferenceType:
                return None
            if reference is not None and reference() is not battle:
                return None
            bindings.append((unit.unit_id, reference is not None))
        return (data, random.getstate(), tuple(bindings), tuple(battle.__dict__), id(battle), id(rng))
    except Exception:
        return None
    finally:
        memo.statistics["independent_row_snapshot_seconds"] += monotonic() - started
        if mask is not None:
            memo.statistics["independent_row_native_visits"] += mask.visits


def _independent_position_value(function, battle, actor, unit, memo):
    from wujiang.tactical.engine import core
    from wujiang.tactical.rooms import ai
    # Bound to the shipped query and dependency descriptor. Runtime query
    # replacements, foreign battles and workers keep the original execution.
    binding = ai._PARALLEL_BASELINE_BINDINGS[1]
    if (type(battle) is not core.Battle or function is not binding[2]
            or function.__code__ is not binding[3]
            or ai.reviewed_r17_position_value is not binding[0]
            or ai.reviewed_r17_position_value.__code__ is not binding[1]
            or ai.reviewed_r17_position_value.__wrapped__ is not function
            or core.Battle.chess_rng is not core._CANONICAL_CHESS_RNG_DESCRIPTOR
            or battle.units.get(actor.unit_id) is not actor or battle.units.get(unit.unit_id) is not unit):
        return function(battle, actor, unit)
    bypass = id(battle), actor.unit_id, unit.unit_id
    if _ADAPTIVE_ROWS_ENABLED and bypass in memo.independent_row_bypass:
        memo.statistics["independent_row_bypasses"] += 1
        return function(battle, actor, unit)
    rng = battle.__dict__.get("chess_rng")
    if (type(rng) is not random.Random or set(rng.__dict__) != {"gauss_next"}
            or rng.__dict__["gauss_next"] is not None and type(rng.__dict__["gauss_next"]) is not float):
        return function(battle, actor, unit)
    ids = _id_state()
    if ids is None:
        return function(battle, actor, unit)
    key = _independent_row_snapshot(battle, rng, memo)
    if key is None:
        memo.statistics["independent_row_unsupported"] += 1
        _record_independent_trial(memo, bypass, False)
        return function(battle, actor, unit)
    _, state = memo.state_from_snapshot(battle, key)
    item = actor.unit_id, unit.unit_id
    if item in state.independent_rows:
        value = state.independent_rows[item]
        if value is not None:
            memo.statistics["independent_row_hits"] += 1
            return value
        return function(battle, actor, unit)
    if len(state.independent_rows) >= 512:
        return function(battle, actor, unit)
    memo.statistics["independent_row_misses"] += 1
    before_rng = rng.getstate()
    with core.battle_rng_dependencies() as dependency:
        value = function(battle, actor, unit)
    pure = False
    if dependency[0]:
        memo.statistics["independent_row_dependent"] += 1
    elif (type(value) in (int, float, bool) and _cacheable_scalar(value) and _id_state() == ids and rng.getstate() == before_rng
          and _independent_row_snapshot(battle, rng, memo) == key
          and battle.units.get(actor.unit_id) is actor and battle.units.get(unit.unit_id) is unit):
        pure = True
    else:
        memo.statistics["independent_row_impure"] += 1
    _record_independent_trial(memo, bypass, pure)
    if memo.states.get(key) is state and memo.bytes + 192 <= 16 * 1024 * 1024:
        state.independent_rows[item] = value if pure else None
        state.size += 192
        memo.bytes += 192
    return value


def _record_independent_trial(memo, bypass, pure):
    if not _ADAPTIVE_ROWS_ENABLED:
        return
    if not pure and len(memo.independent_row_bypass) < 4096:
        # A negative hint only chooses original execution. It is safe to keep
        # it across changing graph/RNG states; it never reuses a result.
        memo.independent_row_bypass.add(bypass)
    tested, reusable = memo.independent_row_trials.get(bypass[0], (0, 0))
    memo.independent_row_trials[bypass[0]] = tested + 1, reusable + int(pure)


def _finish_independent_batch(memo, battle):
    if not _ADAPTIVE_ROWS_ENABLED:
        return
    identity = id(battle)
    tested, reusable = memo.independent_row_trials.get(identity, (0, 0))
    if tested >= 8 and reusable * 2 < tested and identity not in memo.independent_row_disabled:
        memo.independent_row_disabled.add(identity)
        memo.statistics["independent_row_fallbacks"] += 1


def cached_position_value(function):
    @wraps(function)
    def wrapped(battle, actor, unit):
        from wujiang.tactical.prediction import direct_scoring
        if direct_scoring():
            # The whole-baseline cache already validates its complete batch.
            # Individual rows in changed probe states rarely hit: serializing
            # the entire battle twice for each row costs more than the query.
            # Execute the original query in order, without changing any score.
            return function(battle, actor, unit)
        # The ordinary cold baseline keeps its single outer purity check.
        # Only a previously impure dense board opts into dependency-checked
        # independent rows; random-dependent rows still execute in order.
        if _baseline_batch_active.get():
            memo = _memo.get() if _enabled.get() and not _in_worker else None
            if (_RNG_INDEPENDENT_ROWS_ENABLED and memo is not None
                    and id(battle) in memo.random_baseline_battles
                    and (not _ADAPTIVE_ROWS_ENABLED or id(battle) not in memo.independent_row_disabled)):
                return _independent_position_value(function, battle, actor, unit, memo)
            return function(battle, actor, unit)
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
    @wraps(function)
    def observed(battle, actor, unit):
        value = wrapped(battle, actor, unit)
        sink = _position_diagnostic_sink.get()
        if sink is not None:
            if type(value) is float:
                scalar = ("float", struct.pack("!d", value).hex())
            elif type(value) in (int, bool):
                scalar = (type(value).__name__, value)
            else:
                raise TypeError("Position witness requires an exact scalar")
            sink((actor.unit_id, unit.unit_id, scalar))
        return value
    return observed


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


@contextmanager
def baseline_diagnostic_scope(sink):
    """Offline observation of existing guard values, without new game reads."""
    token = _baseline_diagnostic_sink.set(sink)
    try:
        yield
    finally:
        _baseline_diagnostic_sink.reset(token)


def _effect_baseline_rejection(battle, units, unit_ids, key, ids, frozen):
    """Keep the original purity guard's order and short-circuit evaluations."""
    if frozen is None:
        return "result", None, None
    after_ids = _id_state()
    if after_ids != ids:
        return "ids", after_ids, None
    after = _snapshot(battle)
    if after != key:
        if after is None:
            reason = "snapshot_unavailable"
        elif type(after) is tuple and type(key) is tuple and len(after) == len(key) == 4:
            # Only inspect already-captured builtin witness values. These
            # labels identify the first failing field, not every side effect.
            reason = next((name for name, before, current in zip(
                ("graph", "rng", "bindings", "fields"), key, after) if before != current), "snapshot")
        else:
            reason = "snapshot"
        return reason, after_ids, after
    if tuple(unit.unit_id for unit in units) != unit_ids:
        return "unit_order", after_ids, after
    if any(battle.units.get(unit.unit_id) is not unit for unit in units):
        return "unit_identity", after_ids, after
    return None, after_ids, after


def cached_effect_baseline(function):
    """Cache the original ordered baseline comprehension as one pure query."""
    @wraps(function)
    def wrapped(battle, actor, units, *, include_pending=False):
        memo = _memo.get() if _enabled.get() and not _in_worker else None
        if memo is None:
            return function(battle, actor, units, include_pending=include_pending)
        # A late board still snapshots its destroyed units and their components.
        # Keep whole-batch reuse when the retained graph is large; ordinary
        # small openings retain the original per-unit path.
        if len(units) < 12:
            if (not _SMALL_BASELINES_ENABLED or type(units) is not list or len(units) < 2
                    or len(battle.units) + len(battle.destroyed_units) < 12):
                return function(battle, actor, units, include_pending=include_pending)
        elif len(units) > 256:
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
        # A custom iterable can yield a different order on its next pass.
        # Only the original builtin list is an independent transferable batch.
        result = (_parallel_effect_baseline(function, battle, actor, unit_ids, include_pending, key, ids, memo)
                  if type(units) is list and type(include_pending) is bool else None)
        if result is None:
            started = monotonic()
            token = _baseline_batch_active.set(_BASELINE_BATCHES_ENABLED)
            try:
                result = function(battle, actor, units, include_pending=include_pending)
            finally:
                _baseline_batch_active.reset(token)
                _finish_independent_batch(memo, battle)
            elapsed = monotonic() - started
            with _baseline_cost_lock:
                cost_key = _baseline_cost_key(battle, include_pending)
                _baseline_costs[cost_key] = elapsed
                _baseline_costs.move_to_end(cost_key)
                if len(_baseline_costs) > 64:
                    _baseline_costs.popitem(last=False)
            if _BASELINE_BATCHES_ENABLED:
                memo.statistics["baseline_batch_runs"] += 1
        frozen = _freeze_effect_baseline(result, unit_ids, include_pending)
        rejection, after_ids, after = _effect_baseline_rejection(battle, units, unit_ids, key, ids, frozen)
        if rejection is not None:
            state.baselines[item] = None
            memo.statistics["baseline_impure"] += 1
            count_name = "baseline_impure_" + rejection
            memo.statistics[count_name] = memo.statistics.get(count_name, 0) + 1
            if rejection == "graph" and type(battle.__dict__.get("chess_rng")) is random.Random:
                memo.random_baseline_battles.add(id(battle))
            sink = _baseline_diagnostic_sink.get()
            if sink is not None:
                try:
                    # Never pass mutable battle/unit/result objects to the
                    # observer, or repeat a skipped snapshot/ID read.
                    sink({"reason": rejection, "actor_id": item[1], "unit_ids": unit_ids,
                          "include_pending": include_pending is True,
                          "ids_before": ids, "ids_after": after_ids,
                          "before": key, "after": after})
                except Exception:
                    memo.statistics["baseline_diagnostic_errors"] = memo.statistics.get("baseline_diagnostic_errors", 0) + 1
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
        if memo is None or _move_batch_active.get() or len(battle.units) < 12:
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


def cached_move_batch(function):
    """Validate one ordered, uninterrupted scoring reduction as a whole.

    The original loop executes serially, including role reads and subtraction.
    Misses avoid per-cell serialization; hits retain only a finite scalar.
    """
    @wraps(function)
    def wrapped(battle, actor, cells, profile, *, subtract=None):
        memo = _memo.get() if _enabled.get() and not _in_worker else None
        if (not _MOVE_BATCHES_ENABLED or memo is None or len(battle.units) < 12
                or id(battle) in memo.unsupported_battles or type(cells) not in (list, tuple)
                or not 4 <= len(cells) <= 512 or battle.units.get(actor.unit_id) is not actor):
            return function(battle, actor, cells, profile, subtract=subtract)
        from wujiang.tactical.engine.core import Position
        from wujiang.tactical.rooms.ai import DifficultyProfile
        if (type(profile) is not DifficultyProfile
                or any(type(cell) is not Position or type(cell.x) is not int or type(cell.y) is not int for cell in cells)
                or (subtract is not None and (type(subtract) not in (int, float) or not _cacheable_scalar(subtract)))):
            return function(battle, actor, cells, profile, subtract=subtract)
        ids = _id_state()
        if ids is None:
            return function(battle, actor, cells, profile, subtract=subtract)
        request = actor, cells, profile, subtract
        joint = _snapshot(battle, request=request)
        key, state = memo.state_from_snapshot(battle, joint[0] if joint is not None else None)
        if state is None or len(joint[1]) > 16384:
            return function(battle, actor, cells, profile, subtract=subtract)
        item = function, joint[1]
        if item in state.batches:
            result = state.batches[item]
            if result is not None:
                memo.statistics["move_batch_hits"] += 1
                memo.statistics["move_batch_queries_reused"] += len(cells)
                return result
            return function(battle, actor, cells, profile, subtract=subtract)
        if len(state.batches) >= 128:
            return function(battle, actor, cells, profile, subtract=subtract)
        memo.statistics["move_batch_misses"] += 1
        token = _move_batch_active.set(True)
        try:
            result = function(battle, actor, cells, profile, subtract=subtract)
        finally:
            _move_batch_active.reset(token)
        pure = (type(result) in (int, float) and _cacheable_scalar(result)
                and _id_state() == ids and _snapshot(battle, request=request) == joint)
        if not pure:
            memo.statistics["move_batch_impure"] += 1
        size = len(joint[1]) + 256
        if memo.states.get(key) is state and memo.bytes + size <= 16 * 1024 * 1024:
            state.batches[item] = result if pure else None
            state.size += size
            memo.bytes += size
        return result
    return wrapped


def _worker_init():
    global _in_worker
    _in_worker = True


def _source_id():
    from wujiang.platform.ai_build import build_info
    return build_info()["id"]


def _bounded_worker_job(kind, request, source_id):
    """A wedged job cannot keep consuming CPU after its room stops ticking."""
    if not _in_worker:
        raise RuntimeError("bounded jobs require an isolated worker")
    timer = Timer(_WORKER_BATCH_TIMEOUT_SECONDS, os._exit, args=(72,))
    timer.daemon = True
    timer.start()
    try:
        if _source_id() != source_id:
            raise RuntimeError("worker loaded a different AI source version")
        if kind == "baseline":
            return _effect_baseline_batch(request)
        if kind == "position":
            return _position_batch(request, True)
        if kind == "turn":
            from wujiang.tactical.rooms.ai_parallel import _turn_job
            return _turn_job(request)
        raise ValueError("unknown isolated query")
    finally:
        timer.cancel()


def _worker_count():
    try:
        requested = int(os.environ.get("WUJIANG_AI_WORKERS", "4"))
    except ValueError:
        requested = 4
    return max(0, min(requested, 4, (os.cpu_count() or 1) - 1))


def _executor():
    global _pool
    from wujiang.tactical.prediction import forecasting_active
    if forecasting_active():
        return None  # Worker tasks do not transfer probability-tree contexts.
    if _in_worker or _worker_disabled or _worker_count() < 2:
        return None
    with _pool_lock:
        if _pool is None:
            _pool = ProcessPoolExecutor(max_workers=_worker_count(),
                                        mp_context=multiprocessing.get_context("spawn"), initializer=_worker_init)
        return _pool


@atexit.register
def shutdown_workers(*, terminate=False):
    global _pool
    with _pool_lock:
        pool, _pool = _pool, None
    if pool is not None:
        # shutdown(wait=False) alone leaves already-running work consuming CPU.
        # Snapshot the handles before shutdown discards them (CPython < 3.14).
        if terminate:
            stop = getattr(pool, "terminate_workers", None)
            if stop is not None:
                stop()
                return
            processes = tuple((getattr(pool, "_processes", None) or {}).values())
            for process in processes:
                try:
                    process.terminate()
                except (OSError, ValueError):
                    pass
        pool.shutdown(wait=False, cancel_futures=True)


def _baseline_cost_key(battle, include_pending):
    return (len(battle.units) // 4, len(battle.destroyed_units) // 8, bool(include_pending),
            bool(getattr(battle, "fast_ai_simulation", False)))


def _load_pure_worker_state(snapshot, ids):
    from wujiang.tactical.engine import core
    from wujiang.tactical.heroes import base
    battle = _restore_worker_battle(snapshot)
    random.setstate(snapshot[1])
    core._id_counter, base._unit_counter = IdSequence(ids[0]), IdSequence(ids[1])
    local = _snapshot(battle)
    if (local is None or local[1:] != snapshot[1:]
            or _transfer_fingerprint(local[0]) != _transfer_fingerprint(snapshot[0])):
        return None
    return battle, local


def _effect_baseline_batch(request):
    """One transfer/load per contiguous ordered group; no live state returned."""
    from wujiang.tactical.rooms import ai
    snapshot, actor_id, unit_ids, include_pending, ids = request
    try:
        loaded = _load_pure_worker_state(snapshot, ids)
        if loaded is None:
            return None
        battle, local = loaded
        result = ai.reviewed_effect_baseline.__wrapped__(
            battle, battle.get_unit(actor_id), [battle.get_unit(value) for value in unit_ids],
            include_pending=include_pending)
        frozen = _freeze_effect_baseline(result, unit_ids, include_pending)
        if frozen is None or _id_state() != ids or _snapshot(battle) != local:
            return None
        return frozen
    except Exception:
        return None


def _baseline_worker_implementation_matches(function):
    from wujiang.tactical.rooms import ai
    bindings = ai._PARALLEL_BASELINE_BINDINGS
    if function is not bindings[0][2]:
        return False
    for current, (expected, code, original, original_code) in zip(
            (ai.reviewed_effect_baseline, ai.reviewed_r17_position_value), bindings):
        if (current is not expected or current.__code__ is not code
                or current.__wrapped__ is not original or original.__code__ is not original_code):
            return False
    return True


def _parallel_effect_baseline(function, battle, actor, unit_ids, include_pending, key, ids, memo):
    if (not _PARALLEL_BASELINES_ENABLED or not _BASELINE_BATCHES_ENABLED or _in_worker
            or memo.worker_transfer_rejected or len(unit_ids) < 8):
        return None
    if not _baseline_worker_implementation_matches(function):
        return None
    with _baseline_cost_lock:
        previous = _baseline_costs.get(_baseline_cost_key(battle, include_pending), 0.0)
    # The first cold group measures its real cost. Cheap groups stay local;
    # timing affects scheduling only, never candidate selection or coverage.
    if previous < _PARALLEL_BASELINE_MIN_SECONDS:
        return None
    try:
        executor = _executor()
        if executor is None:
            return None
        pending = getattr(executor, "_pending_work_items", None)
        if type(pending) is dict and pending:
            return None  # Other boards/rooms already occupy this shared pool.
        width = max(2, math.ceil(len(unit_ids) / _worker_count()))
        chunks = [unit_ids[index:index + width] for index in range(0, len(unit_ids), width)]
        source_id = _source_id()
        futures = [executor.submit(_bounded_worker_job, "baseline", (key, actor.unit_id, chunk, include_pending, ids), source_id)
                   for chunk in chunks]
        memo.statistics["parallel_baseline_jobs"] += len(futures)
        deadline = monotonic() + _WORKER_BATCH_TIMEOUT_SECONDS
        rows = [future.result(timeout=max(0.0, deadline - monotonic())) for future in futures]
        if any(row is None for row in rows) or _id_state() != ids or _snapshot(battle) != key:
            memo.statistics["parallel_baseline_rejected"] += 1
            memo.worker_transfer_rejected = True
            return None
        result = dict(item for row in rows for item in row)
        if _freeze_effect_baseline(result, unit_ids, include_pending) is None:
            memo.statistics["parallel_baseline_rejected"] += 1
            return None
        memo.statistics["parallel_baseline_hits"] += 1
        return result
    except Exception:
        global _worker_disabled
        _worker_disabled = True
        memo.statistics["worker_failures"] += 1
        shutdown_workers(terminate=True)
        return None


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
    from wujiang.tactical.rooms import ai
    snapshot, actor_id, unit_ids, ids = request
    results = []
    counts = dict(worker_roundtrip_rejected=0, worker_impure=0, worker_errors=0)
    try:
        _transfer_fingerprint(snapshot[0])
    except Exception:
        counts["worker_errors"] = len(unit_ids)
        results = [(unit_id, None) for unit_id in unit_ids]
        return (results, counts) if diagnostics else results
    loaded = None
    for index, unit_id in enumerate(unit_ids):
        try:
            if loaded is None:
                loaded = _load_pure_worker_state(snapshot, ids)
            if loaded is None:
                # Every item in the chunk has the same transfer snapshot.
                # Repeating the identical rejected load cannot make it valid.
                remaining = unit_ids[index:]
                counts["worker_roundtrip_rejected"] += len(remaining)
                results.extend((remaining_id, None) for remaining_id in remaining)
                break
            battle, local_snapshot = loaded
            value = ai.reviewed_r17_position_value.__wrapped__(battle, battle.get_unit(actor_id), battle.get_unit(unit_id))
            # Purity remains byte-exact within this process. Transfer text
            # normalization must never hide a change made by the query itself.
            pure = _id_state() == ids and _snapshot(battle) == local_snapshot
            if not pure or not _cacheable_scalar(value):
                counts["worker_impure"] += 1
                loaded = None
            results.append((unit_id, value if pure and _cacheable_scalar(value) else None))
        except Exception:
            counts["worker_errors"] += 1
            results.append((unit_id, None))
            loaded = None
    return (results, counts) if diagnostics else results


def prepare_position_values(battle, actor, units, *, for_baseline=False):
    """Parallelize independent pure queries; impure/failed values run normally."""
    memo = _memo.get() if _enabled.get() and not _in_worker else None
    if for_baseline and _BASELINE_BATCHES_ENABLED:
        return  # The whole baseline owns preparation and purity validation.
    # Chess already runs each match in a background thread. Sending its whole
    # battle to extra processes can hold the room lock while workers replay
    # every unit query; the ordinary exact memo computes the same values here.
    if (memo is None or memo.worker_transfer_rejected or len(units) < 12
            or getattr(battle, "fast_ai_simulation", False)):
        return
    from wujiang.tactical.rooms import ai
    if not _baseline_worker_implementation_matches(ai._PARALLEL_BASELINE_BINDINGS[0][2]):
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
        pending = getattr(executor, "_pending_work_items", None)
        if type(pending) is dict and pending:
            return
        workers = _worker_count()
        chunks = [missing[index::workers] for index in range(workers)]
        source_id = _source_id()
        futures = [executor.submit(_bounded_worker_job, "position", (key, actor.unit_id, chunk, ids), source_id) for chunk in chunks if chunk]
        memo.statistics["worker_jobs"] += len(futures)
        deadline = monotonic() + _WORKER_BATCH_TIMEOUT_SECONDS
        batches = [future.result(timeout=max(0.0, deadline - monotonic())) for future in futures]
        # Another thread may have advanced global RNG while workers ran. Such
        # results are never installed under a different live state.
        if _id_state() != ids or _snapshot(battle) != key:
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
        shutdown_workers(terminate=True)
