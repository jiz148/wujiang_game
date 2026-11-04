"""One user-run entry point for exact checks, ablations and bounded stress.

The coordinator imports no battle code. Each stage runs in a fresh process;
production settings and source files are never changed by the experiments.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import traceback

from wujiang.platform.ai_build import build_info
from wujiang.tools.ai_scenarios import (
    SERIES_CHESS_FIXTURES, SERIES_MINIMUM_NUMERATOR, SERIES_MINIMUM_DENOMINATOR,
)


ROOT = Path(__file__).resolve().parents[3]


def _write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def check_series_composition(variant, counts, data):
    definition = SERIES_CHESS_FIXTURES.get(variant)
    if definition is None:
        return
    boards = data.get("initial_rosters_by_side")
    if (data.get("fixture_series") != definition["label"]
            or type(counts) is not list or len(counts) != 4
            or type(boards) is not list or len(boards) != 4):
        raise ValueError("Missing four-board majority-series composition")
    members = set(definition["members"])
    for count, sides in zip(counts, boards):
        if (type(sides) is not list or len(sides) != 2
                or any(type(side) is not list or len(side) < 12
                       or any(type(code) is not str for code in side) for side in sides)
                or sum(len(side) for side in sides) != count):
            raise ValueError("Series fixture requires at least 12 actual units per side")
        for side in sides:
            selected = [code for code in side if code in members]
            if (len(selected) * SERIES_MINIMUM_DENOMINATOR < len(side) * SERIES_MINIMUM_NUMERATOR
                    or len(set(selected)) < 4):
                raise ValueError("Series fixture requires a diverse 75-percent majority on each side")


def check_equivalence_report(path):
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not rows:
        raise ValueError("Missing equivalence samples")
    for row in rows:
        if row.get("equal") is not True or row.get("mismatches"):
            raise ValueError(f"Non-equivalent sample: {row.get('scenario')}")
        if row.get("workload") not in ("full_turn_candidates_and_choice", "readonly_area_batch", "sequential_live_chess_actions"):
            raise ValueError("Missing equivalence workload")
        if row.get("workload") != "sequential_live_chess_actions" and (
                "reference" not in row or row.get("reference") != row.get("optimized")):
            raise ValueError("Reported equal flag disagrees with stored outputs")
        if row.get("workload") == "sequential_live_chess_actions":
            if row.get("initial_equal") is not True or row.get("board_count") != 4:
                raise ValueError("Late fixture initial state or board count failed")
            counts = row.get("initial_unit_counts", [])
            if row.get("fixture_variant") == "attrition":
                if counts != [8] * 4 or row.get("initial_destroyed_counts") != [21] * 4:
                    raise ValueError("Attrition fixture requires eight live and 21 destroyed bodies per board")
            elif len(counts) != 4 or min(counts) < 24:
                raise ValueError("Late fixture does not exercise four dense boards")
            check_series_composition(row.get("fixture_variant"), counts, row)
            runs = row.get("steps", {})
            if not runs.get("reference") or not runs.get("optimized") or not runs["reference"][0]:
                raise ValueError("Missing actual late chess steps")
            actions = [step["actions"] for step in runs["reference"][0]]
            if any([step["actions"] for step in run] != actions for group in runs.values() for run in group):
                raise ValueError("Stored late chess actions disagree")
    return rows


def behavior_log_summary(path):
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    matches = re.findall(r"Ran (\d+) tests? in", text)
    ran = int(matches[-1]) if matches else 0
    endings = re.findall(r"^(OK|FAILED)(?:\s*\(([^\n]*)\))?\s*$", text, re.MULTILINE)
    ending, details = endings[-1] if endings else (None, "")
    counts = {name: int(value) for name, value in re.findall(r"(failures|errors|skipped)=(\d+)", details)}
    failures, errors, skipped = (counts.get(name, 0) for name in ("failures", "errors", "skipped"))
    passed = max(0, ran - failures - errors - skipped) if ending is not None else None
    if ending == "FAILED":
        headers = re.findall(r"^(?:FAIL|ERROR): ([^\n]*)$", text, re.MULTILINE)
        if headers:
            cases = [re.fullmatch(r"\S+ \(([^()\n]+)\)(?: .*)?", header) for header in headers]
            # A parent test may report both a failing subtest and a failure
            # after it. Unittest counts those events separately but Ran counts
            # their parent only once. Retain event counts; deduplicate parents.
            if len(headers) == failures + errors and all(cases) and not skipped:
                passed = max(0, ran - len({case[1] for case in cases}))
            else:
                passed = None  # Partial details or skipped subtests are ambiguous.
    if ending == "FAILED" and failures + errors == 0:
        passed = None  # An unrecognized failure category is not a confirmed pass.
    return {"ran": ran, "passed": passed,
            "failures": failures, "errors": errors, "skipped": skipped,
            "result": "passed" if ending == "OK" else "failed" if ending == "FAILED" else "incomplete"}


def check_behavior_log(path):
    result = behavior_log_summary(path)
    if result["ran"] < 1 or result["skipped"] >= result["ran"]:
        raise ValueError("Behavior selection executed no tests")
    if result["failures"] or result["errors"] or result["result"] != "passed":
        raise ValueError("Behavior log does not confirm a successful run")
    return result


def _rss_bytes():
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            class MemoryCounters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
                    (name, ctypes.c_size_t) for name in ("peak", "working", "peak_paged", "paged",
                                                        "peak_nonpaged", "nonpaged", "pagefile", "peak_pagefile")]
            counters = MemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            get_process = ctypes.windll.kernel32.GetCurrentProcess
            get_process.restype = wintypes.HANDLE
            query = ctypes.windll.psapi.GetProcessMemoryInfo
            query.argtypes = (wintypes.HANDLE, ctypes.POINTER(MemoryCounters), wintypes.DWORD)
            if query(get_process(), ctypes.byref(counters), counters.cb):
                return counters.working
        elif sys.platform.startswith("linux"):
            pages = int(Path("/proc/self/statm").read_text().split()[1])
            return pages * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, AttributeError, ImportError):
        pass
    return None


def _latency(values):
    from statistics import median
    ordered = sorted(values)
    return {"count": len(values), "median": median(values) if values else 0,
            "p95": ordered[(95 * len(ordered) + 99) // 100 - 1] if ordered else 0,
            "max": max(values, default=0)}


def stress_chess(*, seed, variant, steps, seconds, output, diagnostics=None):
    # Imported only by a child explicitly running game validation.
    from contextlib import ExitStack
    import random
    from unittest.mock import patch
    from wujiang.tactical.rooms import ai, ai_router
    from wujiang.tactical.rooms import ai_acceleration, ai_parallel
    from wujiang.tactical.rooms.ai_acceleration import last_statistics
    from wujiang.tactical.rooms.multiplayer import GameRoom
    from wujiang.tools.ai_equivalence import create_late_chess_fixture, isolated_id_stream, late_chess_composition

    rows, memory, reasons = [], [], []
    original_rng = random.getstate()
    original_action = GameRoom._perform_battle_action
    def capture(room, payload, *, reason):
        reasons.append(reason)
        return original_action(room, payload, reason=reason)
    progress_path = output.with_suffix(".progress.json")
    try:
        with isolated_id_stream():
            random.seed(seed)
            create_late_chess_fixture(seed, variant=variant)
        with ExitStack() as stack:
            stack.enter_context(isolated_id_stream())
            stack.enter_context(patch.object(ai_router, "_picker", return_value=ai))
            stack.enter_context(patch.object(GameRoom, "_perform_battle_action", capture))
            random.seed(seed)
            chess = create_late_chess_fixture(seed, variant=variant)
            initial_counts = [len(match.runner.battle.units) for match in chess.matches]
            composition = late_chess_composition(chess, variant)
            check_series_composition(variant, initial_counts, composition)
            memory.append({"step": 0, "rss_bytes": _rss_bytes()})
            started = time.perf_counter()
            stop_reason = "steps"
            for ordinal in range(steps):
                pending = [(index, match) for index, match in enumerate(chess.matches) if match.result is None]
                if not pending:
                    stop_reason = "battles_finished"
                    break
                if time.perf_counter() - started >= seconds:
                    stop_reason = "time_budget"
                    break
                cursor = chess.next_match_cursor
                board, match = pending[cursor % len(pending)]
                chess.next_match_cursor = cursor + 1
                actor = match.runner.battle.current_turn_unit()
                info = {"step": ordinal + 1, "board": board + 1,
                        "actor": actor.hero_code if actor else None,
                        "active_boards": len(pending),
                        "unit_count": len(match.runner.battle.units)}
                _write(progress_path, {**info, "phase": "deciding"})
                first_reason = len(reasons)
                wall, cpu = time.perf_counter(), time.process_time()
                if diagnostics is None:
                    chess._advance_match(match)
                else:
                    with diagnostics.step(info):
                        chess._advance_match(match)
                info.update(seconds=time.perf_counter() - wall, cpu_seconds=time.process_time() - cpu,
                            reasons=reasons[first_reason:], acceleration=last_statistics(),
                            parallel_boards=ai_parallel.last_statistics())
                rows.append(info)
                if diagnostics is not None:
                    diagnostics.flush_step(info)
                _write(progress_path, {**info, "phase": "complete"})
                if ordinal % 4 == 3:
                    memory.append({"step": ordinal + 1, "rss_bytes": _rss_bytes()})
    finally:
        ai_acceleration.shutdown_workers(terminate=True)
        random.setstate(original_rng)
    memory.append({"step": len(rows), "rss_bytes": _rss_bytes()})
    by_reason = {}
    for row in rows:
        for reason in set(row["reasons"]):
            by_reason.setdefault(reason, []).append(row["seconds"])
    result = {"workload": "optimized_only_stress", "build": build_info(), "seed": seed,
              "fixture": variant, "initial_unit_counts": initial_counts, "requested_steps": steps,
              **composition,
              "completed_steps": len(rows), "complete": stop_reason != "time_budget", "stop_reason": stop_reason,
              "latency": _latency([row["seconds"] for row in rows]),
              "four_board_sweep_latency": _latency([sum(row["seconds"] for row in rows[i:i + 4])
                     for i in range(0, len(rows) - 3, 4)
                     if all(row["active_boards"] == 4 for row in rows[i:i + 4])
                     and {row["board"] for row in rows[i:i + 4]} == {1, 2, 3, 4}]),
              "by_reason": {reason: _latency(values) for reason, values in by_reason.items()},
              "cpu_seconds": sum(row["cpu_seconds"] for row in rows), "memory_samples": memory, "steps": rows,
              "note": "Optimized-only bounded load, not an equivalence proof. Excludes pacing, HTTP and rendering; no replay recording."}
    rss = [sample["rss_bytes"] for sample in memory if sample["rss_bytes"] is not None]
    result["rss_delta_bytes"] = rss[-1] - rss[0] if rss else None
    result["actors_visited"] = sorted({row["actor"] for row in rows if row["actor"]})
    if diagnostics is not None:
        result.update(workload="baseline_rejection_diagnostic", baseline_diagnostics=diagnostics.report(),
                      not_a_speed_benchmark=True,
                      note="Isolated synthetic four-board diagnosis with extra profiling; not an equivalence test or a speed comparison.")
    _write(output, result)
    return result


def _run_stage(command, log, *, timeout, environment):
    with log.open("w", encoding="utf-8") as stream:
        process = subprocess.Popen(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, env=environment)
        started = last_update = time.monotonic()
        try:
            while process.poll() is None:
                now = time.monotonic()
                if now - started >= timeout:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    raise TimeoutError(f"Stage exceeded {timeout}s; see {log}")
                if now - last_update >= 15:
                    print(f"  running {now - started:.0f}s; log: {log.name}", flush=True)
                    last_update = now
                time.sleep(0.25)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait()
        if process.returncode:
            raise RuntimeError(f"Stage exit={process.returncode}; see {log}")
    return time.monotonic() - started


def check_flat_copy_ablation(row, *, require_profile=False):
    if (row.get("reference_kind") != "no-flat-copy"
            or row.get("reference_native_flat_copy") is not False
            or row.get("optimized_native_flat_copy") is not True
            or row.get("optimized_native_rollback") is not True
            or row.get("optimized_parallel_boards") is not False
            or row.get("optimized_native_checkpoint") is not False):
        raise ValueError("Scalar-copy ablation must isolate the active native copier")
    if not require_profile:
        return
    if row.get("profile_replays_equivalent") is not True:
        raise ValueError("Missing equivalent separate dense profile replays")
    repeats = row.get("repeats")
    if type(repeats) is not int or repeats < 1:
        raise ValueError("Missing independent timing repeat count")
    for mode in ("reference", "optimized"):
        data = row.get("profiles", {}).get(mode, {})
        steps = data.get("steps", [])
        timed_runs = row.get("steps", {}).get(mode, [])
        initial = [item for item in row.get("initial_id_states", []) if item.get("mode") == mode]
        if (len(timed_runs) != repeats or len(row.get("timing_samples", {}).get(mode, [])) != repeats
                or sum(item.get("profiled") is True for item in initial) != 1
                or sum(item.get("profiled") is False for item in initial) != repeats):
            raise ValueError("Profile replays must stay separate from timed repetitions")
        if (data.get("excluded_from_timing_samples") is not True
                or not timed_runs or len(steps) != len(timed_runs[0])
                or not steps or any(item.get("profiled") is not True for item in steps)
                or {item.get("board") for item in steps[:4]} != {1, 2, 3, 4}
                or any(item.get("unit_count", 0) < 24 for item in steps[:4])
                or not data.get("top_cumulative") or not data.get("slowest")
                or not Path(data.get("path", "")).is_file()
                or any(not Path(item.get("path", "")).is_file() for item in data.get("slowest", []))):
            raise ValueError("Missing separate four-board dense call profiles")


def check_control_diagnostic(data):
    expected = {(fast, destroyed, casts) for fast in (False, True) for destroyed in (0, 21) for casts in (0, 1, 2)}
    scenes = data.get("scenes", [])
    if (data.get("workload") != "control_reselection_diagnostic" or data.get("complete") is not True
            or data.get("not_a_speed_benchmark") is not True
            or len(scenes) != len(expected)
            or {(row.get("fast"), row.get("destroyed_units"), row.get("prior_eagle_eye_casts")) for row in scenes} != expected):
        raise ValueError("Missing normal/chess first/repeated-control diagnostic scenes")
    for row in scenes:
        if (row.get("live_units") != 4 or row.get("equal_replays") is not True
                or row.get("profile_excluded_from_timings") is not True
                or len(row.get("seconds", [])) != data.get("repeats")
                or not row.get("seconds") or not Path(row.get("profile_path", "")).is_file()):
            raise ValueError("Control diagnosis is incomplete or mixes profiling into timing")


def check_tactical_control_report(data, *, full=True):
    expected = {(fast, destroyed, casts) for fast in (False, True) for destroyed in (0, 21) for casts in (0, 1, 2)}
    scenes = data.get("scenes", [])
    if (data.get("workload") != "control_query_ablation" or data.get("complete") is not True
            or data.get("candidate_witnesses_excluded_from_timing") is not True
            or data.get("reference_tactical_queries") is not False or data.get("optimized_tactical_queries") is not True
            or data.get("repeats", 0) < (3 if full else 1) or len(scenes) != 12
            or {(row.get("fast"), row.get("destroyed_units"), row.get("prior_eagle_eye_casts")) for row in scenes} != expected):
        raise ValueError("Missing separate exact control OFF/ON comparisons")
    check_production_features(data["build"]["features"])
    for row in scenes:
        if (row.get("live_units") != 4 or row.get("equal") is not True or row.get("candidates_equal") is not True
                or row.get("reference_candidate_count", 0) < 1
                or row.get("reference_candidate_count") != row.get("optimized_candidate_count")
                or any(len(row.get("seconds", {}).get(mode, [])) != data["repeats"]
                       or len(row.get("acceleration", {}).get(mode, [])) != data["repeats"] for mode in ("reference", "optimized"))
                or any(item.get("tactical_query_hits", 0) for item in row["acceleration"]["reference"])):
            raise ValueError("Control query comparison is incomplete or the reference used the cache")
    if not any(row.get("query_hits", 0) > 0 for row in scenes):
        raise ValueError("Control query cache was not exercised")


def check_tactical_query_ablation(row, *, full=True):
    if (row.get("reference_kind") != "no-tactical-queries" or row.get("reference_tactical_queries") is not False
            or row.get("optimized_tactical_queries") is not True):
        raise ValueError("Tactical query comparison must isolate OFF/ON")
    check_production_features(row, row=True)
    if any(step.get("acceleration", {}).get("tactical_query_hits", 0)
           for run in row.get("steps", {}).get("reference", []) for step in run):
        raise ValueError("Reference unexpectedly reused tactical queries")
    selected = {"hell-heavy": (45, 67, 91), "electronic-heavy": (117, 137, 196)}.get(row.get("fixture_variant"))
    if not full:
        return
    if row.get("repeats", 0) < 3:
        raise ValueError("Tactical queries need alternating dense repetitions")
    if any(len(row.get("steps", {}).get(mode, [])) != row["repeats"] for mode in ("reference", "optimized")):
        raise ValueError("Missing tactical-query timing runs")
    if selected is not None:
        witnesses = row.get("position_query_witnesses", [])
        if (row.get("query_witness_replays_equivalent") is not True
                or row.get("query_witnesses_excluded_from_timing") is not True
                or tuple(item.get("step") for item in witnesses) != selected
                or any(item.get("equal") is not True or item.get("reference_calls", 0) < 1
                       or item.get("reference_calls") != item.get("optimized_calls") for item in witnesses)):
            raise ValueError("Missing dense tactical-query exact score witnesses")
        for run in row.get("steps", {}).get("optimized", []):
            by_step = {step["step"]: step for step in run}
            if any(step not in by_step or by_step[step].get("active_boards") != 4
                   or by_step[step].get("unit_count", 0) < 20 for step in selected):
                raise ValueError("Missing known dense tactical-query tail steps")


def check_independent_row_ablation(row, *, full=True):
    adaptation = row.get("reference_kind") == "no-independent-adaptation"
    if (row.get("reference_kind") not in {"no-rng-independent", "no-independent-adaptation"}
            or row.get("reference_rng_independent_rows") is not adaptation
            or row.get("optimized_rng_independent_rows") is not True
            or adaptation and (row.get("reference_adaptive_rows") is not False
                               or row.get("optimized_adaptive_rows") is not True)):
        raise ValueError("Independent-row comparison must isolate OFF/ON")
    check_production_features(row, row=True)
    for run in row.get("steps", {}).get("reference", []):
        if not adaptation and any(step.get("acceleration", {}).get("independent_row_hits", 0) for step in run):
            raise ValueError("Reference unexpectedly reused independent rows")
        if adaptation and any(step.get("acceleration", {}).get("independent_row_fallbacks", 0)
                              or step.get("acceleration", {}).get("independent_row_bypasses", 0) for step in run):
            raise ValueError("Reference unexpectedly used adaptive fallback")
    selected = {"electronic-heavy": (117, 137, 196), "hell-heavy": (45, 67, 91)}.get(row.get("fixture_variant"))
    if not full or selected is None:
        return
    if row.get("repeats", 0) < 3:
        raise ValueError("Dense random-tail comparisons need alternating repeats")
    witnesses = row.get("position_query_witnesses", [])
    if (row.get("query_witness_replays_equivalent") is not True
            or row.get("query_witnesses_excluded_from_timing") is not True
            or tuple(item.get("step") for item in witnesses) != selected
            or any(item.get("equal") is not True or item.get("reference_calls", 0) < 1
                   or item.get("reference_calls") != item.get("optimized_calls") for item in witnesses)):
        raise ValueError("Missing equivalent separate exact position-score witnesses")
    runs = row.get("steps", {}).get("optimized", [])
    if len(runs) != row["repeats"]:
        raise ValueError("Missing independent-row timing runs")
    for run in runs:
        by_step = {step["step"]: step for step in run}
        if any(step not in by_step or by_step[step].get("active_boards") != 4
               or by_step[step].get("unit_count", 0) < 20 for step in selected):
            raise ValueError("Missing known dense four-board tail steps")
        metric = ("independent_row_fallbacks" if row.get("optimized_adaptive_rows") and row.get("fixture_variant") == "hell-heavy"
                  else "independent_row_hits")
        if not any(by_step[step].get("acceleration", {}).get(metric, 0) > 0 for step in selected):
            raise ValueError("Independent rows did not exercise reuse or safe fallback on a known tail step")


def check_row_key_ablation(row, *, full=True):
    if (row.get("reference_kind") != "no-native-row-keys"
            or row.get("reference_native_row_keys") is not False
            or row.get("optimized_native_row_keys") is not True
            or row.get("reference_rng_independent_rows") is not True
            or row.get("optimized_rng_independent_rows") is not True
            or row.get("reference_adaptive_rows") is not True
            or row.get("optimized_adaptive_rows") is not True
            or row.get("reference_tactical_queries") is not False
            or row.get("optimized_tactical_queries") is not False):
        raise ValueError("Row-key comparison must isolate Python/native encoders with queries off")
    check_production_features(row, row=True)
    for mode in ("reference", "optimized"):
        for run in row.get("steps", {}).get(mode, []):
            for step in run:
                stats = step.get("acceleration", {})
                count = stats.get("independent_row_snapshots", 0)
                native = stats.get("independent_row_native_snapshots", 0)
                if mode == "reference" and native:
                    raise ValueError("Reference unexpectedly used native row keys")
                if mode == "optimized" and (native != count or stats.get("independent_row_native_fallbacks", 0)
                                             or count and stats.get("independent_row_native_visits", 0) < count):
                    raise ValueError("Native row encoder was not actually exercised or silently fell back")
                if count and (type(stats.get("independent_row_snapshot_seconds")) not in (int, float)
                              or stats["independent_row_snapshot_seconds"] < 0):
                    raise ValueError("Missing separate row-key timing")
    if not full:
        return
    if row.get("repeats", 0) < 3 or any(len(row.get("steps", {}).get(mode, [])) != row["repeats"]
                                       for mode in ("reference", "optimized")):
        raise ValueError("Row keys need three alternating dense timing runs")
    selected = {"hell-heavy": (45, 67, 91), "electronic-heavy": (117, 137, 196)}.get(row.get("fixture_variant"))
    if selected is None:
        return  # Zero native work in other fixtures is not a measured gain.
    witnesses = row.get("position_query_witnesses", [])
    if (row.get("query_witness_replays_equivalent") is not True
            or row.get("query_witnesses_excluded_from_timing") is not True
            or tuple(item.get("step") for item in witnesses) != selected
            or any(item.get("equal") is not True or item.get("reference_calls", 0) < 1
                   or item.get("reference_calls") != item.get("optimized_calls") for item in witnesses)):
        raise ValueError("Missing separate exact row-key position-score witnesses")
    for mode in ("reference", "optimized"):
        for run in row["steps"][mode]:
            by_step = {step["step"]: step for step in run}
            if any(step not in by_step or by_step[step].get("active_boards") != 4
                   or by_step[step].get("unit_count", 0) < 20
                   or by_step[step].get("acceleration", {}).get("independent_row_snapshots", 0) < 1 for step in selected):
                raise ValueError("Missing known dense four-board row-key tail steps")
    if row.get("profile_replays_equivalent") is not True:
        raise ValueError("Missing equivalent separate row-key profiles")
    for mode in ("reference", "optimized"):
        data = row.get("profiles", {}).get(mode, {})
        if (data.get("excluded_from_timing_samples") is not True or not data.get("slowest")
                or not Path(data.get("path", "")).is_file()
                or any(not Path(item.get("path", "")).is_file() for item in data["slowest"])):
            raise ValueError("Missing row-key profiles outside timing repetitions")


def check_component_scan_ablation(row, *, full=True):
    if (row.get("reference_kind") != "no-component-scan"
            or row.get("reference_native_component_scan") is not False
            or row.get("optimized_native_component_scan") is not True
            or any(row.get(mode + "_" + feature) is not True for mode in ("reference", "optimized")
                   for feature in ("rng_independent_rows", "adaptive_rows", "native_row_keys"))
            or any(row.get(mode + "_tactical_queries") is not False for mode in ("reference", "optimized"))):
        raise ValueError("Component scan must isolate Python/C with identical row caches and queries off")
    check_production_features(row, row=True)
    for mode in ("reference", "optimized"):
        runs = row.get("steps", {}).get(mode, [])
        if not runs or any(not run for run in runs):
            raise ValueError("Missing component scan timing runs")
        calls = sum(step.get("native_component_scan", {}).get("calls", 0) for run in runs for step in run)
        if mode == "reference" and calls:
            raise ValueError("Reference unexpectedly used the native component scanner")
        if mode == "optimized" and calls < 1:
            raise ValueError("Component scanner was not actually exercised")
    if not full:
        return
    if row.get("repeats", 0) < 3 or any(len(row.get("steps", {}).get(mode, [])) != row["repeats"]
                                       or len(row.get("timing_samples", {}).get(mode, [])) != row["repeats"]
                                       for mode in ("reference", "optimized")):
        raise ValueError("Component scan needs three alternating dense timing runs")
    selected = {"hell-heavy": (45, 67, 91), "electronic-heavy": (117, 137, 196)}.get(row.get("fixture_variant"))
    if selected is None:
        return
    witnesses = row.get("position_query_witnesses", [])
    if (row.get("query_witness_replays_equivalent") is not True
            or row.get("query_witnesses_excluded_from_timing") is not True
            or tuple(item.get("step") for item in witnesses) != selected
            or any(item.get("equal") is not True or item.get("reference_calls", 0) < 1
                   or item.get("reference_calls") != item.get("optimized_calls") for item in witnesses)):
        raise ValueError("Missing exact component-scan position-score witnesses")
    for mode in ("reference", "optimized"):
        for run in row["steps"][mode]:
            by_step = {step["step"]: step for step in run}
            if any(step not in by_step or by_step[step].get("active_boards") != 4
                   or by_step[step].get("unit_count", 0) < 20
                   or mode == "optimized" and by_step[step].get("native_component_scan", {}).get("calls", 0) < 1
                   for step in selected):
                raise ValueError("Missing known dense component-scan tail steps")
    if row.get("profile_replays_equivalent") is not True:
        raise ValueError("Missing equivalent separate component scan profiles")
    for mode in ("reference", "optimized"):
        data = row.get("profiles", {}).get(mode, {})
        if (data.get("excluded_from_timing_samples") is not True or not data.get("slowest")
                or not Path(data.get("path", "")).is_file()
                or any(not Path(item.get("path", "")).is_file() for item in data["slowest"])):
            raise ValueError("Missing separate component scan profiles")


def _ablation(rows):
    row = rows[0]
    old, new = row["reference_seconds"], row["optimized_seconds"]
    before = row["latency"]["reference"]["step_max"]
    after = row["latency"]["optimized"]["step_max"]
    steps = [step for run in row["steps"]["optimized"] for step in run]
    activity = dict(
        baseline_batch_runs=sum(step.get("acceleration", {}).get("baseline_batch_runs", 0) for step in steps),
        parallel_baseline_jobs=sum(step.get("acceleration", {}).get("parallel_baseline_jobs", 0) for step in steps),
        parallel_baseline_hits=sum(step.get("acceleration", {}).get("parallel_baseline_hits", 0) for step in steps),
        independent_row_hits=sum(step.get("acceleration", {}).get("independent_row_hits", 0) for step in steps),
        independent_row_dependent=sum(step.get("acceleration", {}).get("independent_row_dependent", 0) for step in steps),
        independent_row_fallbacks=sum(step.get("acceleration", {}).get("independent_row_fallbacks", 0) for step in steps),
        independent_row_bypasses=sum(step.get("acceleration", {}).get("independent_row_bypasses", 0) for step in steps),
        independent_row_native_snapshots=sum(step.get("acceleration", {}).get("independent_row_native_snapshots", 0) for step in steps),
        independent_row_native_visits=sum(step.get("acceleration", {}).get("independent_row_native_visits", 0) for step in steps),
        native_component_scans=sum(step.get("native_component_scan", {}).get("calls", 0) for step in steps),
        native_component_statuses=sum(step.get("native_component_scan", {}).get("statuses", 0) for step in steps),
        native_component_short_circuits=sum(step.get("native_component_scan", {}).get("short_circuits", 0) for step in steps),
        native_component_errors=sum(step.get("native_component_scan", {}).get("errors", 0) for step in steps),
        tactical_query_hits=sum(step.get("acceleration", {}).get("tactical_query_hits", 0) for step in steps),
        tactical_query_impure=sum(step.get("acceleration", {}).get("tactical_query_impure", 0) for step in steps),
        tactical_query_cells_reused=sum(step.get("acceleration", {}).get("tactical_query_cells_reused", 0) for step in steps),
        board_jobs=sum(step.get("parallel_boards", {}).get("submitted", 0) for step in steps),
        board_hits=sum(step.get("parallel_boards", {}).get("hits", 0) for step in steps),
        board_stale=sum(step.get("parallel_boards", {}).get("stale", 0) for step in steps),
        board_timeouts=sum(step.get("parallel_boards", {}).get("timeouts", 0) for step in steps),
        native_flat_containers=sum(sum(step.get("native_flat_copy", {}).values()) for step in steps))
    required = {"no-baseline-batches": "baseline_batch_runs", "no-parallel-baselines": "parallel_baseline_hits",
                "no-parallel-boards": "board_hits", "no-flat-copy": "native_flat_containers",
                "no-rng-independent": "independent_row_hits", "no-tactical-queries": "tactical_query_hits",
                "no-native-row-keys": "independent_row_native_snapshots",
                "no-component-scan": "native_component_scans"}.get(row["reference_kind"])
    if row["reference_kind"] in {"no-rng-independent", "no-independent-adaptation"}:
        exercised = activity["independent_row_hits"] + activity["independent_row_fallbacks"] > 0
    else:
        exercised = required is None or activity[required] > 0
    return {"baseline": row["reference_kind"], "fixture": row.get("fixture_variant", "standard"),
            "reference_seconds": old, "optimized_seconds": new,
            "improvement_percent": 100 * (old - new) / old if old else None,
            "reference_step_max": before, "optimized_step_max": after,
            "step_p95": {mode: row["latency"][mode].get("step_p95") for mode in ("reference", "optimized")},
            "by_reason": {mode: row["latency"][mode].get("by_reason", {}) for mode in ("reference", "optimized")},
            "four_board_sweep_latency": {mode: _latency([seconds for run in row["latency"][mode].get("sweep_seconds", [])
                                                        for seconds in run]) for mode in ("reference", "optimized")},
            "profile_paths": {mode: data["path"] for mode, data in row.get("profiles", {}).items()},
            "activity": activity,
            "row_key_seconds": {mode: sum(step.get("acceleration", {}).get("independent_row_snapshot_seconds", 0)
                                          for run in row.get("steps", {}).get(mode, []) for step in run)
                                for mode in ("reference", "optimized")},
            "row_key_snapshots": {mode: sum(step.get("acceleration", {}).get("independent_row_snapshots", 0)
                                            for run in row.get("steps", {}).get(mode, []) for step in run)
                                  for mode in ("reference", "optimized")},
            "verdict": ("not_exercised" if not exercised else
                        "measured_gain" if new <= old * 0.95 and after <= before * 1.05 else "review_results")}


def validation_environment(workers):
    """Validate exact row/query candidates; keep retired experiments off.

    The candidate is opt-in here, while its production default remains off
    until the dense exact comparisons establish correctness and useful gain.
    """
    return dict(os.environ, PYTHONUTF8="1", WUJIANG_AI_WORKERS=str(workers),
                WUJIANG_AI_NATIVE_ROLLBACK="1", WUJIANG_AI_REQUIRE_NATIVE="1",
                WUJIANG_AI_ROLLBACK_PLANS="1", WUJIANG_AI_ATTACK_IDS="1", WUJIANG_AI_MOVE_BATCHES="1",
                WUJIANG_AI_SMALL_BASELINES="1", WUJIANG_AI_BASELINE_BATCHES="1",
                WUJIANG_AI_PARALLEL_BASELINES="1", WUJIANG_AI_NATIVE_CHECKPOINT="0",
                WUJIANG_AI_PARALLEL_BOARDS="0", WUJIANG_AI_NATIVE_FLAT_COPY="0",
                WUJIANG_AI_RNG_INDEPENDENT_ROWS="1", WUJIANG_AI_ADAPTIVE_ROWS="1", WUJIANG_AI_TACTICAL_QUERIES="1",
                WUJIANG_AI_NATIVE_ROW_KEYS="1", WUJIANG_AI_NATIVE_COMPONENT_SCAN="1")


def check_production_features(features, *, row=False):
    keys = (("optimized_parallel_boards", "optimized_native_checkpoint", "optimized_native_flat_copy") if row else
            ("parallel_boards", "native_checkpoint_requested", "native_flat_copy_requested"))
    if any(features.get(key) is not False for key in keys):
        raise ValueError("Production validation must keep retired and unaccepted experiments disabled")


def main():
    parser = argparse.ArgumentParser(description="Complete full-AI correctness and late-chess performance validation")
    parser.add_argument("--output-dir")
    parser.add_argument("--workers", type=int, choices=range(5), default=4)
    parser.add_argument("--quick", action="store_true", help="Smoke coverage only, not the full acceptance workload")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--rng-rows-only", action="store_true",
                        help="Build, behavior, control diagnosis and exact row/adaptation comparisons; excludes full regression")
    scope.add_argument("--tactical-queries-only", action="store_true", help="Build, behavior, exact control and five dense OFF/ON query comparisons")
    scope.add_argument("--row-keys-only", action="store_true", help="Build, behavior and five dense Python/native row-key comparisons")
    scope.add_argument("--component-scan-only", action="store_true", help="Build, behavior and five dense Python/C component-scan comparisons")
    scope.add_argument("--new-baseline-only", action="store_true", help="Performance only for new expectation/visibility rules; logs and fixed inputs, no old/new equivalence or behavior suite")
    scope.add_argument("--prediction-repair-only", action="store_true", help="Focused performance replay of prior slow requests, including dense electronic tail inputs; no behavior/equivalence suite")
    parser.add_argument("--timeout", type=int, default=3600, help="Maximum seconds for each isolated stage")
    parser.add_argument("--stress-seconds", type=int, default=180)
    parser.add_argument("--stress-steps", type=int, default=256)
    parser.add_argument("--stress-child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--fixture", choices=("standard", "equipped", "summons", *SERIES_CHESS_FIXTURES), default="standard")
    parser.add_argument("--child-output", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.new_baseline_only or args.prediction_repair_only or not (args.rng_rows_only or args.tactical_queries_only
            or args.row_keys_only or args.component_scan_only or args.stress_child):
        from wujiang.tools.ai_baseline import main as baseline_main
        arguments = ["--workers", str(args.workers), "--timeout", str(args.timeout)]
        if args.output_dir:
            arguments.extend(("--output-dir", args.output_dir))
        if args.quick:
            arguments.append("--quick")
        if args.prediction_repair_only:
            arguments.append("--repair-check")
        return baseline_main(arguments)
    if min(args.timeout, args.stress_seconds, args.stress_steps) < 1:
        parser.error("Timeout and stress limits must be positive")
    if args.stress_child:
        if not args.child_output:
            parser.error("Internal stress child requires --child-output")
        result = stress_chess(seed=args.seed, variant=args.fixture, steps=args.stress_steps,
                              seconds=args.stress_seconds, output=Path(args.child_output))
        return 0 if result["complete"] else 2

    output = Path(args.output_dir) if args.output_dir else ROOT / "reports" / ("full-ai-validation-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    if not output.is_absolute():
        output = ROOT / output
    output.mkdir(parents=True, exist_ok=False)
    environment = validation_environment(args.workers)
    # Explicit historical ablations retain the historical decision policy.
    # The flagless/new-baseline path above is the expectation research policy;
    # run.py separately pins the user-approved temporary legacy server profile.
    environment["WUJIANG_AI_SCORING_RULES"] = "legacy"
    os.environ.update({key: value for key, value in environment.items() if key.startswith("WUJIANG_AI_")})
    summary = {"status": "running", "build": build_info(), "quick": args.quick, "stages": [],
               "performance": [], "scope": "component_scan_only" if args.component_scan_only else "row_keys_only" if args.row_keys_only else "tactical_queries_only" if args.tactical_queries_only else "rng_rows_only" if args.rng_rows_only else "full",
               "ready_for_live_test": False, "live_experience": "pending_manual_test"}
    check_production_features(summary["build"]["features"])
    summary_path = output / "summary.json"
    _write(summary_path, summary)
    print(f"Validation directory: {output}", flush=True)
    suite = ROOT / "tools" / "check_ai_equivalence.py"
    def stage(name, command, *, report=None, ablation=False, stress=False, control=False, query_control=False):
        record = {"name": name, "status": "running", "log": str(output / (name + ".log"))}
        summary["stages"].append(record)
        _write(summary_path, summary)
        print(f"Starting {name}", flush=True)
        stage_started = time.monotonic()
        try:
            record["seconds"] = _run_stage(command, Path(record["log"]), timeout=args.timeout, environment=environment)
            if name == "native-build":
                from wujiang.platform.ai_native import native_status
                native_status.cache_clear()
                if not native_status()["available"]:
                    raise ValueError("Native build produced no usable artifact")
                summary["build"] = build_info()
            if name == "behavior":
                record["tests"] = check_behavior_log(record["log"])
            if report:
                record["report"] = str(report)
                if query_control:
                    data = json.loads(report.read_text(encoding="utf-8"))
                    check_tactical_control_report(data, full=not args.quick)
                    if data.get("build", {}).get("id") != summary["build"]["id"]:
                        raise ValueError("Source identity changed during control comparison")
                    record["scenes"] = [{key: row[key] for key in ("scene", "median_seconds", "improvement_percent", "query_hits")}
                                        for row in data["scenes"]]
                elif control:
                    data = json.loads(report.read_text(encoding="utf-8"))
                    check_control_diagnostic(data)
                    if data.get("build", {}).get("id") != summary["build"]["id"]:
                        raise ValueError("Source identity changed during control diagnosis")
                    record["scenes"] = [{key: row[key] for key in ("scene", "median_seconds", "component_id_delta", "unit_id_delta")}
                                        for row in data["scenes"]]
                elif stress:
                    data = json.loads(report.read_text(encoding="utf-8"))
                    if data.get("complete") is not True:
                        raise ValueError("Stress stopped before its requested boundary")
                    if data.get("build", {}).get("id") != summary["build"]["id"]:
                        raise ValueError("Source identity changed during stress")
                    check_production_features(data["build"]["features"])
                    check_series_composition(data.get("fixture"), data.get("initial_unit_counts"), data)
                    record["latency"] = data["latency"]
                    record["four_board_sweep_latency"] = data["four_board_sweep_latency"]
                    record["cpu_seconds"] = data["cpu_seconds"]
                    record["rss_delta_bytes"] = data["rss_delta_bytes"]
                    record["actors_visited"] = data["actors_visited"]
                else:
                    rows = check_equivalence_report(report)
                    if name.startswith("late-") or name == "retired-paths-ablation":
                        for row in rows:
                            check_production_features(row, row=True)
                    if name.startswith("native-") and any(not row.get("optimized_native_rollback") for row in rows):
                        raise ValueError("Native ablation did not activate the native backend")
                    if name == "small-baseline-ablation" and any(not row.get("optimized_small_baselines") for row in rows):
                        raise ValueError("Small baseline ablation did not activate the optimized branch")
                    if name.startswith("flat-copy-"):
                        for row in rows:
                            check_flat_copy_ablation(row, require_profile=not args.quick)
                    if name.startswith("rng-rows-"):
                        for row in rows:
                            check_independent_row_ablation(row, full=not args.quick)
                    if name.startswith("tactical-queries-"):
                        for row in rows:
                            check_tactical_query_ablation(row, full=not args.quick)
                    if name.startswith("row-keys-"):
                        for row in rows:
                            check_row_key_ablation(row, full=not args.quick)
                    if name.startswith("component-scan-"):
                        for row in rows:
                            check_component_scan_ablation(row, full=not args.quick)
                    feature = {"baseline-batch-ablation": "optimized_baseline_batches",
                               "checkpoint-ablation": "optimized_native_checkpoint",
                               "parallel-baseline-ablation": "optimized_parallel_baselines",
                               "parallel-board-ablation": "optimized_parallel_boards"}.get(name)
                    if feature and any(not row.get(feature) for row in rows):
                        raise ValueError("Architecture ablation did not activate " + feature)
                    if any(row.get("build", {}).get("id") != summary["build"]["id"] for row in rows):
                        raise ValueError("Source identity changed during validation")
                    record["equivalent_samples"] = len(rows)
                    if rows[0].get("workload") == "sequential_live_chess_actions":
                        record["reference_kind"] = rows[0]["reference_kind"]
                        record["optimized_latency"] = rows[0]["latency"]["optimized"]
                        record["reference_seconds"] = rows[0]["reference_seconds"]
                        record["optimized_seconds"] = rows[0]["optimized_seconds"]
                    else:
                        decisions = [row for row in rows if row.get("workload") == "full_turn_candidates_and_choice"]
                        record["decision_workload_seconds"] = {
                            mode: sum(row[f"{mode}_seconds"] for row in decisions) for mode in ("reference", "optimized")}
                    if ablation:
                        summary["performance"].append({"stage": name, **_ablation(rows)})
            record["status"] = "passed"
        except Exception as exc:
            record.update(status="failed", error=str(exc))
            if name == "behavior":
                try:
                    record["tests"] = behavior_log_summary(record["log"])
                    tests = record["tests"]
                    passed = tests["passed"] if tests["passed"] is not None else "unknown"
                    print(f"Stopped at behavior: {passed}/{tests['ran']} passed, "
                          f"{tests['failures']} failures, {tests['errors']} errors, {tests['skipped']} skipped. "
                          "Later equivalence/performance stages were not run.", flush=True)
                except OSError:
                    pass
            raise
        finally:
            record.setdefault("seconds", time.monotonic() - stage_started)
            _write(summary_path, summary)

    def compare(name, *, fixture="standard", baseline="original", sweeps=4, repeat=1, seed=17, profile=False):
        report = output / (name + ".json")
        command = [sys.executable, str(suite), "--late-chess", "--fixture", fixture, "--baseline", baseline,
                     "--turns", str(sweeps), "--repeat", str(repeat), "--seed", str(seed),
                     "--workers", str(args.workers), "--output", str(report)]
        if profile:
            command.append("--profile")
        stage(name, command, report=report, ablation=baseline != "original")

    try:
        stage("native-build", [sys.executable, str(ROOT / "tools" / "build_ai_native.py"), "--bootstrap"])
        stage("behavior", [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_behavior.py",
                           "-k", "FullAIEquivalenceBehaviorTests"])
        if args.component_scan_only or not (args.row_keys_only or args.rng_rows_only or args.tactical_queries_only):
            for fixture, seed, sweeps in (("standard", 17, 12), ("equipped", 31, 16), ("summons", 53, 16),
                                           ("hell-heavy", SERIES_CHESS_FIXTURES["hell-heavy"]["seed"], 32),
                                           ("electronic-heavy", SERIES_CHESS_FIXTURES["electronic-heavy"]["seed"], 50)):
                compare("component-scan-" + fixture, fixture=fixture, baseline="no-component-scan", seed=seed,
                        sweeps=1 if args.quick else sweeps, repeat=1 if args.quick else 3,
                        profile=not args.quick and fixture in SERIES_CHESS_FIXTURES)
        if args.component_scan_only:
            summary["status"] = "passed"
            print("Focused component scan checks passed. Full regression and live smoothness remain pending.", flush=True)
            return 0
        if args.row_keys_only or not (args.rng_rows_only or args.tactical_queries_only):
            for fixture, seed, sweeps in (("standard", 17, 12), ("equipped", 31, 16), ("summons", 53, 16),
                                           ("hell-heavy", SERIES_CHESS_FIXTURES["hell-heavy"]["seed"], 32),
                                           ("electronic-heavy", SERIES_CHESS_FIXTURES["electronic-heavy"]["seed"], 50)):
                compare("row-keys-" + fixture, fixture=fixture, baseline="no-native-row-keys", seed=seed,
                        sweeps=1 if args.quick else sweeps, repeat=1 if args.quick else 3,
                        profile=not args.quick and fixture in SERIES_CHESS_FIXTURES)
        if args.row_keys_only:
            summary["status"] = "passed"
            print("Focused row-key comparisons passed. Full regression and live smoothness remain pending.", flush=True)
            return 0
        if not args.rng_rows_only:
            control_directory = output / "control-query-ablation"
            stage("control-query-ablation", [sys.executable, str(ROOT / "tools" / "diagnose_control_ai.py"),
                  "--compare-queries", "--repeats", "1" if args.quick else "3", "--output-dir", str(control_directory)],
                  report=control_directory / "comparison.json", query_control=True)
            for fixture, seed, sweeps in (("standard", 17, 12), ("equipped", 31, 16), ("summons", 53, 16),
                                           ("hell-heavy", SERIES_CHESS_FIXTURES["hell-heavy"]["seed"], 32),
                                           ("electronic-heavy", SERIES_CHESS_FIXTURES["electronic-heavy"]["seed"], 50)):
                compare("tactical-queries-" + fixture, fixture=fixture, baseline="no-tactical-queries", seed=seed,
                        sweeps=1 if args.quick else sweeps, repeat=1 if args.quick else 3)
        if args.tactical_queries_only:
            summary["status"] = "passed"
            print("Focused tactical-query comparisons passed. Full regression and live smoothness remain pending.", flush=True)
            return 0
        if not args.quick:
            control_directory = output / "control-reselection"
            stage("control-reselection", [sys.executable, str(ROOT / "tools" / "diagnose_control_ai.py"),
                  "--output-dir", str(control_directory)], report=control_directory / "diagnosis.json", control=True)
        for fixture, seed, sweeps in (("standard", 17, 12), ("equipped", 31, 16), ("summons", 53, 16),
                                       ("hell-heavy", SERIES_CHESS_FIXTURES["hell-heavy"]["seed"], 32),
                                       ("electronic-heavy", SERIES_CHESS_FIXTURES["electronic-heavy"]["seed"], 50)):
            compare("rng-rows-" + fixture, fixture=fixture, baseline="no-rng-independent", seed=seed,
                    sweeps=1 if args.quick else sweeps, repeat=1 if args.quick else 3)
        for fixture, sweeps in (("hell-heavy", 32), ("electronic-heavy", 50)):
            compare("rng-rows-adapt-" + fixture, fixture=fixture, baseline="no-independent-adaptation",
                    seed=SERIES_CHESS_FIXTURES[fixture]["seed"],
                    sweeps=1 if args.quick else sweeps, repeat=1 if args.quick else 3)
        if args.rng_rows_only:
            summary["status"] = "passed"
            print("Focused independent-row checks passed. Full regression and live smoothness remain pending.", flush=True)
            return 0
        report = output / "suite.json"
        command = [sys.executable, str(suite), "--suite", "--repeat", "1" if args.quick else "3",
                   "--workers", str(args.workers), "--output", str(report)]
        if not args.quick:
            command.append("--profile")
        stage("suite", command, report=report)
        compare("late-original", sweeps=2 if args.quick else 12)
        compare("late-equipped", fixture="equipped", sweeps=1 if args.quick else 4, seed=31)
        compare("late-summons", fixture="summons", sweeps=1 if args.quick else 4, seed=53)
        compare("late-attrition", fixture="attrition", sweeps=1 if args.quick else 4, seed=71)
        for variant, definition in SERIES_CHESS_FIXTURES.items():
            compare("late-" + variant, fixture=variant, seed=definition["seed"], sweeps=1 if args.quick else 4)
        for fixture, seed in (("standard", 17), ("equipped", 31), ("summons", 53),
                              *((variant, definition["seed"]) for variant, definition in SERIES_CHESS_FIXTURES.items())):
            compare("flat-copy-" + fixture, fixture=fixture, baseline="no-flat-copy", seed=seed,
                    sweeps=1 if args.quick else 12 if fixture == "standard" else 4,
                    repeat=1 if args.quick else 3, profile=not args.quick)
        compare("small-baseline-ablation", fixture="attrition", baseline="no-small-baselines", seed=71,
                sweeps=1 if args.quick else 4, repeat=1 if args.quick else 3)
        compare("retired-paths-ablation", baseline="previous-defaults", sweeps=2 if args.quick else 12,
                repeat=1 if args.quick else 3)
        compare("baseline-batch-ablation", fixture="attrition", baseline="no-baseline-batches", seed=71,
                sweeps=1 if args.quick else 4, repeat=1 if args.quick else 3)
        compare("checkpoint-ablation", baseline="no-native-checkpoint", sweeps=2 if args.quick else 12,
                repeat=1 if args.quick else 3)
        compare("parallel-baseline-ablation", fixture="summons", baseline="no-parallel-baselines", seed=53,
                sweeps=1 if args.quick else 4, repeat=1 if args.quick else 3)
        compare("parallel-board-ablation", baseline="no-parallel-boards", sweeps=2 if args.quick else 12,
                repeat=1 if args.quick else 3)
        compare("pack-ablation", baseline="before-pack", sweeps=2 if args.quick else 12, repeat=1 if args.quick else 3)
        compare("rollback-ablation", baseline="no-type-plans", sweeps=2 if args.quick else 12, repeat=1 if args.quick else 3)
        compare("native-ablation", baseline="no-native", sweeps=2 if args.quick else 12, repeat=1 if args.quick else 3)
        compare("native-summons-ablation", fixture="summons", baseline="no-native", seed=53,
                sweeps=1 if args.quick else 4, repeat=1 if args.quick else 3)
        for fixture, seed in (("standard", 17), ("equipped", 31), ("summons", 53),
                              *((variant, definition["seed"]) for variant, definition in SERIES_CHESS_FIXTURES.items())):
            report = output / ("stress-" + fixture + ".json")
            stage("stress-" + fixture, [sys.executable, str(ROOT / "tools" / "validate_full_ai.py"), "--stress-child",
                  "--fixture", fixture, "--seed", str(seed), "--stress-steps", str(16 if args.quick else args.stress_steps),
                  "--stress-seconds", str(args.stress_seconds), "--child-output", str(report)], report=report, stress=True)
        summary["status"] = "passed"
        summary["ready_for_live_test"] = not args.quick
        print("Quick smoke checks passed; run the full validation before acceptance." if args.quick else
              "Automatic checks passed. Gameplay smoothness still requires the four-board live test.", flush=True)
    except Exception as exc:
        summary.update(status="failed", error=str(exc))
        print(str(exc), file=sys.stderr, flush=True)
    finally:
        _write(summary_path, summary)
        print(f"Summary: {summary_path}", flush=True)
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        raise
