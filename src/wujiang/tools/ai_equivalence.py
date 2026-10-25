"""Offline, exact decision comparison against the pre-optimization query code.

The reference patches are for an isolated test process, never a live server.
Candidate order, floating-point scores, payloads and random state are compared.
"""
from __future__ import annotations

import argparse
import os
import pickle
import cProfile
from collections import OrderedDict, deque
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from dataclasses import fields, is_dataclass, replace
from itertools import tee
import json
from pathlib import Path
import random
import pstats
import sys
from statistics import median
from time import perf_counter
from unittest.mock import patch

from wujiang.tactical.engine import core
from wujiang.tactical.engine.core import Position
from wujiang.tactical.heroes import base as hero_base
from wujiang.tactical.heroes.common import dedupe_positions, pattern_signature
from wujiang.tactical.heroes.registry import create_battle, create_hero
from wujiang.tactical.rooms import ai
from wujiang.platform.id_sequence import IdSequence
from wujiang.platform.ai_build import build_info
from wujiang.tools.ai_scenarios import SERIES_CHESS_FIXTURES
from wujiang.tactical.rooms.ai_acceleration import acceleration_disabled, last_statistics, _id_state


def reference_unit_occupies(self, unit, position):
    return position in self.unit_cells(unit)


def reference_footprint_cells_at(self, position):
    return [position.offset(dx, dy) for dx, dy in self.footprint_offsets]


def reference_neighbors(self, position):
    result = []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            candidate = position.offset(dx, dy)
            if self.in_bounds(candidate):
                result.append(candidate)
    return result


def reference_cells_within_distance(self, unit, distance):
    if unit.position is None:
        return []
    return [core.Position(x, y) for y in range(self.height) for x in range(self.width)
            if self.unit_distance_to_cell(unit, core.Position(x, y)) <= distance]


def reference_card_centers(self, battle, actor):
    if actor.position is None:
        return []
    cells = []
    for x in range(battle.width):
        for y in range(battle.height):
            cell = core.Position(x, y)
            if battle.unit_distance_to_cell(actor, cell) <= actor.targeting_range():
                cells.append(cell)
    cells.sort(key=lambda cell: (cell.y, cell.x))
    return cells


def reference_unit_distance_to_cell(self, unit, cell):
    cells = self.unit_cells(unit)
    if not cells:
        if unit.position is None:
            return 10**9
        cells = self.unit_cells_at(unit, unit.position)
    return min(origin.distance_to(cell) for origin in cells)


def reference_distance_between_units(self, left, right):
    left_cells, right_cells = self.unit_cells(left), self.unit_cells(right)
    if not left_cells or not right_cells:
        return 10**9
    return min(a.distance_to(b) for a in left_cells for b in right_cells)


def reference_units_at_cells(self, cells):
    units, seen = [], set()
    for cell in cells:
        for unit in self.units_at(cell):
            if unit.unit_id not in seen:
                seen.add(unit.unit_id)
                units.append(unit)
    return units


def reference_dedupe_damage_area_payloads(battle, payloads):
    unique = {}
    for payload in payloads:
        cells = ai.preview_positions(payload.get("cells"))
        signature = tuple(sorted(
            (unit.unit_id, battle.unit_hit_count_for_cells(unit, cells))
            for unit in battle.effect_units_at_cells(cells)
        ))
        unique.setdefault(signature, payload)
    return list(unique.values())


def reference_dedupe_area_payloads_by_affected_units(battle, payloads, *, clones_and_summons_only):
    unique = {}
    for payload in payloads:
        units = battle.effect_units_at_cells(ai.preview_positions(payload.get("cells")))
        if clones_and_summons_only:
            units = [unit for unit in units if unit.is_clone or unit.is_summon]
        signature = tuple(sorted(unit.unit_id for unit in units))
        unique.setdefault(signature, payload)
    return list(unique.values())


def reference_trim_skill_payloads_for_ai(battle, actor, payloads, *, limit):
    if len(payloads) <= limit:
        return payloads
    if not any(payload.get("cells") for payload in payloads):
        return payloads[:limit]

    def sort_key(payload):
        cells = ai.preview_positions(payload.get("cells"))
        if not cells:
            return (0.0, 0)
        enemies, allies, enemy_value = 0, 0, 0.0
        for unit in battle.effect_units_at_cells(cells):
            if unit.player_id == actor.player_id:
                allies += 1
            else:
                enemies += 1
                enemy_value += ai.hostile_unit_value(unit)
        return (enemies * 1000.0 + enemy_value - allies * 250.0 - len(cells) * 0.01, -len(cells))

    return sorted(payloads, key=sort_key, reverse=True)[:limit]


@contextmanager
def reference_rollback(battle, *, ai_probe=True, commit_on_success=False):
    # Frozen copying algorithm; both sides share actual-choice failure atomicity.
    def value_copy(value):
        if isinstance(value, dict):
            return {key: value_copy(item) for key, item in value.items()}
        if isinstance(value, list):
            return [value_copy(item) for item in value]
        if isinstance(value, tuple):
            return tuple(value_copy(item) for item in value)
        if isinstance(value, set):
            return set(value)
        if isinstance(value, deque):
            return deque(value_copy(item) for item in value)
        return value

    def state(obj):
        if hasattr(obj, "__dict__"):
            return {key: value_copy(value) for key, value in obj.__dict__.items()}
        return {item.name: value_copy(getattr(obj, item.name)) for item in fields(obj)}

    units = list({unit.unit_id: unit for unit in [*battle.all_units(), *battle.destroyed_units]}.values())
    objects = [battle, *units, *battle.field_effects]
    if battle.pending_chain is not None:
        objects.extend([battle.pending_chain, battle.pending_chain.queued_action])
        objects.extend(battle.pending_chain.chosen_reactions)
        for options in battle.pending_chain.options_by_unit.values():
            objects.extend(options)
    objects.extend(battle.pending_followup_actions)
    if battle.resolving_action is not None:
        objects.append(battle.resolving_action)
    for unit in units:
        components = list(unit.iter_components())
        objects.extend(components)
        for component in components:
            objects.extend(component.runtime_children())
        objects.append(unit.base_stats)
    saved = [(obj, state(obj)) for obj in {id(obj): obj for obj in objects}.values()
             if hasattr(obj, "__dict__") or (is_dataclass(obj) and not obj.__dataclass_params__.frozen)]
    stats = [(unit.base_stats, replace(unit.base_stats)) for unit in units]
    rng = random.getstate()
    restore = True
    try:
        if not commit_on_success:
            if ai_probe:
                battle._ai_probe_active = True
            else:
                battle.on_replay_checkpoint = None
        yield
        restore = not commit_on_success
    finally:
        if restore:
            for obj, snapshot in reversed(saved):
                if hasattr(obj, "__dict__"):
                    obj.__dict__.clear()
                    obj.__dict__.update(snapshot)
                else:
                    for name, value in snapshot.items():
                        setattr(obj, name, value)
            for original, snapshot in stats:
                for name in ("attack", "defense", "speed", "attack_range", "mana"):
                    setattr(original, name, getattr(snapshot, name))
            random.setstate(rng)


def reference_effect_baseline(battle, actor, units, *, include_pending=False):
    """The original per-unit reads, before whole-batch memoization."""
    if include_pending:
        return {unit.unit_id: (unit.current_hp, unit.current_mana, unit.total_shields(), unit.dodge_charges,
                              unit.alive, unit.position, ai.reviewed_r17_position_value(battle, actor, unit),
                              bool(getattr(unit.get_status("毁灭电击"), "pending", False))) for unit in units}
    return {unit.unit_id: (unit.current_hp, unit.current_mana, unit.total_shields(), unit.dodge_charges,
                          unit.alive, unit.position, ai.reviewed_r17_position_value(battle, actor, unit)) for unit in units}


def reference_remote_rectangle_patterns(battle, actor, width, height, *, attack_range=None):
    if actor.position is None:
        return []
    patterns: list[list[Position]] = []
    seen: set[tuple[tuple[int, int], ...]] = set()
    range_limit = attack_range if attack_range is not None else actor.targeting_range()
    in_range = {(cell.x, cell.y) for cell in battle.cells_within_distance(actor, range_limit)}
    for start_x in range(1 - width, battle.width):
        for start_y in range(1 - height, battle.height):
            cells: list[Position] = []
            contains_range_cell = False
            for x in range(max(0, start_x), min(battle.width, start_x + width)):
                for y in range(max(0, start_y), min(battle.height, start_y + height)):
                    cell = Position(x, y)
                    cells.append(cell)
                    if (x, y) in in_range:
                        contains_range_cell = True
            if not cells or not contains_range_cell:
                continue
            deduped = dedupe_positions(cells)
            key = pattern_signature(deduped)
            if key in seen:
                continue
            seen.add(key)
            patterns.append(deduped)
    patterns.sort(key=pattern_signature)
    return patterns


def reference_localized_line_patterns(battle, origin, directions, length, *, max_distance=None, touch_distance=None):
    if origin is None or length <= 0:
        return []
    radius = length if max_distance is None else max_distance
    patterns: list[list[Position]] = []
    seen: set[tuple[tuple[int, int], ...]] = set()
    for direction in directions:
        for start_x in range(-length + 1, battle.width):
            for start_y in range(-length + 1, battle.height):
                cells = [
                    Position(start_x + direction[0] * step, start_y + direction[1] * step)
                    for step in range(length)
                ]
                in_bounds = [cell for cell in cells if battle.in_bounds(cell)]
                if not in_bounds:
                    continue
                if any(cell == origin for cell in in_bounds):
                    continue
                if any(origin.distance_to(cell) > radius for cell in in_bounds):
                    continue
                if touch_distance is not None and not any(origin.distance_to(cell) <= touch_distance for cell in in_bounds):
                    continue
                key = pattern_signature(in_bounds)
                if key in seen:
                    continue
                seen.add(key)
                patterns.append(dedupe_positions(in_bounds))
    patterns.sort(key=pattern_signature)
    return patterns


def reference_basic_attack_preview_for_payload(self, actor: Unit, payload: Optional[dict[str, Any]]=None) -> dict[str, Any]:
    resolved_payload = self.resolved_basic_attack_payload(actor, payload)
    if not self.basic_attack_resource_allowed(actor, resolved_payload)[0]:
        return {'cells': [], 'target_unit_ids': [], 'requires_target': True}
    healing_only = resolved_payload.get('attack_variant') == 'allied_heal'
    if not healing_only:
        override = next((component for component in actor.iter_components() if getattr(component, 'overrides_basic_attack_shape', False)), None)
        if override is not None:
            return override.basic_attack_preview(self, actor, resolved_payload)
        for component in actor.iter_components():
            preview = component.basic_attack_preview(self, actor, resolved_payload)
            if preview is not None:
                return preview
    attack_targets: list[str] = []
    attack_cells: list[dict[str, int]] = []
    candidates = list(self.player_units(actor.player_id)) if healing_only else list(self.all_units())
    forced_target = self.forced_basic_attack_target(actor)
    if not healing_only and forced_target is not None and all((unit.unit_id != forced_target.unit_id for unit in candidates)):
        candidates.append(forced_target)
    for target in candidates:
        ignore_stealth = self.attack_ignores_stealth(actor, target)
        if self.attack_target_allowed(actor, target, ignore_stealth=ignore_stealth, payload=resolved_payload)[0]:
            attack_targets.append(target.unit_id)
            valid_cells = []
            for cell in self.unit_cells(target):
                cell_payload = dict(resolved_payload)
                cell_payload['x'] = cell.x
                cell_payload['y'] = cell.y
                if self.attack_target_allowed(actor, target, ignore_stealth=ignore_stealth, payload=cell_payload)[0]:
                    valid_cells.append(cell)
            if not valid_cells:
                valid_cells = self.unit_cells(target)
            attack_cells.extend((cell.to_dict() for cell in valid_cells))
    return {'cells': attack_cells, 'target_unit_ids': attack_targets, 'requires_target': True, 'allied_heal_target_ids': [target_id for target_id in attack_targets if self.allied_basic_heal_amount(actor, self.get_unit(target_id)) > 0]}

def reference_offensive_reach_score_at(battle: Battle, actor: Unit, destination: Position) -> int:
    current = actor.position
    actor.position = destination
    try:
        preview = battle.basic_attack_preview_for_payload(actor, {})
        target_ids = {str(unit_id) for unit_id in preview.get('target_unit_ids', []) if not bool(getattr(battle.units.get(str(unit_id)), 'is_siege_structure', False))}
        return len(target_ids)
    except Exception:
        return 0
    finally:
        actor.position = current


@contextmanager
def reference_calculations():
    from wujiang.tactical.heroes.next_five import ChanterCardSkill
    from wujiang.tactical.heroes import common, next_five
    with ExitStack() as stack:
        stack.enter_context(acceleration_disabled())
        # Patch imported aliases too, so roster helpers cannot silently use
        # optimized geometry on the reference side. Offline process only.
        originals = {"remote_rectangle_patterns": (next_five.remote_rectangle_patterns, reference_remote_rectangle_patterns),
                     "localized_line_patterns": (common.localized_line_patterns, reference_localized_line_patterns)}
        for module in tuple(sys.modules.values()):
            if module is None or not getattr(module, "__name__", "").startswith("wujiang.tactical.heroes."):
                continue
            for name, (original, reference) in originals.items():
                if getattr(module, name, None) is original:
                    stack.enter_context(patch.object(module, name, reference))
        stack.enter_context(patch.object(core.Unit, "footprint_cells_at", reference_footprint_cells_at))
        stack.enter_context(patch.object(ChanterCardSkill, "selectable_centers", reference_card_centers))
        for name, function in (
            ("unit_occupies", reference_unit_occupies),
            ("unit_distance_to_cell", reference_unit_distance_to_cell),
            ("distance_between_units", reference_distance_between_units),
            ("units_at_cells", reference_units_at_cells),
            ("neighbors", reference_neighbors),
            ("cells_within_distance", reference_cells_within_distance),
        ):
            stack.enter_context(patch.object(core.Battle, name, function))
        stack.enter_context(patch.object(core, "battle_state_rollback", reference_rollback))
        stack.enter_context(patch.object(ai, "battle_state_rollback", reference_rollback))
        stack.enter_context(patch.object(ai, "reviewed_effect_baseline", reference_effect_baseline))
        stack.enter_context(patch.object(core.Battle, "basic_attack_preview_for_payload", reference_basic_attack_preview_for_payload))
        stack.enter_context(patch.object(ai, "offensive_reach_score_at", reference_offensive_reach_score_at))
        stack.enter_context(patch.object(ai, "dedupe_damage_area_payloads", reference_dedupe_damage_area_payloads))
        stack.enter_context(patch.object(ai, "dedupe_area_payloads_by_affected_units", reference_dedupe_area_payloads_by_affected_units))
        stack.enter_context(patch.object(ai, "trim_skill_payloads_for_ai", reference_trim_skill_payloads_for_ai))
        yield


@contextmanager
def isolated_id_stream():
    """Replay component/declaration AND summoned-unit IDs, including nesting."""
    if isinstance(core._id_counter, IdSequence):
        rewind, working = core._id_counter, core._id_counter.fork()
    else:
        rewind, working = tee(core._id_counter)
    if isinstance(hero_base._unit_counter, IdSequence):
        unit_rewind, unit_working = hero_base._unit_counter, hero_base._unit_counter.fork()
    else:
        unit_rewind, unit_working = tee(hero_base._unit_counter)
    core._id_counter, hero_base._unit_counter = working, unit_working
    try:
        yield
    finally:
        core._id_counter, hero_base._unit_counter = rewind, unit_rewind


def capture_turn(battle, difficulty="standard", *, stages=None):
    """Capture candidates and choice without committing probes or consuming IDs."""
    started = perf_counter()
    with isolated_id_stream(), reference_rollback(battle, ai_probe=False):
        actors = battle.current_turn_bundle_units(include_banished=False)
        profile = ai.difficulty_profile(difficulty)
        rows = []
        # Use the same perceived-enemy boundary as the production picker.
        with ai.ai_enemy_view(battle, battle.active_player):
            ai.clear_follow_anchor_cache(battle)
            for actor in actors:
                if not actor.can_take_turn_actions(battle):
                    continue
                actions, moves = ai.turn_action_candidates(battle, actor, profile)
                rows.append((actor.unit_id, [
                    [(deepcopy(c.payload), c.score, c.summary) for c in group]
                    for group in (actions, moves)
                ]))
        # Rewind battle/RNG and both ID streams before the choice pass.
    candidates_finished = perf_counter()
    with isolated_id_stream(), reference_rollback(battle, ai_probe=False):
        payload, actor = ai.choose_turn_bundle_action(
            battle, battle.current_turn_bundle_units(include_banished=False), difficulty)
        result = {"candidates": rows, "payload": deepcopy(payload),
                  "actor": actor.unit_id if actor else None, "rng": random.getstate(), "id_state": _id_state()}
        statistics = last_statistics()
    finished = perf_counter()
    if stages is not None:
        stages.update(candidates_seconds=candidates_finished - started,
                      selection_seconds=finished - candidates_finished, acceleration=statistics)
    return result, finished - started


def compare_turn(battle, difficulty="standard", *, repeats=1, profile_dir=None):
    if repeats < 1:
        raise ValueError("repeats must be positive")
    times = {"reference": [], "optimized": []}
    stages = {"reference": [], "optimized": []}
    profiles = {}
    before, after, mismatches = None, None, []
    for repeat in range(repeats):
        # Alternate order to reduce systematic cold/warm-cache timing bias.
        for mode in (("reference", "optimized") if repeat % 2 == 0 else ("optimized", "reference")):
            stage = {}
            with ExitStack() as stack:
                if mode == "reference":
                    stack.enter_context(reference_calculations())
                result, seconds = capture_turn(battle, difficulty, stages=stage)
            if before is None:
                before = result
            if mode == "optimized" and after is None:
                after = result
            if result != before:
                mismatches.append({"mode": mode, "repeat": repeat + 1, "result": result})
            times[mode].append(seconds)
            stages[mode].append(stage)
    profile_equal = True
    if profile_dir is not None:
        # Profiling adds substantial overhead. Run it separately from timings.
        for mode in ("reference", "optimized"):
            profiler = cProfile.Profile()
            with ExitStack() as stack:
                if mode == "reference":
                    stack.enter_context(reference_calculations())
                stack.enter_context(profiler)
                profiled, _ = capture_turn(battle, difficulty)
                profile_equal = profile_equal and profiled == before
                if profiled != before:
                    mismatches.append({"mode": mode, "repeat": "profile", "result": profiled})
            directory = Path(profile_dir)
            directory.mkdir(parents=True, exist_ok=True)
            profiler.dump_stats(str(directory / f"{mode}.prof"))
            stats = pstats.Stats(profiler)
            profiles[mode] = [
                {"file": key[0], "line": key[1], "function": key[2],
                 "primitive_calls": value[0], "calls": value[1],
                 "self_seconds": value[2], "cumulative_seconds": value[3]}
                for key, value in sorted(stats.stats.items(), key=lambda item: item[1][3], reverse=True)[:20]
            ]
    old_seconds, new_seconds = median(times["reference"]), median(times["optimized"])
    return {
        "equal": profile_equal and not mismatches,
        "reference_seconds": old_seconds,
        "optimized_seconds": new_seconds,
        "speedup": old_seconds / new_seconds if new_seconds else None,
        "reference": before,
        "optimized": after,
        "timing_samples": times,
        "stages": stages,
        "profiles": profiles,
        "mismatches": mismatches,
        "candidate_count": sum(len(group) for _, groups in before["candidates"] for group in groups),
        "unit_count": len(battle.units),
        "actors": [{"id": unit.unit_id, "hero_code": unit.hero_code}
                   for unit in battle.current_turn_bundle_units(include_banished=False)],
    }


def pack_contact(battle):
    """Translate each entire side rigidly, keeping bodies/mount overlaps intact."""
    units = battle.all_units()
    for side in (1, 2):
        body = [cell for unit in units if unit.player_id == side for cell in battle.unit_cells(unit)]
        if not body:
            continue
        front = max(cell.x for cell in body) if side == 1 else min(cell.x for cell in body)
        target = battle.width // 2 + (-1 if side == 1 else 1)
        shift = target - front
        for unit in units:
            if unit.player_id == side and unit.position is not None:
                unit.position = unit.position.offset(shift, 0)
    if any(not battle.in_bounds(cell) for unit in units for cell in battle.unit_cells(unit)):
        raise ValueError("Contact fixture does not fit this roster; increase the board size")


def add_density_summons(battle, total=8):
    """Synthetic density fixture; not a claim that this is a legal skill sequence."""
    from wujiang.tactical.heroes.first_five import MedusaSummon

    for index in range(total):
        side = index % 2 + 1
        owner = next((unit for unit in battle.player_units(side) if unit.hero_code == "dark_human"),
                     battle.player_units(side)[0])
        summon = MedusaSummon(side)
        summon.summoner_id = owner.unit_id
        available = [core.Position(x, y) for y in range(battle.height) for x in range(battle.width)
                     if battle.can_place_unit(summon, core.Position(x, y), mover=summon)]
        if not available:
            raise ValueError("No room for the density fixture summons")
        destination = min(available, key=lambda cell: (abs(cell.x - battle.width // 2), cell.y, cell.x))
        battle.add_unit(summon, destination, trigger_enter_effects=False)
        summon.turn_ready = True


def advance_sample_turn(battle):
    """End this turn legally; settle prompts only to prepare the next sample.

    These preparation actions are not benchmarked or claimed as audited choices.
    """
    battle.perform_action({"type": "end_turn"})
    for _ in range(128):
        if battle.winner is not None:
            return
        if battle.pending_damage_choice is not None:
            battle.perform_action(ai.choose_damage_choice_action(battle))
        elif battle.current_respawn_prompt() is not None:
            prompt = battle.current_respawn_prompt()
            unit = battle.get_unit(prompt.unit_id)
            options = sorted(battle.respawn_options_for(unit), key=lambda cell: (cell.x, cell.y))
            if not options:
                raise RuntimeError("Cannot settle respawn while preparing the next sample")
            battle.perform_action({"type": "respawn_select", "unit_id": unit.unit_id,
                                   "x": options[0].x, "y": options[0].y})
        elif battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})
        else:
            return
    raise RuntimeError("Prompt preparation exceeded 128 actions")


def compare_area_batch(*, repeats=3):
    """A separate read-only algorithm workload, not full-decision latency."""
    battle = core.Battle(width=16, height=12)
    for index in range(48):
        unit = create_hero("bard", index % 2 + 1)
        unit.position = core.Position(index % 8 * 2, index // 8 * 2)
        if index % 3 == 0:
            unit.set_footprint_offsets([(0, 0), (0, 1), (1, 0), (1, 1)])
        unit.is_summon = index % 3 == 0
        battle.units[unit.unit_id] = unit
    actor = battle.all_units()[0]
    payloads = [
        {"ordinal": index, "cells": [
            {"x": x + dx, "y": y + dy} for dx in range(size) for dy in range(size)
            if x + dx < battle.width and y + dy < battle.height
        ]}
        for index, (x, y, size) in enumerate(
            (x, y, size) for size in (3, 5, 10) for y in range(battle.height) for x in range(battle.width)
        )
    ]
    records, times = {"reference": [], "optimized": []}, {"reference": [], "optimized": []}
    for repeat in range(repeats):
        for mode in (("reference", "optimized") if repeat % 2 == 0 else ("optimized", "reference")):
            with ExitStack() as stack:
                if mode == "reference":
                    stack.enter_context(reference_calculations())
                started = perf_counter()
                damage = ai.dedupe_damage_area_payloads(battle, payloads)
                summons = ai.dedupe_area_payloads_by_affected_units(battle, payloads, clones_and_summons_only=True)
                trimmed = ai.trim_skill_payloads_for_ai(battle, actor, payloads, limit=64)
                times[mode].append(perf_counter() - started)
                records[mode].append({
                    "damage_ordinals": [item["ordinal"] for item in damage],
                    "summon_ordinals": [item["ordinal"] for item in summons],
                    "trim_ordinals": [item["ordinal"] for item in trimmed],
                })
    baseline = records["reference"][0]
    old_seconds, new_seconds = median(times["reference"]), median(times["optimized"])
    return {"scenario": "area_batch_48_units", "workload": "readonly_area_batch",
            "equal": all(result == baseline for group in records.values() for result in group),
            "reference_seconds": old_seconds, "optimized_seconds": new_seconds,
            "speedup": old_seconds / new_seconds if new_seconds else None,
            "unit_count": len(battle.units), "payload_count": len(payloads),
            "timing_samples": times, "reference": baseline, "optimized": records["optimized"][0]}


LATE_CHESS_FOCUS = ("chanter", "jade", "blood_eater", "elite_soldier")
ATTRITION_CHESS_FOCUS = ("excel_r127", "jade", "blood_eater", "elite_soldier")


def late_chess_focus(variant):
    if variant in SERIES_CHESS_FIXTURES:
        return SERIES_CHESS_FIXTURES[variant]["focus"]
    return ATTRITION_CHESS_FOCUS if variant == "attrition" else LATE_CHESS_FOCUS


def late_chess_composition(chess, variant):
    definition = SERIES_CHESS_FIXTURES.get(variant)
    if definition is None:
        return {}
    return {
        "fixture_series": definition["label"],
        "initial_rosters_by_side": [
            [[unit.hero_code for unit in match.runner.battle.all_units() if unit.player_id == side]
             for side in (1, 2)] for match in chess.matches],
    }


def _place_series_pieces(chess, player):
    # Use the real entry footprint (including 2x2 factories), without changing
    # the 10x10 board or its four-column deployment zone. Setup is not timed.
    from wujiang.tactical.rooms.autochess import _footprint
    occupied = set()
    for piece in player.pieces:
        offsets = _footprint(piece.code)
        for y in range(chess.config.board_height):
            for x in range(chess.config.deployment_width):
                cells = {(x + dx, y + dy) for dx, dy in offsets}
                if (cells and not cells.intersection(occupied)
                        and all(0 <= cx < chess.config.deployment_width
                                and 0 <= cy < chess.config.board_height for cx, cy in cells)):
                    piece.x, piece.y = x, y
                    occupied.update(cells)
                    break
            else:
                continue
            break
        else:
            raise ValueError("Series fixture does not fit the real deployment zone")


def add_attrition_history(battle, total=21):
    """Synthetic neutral deaths, retaining the real corpse/component lifecycle."""
    for index in range(total):
        unit = create_hero("elite_soldier", index % 2 + 1)
        unit.owner_seat_id = unit.player_id
        destination = next((Position(x, y) for y in range(battle.height) for x in range(battle.width)
                            if battle.can_place_unit(unit, Position(x, y), mover=unit)), None)
        if destination is None:
            raise ValueError("No room to prepare attrition history")
        battle.add_unit(unit, destination, trigger_enter_effects=False)
        unit.current_hp, unit.alive = 0, False
        battle.cleanup_dead_units()
        if battle.winner is not None:
            raise RuntimeError("Attrition preparation unexpectedly ended the battle")


def create_late_chess_fixture(seed=17, *, variant="standard"):
    """Synthetic 8-seat/four-board dense or attrition fixture on real runners.

    This exercises battle setup, synergies and the room AI execution path;
    it does not claim to reproduce a saved match's shopping history.
    """
    from wujiang.tactical.rooms.autochess import AutoChessState, ChessMatch, ChessPiece, EQUIPMENT_CATALOG

    if variant not in ("standard", "equipped", "summons", "attrition", *SERIES_CHESS_FIXTURES):
        raise ValueError("Unknown late chess fixture")

    chess = AutoChessState(8, seed=seed)
    rosters = (
        ["fire_funeral", "bard", "dark_human", "elite_soldier", "chanter", "bard"] * 2,
        ["ellie", "blood_eater", "element_hunter", "masamune", "jade", "n"] * 2,
    )
    focus = late_chess_focus(variant)
    if variant in SERIES_CHESS_FIXTURES:
        roster = SERIES_CHESS_FIXTURES[variant]["roster"]
        # Both sides are majority-series; rotate the same mixed roster so the
        # two armies do not share identical deployment positions.
        rosters = (roster, roster[5:] + roster[:5])
    if variant == "attrition":
        # Tail-cost fixture, not a reconstruction of TXKJ3S. Include the shared
        # R17 scoring family seen in its slow stacks, without changing AI logic.
        rosters = (["excel_r127", "bard", "dark_human", "elite_soldier"],
                   ["ellie", "blood_eater", "jade", "n"])
        focus = ATTRITION_CHESS_FOCUS
    chess.round_number = 12
    chess.phase = "battle"
    for seat_id, player in chess.players.items():
        player.level = 8
        player.pieces = [ChessPiece(f"late-{seat_id}-{index}", code,
                                   x=index % 3, y=index // 3 * 2 + 1)
                         for index, code in enumerate(rosters[(seat_id - 1) % 2])]
        if variant in SERIES_CHESS_FIXTURES:
            _place_series_pieces(chess, player)
    chess.matches = [ChessMatch(left, left + 1) for left in (1, 3, 5, 7)]
    if variant == "equipped" or variant in SERIES_CHESS_FIXTURES:
        equipment = [code for code in ("锈剑", "木盾", "宝石", "布鞋", "布甲", "洁白羽毛", "天使之剑", "吸血面具")
                     if code in EQUIPMENT_CATALOG]
        if not equipment:
            raise ValueError("Equipped fixture requires the equipment workbook")
        for player in chess.players.values():
            for index, piece in enumerate(player.pieces):
                piece.equipment = [equipment[index % len(equipment)]]
    # Recording is optional infrastructure, disabled in this reproducible
    # fixture exactly as in the default live chess path.
    with patch("wujiang.platform.app_settings.autochess_replay_enabled", return_value=False):
        for index, match in enumerate(chess.matches):
            match.runner = chess._create_match_battle(match)
            battle = match.runner.battle
            # Prepare different current actors through legal end-turn actions.
            # No AI decisions are timed or claimed audited during preparation.
            # Previously every board started on Chanter, and short traces
            # exercised only that one current hero despite the dense roster.
            for _ in range(len(battle.turn_order_unit_ids) * 2):
                actor = battle.current_turn_unit()
                if actor is not None and actor.hero_code == focus[index]:
                    break
                advance_sample_turn(battle)
            else:
                raise RuntimeError(f"Cannot prepare late chess focus {focus[index]}")
            if variant == "summons":
                add_density_summons(battle, total=8)
            elif variant == "attrition":
                add_attrition_history(battle)
    return chess


@contextmanager
def package_reference(kind):
    from wujiang.tactical.rooms import ai_acceleration
    from wujiang.tactical.rooms import ai_parallel
    if kind == "original":
        with reference_calculations():
            yield
    elif kind == "before-pack":
        with patch.object(core, "_AI_TARGET_IDS_ENABLED", False), \
                patch.object(ai_acceleration, "_MOVE_BATCHES_ENABLED", False):
            yield
    elif kind == "no-type-plans":
        with patch.object(core, "_ROLLBACK_TYPE_PLANS_ENABLED", False):
            yield
    elif kind == "no-native":
        with patch.object(core, "_ROLLBACK_NATIVE_ENABLED", False):
            yield
    elif kind == "no-flat-copy":
        with patch.object(core, "_NATIVE_FLAT_COPY_ENABLED", False):
            yield
    elif kind == "no-small-baselines":
        with patch.object(ai_acceleration, "_SMALL_BASELINES_ENABLED", False):
            yield
    elif kind == "no-baseline-batches":
        with patch.object(ai_acceleration, "_BASELINE_BATCHES_ENABLED", False):
            yield
    elif kind == "no-rng-independent":
        with patch.object(ai_acceleration, "_RNG_INDEPENDENT_ROWS_ENABLED", False):
            yield
    elif kind == "no-independent-adaptation":
        with patch.object(ai_acceleration, "_ADAPTIVE_ROWS_ENABLED", False):
            yield
    elif kind == "no-tactical-queries":
        with patch.object(ai_acceleration, "_TACTICAL_QUERIES_ENABLED", False):
            yield
    elif kind == "no-native-row-keys":
        with patch.object(ai_acceleration, "_NATIVE_ROW_KEYS_ENABLED", False):
            yield
    elif kind == "no-component-scan":
        with patch.object(core, "_NATIVE_COMPONENT_SCAN_ENABLED", False), \
                patch.object(core.Unit, "innate_traits_suppressed", core._PYTHON_INNATE_TRAITS_SUPPRESSED):
            yield
    elif kind == "no-native-checkpoint":
        with patch.object(core, "_NATIVE_CHECKPOINT_ENABLED", False):
            yield
    elif kind == "no-parallel-baselines":
        with patch.object(ai_acceleration, "_PARALLEL_BASELINES_ENABLED", False):
            yield
    elif kind == "no-parallel-boards":
        with patch.object(ai_parallel, "_BOARDS_ENABLED", False):
            yield
    elif kind == "previous-defaults":
        # Only the reference restores the measured-slower defaults. All
        # caches and the native container copier stay identical on both sides.
        with patch.object(core, "_NATIVE_CHECKPOINT_ENABLED", True), \
                patch.object(ai_parallel, "_BOARDS_ENABLED", True):
            yield
    elif kind == "before-architecture":
        with patch.object(ai_acceleration, "_BASELINE_BATCHES_ENABLED", False), \
                patch.object(ai_acceleration, "_PARALLEL_BASELINES_ENABLED", False), \
                patch.object(core, "_NATIVE_CHECKPOINT_ENABLED", False), \
                patch.object(ai_parallel, "_BOARDS_ENABLED", False):
            yield
    else:
        raise ValueError("Unknown reference kind")


class _LateChessProfile:
    """Separate diagnostic replay; retain at most three individual profiles."""
    def __init__(self, directory, mode):
        self.directory = Path(directory) / mode
        self.mode = mode
        self.aggregate = None
        self.slowest = []
        self.steps = []

    def advance(self, chess, match, status):
        profiler = cProfile.Profile()
        started = perf_counter()
        with profiler:
            chess._advance_match(match)
        elapsed = perf_counter() - started
        stats = pstats.Stats(profiler)
        if self.aggregate is None:
            self.aggregate = stats
        else:
            self.aggregate.add(stats)
        info = {**status, "profiled_seconds": elapsed}
        self.steps.append(info)
        self.slowest.append((elapsed, info, profiler))
        self.slowest.sort(key=lambda item: item[0], reverse=True)
        del self.slowest[3:]
        return elapsed

    def finish(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        def rows(stats):
            return [{"function": f"{name[0]}:{name[1]}:{name[2]}", "primitive_calls": value[0],
                     "calls": value[1], "self_seconds": value[2], "cumulative_seconds": value[3]}
                    for name, value in sorted(stats.stats.items(), key=lambda item: item[1][3], reverse=True)[:30]]
        aggregate_path = self.directory / "all-steps.prof"
        if self.aggregate is not None:
            self.aggregate.dump_stats(str(aggregate_path))
        slowest = []
        for _, info, profiler in self.slowest:
            path = self.directory / f"step-{info['step']:03d}-board-{info['board']}.prof"
            profiler.dump_stats(str(path))
            slowest.append({**info, "path": str(path), "top_cumulative": rows(pstats.Stats(profiler))})
        return {"mode": self.mode, "excluded_from_timing_samples": True,
                "path": str(aggregate_path), "steps": self.steps, "slowest": slowest,
                "top_cumulative": rows(self.aggregate) if self.aggregate is not None else []}


def compare_late_chess(*, seed=17, sweeps=4, repeats=1, progress=None,
                       variant="standard", baseline="original", profile_dir=None):
    """Compare actual round-robin actions/state, not four independent pickers."""
    from wujiang.tactical.rooms import ai_router
    from wujiang.tactical.rooms import ai_acceleration
    from wujiang.tactical.rooms import ai_parallel
    from wujiang.tactical.rooms.multiplayer import GameRoom
    from wujiang.platform.ai_native import load_flat_copy_statistics

    if sweeps < 1 or repeats < 1:
        raise ValueError("sweeps and repeats must be positive")
    if baseline == "no-native" and core._NATIVE_ROLLBACK_COPY is None:
        raise RuntimeError("Native comparison requires a compiled, loadable module; run tools/build_ai_native.py --bootstrap")
    if baseline == "no-flat-copy" and (core._NATIVE_ROLLBACK_COPY is None or core._NATIVE_FLAT_COPY is None):
        raise RuntimeError("Scalar batch comparison requires the current compiled module")
    if baseline == "no-native-row-keys" and ai_acceleration.load_rng_mask() is None:
        raise RuntimeError("Row-key comparison requires the current compiled RNG mask")
    if baseline == "no-component-scan" and core._NATIVE_COMPONENT_SCAN is None:
        raise RuntimeError("Component scan comparison requires the current compiled scanner")
    if baseline in {"no-native-checkpoint", "before-architecture", "previous-defaults"} and core._NATIVE_CHECKPOINT is None:
        raise RuntimeError("Native checkpoint comparison requires the current compiled module")
    original_rng = random.getstate()
    traces, timings = {"reference": [], "optimized": []}, {"reference": [], "optimized": []}
    samples = {"reference": [], "optimized": []}
    initial_states, initial_ids = [], []
    initial_composition = {}
    effective_features = None
    profiles = {}
    flat_features = {}
    independent_features = {}
    adaptive_features = {}
    query_features = {}
    row_key_features = {}
    component_features = {}
    witness_steps = ({"electronic-heavy": (117, 137, 196), "hell-heavy": (45, 67, 91)}.get(variant, ())
                     if baseline in {"no-rng-independent", "no-independent-adaptation", "no-tactical-queries", "no-native-row-keys", "no-component-scan"} else ())
    if witness_steps and sweeps * 4 < max(witness_steps):
        witness_steps = ()  # A quick opening check is not a long-tail audit.
    query_witnesses = {"reference": {}, "optimized": {}}
    flat_statistics = load_flat_copy_statistics()
    perform_action = GameRoom._perform_battle_action
    def fixture():
        return create_late_chess_fixture(seed) if variant == "standard" else create_late_chess_fixture(seed, variant=variant)
    try:
        # The first hero_catalog() constructs metadata heroes and consumes IDs.
        # Warm lazy setup under disposable IDs before EITHER measured run;
        # otherwise the reference starts 120 unit IDs ahead of the optimized
        # run solely because its catalog was cold. Setup is never timed.
        with isolated_id_stream():
            random.seed(seed)
            fixture()
        for repeat, profiled, witnessed in [*((index, False, False) for index in range(repeats)),
                *([(repeats, True, False)] if profile_dir is not None else []),
                *([(repeats + 1, False, True)] if witness_steps else [])]:
            for mode in (("reference", "optimized") if repeat % 2 == 0 else ("optimized", "reference")):
                random.seed(seed)
                with ExitStack() as stack:
                    ai_acceleration.shutdown_workers(terminate=True)
                    stack.callback(ai_acceleration.shutdown_workers, terminate=True)
                    stack.enter_context(patch.object(ai_acceleration, "_worker_disabled", False))
                    stack.enter_context(isolated_id_stream())
                    stack.enter_context(patch.object(ai_router, "_picker", return_value=ai))
                    # Isolate each architecture component from cross-board
                    # predictions and from scheduling history in the other run.
                    if baseline not in {"original", "no-parallel-boards", "before-architecture"}:
                        stack.enter_context(patch.object(ai_parallel, "_BOARDS_ENABLED", False))
                    if baseline == "previous-defaults":
                        stack.enter_context(patch.object(core, "_NATIVE_CHECKPOINT_ENABLED", False))
                        stack.enter_context(patch.object(core, "_ROLLBACK_NATIVE_ENABLED", True))
                        stack.enter_context(patch.object(core, "_ROLLBACK_TYPE_PLANS_ENABLED", True))
                    if baseline == "no-flat-copy":
                        stack.enter_context(patch.object(core, "_NATIVE_FLAT_COPY_ENABLED", True))
                        stack.enter_context(patch.object(core, "_NATIVE_CHECKPOINT_ENABLED", False))
                        stack.enter_context(patch.object(core, "_ROLLBACK_NATIVE_ENABLED", True))
                        stack.enter_context(patch.object(core, "_ROLLBACK_TYPE_PLANS_ENABLED", True))
                    if baseline in {"no-baseline-batches", "no-native-checkpoint"}:
                        stack.enter_context(patch.object(ai_acceleration, "_PARALLEL_BASELINES_ENABLED", False))
                    if baseline in {"no-baseline-batches", "before-architecture"}:
                        stack.enter_context(patch.object(ai_acceleration, "_BASELINE_BATCHES_ENABLED", True))
                    if baseline in {"no-native-checkpoint", "before-architecture"}:
                        stack.enter_context(patch.object(core, "_NATIVE_CHECKPOINT_ENABLED", True))
                        stack.enter_context(patch.object(core, "_ROLLBACK_NATIVE_ENABLED", True))
                    if baseline in {"no-parallel-baselines", "before-architecture"}:
                        stack.enter_context(patch.object(ai_acceleration, "_PARALLEL_BASELINES_ENABLED", True))
                    if baseline in {"no-parallel-boards", "before-architecture"}:
                        stack.enter_context(patch.object(ai_parallel, "_BOARDS_ENABLED", True))
                    stack.enter_context(patch.object(ai_acceleration, "_baseline_costs", OrderedDict()))
                    if baseline in {"no-rng-independent", "no-independent-adaptation", "no-native-row-keys", "no-component-scan"}:
                        stack.enter_context(patch.object(ai_acceleration, "_RNG_INDEPENDENT_ROWS_ENABLED", True))
                        stack.enter_context(patch.object(ai_acceleration, "_ADAPTIVE_ROWS_ENABLED", True))
                    if baseline in {"no-native-row-keys", "no-component-scan"}:
                        # Isolate the encoder from the measured-low-gain query
                        # candidate. Both sides keep identical row/adaptation.
                        stack.enter_context(patch.object(ai_acceleration, "_TACTICAL_QUERIES_ENABLED", False))
                        stack.enter_context(patch.object(ai_acceleration, "_NATIVE_ROW_KEYS_ENABLED", True))
                    if baseline == "no-component-scan":
                        stack.enter_context(patch.object(core, "_NATIVE_COMPONENT_SCAN_ENABLED", True))
                        stack.enter_context(patch.object(core.Unit, "innate_traits_suppressed", core._native_innate_traits_suppressed))
                    if baseline == "no-tactical-queries":
                        stack.enter_context(patch.object(ai_acceleration, "_TACTICAL_QUERIES_ENABLED", True))
                    actions = []
                    def record_action(room, payload, *, reason):
                        actions.append((room.room_id, deepcopy(payload), reason))
                        return perform_action(room, payload, reason=reason)
                    stack.enter_context(patch.object(GameRoom, "_perform_battle_action", record_action))
                    if baseline == "no-native":
                        stack.enter_context(patch.object(core, "_ROLLBACK_NATIVE_ENABLED", True))
                        stack.enter_context(patch.object(core, "_ROLLBACK_TYPE_PLANS_ENABLED", True))
                    elif baseline == "no-type-plans":
                        # Isolate the historical Python dispatch change from
                        # the new native copier, which otherwise confounds it.
                        stack.enter_context(patch.object(core, "_ROLLBACK_NATIVE_ENABLED", False))
                    elif baseline == "no-small-baselines":
                        stack.enter_context(patch.object(ai_acceleration, "_SMALL_BASELINES_ENABLED", True))
                    if mode == "reference":
                        stack.enter_context(package_reference(baseline))
                    if baseline in {"no-rng-independent", "no-independent-adaptation", "no-tactical-queries", "no-native-row-keys", "no-component-scan"}:
                        stack.enter_context(patch.dict(os.environ, {
                            "WUJIANG_AI_RNG_INDEPENDENT_ROWS": "1" if ai_acceleration._RNG_INDEPENDENT_ROWS_ENABLED else "0",
                            "WUJIANG_AI_ADAPTIVE_ROWS": "1" if ai_acceleration._ADAPTIVE_ROWS_ENABLED else "0",
                            "WUJIANG_AI_TACTICAL_QUERIES": "1" if ai_acceleration._TACTICAL_QUERIES_ENABLED else "0",
                            "WUJIANG_AI_NATIVE_ROW_KEYS": "1" if ai_acceleration._NATIVE_ROW_KEYS_ENABLED else "0",
                            "WUJIANG_AI_NATIVE_COMPONENT_SCAN": "1" if core._NATIVE_COMPONENT_SCAN_ENABLED else "0"}))
                    if baseline == "no-flat-copy":
                        # Fresh spawn workers must use the same copier as
                        # their parent; class/global patches do not cross spawn.
                        stack.enter_context(patch.dict(os.environ, {
                            "WUJIANG_AI_NATIVE_FLAT_COPY": "1" if core._NATIVE_FLAT_COPY_ENABLED else "0",
                            "WUJIANG_AI_NATIVE_ROLLBACK": "1", "WUJIANG_AI_ROLLBACK_PLANS": "1",
                            "WUJIANG_AI_NATIVE_CHECKPOINT": "0", "WUJIANG_AI_PARALLEL_BOARDS": "0"}))
                    flat_features[mode] = bool(core._NATIVE_FLAT_COPY_ENABLED
                        and core._NATIVE_FLAT_COPY is not None and core._ROLLBACK_NATIVE_ENABLED
                        and core._ROLLBACK_TYPE_PLANS_ENABLED and not core._NATIVE_CHECKPOINT_ENABLED)
                    independent_features[mode] = ai_acceleration._RNG_INDEPENDENT_ROWS_ENABLED
                    adaptive_features[mode] = ai_acceleration._ADAPTIVE_ROWS_ENABLED
                    query_features[mode] = ai_acceleration._TACTICAL_QUERIES_ENABLED
                    row_key_features[mode] = ai_acceleration._NATIVE_ROW_KEYS_ENABLED
                    component_features[mode] = (core.Unit.innate_traits_suppressed is core._native_innate_traits_suppressed
                                                and core._NATIVE_COMPONENT_SCAN is not None)
                    if mode == "optimized":
                        effective_features = dict(
                            optimized_native_flat_copy=bool(core._NATIVE_FLAT_COPY_ENABLED
                                and core._NATIVE_FLAT_COPY is not None and core._ROLLBACK_NATIVE_ENABLED
                                and core._ROLLBACK_TYPE_PLANS_ENABLED and not core._NATIVE_CHECKPOINT_ENABLED),
                            optimized_baseline_batches=ai_acceleration._BASELINE_BATCHES_ENABLED,
                            optimized_rng_independent_rows=ai_acceleration._RNG_INDEPENDENT_ROWS_ENABLED,
                            optimized_adaptive_rows=ai_acceleration._ADAPTIVE_ROWS_ENABLED,
                            optimized_tactical_queries=ai_acceleration._TACTICAL_QUERIES_ENABLED,
                            optimized_native_row_keys=ai_acceleration._NATIVE_ROW_KEYS_ENABLED,
                            optimized_native_component_scan=component_features[mode],
                            optimized_parallel_baselines=ai_acceleration._PARALLEL_BASELINES_ENABLED,
                            optimized_parallel_boards=ai_parallel._BOARDS_ENABLED,
                            optimized_native_checkpoint=bool(core._NATIVE_CHECKPOINT_ENABLED
                                and core._ROLLBACK_NATIVE_ENABLED and core._NATIVE_CHECKPOINT is not None))
                    chess = fixture()
                    initial_composition = late_chess_composition(chess, variant)
                    initial_counts = [len(match.runner.battle.units) for match in chess.matches]
                    initial_destroyed_counts = [len(match.runner.battle.destroyed_units) for match in chess.matches]
                    initial_states.append((tuple(pickle.dumps(match.runner.battle, protocol=pickle.HIGHEST_PROTOCOL)
                                                 for match in chess.matches),
                                           pickle.dumps(chess, protocol=pickle.HIGHEST_PROTOCOL),
                                           random.getstate(), _id_state()))
                    initial_ids.append(dict(mode=mode, repeat=repeat + 1, profiled=profiled, witnessed=witnessed, ids=_id_state(),
                                            actors=[match.runner.battle.current_turn_unit().unit_id
                                                    for match in chess.matches]))
                    trace, steps = [], []
                    diagnostic = _LateChessProfile(profile_dir, mode) if profiled else None
                    for ordinal in range(sweeps * 4):
                        pending = [(index, match) for index, match in enumerate(chess.matches) if match.result is None]
                        if not pending:
                            break
                        cursor = chess.next_match_cursor
                        index, match = pending[cursor % len(pending)]
                        chess.next_match_cursor = cursor + 1
                        actor = match.runner.battle.current_turn_unit()
                        status = dict(mode=mode, repeat=repeat + 1, step=ordinal + 1, board=index + 1,
                                      actor=actor.hero_code if actor else None,
                                      profiled=profiled,
                                      witnessed=witnessed,
                                      active_boards=len(pending),
                                      unit_count=len(match.runner.battle.units))
                        if progress is not None:
                            progress(dict(status, phase="deciding"))
                        before_actions = len(actions)
                        flat_before = flat_statistics() if flat_statistics is not None else {}
                        component_before = core._NATIVE_COMPONENT_SCAN[1]() if core._NATIVE_COMPONENT_SCAN is not None else {}
                        if witnessed and ordinal + 1 in witness_steps:
                            values = []
                            with ai_acceleration.position_value_diagnostic_scope(values.append):
                                started = perf_counter()
                                chess._advance_match(match)
                                elapsed = perf_counter() - started
                            query_witnesses[mode][ordinal + 1] = values
                        elif diagnostic is not None:
                            elapsed = diagnostic.advance(chess, match, status)
                        else:
                            started = perf_counter()
                            chess._advance_match(match)
                            elapsed = perf_counter() - started
                        flat_after = flat_statistics() if flat_statistics is not None else {}
                        component_after = core._NATIVE_COMPONENT_SCAN[1]() if core._NATIVE_COMPONENT_SCAN is not None else {}
                        # Compare complete battle bytes for ALL boards after
                        # every action, plus RNG/IDs/tournament state. Never use
                        # a hash as the equivalence decision.
                        trace.append((tuple(pickle.dumps(item.runner.battle, protocol=pickle.HIGHEST_PROTOCOL)
                                            for item in chess.matches),
                                      pickle.dumps(chess, protocol=pickle.HIGHEST_PROTOCOL),
                                      pickle.dumps(actions[before_actions:], protocol=pickle.HIGHEST_PROTOCOL),
                                      random.getstate(), _id_state()))
                        steps.append(dict(step=ordinal + 1, board=index + 1,
                                          actor=actor.hero_code if actor else None,
                                          active_boards=len(pending),
                                          actions=deepcopy(actions[before_actions:]),
                                          unit_count=len(match.runner.battle.units), seconds=elapsed,
                                          native_flat_copy={name: count - flat_before.get(name, 0)
                                                            for name, count in flat_after.items()},
                                          native_component_scan={name: count - component_before.get(name, 0)
                                                                 for name, count in component_after.items()},
                                          acceleration=last_statistics(), parallel_boards=ai_parallel.last_statistics()))
                        if progress is not None:
                            progress(dict(status, phase="complete", seconds=elapsed))
                    traces[mode].append(trace)
                    if diagnostic is not None:
                        profiles[mode] = diagnostic.finish()
                    elif not witnessed:
                        timings[mode].append(sum(item["seconds"] for item in steps))
                        samples[mode].append(steps)
    finally:
        ai_acceleration.shutdown_workers(terminate=True)
        random.setstate(original_rng)
    baseline_trace = traces["reference"][0]
    mismatch = [(mode, repeat + 1, next((i + 1 for i, (a, b) in enumerate(zip(baseline_trace, trace)) if a != b),
                                      min(len(trace), len(baseline_trace)) + 1))
                for mode, runs in traces.items() for repeat, trace in enumerate(runs) if trace != baseline_trace]
    initial_equal = all(state == initial_states[0] for state in initial_states)
    query_checks = []
    for step in witness_steps:
        expected = query_witnesses["reference"].get(step, [])
        actual = query_witnesses["optimized"].get(step, [])
        same = bool(expected) and expected == actual
        difference = next((index for index, pair in enumerate(zip(expected, actual)) if pair[0] != pair[1]),
                          min(len(expected), len(actual))) if not same else None
        query_checks.append(dict(step=step, equal=same, reference_calls=len(expected), optimized_calls=len(actual),
            first_difference=(dict(index=difference,
                reference=expected[difference] if difference < len(expected) else None,
                optimized=actual[difference] if difference < len(actual) else None) if difference is not None else None)))
        if not same:
            mismatch.append(("position_query", step))
    old, new = median(timings["reference"]), median(timings["optimized"])
    latency = {}
    for mode, runs in samples.items():
        seconds = sorted(item["seconds"] for run in runs for item in run)
        by_reason = {}
        for run in runs:
            for item in run:
                for reason in {action[2] for action in item["actions"]}:
                    by_reason.setdefault(reason, []).append(item["seconds"])
        latency[mode] = dict(
            step_median=median(seconds) if seconds else 0,
            step_p95=seconds[(95 * len(seconds) + 99) // 100 - 1] if seconds else 0,
            step_max=max(seconds, default=0),
            sweep_seconds=[[sum(item["seconds"] for item in run[start:start + 4])
                            for start in range(0, len(run) - 3, 4)
                            if all(item["active_boards"] == 4 for item in run[start:start + 4])
                            and {item["board"] for item in run[start:start + 4]} == {1, 2, 3, 4}] for run in runs],
            by_reason={reason: dict(count=len(values), median=median(values), max=max(values),
                                   p95=sorted(values)[(95 * len(values) + 99) // 100 - 1])
                       for reason, values in by_reason.items()},
        )
    return dict(scenario=("late_chess_4_boards_8_units_21_destroyed" if variant == "attrition"
                          else "late_chess_4_boards_24_units"), workload="sequential_live_chess_actions",
                equal=initial_equal and not mismatch, mismatches=mismatch, board_count=4,
                fixture_version=5 if variant in SERIES_CHESS_FIXTURES else 4 if variant == "attrition" else 2 if variant == "standard" else 3,
                fixture_variant=variant,
                reference_kind=baseline, focus_heroes=list(late_chess_focus(variant)),
                **initial_composition,
                optimized_small_baselines=baseline == "no-small-baselines" or ai_acceleration._SMALL_BASELINES_ENABLED,
                **effective_features,
                reference_native_flat_copy=flat_features["reference"],
                reference_rng_independent_rows=independent_features["reference"],
                reference_adaptive_rows=adaptive_features["reference"],
                reference_tactical_queries=query_features["reference"],
                reference_native_row_keys=row_key_features["reference"],
                reference_native_component_scan=component_features["reference"],
                optimized_native_rollback=(baseline == "no-native" or (
                    baseline != "no-type-plans" and core._ROLLBACK_NATIVE_ENABLED and core._ROLLBACK_TYPE_PLANS_ENABLED))
                    and core._NATIVE_ROLLBACK_COPY is not None,
                initial_equal=initial_equal, initial_id_states=initial_ids,
                base_heroes_per_board=8 if variant == "attrition" else 24, initial_unit_counts=initial_counts,
                initial_destroyed_counts=initial_destroyed_counts, sweeps=sweeps, repeats=repeats,
                reference_seconds=old, optimized_seconds=new, speedup=old / new if new else None,
                timing_samples=timings, steps=samples, latency=latency, profiles=profiles,
                position_query_witnesses=query_checks,
                query_witness_replays_equivalent=(all(row["equal"] for row in query_checks) and initial_equal
                                                  and not mismatch) if witness_steps else None,
                query_witnesses_excluded_from_timing=True,
                profile_replays_equivalent=(profile_dir is not None and initial_equal and not mismatch),
                note="Synthetic late-game roster; timings exclude setup, full-state comparison, HTTP, rendering and intentional pacing; include lightweight payload capture.")


def main():
    parser = argparse.ArgumentParser(description="Compare full-AI candidates, exact scores and choice; time both implementations.")
    parser.add_argument("--team1", default="fire_funeral,bard")
    parser.add_argument("--team2", default="dark_human,ellie")
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--fast", action="store_true", help="Use the existing fast-simulation policy on BOTH sides")
    parser.add_argument("--suite", action="store_true", help="Cover each hero turn, crowded/summon fixtures, both policies and area batches")
    parser.add_argument("--late-chess", action="store_true", help="Actual 4-board chess progression with 24 units per board; --turns sets sweeps (default 4)")
    parser.add_argument("--fixture", choices=("standard", "equipped", "summons", "attrition", *SERIES_CHESS_FIXTURES), default="standard")
    parser.add_argument("--baseline", choices=("original", "before-pack", "no-type-plans", "no-native", "no-flat-copy", "no-small-baselines",
                        "no-baseline-batches", "no-rng-independent", "no-independent-adaptation", "no-tactical-queries", "no-native-row-keys", "no-component-scan", "no-native-checkpoint", "no-parallel-baselines", "no-parallel-boards",
                        "before-architecture", "previous-defaults"), default="original")
    parser.add_argument("--repeat", type=int, help="Alternating-order repetitions; default 3 for suite, otherwise 1")
    parser.add_argument("--turns", type=int, help="Hero turns per scene; suite defaults to the whole opening order")
    parser.add_argument("--profile", action="store_true", help="Collect separate, untimed call profiles for each decision sample")
    parser.add_argument("--workers", type=int, choices=range(5), help="CPU worker limit; 0 disables processes but keeps exact memoization")
    parser.add_argument("--output")
    args = parser.parse_args()
    if args.workers is not None:
        os.environ["WUJIANG_AI_WORKERS"] = str(args.workers)
    if args.late_chess and args.suite:
        parser.error("--late-chess and --suite are separate workloads")
    if not args.late_chess and (args.fixture != "standard" or args.baseline != "original"):
        parser.error("--fixture and --baseline require --late-chess")
    if args.baseline == "no-native":
        if core._NATIVE_ROLLBACK_COPY is None:
            parser.error("Native module unavailable; run python tools/build_ai_native.py --bootstrap")
        os.environ["WUJIANG_AI_NATIVE_ROLLBACK"] = "1"
        core._ROLLBACK_NATIVE_ENABLED = True
    repeats = args.repeat if args.repeat is not None else (3 if args.suite else 1)
    if repeats < 1 or (args.turns is not None and args.turns < 1):
        parser.error("--repeat and --turns must be positive")
    results = []
    mode_label = "late-chess" if args.late_chess else "suite-fast" if args.suite and args.fast else "suite" if args.suite else "fast" if args.fast else "normal"
    if args.workers is not None:
        mode_label += f"-w{args.workers}"
    if args.fixture != "standard" or args.baseline != "original":
        mode_label += f"-{args.fixture}-{args.baseline}"
    target = Path(args.output or f"reports/ai-equivalence-v3-{mode_label}-{args.seed}.json")
    if args.output is None:
        original, suffix = target, 2
        while target.exists():
            target = original.with_name(f"{original.stem}-{suffix}{original.suffix}")
            suffix += 1
    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"Report: {target}", flush=True)

    if args.late_chess:
        print("late chess: comparing sequential actions on all four boards...", flush=True)
        progress_path = target.with_suffix(".progress.json")
        def progress(status):
            progress_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
            if status["phase"] == "deciding":
                print(f"  {status['mode']} step={status['step']} board={status['board']} actor={status['actor']} units={status['unit_count']}", flush=True)
        result = compare_late_chess(seed=args.seed, sweeps=args.turns or 4, repeats=repeats, progress=progress,
                                    variant=args.fixture, baseline=args.baseline,
                                    profile_dir=target.with_suffix("") / "profiles" if args.profile else None)
        result["build"] = build_info()
        target.write_text(json.dumps([result], ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"late chess: equal={result['equal']} reference={result['reference_seconds']:.3f}s optimized={result['optimized_seconds']:.3f}s", flush=True)
        latency = result["latency"]["optimized"]
        print(f"optimized steps: median={latency['step_median']:.3f}s p95={latency['step_p95']:.3f}s max={latency['step_max']:.3f}s", flush=True)
        return 0 if result["equal"] else 1

    def save(result):
        result["build"] = build_info()
        results.append(result)
        target.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{result['scenario']}: equal={result['equal']} reference={result['reference_seconds']:.3f}s optimized={result['optimized_seconds']:.3f}s", flush=True)
        metrics = [stage.get("acceleration", {}) for stage in result.get("stages", {}).get("optimized", [])]
        if metrics:
            print("  acceleration (median per selection): " + ", ".join(
                f"{name}={median(metric.get(name, 0) for metric in metrics):g}"
                for name in ("hits", "misses", "move_hits", "move_misses", "baseline_hits", "baseline_misses",
                             "move_batch_hits", "move_batch_misses", "move_batch_queries_reused", "move_batch_impure",
                             "baseline_units_reused", "baseline_impure",
                             "snapshots", "snapshot_seconds", "worker_jobs", "worker_values", "impure", "unsupported", "worker_failures",
                             "worker_roundtrip_rejected", "worker_impure", "worker_errors")
            ), flush=True)

    cases = [("custom", args.team1.split(","), args.team2.split(","), 0)]
    if args.suite:
        cases.extend([
            ("crowded", ["fire_funeral", "bard", "dark_human", "elite_soldier", "chanter", "rock_god"],
             ["ellie", "blood_eater", "element_hunter", "masamune", "jade", "n"], 0),
            ("summons", ["dark_human", "bard"], ["dark_human", "ellie"], 8),
            ("attrition", ["excel_r127", "bard", "dark_human", "elite_soldier"],
             ["ellie", "blood_eater", "jade", "n"], 0),
        ])
    for fast in ((False, True) if args.suite and not args.fast else (args.fast,)):
        for case, team1, team2, summons in cases:
            random.seed(args.seed)
            battle = create_battle(team1, team2)
            battle.fast_ai_simulation = fast
            if summons:
                add_density_summons(battle, summons)
            if case == "attrition":
                add_attrition_history(battle)
            turns = args.turns or (len(battle.turn_order_unit_ids) if args.suite else 1)
            for scene in ("opening", "contact"):
                if scene == "contact":
                    pack_contact(battle)
                for turn in range(turns):
                    if battle.winner is not None:
                        break
                    label = f"{'fast' if fast else 'normal'}-{case}-{scene}-turn{turn + 1}"
                    actor = battle.current_turn_unit()
                    print(f"{label}: actor={actor.hero_code if actor else None} units={len(battle.units)} comparing...", flush=True)
                    result = compare_turn(battle, repeats=repeats,
                                          profile_dir=target.with_suffix("") / label if args.profile else None)
                    result.update(scenario=label, fast=fast, workload="full_turn_candidates_and_choice")
                    save(result)
                    if not result["equal"]:
                        return 1
                    if args.suite or turn + 1 < turns:
                        advance_sample_turn(battle)
    if args.suite:
        print("area_batch_48_units: comparing read-only area algorithms...", flush=True)
        save(compare_area_batch(repeats=repeats))
    return 0 if all(result["equal"] for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
