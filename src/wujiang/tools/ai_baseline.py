"""User-run performance baseline for the new expected/visible full-AI rules.

No behavior suite and no legacy/new decision-equivalence gate. Each measured
request reloads the SAME saved battle/arguments/RNG/IDs; a changed action never
changes the next timed input. Profiles are separate, untimed replays.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
import cProfile
from datetime import datetime
import gzip
import hashlib
import json
import os
from pathlib import Path
import pickle
import random
import shutil
from statistics import median
import sys
import time
import traceback
import zipfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
CASES = (("standard", 17, 48), ("equipped", 37, 64), ("summons", 53, 64),
         ("hell-heavy", 83, 128), ("electronic-heavy", 97, 200))
KINDS = ("choose_turn_bundle_action", "choose_instant_action", "choose_chain_reaction",
         "choose_respawn_action", "choose_damage_choice_action")


@contextmanager
def capture_room_choosers(capture):
    # GameRoom imports these functions by name. Replacing ai_router's
    # attributes afterwards cannot intercept those already-bound room calls.
    from wujiang.tactical.rooms import multiplayer

    with ExitStack() as stack:
        for name in KINDS:
            stack.enter_context(patch.object(multiplayer, name, capture(name)))
        yield


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def percentile(values, fraction):
    values = sorted(values)
    return values[min(len(values) - 1, max(0, int((len(values) - 1) * fraction + 0.5)))] if values else None


def summarize(runs):
    totals = [sum(row["seconds"] for row in run) for run in runs]
    all_rows = [row for run in runs for row in run]
    main = [row["seconds"] for row in all_rows if row["kind"] == "choose_turn_bundle_action"]
    sweeps = []
    for run in runs:
        groups = {}
        for row in run:
            group = (row["step"] - 1) // 4
            groups.setdefault(group, []).append(row)
        sweeps.extend(sum(row["seconds"] for row in group) for group in groups.values()
                      if {row["board"] for row in group} == {1, 2, 3, 4}
                      and all(row["active_boards"] == 4 for row in group))
    return {"run_total_seconds": totals, "median_total_seconds": median(totals),
            "requests_per_run": [len(run) for run in runs],
            "request_p95": percentile([row["seconds"] for row in all_rows], .95),
            "request_max": max((row["seconds"] for row in all_rows), default=None),
            "main_action_p95": percentile(main, .95), "main_action_max": max(main, default=None),
            "four_board_sweep_p95": percentile(sweeps, .95),
            "four_board_sweep_max": max(sweeps, default=None)}


def _payload(value):
    if isinstance(value, tuple) and len(value) == 2 and hasattr(value[1], "unit_id"):
        return {"payload": value[0], "actor": value[1].unit_id}
    return value


def dump_live_checkpoint(chess, board, rng, ids):
    # AutoChessState.__getstate__ intentionally strips runners. Preserve their
    # explicit graph here, without changing the production persistence format.
    return pickle.dumps((chess, [match.runner for match in chess.matches], board, rng, ids),
                        protocol=pickle.HIGHEST_PROTOCOL)


def load_live_checkpoint(data):
    chess, runners, board, rng, ids = pickle.loads(data)
    if len(chess.matches) != len(runners):
        raise ValueError("Live baseline checkpoint lost a battle runner")
    for match, runner in zip(chess.matches, runners):
        match.runner = runner
    return chess, board, rng, ids


def recorded_new_requests(directory):
    rows = {}
    path = directory / "decisions.jsonl"
    if path.is_file():
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue  # A terminated child may leave a final partial line.
                if row.get("mode") == "new":
                    rows.setdefault(row["request"], []).append(row)
    return rows


def latest_prediction_reference(directory, index):
    """Choose the latest recorded timings per identical compressed checkpoint.

    Replaying an older full input pack must not label the *first* expectation
    implementation as the immediately preceding repair. Partial runs remain
    useful, with their actual repeat count and per-request provenance.
    """
    expected = {row["request"]: row.get("sha256") for row in index}
    selected = {}
    for candidate in sorted((ROOT / "reports").glob("full-ai-baseline-*"), reverse=True):
        if candidate.resolve() == directory.parent.resolve():
            continue
        try:
            summary = json.loads((candidate / "summary.json").read_text(encoding="utf-8"))
            rows = recorded_new_requests(candidate / directory.name)
        except (OSError, ValueError, KeyError):
            continue
        for number, values in rows.items():
            if number in selected or not expected.get(number):
                continue
            matching = [row for row in values if row.get("sha256") == expected[number]]
            if matching:
                selected[number] = [{**row, "reference_build": summary.get("build", {}).get("id"),
                                     "reference_directory": str(candidate)} for row in matching]
    return selected


def focused_requests(index, previous):
    """Cover four boards, prior worst requests, and known dense tail steps."""
    costs = {number: median(row["seconds"] for row in rows) for number, rows in previous.items()}
    selected = []
    def add(record):
        if record is not None and record not in selected and len(selected) < 12:
            selected.append(record)
    for board in range(1, 5):
        records = [row for row in index if row["board"] == board]
        if records:
            add(max(records, key=lambda row: costs.get(row["request"], 0)))
    for record in sorted(index, key=lambda row: costs.get(row["request"], 0), reverse=True)[:3]:
        add(record)
    for step in (117, 137, 196):
        for record in index:
            if record["step"] == step:
                add(record)
    for board in range(1, 5):
        dense = [row for row in index if row["board"] == board and row["units"] >= 20]
        if dense:
            add(dense[-1])
    return sorted(selected, key=lambda row: row["request"])


def historical_expected_comparison(previous, current):
    """Same saved inputs, separately recorded runs; no equivalence gate."""
    comparable = []
    for number in sorted({row["request"] for run in current for row in run} & previous.keys()):
        old = previous[number]
        new = [row for run in current for row in run if row["request"] == number]
        if new[0].get("sha256") is not None:
            old = [row for row in old if row.get("sha256") == new[0]["sha256"]]
        if not old:
            continue
        comparable.append({"request": number, "prior_repeats": len(old), "current_repeats": len(new),
            "prior_seconds": median(row["seconds"] for row in old),
            "current_seconds": median(row["seconds"] for row in new),
            "choice_changed": old[0]["choice"] != new[0]["choice"],
            "reference_build": old[0].get("reference_build"),
            "reference_directory": old[0].get("reference_directory")})
    old = sum(row["prior_seconds"] for row in comparable)
    new = sum(row["current_seconds"] for row in comparable)
    return {"scope": "historical_expected_same_inputs_not_contemporaneous_AB", "requests": comparable,
            "prior_sum_of_request_medians": old, "current_sum_of_request_medians": new,
            "time_reduction_percent": (old - new) / old * 100 if old else None}


def run_fixture(directory, variant, seed, steps, repeats, *, replay_from=None, focused=False):
    from wujiang.platform.ai_build import build_info
    from wujiang.tactical.prediction import RULES_VERSION, prediction_rules, prediction_observer
    from wujiang.platform.id_sequence import IdSequence
    from wujiang.tactical.engine import core
    from wujiang.tactical.heroes import base
    from wujiang.tactical.rooms import ai, ai_router, ai_acceleration, ai_parallel
    from wujiang.tools.ai_equivalence import create_late_chess_fixture, isolated_id_stream, late_chess_composition
    from wujiang.tactical.rooms.multiplayer import GameRoom
    from wujiang.tools.ai_validation import _rss_bytes

    directory.mkdir(parents=True, exist_ok=True)
    checkpoints = directory / "checkpoints"
    checkpoints.mkdir(exist_ok=True)
    progress = directory / "progress.json"
    index = []
    current = {}
    chooser = {name: getattr(ai_router, name) for name in KINDS}
    original_rng = random.getstate()
    report = {"scope": "focused_prediction_performance_repair" if focused else "performance_only_new_rules_baseline", "rules": RULES_VERSION,
              "build": build_info(), "fixture": variant, "seed": seed, "requested_steps": steps,
              "runtime": {"python": sys.version, "hash_seed": os.environ.get("PYTHONHASHSEED"),
                          "cpu_count": os.cpu_count(), "process_id": os.getpid(),
                          "cpu_scope": "measuring_process; worker CPU not included"},
              "repeats": repeats, "decision_equivalence_required": False,
              "timing_workload": "identical_saved_AI_requests_excluding_restore_and_logging",
              "legacy_reference": "legacy_policy_adapter_in_same_build; includes wrapper bypass overhead",
              "profiles_excluded_from_timing": True, "focused_repair_check": focused, "status": "running"}
    write(directory / "result.json", report)

    def capture(name):
        def wrapped(battle, *args, **kwargs):
            # Consume an iterable once, preserving the real chooser contract.
            if name in {"choose_turn_bundle_action", "choose_instant_action"} and args:
                args = (list(args[0]), *args[1:])
            number = len(index) + 1
            data = pickle.dumps((battle, args, kwargs, random.getstate(), ai_acceleration._id_state()),
                                protocol=pickle.HIGHEST_PROTOCOL)
            relative = f"checkpoints/{number:05d}.pkl.gz"
            with gzip.open(directory / relative, "wb", compresslevel=1) as handle:
                handle.write(data)
            actor = battle.current_turn_unit()
            record = {**current, "request": number, "kind": name, "checkpoint": relative,
                      "sha256": hashlib.sha256(data).hexdigest(), "units": len(battle.units),
                      "actor": actor.hero_code if actor else None}
            index.append(record)
            write(progress, {"phase": "collecting_same_inputs", **record})
            if not any(row["board"] == record["board"] for row in index[:-1]):
                print(f"Captured first AI request for board {record['board']}: {name}", flush=True)
            return chooser[name](battle, *args, **kwargs)
        return wrapped

    def invoke(record, mode, repeat, *, profile=None):
        with gzip.open(directory / record["checkpoint"], "rb") as handle:
            data = handle.read()
        if hashlib.sha256(data).hexdigest() != record["sha256"]:
            raise ValueError("Saved checkpoint checksum changed")
        battle, args, kwargs, rng, ids = pickle.loads(data)
        if ids is None:
            raise ValueError("Checkpoint has no reproducible ID origin")
        observations = []
        with isolated_id_stream(), prediction_rules(mode == "new"), prediction_observer(observations.append):
            core._id_counter, base._unit_counter = IdSequence(ids[0]), IdSequence(ids[1])
            random.setstate(rng)
            before = pickle.dumps(battle, protocol=pickle.HIGHEST_PROTOCOL)
            write(progress, {"phase": "profiling" if profile else "timing", "mode": mode,
                             "repeat": repeat, **record})
            cpu = time.process_time()
            started = time.perf_counter()
            if profile is not None:
                value = profile.runcall(chooser[record["kind"]], battle, *args, **kwargs)
            else:
                value = chooser[record["kind"]](battle, *args, **kwargs)
            elapsed = time.perf_counter() - started
            cpu = time.process_time() - cpu
            row = {**record, "mode": mode, "repeat": repeat, "seconds": elapsed, "cpu_seconds": cpu,
                   "rss_bytes": _rss_bytes(),
                   "choice": _payload(value), "acceleration": ai_acceleration.last_statistics(),
                   "prediction": observations,
                   "observations": {"battle_unchanged": pickle.dumps(battle, protocol=pickle.HIGHEST_PROTOCOL) == before,
                                    "global_rng_unchanged": random.getstate() == rng,
                                    "battle_rng_unchanged": pickle.loads(data)[0].__dict__.get("chess_rng").getstate() == battle.__dict__["chess_rng"].getstate()
                                        if "chess_rng" in battle.__dict__ else None,
                                    "ids_unchanged": ai_acceleration._id_state() == ids}}
        return row

    try:
        with ExitStack() as stack:
            stack.enter_context(patch.object(ai_router, "_picker", return_value=ai))
            stack.enter_context(patch.object(ai_parallel, "_BOARDS_ENABLED", False))
            stack.callback(ai_acceleration.shutdown_workers, terminate=True)
            with isolated_id_stream(), prediction_rules(False):
                random.seed(seed)
                # Warm the lazy catalog before collecting IDs or timed inputs.
                create_late_chess_fixture(seed, variant=variant)
            with isolated_id_stream(), prediction_rules(False):
                random.seed(seed)
                chess = create_late_chess_fixture(seed, variant=variant)
                print("Reusing saved AI inputs" if replay_from is not None else
                      f"Collecting {steps} legacy trajectory steps into fixed AI inputs", flush=True)
                report["composition"] = late_chess_composition(chess, variant)
                report["initial_unit_counts"] = [len(match.runner.battle.units) for match in chess.matches]
                if len(chess.matches) != 4 or min(report["initial_unit_counts"]) < 24:
                    raise ValueError("Performance baseline requires four dense boards")
                report["collection_capture_module"] = "wujiang.tactical.rooms.multiplayer"
                report["collection_advanced_steps"] = 0
                previous = recorded_new_requests(replay_from) if replay_from is not None else {}
                if replay_from is not None:
                    replay_from = replay_from.resolve()
                    index.extend(json.loads((replay_from / "checkpoints.json").read_text(encoding="utf-8")))
                    for record in index:
                        source = (replay_from / record["checkpoint"]).resolve()
                        target = (directory / record["checkpoint"]).resolve()
                        if not source.is_relative_to(replay_from) or not target.is_relative_to(directory.resolve()):
                            raise ValueError("Checkpoint path escaped its baseline directory")
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(source, target)
                    report["checkpoint_source"] = str(replay_from)
                    if (replay_from / "result.json").is_file():
                        report["checkpoint_source_build"] = json.loads(
                            (replay_from / "result.json").read_text(encoding="utf-8")).get("build", {}).get("id")
                    report["completed_collection_steps"] = max((row["step"] for row in index), default=0)
                with capture_room_choosers(capture):
                    for ordinal in range(0 if replay_from is not None else steps):
                        pending = [(i, match) for i, match in enumerate(chess.matches) if match.result is None]
                        if not pending:
                            break
                        board, match = pending[ordinal % len(pending)]
                        current.update(step=ordinal + 1, board=board + 1, active_boards=len(pending))
                        write(progress, {"phase": "collecting_step", **current,
                                         "requests_so_far": len(index)})
                        advanced = chess._advance_match(match)
                        report["completed_collection_steps"] = ordinal + 1
                        report["collection_advanced_steps"] += bool(advanced)
                        write(progress, {"phase": "collected_step", **current, "advanced": advanced,
                                         "requests_so_far": len(index)})
                report["collection_requests_by_board"] = {
                    str(board): sum(row["board"] == board for row in index) for board in range(1, 5)}
            if not index:
                raise ValueError("Collection produced no actual AI requests from GameRoom's bound chooser calls; "
                                 f"advanced_steps={report['collection_advanced_steps']}; see progress.json")
            write(directory / "checkpoints.json", index)
            report["checkpoint_requests"] = len(index)
            report["completed_collection_steps"] = report.get("completed_collection_steps", current.get("step", 0))
            print(f"Saved {len(index)} fixed requests", flush=True)
            if focused:
                previous.update(latest_prediction_reference(directory, index))
            measured = focused_requests(index, previous) if focused else index
            report["measured_requests"] = [row["request"] for row in measured]
            report["measured_boards"] = sorted({row["board"] for row in measured})
            print(f"Timing {len(measured)} requests across boards {report['measured_boards']}", flush=True)
            from wujiang.tactical.prediction_factors import engine_factor_support
            write(progress, {"phase": "checking_prediction_factor_bindings"})
            enumerating = os.environ.get("WUJIANG_AI_EXPECTATION_METHOD", "direct") == "enumerate"
            report["prediction_factor_engine_support"] = engine_factor_support() if enumerating else {"unused_in_direct_scoring": True}
            write(directory / "result.json", report)
            if (enumerating and focused and os.environ.get("WUJIANG_AI_EXPECTATION_FACTORS", "1") != "0"
                    and not all(report["prediction_factor_engine_support"].values())):
                raise ValueError("Recipient factor prerequisite bindings rejected; no timing was started. "
                                 "See prediction_factor_engine_support in result.json")
            # Checkpoint density is logged per request, including all known tail
            # steps. The fixed pack itself becomes the future comparison input.
            runs = {"legacy": [], "new": []}
            with (directory / "decisions.jsonl").open("w", encoding="utf-8") as log:
                for repeat in range(repeats):
                    for mode in (("legacy", "new") if repeat % 2 == 0 else ("new", "legacy")):
                        print(f"Timing {mode}, repeat {repeat + 1}/{repeats}", flush=True)
                        records = []
                        runs[mode].append(records)
                        for record in measured:
                            row = invoke(record, mode, repeat + 1)
                            records.append(row)
                            log.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
                            log.flush()
                            # Persist outside the chooser's clock. Ctrl+C or a
                            # stage timeout must not erase completed evidence.
                            report["timing_progress"] = {
                                name: {"completed_requests": sum(map(len, values)),
                                       "completed_repeats": sum(len(run) == len(measured) for run in values),
                                       "recorded_seconds": sum(item["seconds"] for run in values for item in run),
                                       "request_max": max((item["seconds"] for run in values for item in run), default=None)}
                                for name, values in runs.items()}
                            report["last_completed_request"] = {key: row[key] for key in ("mode", "repeat", "request", "seconds")}
                            write(directory / "result.json", report)
            report["timing"] = {mode: summarize(values) for mode, values in runs.items()}
            report["timing_completed"] = True
            if previous:
                report["historical_expected_comparison"] = historical_expected_comparison(previous, runs["new"])
                references = sorted({row.get("reference_build") or report.get("checkpoint_source_build")
                    for row in report["historical_expected_comparison"]["requests"]} - {None})
                report["historical_expected_comparison"]["reference_builds"] = references
                report["historical_expected_comparison"]["reference_build"] = references[0] if len(references) == 1 else None
            old, new = (report["timing"][name]["median_total_seconds"] for name in ("legacy", "new"))
            report["time_reduction_percent"] = (old - new) / old * 100 if old else None
            report["changed_choices_first_run"] = sum(
                left["choice"] != right["choice"] for left, right in zip(runs["legacy"][0], runs["new"][0]))
            report["new_repeat_choice_differences"] = sum(
                first["choice"] != later["choice"] for run in runs["new"][1:]
                for first, later in zip(runs["new"][0], run))
            report["new_observation_changes"] = {
                key: sum(row["observations"][key] is False for run in runs["new"] for row in run)
                for key in ("battle_unchanged", "global_rng_unchanged", "battle_rng_unchanged", "ids_unchanged")}
            report["new_prediction_totals"] = {}
            for run in runs["new"]:
                for row in run:
                    for observation in row["prediction"]:
                        for key, value in observation["statistics"].items():
                            if isinstance(value, (int, float)):
                                totals = report["new_prediction_totals"]
                                totals[key] = max(totals.get(key, 0), value) if key.startswith("maximum_") else totals.get(key, 0) + value
            report["profiles"] = []
            report["phase"] = "profiles_pending"
            write(directory / "result.json", report)
            profile_directory = directory / "profiles"
            profile_directory.mkdir(exist_ok=True)
            # Profile the same union of the three slowest requests in each mode.
            slow = set()
            for mode in runs:
                slow.update(row["request"] for row in sorted(runs[mode][0], key=lambda row: row["seconds"], reverse=True)[:1 if focused else 3])
            for mode in runs:
                for number in sorted(slow):
                    profiler = cProfile.Profile()
                    row = invoke(index[number - 1], mode, 0, profile=profiler)
                    path = profile_directory / f"{mode}-request-{number:05d}.prof"
                    profiler.dump_stats(str(path))
                    write(path.with_suffix(".json"), row)
                    report["profiles"].append({"mode": mode, "request": number, "path": str(path)})
                    write(directory / "result.json", report)
            if focused:
                # Do not even construct a live fixture: its turn preparation
                # can trigger automatic skills under the expected policy.
                report.update(status="completed", phase="finished", baseline_established=False,
                              ready_for_live_test=False,
                              live_new_baseline={"steps": 0, "stop_reason": "not_run_focused_repair"},
                              note="Focused performance evidence only; no behavior/equivalence or live acceptance. "
                                   "Historical comparisons use prior recordings of matching checkpoint bytes. "
                                   "Profiles and IO excluded from measured requests.")
                write(progress, {"phase": "finished", "timing_completed": True})
                write(directory / "result.json", report)
                return report
            print("Focused check skips the live trajectory" if focused else
                  "Starting separate new-policy four-board live baseline", flush=True)
            # The fixed comparison above holds inputs constant. This separate
            # new-policy trajectory records actual four-board advancement and
            # real settlement, without claiming old/new trajectories are equal.
            live_rows, slow_live = [], []
            with isolated_id_stream(), prediction_rules(True):
                random.seed(seed)
                live = create_late_chess_fixture(seed, variant=variant)
                live_started = time.perf_counter()
                with (directory / "live-new-decisions.jsonl").open("w", encoding="utf-8") as log:
                    for ordinal in range(0 if focused else steps):
                        pending = [(i, match) for i, match in enumerate(live.matches) if match.result is None]
                        if not pending or time.perf_counter() - live_started > 180:
                            break
                        board, match = pending[ordinal % len(pending)]
                        battle = match.runner.battle
                        actor = battle.current_turn_unit()
                        status = {"step": ordinal + 1, "board": board + 1, "active_boards": len(pending),
                                  "units": len(battle.units), "actor": actor.hero_code if actor else None}
                        write(progress, {"phase": "live_new_baseline", **status})
                        saved = dump_live_checkpoint(live, board, random.getstate(), ai_acceleration._id_state())
                        observations, actions = [], []
                        perform = GameRoom._perform_battle_action
                        def record_action(runner, payload, *, reason):
                            entry = {"payload": payload.copy(), "reason": reason}
                            actions.append(entry)
                            try:
                                result = perform(runner, payload, reason=reason)
                            except BaseException as exc:
                                entry.update(completed_without_error=False, error=str(exc), error_type=type(exc).__name__)
                                raise
                            entry["completed_without_error"] = True
                            return result
                        before_logs = len(battle.logs)
                        with prediction_observer(observations.append), patch.object(GameRoom, "_perform_battle_action", record_action):
                            cpu = time.process_time()
                            started = time.perf_counter()
                            advanced = live._advance_match(match)
                            elapsed = time.perf_counter() - started
                            cpu = time.process_time() - cpu
                        row = {**status, "seconds": elapsed, "cpu_seconds": cpu, "advanced": advanced,
                               "actions": actions, "prediction": observations, "rss_bytes": _rss_bytes(),
                               "battle_logs": battle.logs[before_logs:], "acceleration": ai_acceleration.last_statistics()}
                        live_rows.append(row)
                        log.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
                        log.flush()
                        slow_live.append((elapsed, ordinal + 1, saved))
                        slow_live.sort(key=lambda item: item[0], reverse=True)
                        del slow_live[3:]
                report["live_new_baseline"] = {
                    "steps": len(live_rows), "requested_steps": steps,
                    "stop_reason": "not_run_focused_repair" if focused else "step_limit" if len(live_rows) == steps else "finished" if all(m.result is not None for m in live.matches) else "time_budget",
                    "total_seconds": sum(row["seconds"] for row in live_rows),
                    "step_p95": percentile([row["seconds"] for row in live_rows], .95),
                    "step_max": max((row["seconds"] for row in live_rows), default=None),
                    "four_board_sweep_max": max((sum(r["seconds"] for r in live_rows[i:i + 4])
                        for i in range(0, len(live_rows) - 3, 4)
                        if all(r["active_boards"] == 4 for r in live_rows[i:i + 4])
                        and {r["board"] for r in live_rows[i:i + 4]} == {1, 2, 3, 4}), default=None),
                    "slow_checkpoints": []}
                for _, step, saved in slow_live:
                    path = directory / f"live-new-step-{step:03d}.pkl.gz"
                    with gzip.open(path, "wb", compresslevel=1) as handle:
                        handle.write(saved)
                    copy, board, rng, ids = load_live_checkpoint(saved)
                    with isolated_id_stream():
                        core._id_counter, base._unit_counter = IdSequence(ids[0]), IdSequence(ids[1])
                        random.setstate(rng)
                        profiler = cProfile.Profile()
                        write(progress, {"phase": "profiling_new_live", "step": step, "board": board + 1})
                        profiler.runcall(copy._advance_match, copy.matches[board])
                        profile_path = profile_directory / f"live-new-step-{step:03d}.prof"
                        profiler.dump_stats(str(profile_path))
                    report["live_new_baseline"]["slow_checkpoints"].append({
                        "step": step, "checkpoint": str(path), "profile": str(profile_path)})
            report["status"] = "completed"
            report["baseline_established"] = not focused
            report["ready_for_live_test"] = False
            report["note"] = ("Focused performance evidence only; saved input selection is not a full baseline or live acceptance. "
                              "Historical expected comparison uses prior recordings, not a contemporaneous A/B. "
                              "Profiles and checkpoint IO excluded from timed requests." if focused else
                              "Performance-only evidence; no behavior/equivalence acceptance. Fixed comparison excludes settlement; separate live-new baseline includes real advancement. Profiles and checkpoint IO excluded from measured samples.")
    except BaseException as exc:
        report["status"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "error"
        report["error"] = str(exc)
        report["error_type"] = type(exc).__name__
        report["traceback"] = traceback.format_exc()
        write(directory / "checkpoints.json", index)
        raise
    finally:
        random.setstate(original_rng)
        write(directory / "result.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Full-AI expected/visible rules: performance only, detailed baseline logs")
    parser.add_argument("--output-dir")
    parser.add_argument("--workers", type=int, choices=range(5), default=4)
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--repair-check", action="store_true", help="Replay representative slow inputs; not a full baseline")
    parser.add_argument("--replay-from", help="Existing baseline root (coordinator) or fixture directory (child)")
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--child", choices=tuple(row[0] for row in CASES))
    parser.add_argument("--seed", type=int)
    parser.add_argument("--steps", type=int)
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args(argv)
    if args.repeat < 1 or args.timeout < 1:
        parser.error("repeat and timeout must be positive")
    if args.child:
        if not args.output_dir or args.steps is None or args.seed is None:
            parser.error("Child requires output-dir, steps and seed")
        run_fixture(Path(args.output_dir), args.child, args.seed, args.steps, args.repeat,
                    replay_from=Path(args.replay_from) if args.replay_from else None, focused=args.repair_check)
        return 0
    from wujiang.tools.ai_validation import validation_environment, _run_stage
    from wujiang.platform.ai_build import build_info, AI_SOURCE_FILES
    output = Path(args.output_dir) if args.output_dir else ROOT / "reports" / (
        "full-ai-baseline-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    output.mkdir(parents=True, exist_ok=False)
    replay_root = Path(args.replay_from).resolve() if args.replay_from else None
    if args.repair_check and replay_root is None:
        for candidate in sorted((ROOT / "reports").glob("full-ai-baseline-*"), reverse=True):
            summary_path = candidate / "summary.json"
            if candidate == output or not summary_path.is_file():
                continue
            old = json.loads(summary_path.read_text(encoding="utf-8"))
            index_path = candidate / "standard/checkpoints.json"
            if not old.get("focused_repair_check") and index_path.is_file() and json.loads(index_path.read_text(encoding="utf-8")):
                replay_root = candidate
                break
    environment = validation_environment(args.workers)
    environment.update(WUJIANG_AI_TACTICAL_QUERIES="0", WUJIANG_AI_NATIVE_COMPONENT_SCAN="0",
                       WUJIANG_AI_RNG_INDEPENDENT_ROWS="1", WUJIANG_AI_NATIVE_ROW_KEYS="1",
                       WUJIANG_AI_PREDICTION_NATIVE="1", WUJIANG_AI_REQUIRE_PREDICTION_NATIVE="1",
                       WUJIANG_AI_EXPECTATION_FACTORS="1",
                       WUJIANG_AI_SCORING_RULES="expected-visible-v1")
    environment["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + environment.get("PYTHONPATH", "")
    environment["PYTHONHASHSEED"] = "0"
    os.environ.update({key: value for key, value in environment.items() if key.startswith("WUJIANG_AI_")})
    summary = {"scope": "focused_prediction_performance_repair" if args.repair_check else "performance_only_new_rules_baseline",
               "status": "running", "build": build_info(),
               "quick": args.quick, "focused_repair_check": args.repair_check,
               "replay_from": str(replay_root) if replay_root else None,
               "stage_timeout_seconds": min(args.timeout, 300) if args.repair_check else args.timeout,
               "behavior_tests_run": False, "decision_equivalence_required": False,
               "fixtures": [], "baseline_established": False, "ready_for_live_test": False}
    write(output / "summary.json", summary)
    source_names = set(AI_SOURCE_FILES)
    source_names.update(path.relative_to(ROOT).as_posix() for path in (ROOT / "src/wujiang").rglob("*.py"))
    source_names.update(("data/装备.xlsx", "data/武将yoo.xlsx", "data/特殊规则.xlsx"))
    with zipfile.ZipFile(output / "baseline-sources.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(source_names):
            if (ROOT / name).is_file():
                archive.write(ROOT / name, name)
    print(f"Performance baseline directory: {output}", flush=True)
    try:
        if os.environ.get("WUJIANG_AI_EXPECTATION_METHOD", "direct") == "enumerate":
            print("Building independent expectation-preparation kernel", flush=True)
            _run_stage([sys.executable, str(ROOT / "tools/build_ai_prediction_native.py"), "--bootstrap"],
                       output / "prediction-native-build.log", timeout=args.timeout, environment=environment)
            from wujiang.platform.ai_prediction_native import native_status as prediction_native_status
            prediction_native_status.cache_clear()
            if not prediction_native_status()["available"]:
                raise ValueError("Expectation preparation build did not produce a matching artifact")
        summary["build"] = build_info()
        write(output / "summary.json", summary)
        # The user's priority is four crowded Hell/Electronic boards. Do not
        # leave both until after all ordinary fixtures when a run is stopped.
        cases = sorted(CASES, key=lambda row: (row[0] != "electronic-heavy", row[0] != "hell-heavy")) if args.repair_check else CASES
        for fixture, seed, steps in cases:
            target = output / fixture
            target.mkdir()
            record = {"fixture": fixture, "status": "running", "log": str(output / (fixture + ".log"))}
            summary["fixtures"].append(record)
            write(output / "summary.json", summary)
            command = [sys.executable, "-m", "wujiang.tools.ai_baseline", "--child", fixture,
                       "--output-dir", str(target), "--seed", str(seed), "--steps", str(min(steps, 8) if args.quick else steps),
                       "--repeat", str(1 if args.quick else args.repeat)]
            if args.repair_check:
                command.append("--repair-check")
            if replay_root is not None and (replay_root / fixture / "checkpoints.json").is_file():
                source_index = json.loads((replay_root / fixture / "checkpoints.json").read_text(encoding="utf-8"))
                if source_index:
                    command.extend(("--replay-from", str(replay_root / fixture)))
            print(f"Starting performance-only {fixture}", flush=True)
            try:
                _run_stage(command, Path(record["log"]), timeout=summary["stage_timeout_seconds"], environment=environment)
                result = json.loads((target / "result.json").read_text(encoding="utf-8"))
                if result.get("status") != "completed" or result["build"]["id"] != summary["build"]["id"]:
                    raise ValueError("Incomplete baseline or changed source identity")
                record.update(status="completed", report=str(target / "result.json"),
                              time_reduction_percent=result["time_reduction_percent"], timing=result["timing"],
                              new_observation_changes=result["new_observation_changes"])
                if "historical_expected_comparison" in result:
                    record["historical_expected_comparison"] = result["historical_expected_comparison"]
                print(f"{fixture}: time reduction against legacy {result['time_reduction_percent']:.2f}%", flush=True)
            except Exception as exc:
                rows = [row for values in recorded_new_requests(target).values() for row in values]
                record.update(status="timeout" if isinstance(exc, TimeoutError) else "error", error=str(exc),
                              partial_new_requests=len(rows),
                              partial_new_request_max=max((row["seconds"] for row in rows), default=None))
                if (target / "progress.json").is_file():
                    record["last_progress"] = json.loads((target / "progress.json").read_text(encoding="utf-8"))
                if (target / "result.json").is_file():
                    partial = json.loads((target / "result.json").read_text(encoding="utf-8"))
                    for key in ("timing_completed", "timing_progress", "timing", "time_reduction_percent", "phase"):
                        if key in partial:
                            record[key] = partial[key]
                write(target / "stage-failure.json", record)
                print(f"{fixture}: {exc}; continuing with the other fixtures", flush=True)
            write(output / "summary.json", summary)
        summary["status"] = "completed" if all(row["status"] == "completed" for row in summary["fixtures"]) else "error"
        summary["baseline_established"] = summary["status"] == "completed" and not args.quick and not args.repair_check
    except (Exception, KeyboardInterrupt) as exc:
        summary["status"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "error"
        summary["error"] = str(exc)
        if summary["fixtures"]:
            summary["fixtures"][-1]["status"] = summary["status"]
        print(str(exc), flush=True)
        return 1
    finally:
        write(output / "summary.json", summary)
        print(f"Summary: {output / 'summary.json'}", flush=True)
    return 0 if summary["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
