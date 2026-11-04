"""Speculative board calculations, with the original serial commit order.

Everything here is transient and outside room/checkpoint state. A worker can
never replace a battle. Only a matching full-AI turn result and its exact ID
consumption may be accepted; other phases continue through the original room.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import os
import pickle
from io import BytesIO
import random
import sys
from types import CodeType, FunctionType
from abc import ABCMeta
from threading import Lock
from time import monotonic
import weakref

from wujiang.tactical.rooms import ai_acceleration as acceleration

# Retained for explicit offline comparisons; the full run found no accepted
# jobs and 15.40% extra time. Ordinary play uses the original serial boards.
_BOARDS_ENABLED = os.environ.get("WUJIANG_AI_PARALLEL_BOARDS", "0") != "0"
_current_job = ContextVar("ai_speculative_board_job", default=None)
_last_statistics = ContextVar("ai_speculative_board_statistics", default=None)
_coordinators = weakref.WeakKeyDictionary()
_guard = Lock()
_MAX_JOB_AGE = 2.0


@dataclass
class _Coordinator:
    entries: dict = field(default_factory=dict)
    running: list = field(default_factory=list)
    deltas: dict = field(default_factory=dict)
    wave_hits: int = 0
    waves: int = 0
    cooldown: int = 0
    statistics: dict = field(default_factory=lambda: dict(
        submitted=0, hits=0, not_ready=0, stale=0, rejected=0, timeouts=0, waves=0))


def last_statistics():
    return dict(_last_statistics.get() or {})


def _config(room):
    return (room.default_ai_difficulty, pickle.dumps(room.seats, protocol=pickle.HIGHEST_PROTOCOL))


def _freeze_anchor(value):
    from wujiang.tactical.engine.core import Position
    if type(value) is not dict:
        return None
    for key, row in value.items():
        if (type(key) is not int or type(row) is not tuple or len(row) != 2
                or (row[0] is not None and (type(row[0]) is not Position
                    or type(row[0].x) is not int or type(row[0].y) is not int))
                or type(row[1]) is not tuple or len(row[1]) != 2
                or any(type(item) is not int for item in row[1])):
            return None
    return tuple(value.items())


def _payload_supported(value, depth=0):
    if depth > 12:
        return False
    if type(value) in (str, int, bool, type(None)):
        return True
    if type(value) is float:
        return acceleration._cacheable_scalar(value)
    if type(value) in (list, tuple):
        return len(value) <= 512 and all(_payload_supported(item, depth + 1) for item in value)
    if type(value) is dict:
        return len(value) <= 64 and all(type(key) is str and _payload_supported(item, depth + 1)
                                      for key, item in value.items())
    return False


def _payload_is_unshared(battle, payload):
    stream = BytesIO()
    writer = pickle.Pickler(stream, protocol=pickle.HIGHEST_PROTOCOL)
    writer.dump(battle)
    memo = writer.memo.copy()
    todo, seen = [payload], set()
    while todo:
        value = todo.pop()
        if type(value) not in (dict, list, tuple) or id(value) in seen:
            continue
        seen.add(id(value))
        if id(value) in memo:
            return False
        todo.extend(value.values() if type(value) is dict else value)
    return True


def _literal_locators():
    """Locate actual loaded constants, preserving payload string sharing.

    A raw cross-process payload would turn actor IDs and literal action keys
    into unrelated strings, changing subsequent checkpoint pickle graphs.
    References are to trusted loaded code, never to worker-created code.
    """
    result, seen = {}, set()
    def visit(value, path):
        if type(value) is str:
            result.setdefault(id(value), (value, path))
            return
        if id(value) in seen:
            return
        seen.add(id(value))
        if type(value) is FunctionType:
            visit(value.__code__, (*path, ("code", None)))
            wrapped = value.__dict__.get("__wrapped__")
            if type(wrapped) is FunctionType:
                visit(wrapped, (*path, ("wrapped", None)))
        elif type(value) is CodeType:
            for index, item in enumerate(value.co_consts):
                visit(item, (*path, ("const", index)))
        elif type(value) is tuple:
            for index, item in enumerate(value):
                visit(item, (*path, ("item", index)))
        elif type(value) in (staticmethod, classmethod):
            visit(value.__func__, (*path, ("func", None)))
        elif type(value) in (type, ABCMeta) and getattr(value, "__module__", "").startswith("wujiang."):
            for key, item in sorted(vars(value).items()):
                visit(item, (*path, ("dict", key)))
    for name, module in sorted(tuple(sys.modules.items())):
        if name.startswith(("wujiang.tactical.", "wujiang.platform.")) and module is not None:
            for key, value in sorted(vars(module).items()):
                visit(value, (("module", name), ("dict", key)))
    return result


def _state_texts(battle):
    stream = BytesIO()
    writer = pickle.Pickler(stream, protocol=pickle.HIGHEST_PROTOCOL)
    writer.dump(battle)
    return {identity: obj for identity, (_index, obj) in writer.memo.copy().items() if type(obj) is str}


def _pack_payload(battle, payload):
    literals, texts = _literal_locators(), _state_texts(battle)
    class Writer(pickle.Pickler):
        def persistent_id(self, value):
            if type(value) is not str:
                return None
            if id(value) in literals:
                reference = ("literal", literals[id(value)][1], value)
            elif id(value) in texts:
                reference = ("state", value)
            else:
                return None
            # PID bytes prevent recursive persistent handling of the locator.
            return pickle.dumps(reference, protocol=pickle.HIGHEST_PROTOCOL)
    stream = BytesIO()
    Writer(stream, protocol=pickle.HIGHEST_PROTOCOL).dump(payload)
    return stream.getvalue()


def _unpack_payload(battle, data):
    if type(data) is not bytes or len(data) > 128 * 1024:
        raise ValueError("payload transport budget exceeded")
    texts = {}
    for value in _state_texts(battle).values():
        texts.setdefault(value, {})[id(value)] = value
    class Reader(pickle.Unpickler):
        def persistent_load(self, pid):
            reference = pickle.loads(pid)
            if reference[0] == "state":
                matches = texts.get(reference[1], {})
                if len(matches) != 1:
                    raise ValueError("ambiguous battle string reference")
                return next(iter(matches.values()))
            value = None
            for operation, key in reference[1]:
                if operation == "module":
                    value = sys.modules[key]
                elif operation == "dict":
                    value = vars(value)[key]
                elif operation == "code":
                    value = value.__code__
                elif operation == "wrapped":
                    value = vars(value)["__wrapped__"]
                elif operation == "const":
                    value = value.co_consts[key]
                elif operation == "item":
                    value = value[key]
                elif operation == "func":
                    value = value.__func__
                else:
                    raise ValueError("unknown payload reference")
            if type(value) is not str or value != reference[2]:
                raise ValueError("loaded payload literal mismatch")
            return value
    return Reader(BytesIO(data)).load()


def _turn_job(request):
    """Evaluate pre-turn checks and one complete picker, never execute an action."""
    from wujiang.tactical.rooms import ai, ai_router
    from wujiang.tactical.rooms.multiplayer import GameRoom
    snapshot, config, ids = request
    try:
        loaded = acceleration._load_pure_worker_state(snapshot, ids)
        if loaded is None:
            return {"rejected": "transfer"}
        battle, _ = loaded
        room = GameRoom.__new__(GameRoom)
        room.battle = battle
        room.default_ai_difficulty, seats = config
        room.seats = pickle.loads(seats)
        if (battle.winner is not None or battle.pending_damage_choice is not None
                or battle.pending_chain is not None or battle.current_respawn_prompt() is not None
                or battle.destroyed_active_turn_is_idle()):
            return {"rejected": "pending_phase"}
        # The parent performs these original checks too. Capture the picker
        # input AFTER them, including any IDs they consumed, for exact matching.
        with ai_router.detailed_picker_scope():
            if room._choose_ai_dragon_return_payload() is not None or room._choose_ai_instant_payload() is not None:
                return {"rejected": "preempted"}
        if battle.is_army_turn():
            return {"rejected": "army_phase"}
        seat = room._current_prompt_seat()
        if seat is None or not seat.is_ai:
            return {"rejected": "seat"}
        current = battle.current_turn_unit()
        actors = battle.current_turn_bundle_units(include_banished=False)
        if current is None and not actors:
            return {"rejected": "no_actor"}
        difficulty = seat.ai_difficulty_override or room.default_ai_difficulty
        before = acceleration._snapshot(battle)
        before_ids = acceleration._id_state()
        present = "_army_anchor_cache" in battle.__dict__
        old_anchor = battle.__dict__.get("_army_anchor_cache")
        payload, actor = ai.choose_turn_bundle_action(battle, actors, difficulty)
        end_ids, end_rng = acceleration._id_state(), random.getstate()
        anchor_present = "_army_anchor_cache" in battle.__dict__
        after_anchor = _freeze_anchor(battle._army_anchor_cache) if anchor_present else None
        payload_data = _pack_payload(battle, payload) if _payload_supported(payload) else None
        committed = acceleration._snapshot(battle)
        if ((anchor_present and after_anchor is None) or type(payload) is not dict or not _payload_supported(payload)
                or before is None or committed is None or not _payload_is_unshared(battle, payload)):
            return {"rejected": "payload"}
        # This documented cache is the sole allowed battle-side effect of a
        # returned plan. All other state, bindings and field order must agree.
        if present:
            battle._army_anchor_cache = old_anchor
        else:
            battle.__dict__.pop("_army_anchor_cache", None)
        after = acceleration._snapshot(battle)
        if (after != before or end_rng != before[1] or end_ids is None
                or acceleration._id_state() != end_ids or random.getstate() != end_rng):
            return {"rejected": "state_effects"}
        return dict(before=before, committed=committed, ids=before_ids, end_ids=end_ids, difficulty=difficulty,
                    units=tuple(unit.unit_id for unit in actors), payload_data=payload_data,
                    actor=actor.unit_id if actor is not None else None,
                    anchor=after_anchor, statistics=acceleration.last_statistics())
    except Exception as exc:
        return {"rejected": "exception", "error": f"{type(exc).__name__}: {exc}"}


def take_turn_result(battle, units, difficulty):
    """Called exactly where the parent would call the full picker."""
    job = _current_job.get()
    if job is None:
        return None
    coordinator, future, config, room, _submitted = job
    if not future.done():
        coordinator.statistics["not_ready"] += 1
        future.cancel()
        return None
    try:
        result = future.result(timeout=0)
    except Exception:
        coordinator.statistics["rejected"] += 1
        acceleration._worker_disabled = True
        acceleration.shutdown_workers(terminate=True)
        return None
    try:
        if result is None or "rejected" in result:
            coordinator.statistics["rejected"] += 1
            return None
        key, ids = acceleration._snapshot(battle), acceleration._id_state()
        before = result["before"]
        if (key is None or key[1:] != before[1:] or ids != result["ids"]
                or config != _config(room) or difficulty != result["difficulty"]
                or tuple(unit.unit_id for unit in units) != result["units"]
                or any(battle.units.get(unit.unit_id) is not unit for unit in units)
                or acceleration._transfer_fingerprint(key[0]) != acceleration._transfer_fingerprint(before[0])):
            coordinator.statistics["stale"] += 1
            return None
        actor_id = result["actor"]
        actor = battle.units.get(actor_id) if actor_id is not None else None
        if actor_id is not None and actor is None:
            coordinator.statistics["rejected"] += 1
            return None
        payload, statistics = _unpack_payload(battle, result["payload_data"]), dict(result["statistics"])
        anchor = result["anchor"]
        proposed_anchor = None if anchor is None else dict(anchor)
        if (type(payload) is not dict or not _payload_supported(payload)
                or (proposed_anchor is not None and _freeze_anchor(proposed_anchor) is None)):
            coordinator.statistics["rejected"] += 1
            return None
        # Validate the proposed cache's complete alias graph before reserving
        # IDs. In particular a transferred Position must not silently sever an
        # alias with a live unit or another battle field.
        present = "_army_anchor_cache" in battle.__dict__
        old_anchor = battle.__dict__.get("_army_anchor_cache")
        try:
            if proposed_anchor is None:
                battle.__dict__.pop("_army_anchor_cache", None)
            else:
                battle._army_anchor_cache = proposed_anchor
            proposed = acceleration._snapshot(battle)
        finally:
            if present:
                battle._army_anchor_cache = old_anchor
            else:
                battle.__dict__.pop("_army_anchor_cache", None)
        expected = result["committed"]
        if (proposed is None or proposed[1:] != expected[1:]
                or acceleration._transfer_fingerprint(proposed[0]) != acceleration._transfer_fingerprint(expected[0])):
            coordinator.statistics["rejected"] += 1
            return None
        from wujiang.platform.id_sequence import advance_pair_if_unchanged
        from wujiang.tactical.engine import core
        from wujiang.tactical.heroes import base
        end_ids = result["end_ids"]
    except Exception:
        coordinator.statistics["rejected"] += 1
        return None
    if not advance_pair_if_unchanged(core._id_counter, base._unit_counter, ids, end_ids, key[1]):
        coordinator.statistics["stale"] += 1
        return None
    # No fall back after reservation: that would consume the same IDs twice.
    if proposed_anchor is None:
        battle.__dict__.pop("_army_anchor_cache", None)
    else:
        battle._army_anchor_cache = proposed_anchor
    acceleration._last_statistics.set(statistics)
    coordinator.statistics["hits"] += 1
    coordinator.wave_hits += 1
    return payload, actor


def _submit_wave(chess, match, coordinator):
    from wujiang.tactical.rooms import ai
    if not acceleration._baseline_worker_implementation_matches(ai._PARALLEL_BASELINE_BINDINGS[0][2]):
        return
    pool = acceleration._executor()
    if pool is None:
        return
    work = getattr(pool, "_pending_work_items", None)
    if type(work) is dict and work:
        return
    pending = [item for item in chess.matches if item.result is None]
    if len(pending) < 2 or all(item is not match for item in pending):
        return
    # The parent computes the current board. Other processes compute the next
    # boards at the same time; no waiting is added at the commit boundary.
    index = next(index for index, item in enumerate(pending) if item is match)
    ordered = pending[index:] + pending[:index]
    predicted = acceleration._id_state()
    if predicted is None:
        return
    for item in ordered[:4]:
        runner = item.runner
        if runner is None or runner.battle is None:
            break  # Construction itself consumes shared IDs.
        battle = runner.battle
        if item is not match and battle.on_replay_checkpoint is None:
            snapshot = acceleration._snapshot(battle)
            if snapshot is not None:
                config = _config(runner)
                future = pool.submit(acceleration._bounded_worker_job, "turn", (snapshot, config, predicted), acceleration._source_id())
                submitted = monotonic()
                coordinator.entries[id(runner)] = (future, config, weakref.ref(runner), submitted)
                coordinator.running.append((future, submitted))
                coordinator.statistics["submitted"] += 1
        delta = coordinator.deltas.get(id(runner), (0, 0))
        predicted = tuple(before + amount for before, amount in zip(predicted, delta))
    coordinator.waves += 1
    coordinator.statistics["waves"] += 1
    coordinator.wave_hits = 0


@contextmanager
def board_step(chess, match):
    from wujiang.tactical.rooms import ai, ai_router
    if (not _BOARDS_ENABLED or acceleration._in_worker or not acceleration._enabled.get()
            or acceleration._worker_disabled or acceleration._worker_count() < 2
            or ai_router._picker() is not ai or match.runner is None):
        _last_statistics.set({})
        yield
        return
    with _guard:
        coordinator = _coordinators.setdefault(chess, _Coordinator())
    before_statistics = dict(coordinator.statistics)
    before_ids = acceleration._id_state()
    token = None
    try:
        now = monotonic()
        active = {id(item.runner) for item in chess.matches if item.result is None and item.runner is not None}
        for identity in tuple(coordinator.entries):
            if identity not in active:
                coordinator.entries.pop(identity)[0].cancel()
        coordinator.running[:] = [(future, at) for future, at in coordinator.running if not future.done()]
        if any(now - at > _MAX_JOB_AGE for _future, at in coordinator.running):
            coordinator.statistics["timeouts"] += 1
            acceleration._worker_disabled = True
            acceleration.shutdown_workers(terminate=True)
            coordinator.entries.clear()
            coordinator.running.clear()
        if not coordinator.entries and not coordinator.running and not acceleration._worker_disabled:
            if coordinator.waves >= 3 and coordinator.wave_hits == 0:
                coordinator.cooldown = max(coordinator.cooldown, 16)
                coordinator.waves = 0
            if coordinator.cooldown:
                coordinator.cooldown -= 1
            else:
                _submit_wave(chess, match, coordinator)
        entry = coordinator.entries.pop(id(match.runner), None)
        if entry is not None:
            future, config, reference, submitted = entry
            if reference() is match.runner and monotonic() - submitted <= _MAX_JOB_AGE:
                token = _current_job.set((coordinator, future, config, match.runner, submitted))
    except Exception:
        acceleration._worker_disabled = True
        acceleration.shutdown_workers(terminate=True)
        coordinator.entries.clear()
    try:
        yield
    finally:
        if token is not None:
            _current_job.reset(token)
        after_ids = acceleration._id_state()
        if before_ids is not None and after_ids is not None:
            coordinator.deltas[id(match.runner)] = tuple(max(0, end - start) for start, end in zip(before_ids, after_ids))
        # Match runners change each round. Do not retain a growing ID history.
        active = {id(item.runner) for item in chess.matches if item.runner is not None}
        coordinator.deltas = {key: value for key, value in coordinator.deltas.items() if key in active}
        _last_statistics.set({name: value - before_statistics[name] for name, value in coordinator.statistics.items()})
