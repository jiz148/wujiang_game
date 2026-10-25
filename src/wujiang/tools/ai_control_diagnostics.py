"""Offline control-reselection diagnosis; no game imports until explicitly run."""
from __future__ import annotations

import argparse
import cProfile
from datetime import datetime
import json
import hashlib
from pathlib import Path
import pstats
import random
import struct
from statistics import median
import time


def create_control_fixture(*, seed, casts, destroyed, fast):
    """Four live units, legally prepared eagle-eye casts; not a saved match."""
    from wujiang.tactical.engine.core import Position
    from wujiang.tactical.heroes.registry import create_battle
    from wujiang.tools.ai_equivalence import add_attrition_history, advance_sample_turn

    random.seed(seed)
    battle = create_battle(["excel_r264", "elite_soldier"], ["elite_soldier", "elite_soldier"])
    battle.width = battle.height = 10
    actor = next(unit for unit in battle.player_units(1) if unit.hero_code == "excel_r264")
    others = [unit for unit in battle.player_units(1) if unit is not actor] + list(battle.player_units(2))
    actor.position = Position(4, 4)
    for unit, position in zip(others, (Position(3, 4), Position(5, 4), Position(6, 5))):
        unit.position = position
    for _ in range(len(battle.turn_order_unit_ids) * 2):
        if battle.current_turn_unit() is actor:
            break
        advance_sample_turn(battle)
    else:
        raise RuntimeError("Cannot prepare the control caster's legal turn")
    battle.fast_ai_simulation = fast
    if destroyed:
        add_attrition_history(battle, destroyed)
    skill = actor.get_skill("eagle_eye")
    for _ in range(casts):
        patterns = [cells for cells in skill.patterns(battle, actor)
                    if actor.position not in cells
                    and any(unit.player_id != actor.player_id for unit in battle.effect_units_at_cells(cells))]
        if not patterns:
            raise RuntimeError("No legal non-self control area")
        # Preparation follows the source pattern order on tied coverage. It
        # does not claim the full AI would choose this scripted setup action.
        cells = max(patterns, key=lambda cells: sum(unit.player_id != actor.player_id
                    for unit in battle.effect_units_at_cells(cells)))
        battle.perform_action({"type": "skill", "unit_id": actor.unit_id, "skill_code": "eagle_eye",
                               "cells": [cell.to_dict() for cell in cells]})
        for _ in range(128):
            if battle.pending_chain is None:
                break
            battle.perform_action({"type": "chain_skip"})
        else:
            raise RuntimeError("Control setup reaction chain did not settle")
        if battle.current_turn_unit() is not actor or battle.winner is not None:
            raise RuntimeError("Control setup unexpectedly changed the action window")
    if skill.uses_this_turn != casts or not actor.can_take_turn_actions(battle):
        raise RuntimeError("Control setup did not preserve paid/free cast history")
    return battle, actor


def diagnose_control(output, *, repeats=1, policies=(False, True), histories=(0, 21), cast_counts=(0, 1, 2), seed=109):
    from wujiang.platform.ai_build import build_info
    from wujiang.tactical.rooms import ai, ai_acceleration as acceleration
    from wujiang.tools.ai_equivalence import isolated_id_stream

    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    result = {"workload": "control_reselection_diagnostic", "build": build_info(), "complete": False,
              "seed": seed, "repeats": repeats, "scenes": [], "not_a_speed_benchmark": True,
              "note": "Synthetic legal setup, four live units; no live room. Profile replays excluded from timing; no optimization speedup inferred."}
    original_rng = random.getstate()
    progress = output / "progress.json"
    report = output / "diagnosis.json"
    tracked = {"r21_control_score", "r21_unit_action_opportunities", "r21_winged_contact_value",
               "reviewed_r18_effect_score", "battle_state_rollback", "reachable_positions", "build_queued_action",
               "probe_attack_damage_impact", "_snapshot", "_independent_row_snapshot", "iter_components",
               "skill_payload_has_effective_enemy_impact", "build_skill_candidates", "build_move_candidates",
               "turn_action_candidates", "score_skill_payload", "score_move_destination",
               "skill_payloads_for_action", "r21_winged_leap_score", "best_move_destination_score"}
    try:
        # Warm metadata/setup under disposable IDs before either observation.
        with isolated_id_stream():
            create_control_fixture(seed=seed, casts=0, destroyed=0, fast=True)
        for fast in policies:
            for destroyed in histories:
                for casts in cast_counts:
                    label = f"{'chess' if fast else 'normal'}-live4-dead{destroyed}-casts{casts}"
                    print(f"Control diagnosis: {label}", flush=True)
                    timings, cpu_timings, expected = [], [], None
                    scene = {"scene": label, "fast": fast, "live_units": 4, "destroyed_units": destroyed,
                             "prior_eagle_eye_casts": casts, "equal_replays": True}
                    for repeat in range(repeats + 1):
                        profiled = repeat == repeats
                        progress.write_text(json.dumps({"scene": label, "repeat": repeat + 1,
                            "profiled": profiled, "phase": "deciding"}, indent=2), encoding="utf-8")
                        with isolated_id_stream():
                            battle, actor = create_control_fixture(seed=seed, casts=casts, destroyed=destroyed, fast=fast)
                            if len(battle.units) != 4 or len(battle.destroyed_units) != destroyed:
                                raise RuntimeError("Control setup has unexpected live/dead unit counts")
                            before_ids = acceleration._id_state()
                            before = acceleration._snapshot(battle)
                            if before is None or before_ids is None:
                                raise RuntimeError("Control diagnosis requires complete state and ID snapshots")
                            initial = before, before_ids
                            profiler = cProfile.Profile() if profiled else None
                            started, cpu_started = time.perf_counter(), time.process_time()
                            if profiler is None:
                                payload, chosen = ai.choose_turn_bundle_action(battle, battle.current_turn_bundle_units(include_banished=False), "standard")
                            else:
                                with profiler:
                                    payload, chosen = ai.choose_turn_bundle_action(battle, battle.current_turn_bundle_units(include_banished=False), "standard")
                            elapsed, cpu_elapsed = time.perf_counter() - started, time.process_time() - cpu_started
                            after_ids, after = acceleration._id_state(), acceleration._snapshot(battle)
                            if after is None or after_ids is None:
                                raise RuntimeError("Control diagnosis requires complete state and ID snapshots")
                            capture = (initial, payload, chosen.unit_id if chosen else None, after_ids, after)
                            if expected is None:
                                expected = capture
                            elif capture != expected:
                                scene["equal_replays"] = False
                            scene.update(selected_payload=payload, actor=chosen.unit_id if chosen else None,
                                component_id_delta=after_ids[0] - before_ids[0], unit_id_delta=after_ids[1] - before_ids[1],
                                acceleration=acceleration.last_statistics(), actor_mana=actor.current_mana,
                                controlled_units=sum(unit.has_status("鹰眼") for unit in battle.all_units()))
                            if not profiled:
                                timings.append(elapsed)
                                cpu_timings.append(cpu_elapsed)
                            else:
                                path = output / (label + ".prof")
                                profiler.dump_stats(str(path))
                                stats = pstats.Stats(profiler)
                                def row(name, values):
                                    return {"function": f"{name[0]}:{name[1]}:{name[2]}", "calls": values[1],
                                            "self_seconds": values[2], "cumulative_seconds": values[3]}
                                scene.update(profile_path=str(path), profiled_seconds=elapsed,
                                    profile_excluded_from_timings=True,
                                    functions=[row(name, values) for name, values in stats.stats.items() if name[2] in tracked],
                                    top_cumulative=[row(name, values) for name, values in sorted(
                                        stats.stats.items(), key=lambda item: item[1][3], reverse=True)[:40]])
                    scene.update(seconds=timings, median_seconds=median(timings), cpu_seconds=cpu_timings)
                    result["scenes"].append(scene)
                    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                    if not scene["equal_replays"]:
                        raise RuntimeError("Control profiling replay changed the observed decision/state/RNG/IDs")
        result["complete"] = True
    except Exception as error:
        result["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        acceleration.shutdown_workers(terminate=True)
        random.setstate(original_rng)
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def _exact_candidate_tree(value):
    """Ordered payloads and exact score types/bits, without pickle interning."""
    if type(value) is float:
        return "float", struct.pack("!d", value).hex()
    if type(value) in (type(None), bool, int, str):
        return type(value).__name__, value
    if type(value) in (list, tuple):
        return type(value).__name__, tuple(_exact_candidate_tree(item) for item in value)
    if type(value) is dict:
        return "dict", tuple((_exact_candidate_tree(key), _exact_candidate_tree(item)) for key, item in value.items())
    raise TypeError("Control candidate witness requires plain ordered values")


def compare_control_queries(output, *, repeats=3, policies=(False, True), histories=(0, 21), cast_counts=(0, 1, 2), seed=109):
    from unittest.mock import patch
    from wujiang.platform.ai_build import build_info
    from wujiang.tactical.rooms import ai, ai_acceleration as acceleration
    from wujiang.tools.ai_equivalence import capture_turn, isolated_id_stream

    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    result = {"workload": "control_query_ablation", "build": build_info(), "complete": False,
              "seed": seed, "repeats": repeats, "scenes": [], "candidate_witnesses_excluded_from_timing": True,
              "reference_tactical_queries": False, "optimized_tactical_queries": True}
    report, progress = output / "comparison.json", output / "progress.json"
    original_rng = random.getstate()
    def snapshot(battle):
        state, ids = acceleration._snapshot(battle), acceleration._id_state()
        if state is None or ids is None:
            raise RuntimeError("Control comparison requires complete state and ID snapshots")
        return state, ids
    try:
        with isolated_id_stream():
            create_control_fixture(seed=seed, casts=0, destroyed=0, fast=True)
        for fast in policies:
            for destroyed in histories:
                for casts in cast_counts:
                    label = f"{'chess' if fast else 'normal'}-live4-dead{destroyed}-casts{casts}"
                    print(f"Control OFF/ON: {label}", flush=True)
                    scene = dict(scene=label, fast=fast, live_units=4, destroyed_units=destroyed,
                                 prior_eagle_eye_casts=casts, equal=True, candidates_equal=True,
                                 seconds={"reference": [], "optimized": []}, acceleration={"reference": [], "optimized": []})
                    expected = None
                    for repeat in range(repeats):
                        for mode in (("reference", "optimized") if repeat % 2 == 0 else ("optimized", "reference")):
                            progress.write_text(json.dumps(dict(scene=label, mode=mode, repeat=repeat+1, phase="timing")), encoding="utf-8")
                            with isolated_id_stream(), patch.object(acceleration, "_TACTICAL_QUERIES_ENABLED", mode == "optimized"):
                                battle, actor = create_control_fixture(seed=seed, casts=casts, destroyed=destroyed, fast=fast)
                                if len(battle.units) != 4 or len(battle.destroyed_units) != destroyed:
                                    raise RuntimeError("Control comparison has unexpected unit counts")
                                initial = snapshot(battle)
                                started = time.perf_counter()
                                payload, chosen = ai.choose_turn_bundle_action(battle, battle.current_turn_bundle_units(include_banished=False), "standard")
                                seconds = time.perf_counter() - started
                                capture = initial, payload, chosen.unit_id if chosen else None, snapshot(battle)
                                if expected is None:
                                    expected = capture
                                scene["equal"] &= capture == expected
                                scene["seconds"][mode].append(seconds)
                                scene["acceleration"][mode].append(acceleration.last_statistics())
                    expected_candidates = None
                    for mode in ("reference", "optimized"):
                        progress.write_text(json.dumps(dict(scene=label, mode=mode, phase="untimed_candidates")), encoding="utf-8")
                        with isolated_id_stream(), patch.object(acceleration, "_TACTICAL_QUERIES_ENABLED", mode == "optimized"):
                            battle, _ = create_control_fixture(seed=seed, casts=casts, destroyed=destroyed, fast=fast)
                            initial = snapshot(battle)
                            capture, _ = capture_turn(battle)
                            candidate_count = sum(len(group) for _, groups in capture["candidates"] for group in groups)
                            if candidate_count < 1:
                                raise RuntimeError("Control comparison has no exact candidate witness")
                            witness = initial, _exact_candidate_tree(capture), snapshot(battle)
                            if expected_candidates is None:
                                expected_candidates = witness
                            scene["candidates_equal"] &= witness == expected_candidates
                            scene[mode + "_candidate_count"] = candidate_count
                    scene["candidate_digest"] = hashlib.sha256(repr(expected_candidates[1]).encode("utf-8")).hexdigest()
                    scene["median_seconds"] = {mode: median(values) for mode, values in scene["seconds"].items()}
                    before, after = scene["median_seconds"]["reference"], scene["median_seconds"]["optimized"]
                    scene["improvement_percent"] = 100 * (before - after) / before if before else None
                    scene["query_hits"] = sum(row.get("tactical_query_hits", 0) for row in scene["acceleration"]["optimized"])
                    result["scenes"].append(scene)
                    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                    if not scene["equal"] or not scene["candidates_equal"]:
                        raise RuntimeError("Tactical queries changed a control decision, candidate score, state, RNG or IDs")
        result["complete"] = True
    except Exception as error:
        result["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        acceleration.shutdown_workers(terminate=True)
        random.setstate(original_rng)
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main():
    from wujiang.tools.ai_validation import validation_environment
    import os
    parser = argparse.ArgumentParser(description="Locate pauses while the full AI reselects after eagle-eye control")
    parser.add_argument("--output-dir")
    parser.add_argument("--repeats", type=int)
    parser.add_argument("--compare-queries", action="store_true", help="Alternating OFF/ON timings and separate exact candidate witnesses")
    parser.add_argument("--policy", choices=("both", "normal", "chess"), default="both")
    args = parser.parse_args()
    repeats = args.repeats if args.repeats is not None else (3 if args.compare_queries else 1)
    if repeats < 1:
        parser.error("repeats must be positive")
    root = Path(__file__).resolve().parents[3]
    output = Path(args.output_dir) if args.output_dir else root / "reports" / (
        "full-ai-control-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    if not output.is_absolute():
        output = root / output
    output.mkdir(parents=True, exist_ok=False)
    os.environ.update({key: value for key, value in validation_environment(4).items() if key.startswith("WUJIANG_AI_")})
    print(f"Control diagnosis directory: {output}", flush=True)
    runner = compare_control_queries if args.compare_queries else diagnose_control
    runner(output, repeats=repeats,
        policies=(False, True) if args.policy == "both" else (args.policy == "chess",))
    print(f"Complete: {output / ('comparison.json' if args.compare_queries else 'diagnosis.json')}", flush=True)
    return 0
