"""Record or verify a batch of hero-kit migrations with fixed witnesses."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def workspace_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def run_logged(args: list[str], log: Path, timeout: int) -> bool:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC)
    env["PYTHONIOENCODING"] = "utf-8"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w", encoding="utf-8") as output:
        try:
            result = subprocess.run(args, cwd=ROOT, env=env, stdout=output,
                                    stderr=subprocess.STDOUT, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            output.write(f"\nTIMEOUT after {timeout}s\n")
            return False
    return result.returncode == 0


def normalized_findings(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [{key: value for key, value in json.loads(line).items()
             if key not in {"battle_report", "match_output_dir"}}
            for line in path.read_text(encoding="utf-8").splitlines()]


def compare_audits(before: Path, after: Path, code: str) -> tuple[bool, int, bool]:
    traces = list(before.glob(f"{code}/*/trace.jsonl"))
    if not traces:
        return False, 0, False
    matching = all((after / trace.relative_to(before)).is_file()
                   and trace.read_bytes() == (after / trace.relative_to(before)).read_bytes()
                   for trace in traces)
    matching &= len(list(after.glob(f"{code}/*/trace.jsonl"))) == len(traces)
    findings_equal = normalized_findings(before / "findings.jsonl") == normalized_findings(after / "findings.jsonl")
    return matching, len(traces), findings_equal


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="JSON list of {code, test_class}; reusable for baseline and verify")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--mode", choices=("baseline", "verify"), default="verify")
    parser.add_argument("--baseline-root", type=Path,
                        help="Baseline output directory; defaults to a pre/ sibling of --out")
    parser.add_argument("--matches-per-hero", type=int, default=2)
    parser.add_argument("--max-steps", type=int, default=150)
    parser.add_argument("--timeout-seconds", type=int, default=90)
    parser.add_argument("--workers", type=int, default=1,
                        help="Independent hero audit subprocesses to run together")
    args = parser.parse_args()

    heroes = json.loads(workspace_path(args.manifest).read_text(encoding="utf-8"))
    if not isinstance(heroes, list) or not heroes:
        parser.error("manifest must be a nonempty JSON list")
    if args.workers < 1:
        parser.error("--workers must be positive")
    codes = [item["code"] for item in heroes]
    if len(codes) != len(set(codes)):
        parser.error("manifest contains duplicate hero codes")
    output_root = workspace_path(args.out)
    baseline_root = (workspace_path(args.baseline_root) if args.baseline_root is not None
                     else output_root.parent / "pre")
    def verify_one(item: dict) -> dict:
        code, test_class = item["code"], item["test_class"]
        folder = output_root / code
        snapshot, audit = folder / "snapshot.json", folder / "audit"
        snapshot_args = [sys.executable, str(ROOT / "tools/oop_assembly_snapshot.py"), code,
                         "--out", str(snapshot)]
        if args.mode == "verify":
            snapshot_args += ["--compare", str(baseline_root / code / "snapshot.json")]
        snapshot_ok = run_logged(snapshot_args, folder / "snapshot.log", args.timeout_seconds)
        audit_args = [sys.executable, str(ROOT / "detail_test_all_wujiangs.py"),
                      "--targets", code, "--regression-filter", test_class,
                      "--matches-per-hero", str(args.matches_per_hero),
                      "--max-steps", str(args.max_steps), "--out", str(audit)]
        audit_ok = run_logged(audit_args, folder / "audit.log", args.timeout_seconds)
        traces_equal = findings_equal = args.mode == "baseline"
        trace_count = len(list(audit.glob(f"{code}/*/trace.jsonl")))
        trace_count_ok = trace_count == args.matches_per_hero
        findings_file_ok = (audit / "findings.jsonl").is_file()
        if audit_ok and args.mode == "verify":
            traces_equal, trace_count, findings_equal = compare_audits(
                baseline_root / code / "audit", audit, code)
        row = {"code": code, "snapshot_ok": snapshot_ok, "audit_ok": audit_ok,
               "trace_count_ok": trace_count_ok, "trace_count": trace_count,
               "findings_file_ok": findings_file_ok, "traces_equal": traces_equal,
               "findings_equal": findings_equal}
        return row

    report: list[dict | None] = [None] * len(heroes)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        jobs = {pool.submit(verify_one, item): index for index, item in enumerate(heroes)}
        for job in as_completed(jobs):
            row = job.result()
            report[jobs[job]] = row
            print(json.dumps(row, ensure_ascii=False), flush=True)
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if all(all(value for key, value in row.items() if key.endswith("_ok") or key.endswith("_equal"))
                    for row in report) else 1


if __name__ == "__main__":
    raise SystemExit(main())
