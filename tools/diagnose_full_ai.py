"""Investigate dense late-chess baselines without the full validation suite."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main():
    from wujiang.tools.ai_validation import validation_environment
    from wujiang.tools.ai_scenarios import SERIES_CHESS_FIXTURES
    defaults = {"electronic-heavy": (200, (117, 137, 196), SERIES_CHESS_FIXTURES["electronic-heavy"]["seed"]),
                "hell-heavy": (128, (45, 67, 91), SERIES_CHESS_FIXTURES["hell-heavy"]["seed"]),
                "standard": (208, (2, 46, 206), 17), "equipped": (64, (2, 46), 31),
                "summons": (64, (2, 46), 53)}
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", choices=tuple(defaults), default="electronic-heavy")
    parser.add_argument("--steps", type=int)
    parser.add_argument("--profile-steps", help="Comma-separated step numbers; defaults to the known slow steps")
    parser.add_argument("--seconds", type=int, default=1200)
    parser.add_argument("--workers", type=int, choices=range(5), default=4)
    parser.add_argument("--output-dir")
    args = parser.parse_args()
    default_steps, selected, seed = defaults[args.fixture]
    steps = args.steps if args.steps is not None else default_steps
    try:
        selected = tuple(int(value) for value in args.profile_steps.split(",")) if args.profile_steps else selected
    except ValueError:
        parser.error("--profile-steps must contain integers")
    if steps < 1 or args.seconds < 1 or not selected or any(value < 1 or value > steps for value in selected):
        parser.error("Positive limits and profile steps within --steps are required")
    output = Path(args.output_dir) if args.output_dir else ROOT / "reports" / (
        "full-ai-diagnosis-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    if not output.is_absolute():
        output = ROOT / output
    output.mkdir(parents=True, exist_ok=False)
    os.environ.update({key: value for key, value in validation_environment(args.workers).items()
                       if key.startswith("WUJIANG_AI_")})
    from wujiang.tools.ai_diagnostics import BaselineDiagnostics
    from wujiang.tools.ai_validation import stress_chess
    from wujiang.platform.ai_build import build_info
    report_path = output / "diagnosis.json"
    print(f"Diagnostic directory: {output}", flush=True)
    print(f"Fixture: {args.fixture}; steps: {steps}; separate call profiles at: {selected}", flush=True)
    print("Profiling adds overhead; this run reports causes, not a speedup percentage.", flush=True)
    try:
        result = stress_chess(seed=seed, variant=args.fixture, steps=steps, seconds=args.seconds,
                              output=report_path, diagnostics=BaselineDiagnostics(output / "details", selected, verbose=True))
        complete = result["complete"] and not result["baseline_diagnostics"]["missing_profile_steps"]
        print(f"Complete: {complete}; report: {report_path}", flush=True)
        return 0 if complete else 2
    except Exception as exc:
        report_path.write_text(json.dumps({"status": "failed", "build": build_info(),
            "error": type(exc).__name__, "traceback": traceback.format_exc()}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Diagnosis failed; details retained in {report_path}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
