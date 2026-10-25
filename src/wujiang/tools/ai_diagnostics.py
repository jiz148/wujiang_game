"""Bounded offline observations; never unpickle a diagnostic witness."""
from __future__ import annotations

from collections import deque
from contextlib import contextmanager
import cProfile
import hashlib
from itertools import zip_longest
import json
from pathlib import Path
import pickletools
import pstats
import struct


def pickle_difference(before, after):
    """First opcode difference, ignoring frames; hints are not field ownership."""
    def operations(data):
        for opcode, argument, _ in pickletools.genops(data):
            if opcode.name != "FRAME":
                yield opcode.name, argument
    def comparable(item):
        if item is None:
            return None
        name, argument = item
        return name, struct.pack("!d", argument) if type(argument) is float else argument
    def describe(item):
        if item is None:
            return None
        name, argument = item
        if type(argument) in (str, bytes):
            argument = argument[:120]
        return {"opcode": name, "argument": repr(argument)[:180]}
    context = deque(maxlen=8)
    try:
        for ordinal, (left, right) in enumerate(zip_longest(operations(before), operations(after))):
            if comparable(left) != comparable(right):
                return {"opcode_index": ordinal, "before": describe(left), "after": describe(right),
                        "previous_text": list(context)}
            if left is not None and type(left[1]) is str:
                context.append(left[1][:100])
        return {"opcode_index": None, "same_non_frame_operations": True}
    except Exception as exc:
        return {"error": type(exc).__name__}


def _snapshot_summary(key):
    if key is None:
        return None
    return {"bytes": len(key[0]), "sha256": hashlib.sha256(key[0]).hexdigest(),
            "rng_sha256": hashlib.sha256(repr(key[1]).encode()).hexdigest(),
            "unit_binding_count": len(key[2]), "field_order": list(key[3])}


class BaselineDiagnostics:
    """Observe selected steps using immutable witnesses already read by guards."""
    def __init__(self, directory, profile_steps, *, max_samples=8, max_bytes=8 * 1024 * 1024, verbose=False):
        self.directory = Path(directory)
        self.profile_steps = frozenset(profile_steps)
        self.max_samples, self.max_bytes = max_samples, max_bytes
        self.verbose = verbose
        self.captured_bytes = 0
        self.truncated = False
        self.samples = []
        self.profiles = []
        self._seen = set()
        self._pending = []
        self._status = None
        self._profiler = None

    def record(self, event):
        if self._status is None:
            return
        identity = self._status["step"], event["reason"]
        if identity in self._seen:
            return
        self._seen.add(identity)
        size = sum(len(key[0]) for key in (event["before"], event["after"]) if key is not None)
        if len(self.samples) + len(self._pending) >= self.max_samples or self.captured_bytes + size > self.max_bytes:
            self.truncated = True
            return
        self.captured_bytes += size
        self._pending.append((dict(self._status), dict(event)))

    @contextmanager
    def step(self, info):
        if info["step"] not in self.profile_steps:
            yield
            return
        from wujiang.tactical.rooms.ai_acceleration import baseline_diagnostic_scope
        previous = self._status
        self._status = dict(info)
        if self.verbose:
            print(f"Profiling step {info['step']}, board {info['board']}, actor {info['actor']}, units {info['unit_count']}...", flush=True)
        profiler = cProfile.Profile()
        try:
            with baseline_diagnostic_scope(self.record), profiler:
                yield
        finally:
            self._status = previous
            self._profiler = profiler

    def flush_step(self, info):
        # All disk writes, disassembly and summaries happen after timing and
        # profiling the action. No game object or live callback reaches here.
        self.directory.mkdir(parents=True, exist_ok=True)
        if self._profiler is not None:
            path = self.directory / f"step-{info['step']:03d}-board-{info['board']}.prof"
            self._profiler.dump_stats(str(path))
            stats = pstats.Stats(self._profiler)
            self.profiles.append({**info, "path": str(path), "top_cumulative": [
                {"function": f"{name[0]}:{name[1]}:{name[2]}", "calls": values[1],
                 "self_seconds": values[2], "cumulative_seconds": values[3]}
                for name, values in sorted(stats.stats.items(), key=lambda item: item[1][3], reverse=True)[:40]]})
            self._profiler = None
            if self.verbose:
                print(f"Profile saved: {path}", flush=True)
        pending, self._pending = self._pending, []
        for status, event in pending:
            sample = {**status, "reason": event["reason"], "actor_id": event["actor_id"],
                      "unit_ids": list(event["unit_ids"]), "include_pending": event["include_pending"],
                      "ids_before": event["ids_before"], "ids_after": event["ids_after"],
                      "before": _snapshot_summary(event["before"]), "after": _snapshot_summary(event["after"])}
            for name in ("before", "after"):
                key = event[name]
                if key is not None:
                    path = self.directory / f"step-{status['step']:03d}-{event['reason']}-{name}.pickle"
                    path.write_bytes(key[0])
                    sample[name]["path"] = str(path)
            if event["before"] is not None and event["after"] is not None:
                before, after = event["before"][0], event["after"][0]
                sample["first_pickle_difference"] = pickle_difference(before, after)
                try:
                    from wujiang.tactical.rooms.ai_acceleration import _transfer_fingerprint
                    sample["same_transfer_fingerprint"] = _transfer_fingerprint(before) == _transfer_fingerprint(after)
                except Exception as exc:
                    sample["fingerprint_error"] = type(exc).__name__
            self.samples.append(sample)
        (self.directory / "index.json").write_text(json.dumps(self.report(), ensure_ascii=False, indent=2), encoding="utf-8")

    def report(self):
        visited = {item["step"] for item in self.profiles}
        return {"profile_steps": sorted(self.profile_steps),
                "missing_profile_steps": sorted(self.profile_steps - visited),
                "samples": self.samples, "profiles": self.profiles,
                "captured_bytes": self.captured_bytes, "truncated": self.truncated,
                "not_a_speed_benchmark": True,
                "note": "First failed guard only; skipped guards remain unobserved. Snapshot bytes are witnesses, never unpickled. Opcode context is a hint. Profiling adds overhead; no speedup or equivalence acceptance is inferred."}
