"""Full-AI scoring context and guarded random adapters, never live RNG.

Real battle code uses the adapters below too; outside a prediction session they
delegate to the original standard-library generators without changing draws.
The default direct policy uses local arithmetic expectations and original AI
probes. The old finite probability tree is an explicit research reference only.
"""
from __future__ import annotations

from collections import OrderedDict, deque
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from functools import wraps
import math
import os
import sys
import random as _random
import secrets as _secrets
import time
import weakref
from types import FunctionType, MethodType

from wujiang.platform.ai_scoring_policy import RULES_VERSION, rules_enabled, prediction_rules
_session = ContextVar("full_ai_prediction_session", default=None)
_frame = ContextVar("full_ai_probability_frame", default=None)
_observer = ContextVar("full_ai_prediction_observer", default=None)
_PREDICTION_RNG_STATE = _random.Random(0).getstate()


def forecasting_active():
    return rules_enabled() and _session.get() is not None


def direct_scoring():
    session = _session.get()
    return session is not None and session.get("method") == "direct" and rules_enabled()


def probability_frame_active():
    return _frame.get() is not None


def scoring_random_state():
    return _PREDICTION_RNG_STATE if forecasting_active() else _random.getstate()


@contextmanager
def prediction_observer(sink):
    token = _observer.set(sink)
    try:
        yield
    finally:
        _observer.reset(token)


def note(name, amount=1):
    session = _session.get()
    if session is not None:
        stats = session["statistics"]
        stats[name] = stats.get(name, 0) + amount


def record_candidates(actor, candidates, moves):
    session = _session.get()
    sink = _observer.get()
    if sink is not None:
        row = {"actor": actor.unit_id,
            "actions": [{"payload": c.payload.copy(), "score": c.score, "summary": c.summary} for c in candidates],
            "moves": [{"payload": c.payload.copy(), "score": c.score, "summary": c.summary} for c in moves]}
        if session is not None:
            session["candidates"].append(row)
        else:
            sink({"rules": "legacy", "statistics": {}, "candidates": [row]})


@contextmanager
def prediction_session(player_id):
    if _session.get() is not None:
        yield
        return
    method = os.environ.get("WUJIANG_AI_EXPECTATION_METHOD", "direct")
    if method not in {"direct", "enumerate"}:
        raise ValueError("Unknown AI expectation method: " + method)
    kernel = None
    if method == "enumerate" and rules_enabled() and os.environ.get("WUJIANG_AI_PREDICTION_NATIVE", "1") != "0":
        from wujiang.platform.ai_prediction_native import load_kernel
        kernel = load_kernel()
    if method == "enumerate" and kernel is None and os.environ.get("WUJIANG_AI_REQUIRE_PREDICTION_NATIVE") == "1":
        raise RuntimeError("Expectation preparation kernel required but unavailable/disabled")
    session = {"player_id": player_id, "method": method,
               "statistics": {"native_preparation_sessions": int(kernel is not None)}, "candidates": [],
               "preparation_kernel": kernel,
               "preparation_batch": method == "enumerate" and os.environ.get("WUJIANG_AI_PREPARATION_BATCH", "1") != "0",
               "structural_batch": method == "enumerate" and os.environ.get("WUJIANG_AI_STRUCTURAL_BATCH", "1") != "0",
               "cache": OrderedDict(), "cache_bytes": 0,
               "graph_cache": [], "graph_cache_bytes": 0}
    session["observation_unit_state"] = None
    session["observation_damage_rule"] = None
    session["static_runtime_contract"] = None
    if session["preparation_batch"]:
        from wujiang.tactical.engine.core import Unit, SummaryDamageRule
        from wujiang.tactical.prediction_factors import _original
        if _original(Unit.__getstate__):
            session["observation_unit_state"] = Unit.__getstate__, Unit.__getstate__.__code__
        if _original(SummaryDamageRule.calculate_damage):
            session["observation_damage_rule"] = SummaryDamageRule.calculate_damage, SummaryDamageRule.calculate_damage.__code__
        from wujiang.tactical.prediction_runtime import reviewed_contract
        session["static_runtime_contract"] = reviewed_contract()
    token = _session.set(session)
    started = time.perf_counter()
    try:
        yield
    finally:
        sink = _observer.get()
        stats = dict(session["statistics"])
        stats["session_seconds"] = time.perf_counter() - started
        _session.reset(token)
        if sink is not None:
            sink({"rules": "expected-visible-direct-v2" if method == "direct" else RULES_VERSION,
                  "method": method, "player_id": player_id,
                  "statistics": stats, "candidates": session["candidates"]})
        if (method == "direct" and stats.get("unhandled_direct_random_calls", 0)
                and sys.exc_info()[0] is None):
            raise RuntimeError("Direct AI scoring encountered an uncovered random mechanism; see prediction statistics")


class _Fork(BaseException):
    # Deliberately bypass gameplay's broad except Exception clauses. All normal
    # rollback/finally handlers still run before the evaluator resumes a branch.
    def __init__(self, event, probabilities):
        self.event, self.probabilities = event, probabilities


class _Frame:
    def __init__(self, tape):
        self.tape, self.cursor = tape, 0

    def choose(self, event, probabilities):
        cursor = self.cursor
        self.cursor += 1
        if cursor == len(self.tape):
            raise _Fork(event, probabilities)
        saved_event, index = self.tape[cursor]
        if saved_event != event or not 0 <= index < len(probabilities):
            raise RuntimeError("Probability replay changed its event sequence")
        return index


def _choose(event, probabilities):
    frame = _frame.get()
    if frame is None:
        note("unscoped_probability_errors")
        raise RuntimeError("Random effect escaped a full-AI expectation boundary: " + str(event))
    note("probability_reads")
    note("probability_events:" + str(event[0]))
    return frame.choose(event, probabilities)


class _Uniform:
    def __init__(self):
        self.lower, self.upper = 0.0, 1.0

    def __lt__(self, threshold):
        threshold = float(threshold)
        probability = max(0.0, min(1.0, (threshold - self.lower) / (self.upper - self.lower)))
        if probability == 0:
            return False
        if probability == 1:
            return True
        result = _choose(("uniform_lt", threshold, self.lower, self.upper), (1 - probability, probability)) == 1
        if result:
            self.upper = min(self.upper, threshold)
        else:
            self.lower = max(self.lower, threshold)
        return result

    def __le__(self, threshold):
        return self < threshold  # Continuous distribution: equality has mass 0.

    def __ge__(self, threshold):
        return not self < threshold

    def __gt__(self, threshold):
        return not self <= threshold

    def __float__(self):
        raise TypeError("A prediction uniform must be reduced through a probability comparison")


class _RandomAdapter:
    def __init__(self, original):
        self.original = original

    def __getattr__(self, name):
        value = getattr(self.original, name)
        if forecasting_active() and callable(value):
            note("unsupported_probability_errors")
            raise RuntimeError("Unsupported prediction distribution: " + name)
        return value

    def getstate(self):
        return _PREDICTION_RNG_STATE if forecasting_active() else self.original.getstate()

    def setstate(self, state):
        if forecasting_active():
            if state != _PREDICTION_RNG_STATE:
                raise RuntimeError("Prediction attempted to restore a live RNG state")
            return
        self.original.setstate(state)

    def random(self):
        if direct_scoring():
            note("unhandled_direct_random_calls")
            raise RuntimeError("Direct AI scoring needs an explicit expected contribution for this random event")
        return _Uniform() if forecasting_active() else self.original.random()

    def choice(self, values):
        if not forecasting_active():
            return self.original.choice(values)
        if not values:
            raise IndexError("Cannot choose from an empty sequence")
        if len(values) == 1:
            return values[0]
        if direct_scoring():
            note("unhandled_direct_random_calls")
            raise RuntimeError("Direct AI scoring needs an explicit expected contribution for this choice")
        probabilities = (1.0 / len(values),) * len(values)
        return values[_choose(("choice", len(values)), probabilities)]

    def randrange(self, start, stop=None, step=1):
        if not forecasting_active():
            return self.original.randrange(start, stop, step)
        values = range(start) if stop is None else range(start, stop, step)
        if not values:
            raise ValueError("empty range for prediction randrange")
        return self.choice(values)

    def randint(self, left, right):
        if not forecasting_active():
            return self.original.randint(left, right)
        return self.randrange(left, right + 1)

    def shuffle(self, values):
        if not forecasting_active():
            return self.original.shuffle(values)
        for index in range(len(values) - 1, 0, -1):
            other = self.randrange(index + 1)
            values[index], values[other] = values[other], values[index]


random = _RandomAdapter(_random)
secrets = _RandomAdapter(_secrets)


class ForecastRandom(_random.Random):
    """Serializable, fixed bookkeeping state; all draws are probability nodes."""
    def __init__(self):
        super().__init__(0)

    def random(self):
        return random.random()

    def choice(self, values):
        return random.choice(values)

    def randint(self, left, right):
        return random.randint(left, right)

    def randrange(self, start, stop=None, step=1):
        return random.randrange(start, stop, step)

    def shuffle(self, values):
        return random.shuffle(values)

    def getrandbits(self, count):
        raise RuntimeError("Prediction must use a finite probability distribution")


class _GraphSnapshot:
    """A graph of original references; validation compares identity, not equality.

    Reusing this immutable saved state still checks every captured namespace,
    slot and container. Changed topology or equal-valued replacement objects
    require a fresh capture. No gameplay hook is cached or skipped.
    """
    def __init__(self, battle, parent=None):
        from dataclasses import is_dataclass, _dataclass_getstate
        from wujiang.tactical.engine.core import Battle, BattleComponent, Unit, Position, SummaryDamageRule, _rollback_slot_names
        self.battle, self.parent = battle, parent
        self.seen, self.containers, self.namespaces, self.slots = set(), [], [], []
        self.object_types = (Battle, Unit, BattleComponent)
        self.unit_type, self.position_type = Unit, Position
        self.frozen_getstate = _dataclass_getstate
        self.is_dataclass, self.slot_names = is_dataclass, _rollback_slot_names
        session = _session.get()
        rule = session.get("observation_damage_rule") if session is not None else None
        if (rule is not None and SummaryDamageRule.calculate_damage is rule[0]
                and rule[0].__code__ is rule[1]):
            self.object_types += (SummaryDamageRule,)
        self.kernel = session.get("preparation_kernel") if session is not None else None
        self.unit_state_method = session.get("observation_unit_state") if session is not None else None
        self.bytes = 256
        self.complete_observation = True
        self.observed_rngs, self.observed_frozen_slots = [], []
        self.observation_protocol_names = ("__getattribute__", "__getstate__", "__reduce__", "__reduce_ex__")
        self.observation_protocols = {}

    @staticmethod
    def identities(value):
        if type(value) is dict:
            return tuple(map(id, value)), tuple(map(id, value.values()))
        if type(value) is set:
            return frozenset(map(id, value))
        return tuple(map(id, value))

    def visit(self, value):
        kind = type(value)
        identity = id(value)
        if value is None or kind in (bool, int, float, str, bytes) or identity in self.seen:
            return
        if self.parent is not None and identity in self.parent.seen:
            return
        self.seen.add(identity)
        if kind in (dict, list, set, deque):
            saved = tuple(value.items()) if kind is dict else tuple(value)
            self.containers.append((value, saved, self.identities(value)))
            self.bytes += 128 + len(saved) * (64 if kind is dict else 32)
            if kind is dict:
                for key, item in saved:
                    self.visit(key)
                    self.visit(item)
            else:
                for item in saved:
                    self.visit(item)
        elif kind in (tuple, frozenset):
            for item in value:
                self.visit(item)
        elif kind is MethodType:
            self.visit(value.__self__)
            self.visit(value.__func__)
        elif isinstance(value, self.object_types) or self.is_dataclass(value) and not isinstance(value, type):
            state_method = getattr(kind, "__getstate__", None)
            self.observation_protocols.setdefault(kind, tuple(
                (getattr(kind, name, None), getattr(getattr(kind, name, None), "__code__", None))
                for name in self.observation_protocol_names))
            unit_state = (self.unit_state_method is not None and isinstance(value, self.unit_type)
                          and state_method is self.unit_state_method[0]
                          and state_method.__code__ is self.unit_state_method[1])
            if (not kind.__module__.startswith("wujiang.tactical.")
                    or kind.__getattribute__ is not object.__getattribute__
                    or (state_method is not object.__getstate__ and
                        not unit_state and not (kind is self.position_type and state_method is self.frozen_getstate))
                    or kind.__reduce__ is not object.__reduce__ or kind.__reduce_ex__ is not object.__reduce_ex__):
                if self.complete_observation:
                    note("graph_key_protocol_fallback:" + kind.__module__ + "." + kind.__qualname__)
                self.complete_observation = False
            namespace = None
            if hasattr(value, "__dict__"):
                namespace = value.__dict__
                self.namespaces.append((value, namespace))
                self.visit(namespace)
            if self.is_dataclass(value) and not value.__dataclass_params__.frozen:
                # Ordinary dataclass fields already live in the captured
                # namespace. Only real slots/descriptors need a second path.
                names = tuple(name for name in self.slot_names(kind)
                              if namespace is None or name not in namespace
                              or hasattr(getattr(kind, name, None), "__set__"))
                saved = tuple((name, getattr(value, name)) for name in names)
                if saved:
                    self.slots.append((value, saved))
                    self.bytes += 64 + len(saved) * 32
                for _, item in saved:
                    self.visit(item)
            elif self.is_dataclass(value) and value.__dataclass_params__.frozen:
                if kind is self.position_type:
                    self.observed_frozen_slots.append((value, value.x, value.y))
                else:
                    if self.complete_observation:
                        note("graph_key_frozen_fallback:" + kind.__module__ + "." + kind.__qualname__)
                    self.complete_observation = False
        elif kind in (_random.Random, ForecastRandom) and kind.getstate is _random.Random.getstate:
            self.observed_rngs.append(value)
            self.observation_protocols.setdefault(kind, tuple(
                (getattr(kind, name, None), getattr(getattr(kind, name, None), "__code__", None))
                for name in (*self.observation_protocol_names, "getstate")))
        elif kind is weakref.ReferenceType and value() is self.battle:
            pass  # The Unit serializer omits this binding; the referent is held.
        elif kind not in (type, FunctionType):
            if self.complete_observation:
                note("graph_key_opaque_fallback:" + kind.__module__ + "." + kind.__qualname__)
            self.complete_observation = False

    def matches(self):
        session = _session.get()
        if self.kernel is not None and session is not None and session.get("preparation_batch", False):
            note("native_complete_graph_validations")
            return self.kernel.graph_matches(self.namespaces, self.slots, self.containers, deque)
        if any(obj.__dict__ is not namespace for obj, namespace in self.namespaces):
            return False
        if any(getattr(obj, name) is not item for obj, saved in self.slots for name, item in saved):
            return False
        if self.kernel is not None:
            note("native_graph_validations")
            note("native_graph_validation_container_candidates", len(self.containers))
            return self.kernel.containers_match(self.containers, deque)
        return all(len(target) == len(saved) and self.identities(target) == identities
                   for target, saved, identities in self.containers)

    def restore_namespaces(self):
        session = _session.get()
        if self.kernel is not None and session is not None and session.get("preparation_batch", False):
            self.kernel.restore_namespaces(self.namespaces)
            return
        for obj, namespace in self.namespaces:
            if obj.__dict__ is not namespace:
                object.__setattr__(obj, "__dict__", namespace)

    def restore_slots(self):
        session = _session.get()
        if self.kernel is not None and session is not None and session.get("preparation_batch", False):
            self.kernel.restore_slots(self.slots)
            return
        for obj, saved in self.slots:
            for name, item in saved:
                object.__setattr__(obj, name, item)

    def restore_containers(self):
        if self.kernel is not None:
            note("native_graph_restores")
            note("native_graph_restore_container_candidates", len(self.containers))
            self.kernel.restore_graph(self.containers, deque)
            return
        for target, saved, identities in reversed(self.containers):
            if len(target) == len(saved) and self.identities(target) == identities:
                continue
            kind = type(target)
            if kind is list:
                target[:] = saved
            else:
                target.clear()
                target.update(saved) if kind in (dict, set) else target.extend(saved)


def _remove_graph(session, removed):
    session["graph_cache"].remove(removed)
    session["graph_cache_bytes"] -= removed.bytes
    for key in list(session["cache"]):
        if len(key[0]) > 2 and key[0][2] is removed:
            _, size = session["cache"].pop(key)
            session["cache_bytes"] -= size


def reserve_graph_metadata(saved, key, size):
    """Charge retained certificates to the same bounded graph cache."""
    session = _session.get()
    if session is None or saved not in session["graph_cache"]:
        return False
    charges = getattr(saved, "metadata_bytes", None)
    if charges is None:
        saved.metadata_bytes = charges = {}
    previous = charges.get(key, 0)
    delta = size - previous
    if saved.bytes + delta > 8 * 1024 * 1024:
        return False
    while session["graph_cache_bytes"] + delta > 8 * 1024 * 1024:
        removed = next(graph for graph in reversed(session["graph_cache"]) if graph is not saved)
        _remove_graph(session, removed)
    saved.bytes += delta
    session["graph_cache_bytes"] += delta
    charges[key] = size
    return True


def _saved_graph(battle):
    session = _session.get()
    cache = session["graph_cache"] if session is not None else []
    for index, saved in enumerate(cache):
        if saved.battle is battle and saved.matches():
            cache.insert(0, cache.pop(index))
            note("graph_snapshot_reuses")
            return saved
    saved = _GraphSnapshot(battle)
    try:
        saved.visit(battle)
    except BaseException:
        saved.restore_namespaces()
        saved.restore_slots()
        saved.restore_containers()
        raise
    note("graph_objects_visited", len(saved.seen))
    note("graph_container_snapshots", len(saved.containers))
    if session is not None and saved.bytes <= 8 * 1024 * 1024:
        capacity = 16 if session.get("preparation_batch", False) else 4
        while cache and (len(cache) >= capacity or session["graph_cache_bytes"] + saved.bytes > 8 * 1024 * 1024):
            # Distribution entries must not keep evicted graph/memo objects
            # alive beyond the graph memory budget.
            _remove_graph(session, cache[-1])
        cache.insert(0, saved)
        session["graph_cache_bytes"] += saved.bytes
    return saved


@contextmanager
def prediction_rollback(battle):
    """Restore original graph identities; reuse only a completely matched graph."""
    note("graph_captures")
    from wujiang.tactical.prediction_structure import prepared_graph
    saved = prepared_graph.get()
    if saved is not None and saved.battle is battle:
        prepared_graph.set(None)
        note("shared_inner_checkpoints")
    else:
        saved = _saved_graph(battle)
    from wujiang.tactical.prediction_runtime import static_runtime_scan
    session = _session.get()
    skip_scan = session is not None and static_runtime_scan(saved, session)
    extra = None if skip_scan else _GraphSnapshot(battle, parent=saved)
    try:
        if skip_scan:
            note("static_runtime_scans_inlined")
        else:
            # Every custom callback still executes on EVERY entry in order.
            for unit in [*battle.all_units(), *battle.destroyed_units]:
                for component in list(unit.iter_components()):
                    extra.visit(component)
                    for child in component.runtime_children():
                        extra.visit(child)
        yield
    finally:
        if extra is not None:
            note("graph_objects_visited", len(extra.seen))
            note("graph_container_snapshots", len(extra.containers))
        saved.restore_namespaces()
        if extra is not None:
            extra.restore_namespaces()
        saved.restore_slots()
        if extra is not None:
            extra.restore_slots()
            extra.restore_containers()
        saved.restore_containers()


def equipment_random(battle):
    if forecasting_active():
        return random
    return getattr(battle, "chess_rng", None) or _random


def _memo_key(battle, request):
    # The existing joint Pickler preserves battle/argument aliases and complete
    # bindings. Never reuse a partial state signature or a random leaf sample.
    from wujiang.tactical.rooms.ai_acceleration import _snapshot
    session = _session.get()
    if session is not None and session.get("preparation_batch", False):
        from wujiang.tactical.prediction_keys import complete_graph_key
        for saved in session["graph_cache"]:
            if saved.battle is battle and saved.complete_observation and saved.matches():
                key = complete_graph_key(battle, request, saved)
                if key is not None:
                    return key
                break
    return _snapshot(battle, request=request, strict=True)


def _replay_inputs(callback):
    """Snapshot exact input containers, including a lambda's closed payload.

    Battle/Unit/component instances belong to battle_state_rollback. Keeping
    input container identities here also preserves aliases held by callers.
    """
    session = _session.get()
    kernel = session.get("preparation_kernel") if session is not None else None
    if kernel is not None:
        snapshots, visits = kernel.capture_inputs(callback, deque)
        note("native_input_captures")
        note("input_references_visited", visits)
        note("input_container_snapshots", len(snapshots))
        def restore_native():
            note("native_input_restores")
            kernel.restore_inputs(snapshots, deque, session.get("preparation_batch", False))
        return restore_native
    seen, snapshots, visits = set(), [], 0
    def visit(value):
        nonlocal visits
        visits += 1
        kind = type(value)
        # Terminal references have nothing to save or expand. In particular,
        # large preview tables do not need a visited-set ID for every scalar.
        if not (kind is FunctionType or kind is dict or kind is list
                or kind is deque or kind is set or kind is tuple):
            return
        if id(value) in seen:
            return
        seen.add(id(value))
        if kind is FunctionType:
            for cell in value.__closure__ or ():
                try:
                    visit(cell.cell_contents)
                except ValueError:
                    pass
        elif kind is dict:
            snapshots.append((value, list(value.items())))
            for item in value.values():
                visit(item)
        elif kind in (list, deque):
            snapshots.append((value, list(value)))
            for item in value:
                visit(item)
        elif kind is set:
            snapshots.append((value, value.copy()))
        elif kind is tuple:
            for item in value:
                visit(item)
    visit(callback)
    note("python_input_captures")
    note("input_references_visited", visits)
    note("input_container_snapshots", len(snapshots))
    def restore():
        for target, saved in reversed(snapshots):
            if type(target) is dict:
                target.clear()
                target.update(saved)
            elif type(target) is list:
                target[:] = saved
            elif type(target) is deque:
                target.clear()
                target.extend(saved)
            else:
                target.clear()
                target.update(saved)
    return restore


def outcomes(battle, callback, *, cache_request=None, vector=False, eager_cache=False, marginal_utilities=False,
             readonly_inputs=False):
    """Enumerate a finite result distribution, with branch-local battle/IDs.

    Nested calls intentionally start independent trees. Use this only at a
    complete query boundary, never to average an intermediate random damage
    before a caller's nonlinear kill/control decision.
    """
    if direct_scoring():
        note("direct_queries")
        value = callback()
        return tuple(((item, 1.0),) for item in value) if marginal_utilities else ((value, 1.0),)
    from wujiang.platform.id_sequence import isolated_ids
    session = _session.get()
    key = (_memo_key(battle, cache_request)
           if eager_cache and session is not None and cache_request is not None else None)
    cache_checked = eager_cache
    if key is not None and key in session["cache"]:
        session["cache"].move_to_end(key)
        note("distribution_cache_hits")
        return session["cache"][key][0]
    if eager_cache:
        note("distribution_cache_misses")
        if cache_request is not None and key is None:
            note("distribution_cache_unsupported")
    restore_inputs = (lambda: None) if readonly_inputs else _replay_inputs(callback)
    pending, result, marginals = [((), 1.0)], {}, None
    while pending:
        tape, weight = pending.pop()
        token = _frame.set(_Frame(tape))
        try:
            restore_inputs()
            from wujiang.tactical.prediction_structure import callback_checkpoint, prepared_graph
            checkpoint = callback_checkpoint(battle, callback, session) if session is not None else None
            prepared_token = prepared_graph.set(checkpoint)
            try:
                with isolated_ids(), (nullcontext() if checkpoint is not None else prediction_rollback(battle)):
                    value = callback()
            finally:
                prepared_graph.reset(prepared_token)
            valid = (type(value) is tuple and all(isinstance(item, (int, float, bool)) and math.isfinite(item)
                                                  for item in value)) if vector else (
                isinstance(value, (int, float, bool)) and math.isfinite(value))
            if not valid:
                raise ValueError("Expectation requires a finite scalar result")
            if marginal_utilities:
                if marginals is None:
                    marginals = [{} for _ in value]
                if len(value) != len(marginals):
                    raise ValueError("Prediction vector changed its shape")
                for item, marginal in zip(value, marginals):
                    marginal[item] = marginal.get(item, 0.0) + weight
            else:
                result[value] = result.get(value, 0.0) + weight
            note("probability_leaves")
        except _Fork as fork:
            # Deterministic queries avoid both full-state serializations. At
            # the first real random event, unwind the probe and restore closed
            # payloads before looking up the COMPLETE distribution.
            restore_inputs()
            if not cache_checked:
                cache_checked = True
                key = _memo_key(battle, cache_request) if session is not None and cache_request is not None else None
                if key is not None and key in session["cache"]:
                    session["cache"].move_to_end(key)
                    note("distribution_cache_hits")
                    return session["cache"][key][0]
                note("distribution_cache_misses")
                if cache_request is not None and key is None:
                    note("distribution_cache_unsupported")
            note("probability_forks")
            note("maximum_probability_depth", 0)
            if session is not None:
                stats = session["statistics"]
                stats["maximum_probability_depth"] = max(stats["maximum_probability_depth"], len(tape) + 1)
            for index in range(len(fork.probabilities) - 1, -1, -1):
                probability = fork.probabilities[index]
                if probability > 0:
                    pending.append(((*tape, (fork.event, index)), weight * probability))
        finally:
            _frame.reset(token)
            restore_inputs()
    distribution = tuple(tuple(group.items()) for group in marginals) if marginal_utilities else tuple(result.items())
    if not cache_checked:
        note("deterministic_queries_without_cache_keys")
    groups = distribution if marginal_utilities else (distribution,)
    if not all(math.isclose(math.fsum(p for _, p in group), 1.0, rel_tol=1e-10, abs_tol=1e-12) for group in groups):
        raise RuntimeError("Probability tree lost mass")
    after_key = _memo_key(battle, cache_request) if key is not None else None
    if (key is not None and after_key != key and after_key is not None
            and isinstance(after_key[0][2], _GraphSnapshot) and not isinstance(key[0][2], _GraphSnapshot)):
        # The first eager query may precede the first matched rollback graph.
        # Upgrade its representation only after the ORIGINAL complete joint
        # serializer independently confirms purity, including argument aliases.
        from wujiang.tactical.rooms.ai_acceleration import _snapshot
        if _snapshot(battle, request=cache_request, strict=True) == key:
            key = after_key
            note("complete_state_key_upgrades")
    if key is not None and after_key == key:
        results_size = (sum(64 + 32 * len(group) for group in distribution) if marginal_utilities else sum(
            32 + (32 * len(value) if type(value) is tuple else 0) for value, _ in distribution))
        size = len(key[0][0]) + len(key[1]) + 256 + results_size
        if size <= 16 * 1024 * 1024:
            while session["cache"] and (len(session["cache"]) >= 256 or session["cache_bytes"] + size > 16 * 1024 * 1024):
                _, (_, removed) = session["cache"].popitem(last=False)
                session["cache_bytes"] -= removed
            session["cache"][key] = distribution, size
            session["cache_bytes"] += size
            note("distribution_cache_stores")
    elif key is not None:
        note("distribution_cache_rejected")
    return distribution


def expected_vector(battle, callback, *, cache_request):
    """One action replay yields all target utilities, with their joint effects.

    Expectations of individual utilities share this distribution; their damage
    or death outcomes are never averaged before computing each final utility.
    """
    marginals = outcomes(battle, callback, cache_request=("final_utility_marginals", cache_request),
                         vector=True, eager_cache=True, marginal_utilities=True)
    note("joint_utility_queries")
    # Merge each target's FINAL utility in leaf order, just like the original
    # scalar evaluator. Do not retain an unnecessary Cartesian output table or
    # change float accumulation by first merging complete utility vectors.
    return tuple(math.fsum(value * probability for value, probability in marginal) for marginal in marginals)


class UnstableObservation(Exception):
    """A utility getter cannot safely share a replay with other observers."""


def joint_utilities_enabled():
    if direct_scoring():
        return False
    session = _session.get()
    return forecasting_active() and not session.get("joint_utility_unsafe", False)


def disable_joint_utilities():
    _session.get()["joint_utility_unsafe"] = True
    note("joint_utility_impure_observer_fallbacks")


@contextmanager
def stable_observations(battle):
    # Additional observers must not change the action's starting state or ID
    # origin. Unsupported graphs and impure getters keep the original queries.
    from wujiang.tactical.rooms.ai_acceleration import _id_state
    session = _session.get()
    if session.get("preparation_batch", False):
        saved = _saved_graph(battle)
        from wujiang.tactical.prediction_keys import graph_observation_stamp
        stamp = graph_observation_stamp(saved) if saved.complete_observation else None
        if stamp is not None:
            ids = _id_state()
            reads = session["statistics"].get("probability_reads", 0)
            if ids is None:
                raise UnstableObservation("Unsupported utility observation ID origin")
            try:
                yield
            except _Fork as exc:
                raise UnstableObservation("Stochastic utility observer") from exc
            except Exception as exc:
                raise UnstableObservation("Utility observation failed") from exc
            if (_id_state() != ids or session["statistics"].get("probability_reads", 0) != reads
                    or not saved.matches()
                    or graph_observation_stamp(saved) != stamp):
                raise UnstableObservation("Utility observation changed battle state or IDs")
            note("graph_utility_observations")
            return
        note("graph_utility_observation_fallbacks")
    before = _memo_key(battle, ("utility_observation",))
    ids = _id_state()
    reads = _session.get()["statistics"].get("probability_reads", 0)
    if before is None or ids is None:
        raise UnstableObservation("Unsupported utility observation graph")
    try:
        yield
    except _Fork as exc:
        raise UnstableObservation("Stochastic utility observer") from exc
    except Exception as exc:
        raise UnstableObservation("Utility observation failed") from exc
    if (_id_state() != ids or _session.get()["statistics"].get("probability_reads", 0) != reads
            or _memo_key(battle, ("utility_observation",)) != before):
        raise UnstableObservation("Utility observation changed battle state or IDs")


def _copy_arguments(value, fallback=None):
    # Reuse the already validated container copier, preserving its original
    # no-memo semantics and Python fallback for custom containers/objects.
    # This helper is reached only after a new-policy wrapper's early guard.
    from wujiang.tactical.engine import core
    if fallback is None:
        fallback = core._rollback_copy_value
    if core._ROLLBACK_NATIVE_ENABLED and core._NATIVE_ROLLBACK_COPY is not None:
        note("native_argument_copies")
        return core._NATIVE_ROLLBACK_COPY(value, fallback, core._ROLLBACK_SCALAR_TYPES)
    note("python_argument_copies")
    return fallback(value)


def expected_numeric(function=None, *, independent=False, boolean=False, eager_cache=False):
    def decorate(function):
        @wraps(function)
        def wrapped(battle, *args, **kwargs):
            if not forecasting_active() or (_frame.get() is not None and not independent):
                return function(battle, *args, **kwargs)
            if direct_scoring():
                note("direct_numeric_queries")
                return function(battle, *args, **kwargs)
            from wujiang.tactical.engine.core import _rollback_copy_value
            note("numeric_queries:" + function.__name__)
            session = _session.get()
            if session is not None and session.get("preparation_batch", False):
                from wujiang.tactical.prediction_inline import can_inline
                if can_inline(function, battle, args, kwargs, session):
                    result = function(battle, *args, **kwargs)
                    if not isinstance(result, (int, float, bool)) or not math.isfinite(result):
                        raise ValueError("Expectation requires a finite scalar result")
                    note("certified_inline_queries:" + function.__name__)
                    return result > 0 if boolean else float(result)
            def evaluate():
                from wujiang.tactical.prediction_structure import copy_scoring_arguments
                return function(battle, *copy_scoring_arguments(function, args, _rollback_copy_value), **_copy_arguments(kwargs, _rollback_copy_value))
            distribution = outcomes(battle, evaluate, cache_request=(
                "scalar", function.__module__, function.__qualname__, id(function.__code__), args, kwargs,
                tuple(cell.cell_contents for cell in function.__closure__ or ())), eager_cache=eager_cache)
            value = math.fsum(float(result) * probability for result, probability in distribution)
            return value > 0 if boolean else value
        return wrapped
    return decorate(function) if function is not None else decorate


def expected_local(battle):
    """A scored alternative captured by a local closure, before max/choice."""
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            if not forecasting_active():
                return function(*args, **kwargs)
            if direct_scoring():
                return function(*args, **kwargs)
            from wujiang.tactical.engine.core import _rollback_copy_value
            note("local_queries:" + function.__name__)
            def evaluate():
                return function(*_copy_arguments(args, _rollback_copy_value), **_copy_arguments(kwargs, _rollback_copy_value))
            return math.fsum(value * probability for value, probability in outcomes(battle, evaluate))
        return wrapped
    return decorate


def attack_damage_distribution(battle, actor, target, payload, *, attack_power, area_cell_hits=1):
    from wujiang.tactical.rooms.ai import probe_attack_damage_impact
    request = ("attack_damage_distribution", id(probe_attack_damage_impact.__code__), actor.unit_id, target.unit_id, payload,
               attack_power, area_cell_hits)
    return outcomes(battle, lambda: probe_attack_damage_impact(battle, actor, target, payload,
                    attack_power=attack_power, area_cell_hits=area_cell_hits).damage,
                    cache_request=request, eager_cache=True)


def expected_attack_damage(battle, actor, target, payload, *, attack_power):
    return math.fsum(value * probability for value, probability in
                    attack_damage_distribution(battle, actor, target, payload, attack_power=attack_power))


def sum_distributions(left, right):
    combined = {}
    for first, first_probability in left:
        for second, second_probability in right:
            value = first + second
            combined[value] = combined.get(value, 0.0) + first_probability * second_probability
    note("convolved_distributions")
    return tuple(combined.items())
