from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import time
import traceback
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from wujiang.tactical.heroes.registry import list_heroes
from wujiang.tools.batch_match_audit import HIGH_SIGNAL_CATEGORIES, read_jsonl
from wujiang.tools.match_audit import DEFAULT_MAX_STEPS, parse_roster, sanitize_label, write_json, write_jsonl
from wujiang.tactical.rooms import ai as ai_policy


DEFAULT_PER_HERO_DIR = Path("reports") / "per-hero-ai-debug"
DEFAULT_MATCHES_PER_HERO = 10
MATCH_PROGRESS_STALL_SECONDS = 60


@dataclass(slots=True)
class PerHeroDebugResult:
    output_dir: Path
    summary_path: Path
    findings_path: Path
    suspected_defects_path: Path
    report_path: Path
    target_count: int
    match_count: int
    finding_count: int
    high_signal_count: int


def public_hero_catalog() -> list[dict[str, Any]]:
    return [dict(hero) for hero in list_heroes()]


def public_hero_codes() -> list[str]:
    return [str(hero["code"]) for hero in public_hero_catalog()]


def build_per_hero_match_plan(
    targets: Iterable[str],
    roster: Iterable[str],
    *,
    seed: int,
    matches_per_hero: int = DEFAULT_MATCHES_PER_HERO,
) -> list[dict[str, Any]]:
    all_codes = [str(code).strip() for code in roster if str(code).strip()]
    plans: list[dict[str, Any]] = []
    for target_index, target in enumerate(str(code).strip() for code in targets if str(code).strip()):
        if target not in all_codes:
            raise ValueError(f"Unknown or non-public target hero code: {target}")
        pool = [code for code in all_codes if code != target]
        if len(pool) < 3:
            raise ValueError("Need at least four public heroes to build 2v2 per-hero debug matches.")
        rng = random.Random(seed + target_index * 1009)
        used_compositions: set[tuple[str, str, str]] = set()
        for match_index in range(max(1, int(matches_per_hero))):
            local_pool = list(pool)
            rng.shuffle(local_pool)
            teammate = local_pool[0]
            opponents = local_pool[1:3]
            composition = (teammate, opponents[0], opponents[1])
            if composition in used_compositions:
                # A deterministic retry keeps duplicate teams rare without making planning fragile.
                for offset in range(1, len(local_pool)):
                    rotated = local_pool[offset:] + local_pool[:offset]
                    candidate = (rotated[0], rotated[1], rotated[2])
                    if candidate not in used_compositions:
                        teammate = rotated[0]
                        opponents = rotated[1:3]
                        composition = candidate
                        break
            used_compositions.add(composition)
            plans.append(
                {
                    "target": target,
                    "target_match_index": match_index + 1,
                    "global_match_index": len(plans) + 1,
                    "team1": [target, teammate],
                    "team2": opponents,
                    "seed": seed + len(plans),
                }
            )
    return plans


def run_per_hero_ai_debug(
    targets: Iterable[str],
    *,
    seed: int = 1,
    matches_per_hero: int = DEFAULT_MATCHES_PER_HERO,
    max_steps: int = DEFAULT_MAX_STEPS,
    difficulty: str = "standard",
    output_dir: Optional[Path | str] = None,
    label: Optional[str] = None,
    resume: bool = False,
    progress: bool = False,
) -> PerHeroDebugResult:
    catalog = public_hero_catalog()
    heroes_by_code = {str(hero["code"]): hero for hero in catalog}
    target_codes = [str(code).strip() for code in targets if str(code).strip()]
    if not target_codes:
        target_codes = [str(hero["code"]) for hero in catalog]
    plan = build_per_hero_match_plan(target_codes, heroes_by_code.keys(), seed=seed, matches_per_hero=matches_per_hero)
    run_dir = Path(output_dir) if output_dir is not None else default_output_dir(seed=seed, label=label)
    run_dir.mkdir(parents=True, exist_ok=True)

    all_findings: list[dict[str, Any]] = []
    suspected_defects: list[dict[str, Any]] = []
    match_summaries: list[dict[str, Any]] = []
    category_counts: Counter[str] = Counter()
    severity_counts: Counter[str] = Counter()
    target_counts: Counter[str] = Counter()
    target_high_signal: Counter[str] = Counter()
    target_ordinals = {code: index + 1 for index, code in enumerate(target_codes)}
    announced_target: str | None = None

    for match in plan:
        target = str(match["target"])
        if progress and target != announced_target:
            announced_target = target
            hero_name = str(heroes_by_code.get(target, {}).get("name") or target)
            print(
                f"[{target_ordinals[target]}/{len(target_codes)}] auditing {hero_name} ({target}) "
                f"with {matches_per_hero} matches...",
                flush=True,
            )
        target_counts[target] += 1
        match_label = (
            f"{sanitize_label(target)}-{int(match['target_match_index']):02d}-"
            f"{sanitize_label('-'.join(match['team1']))}-vs-{sanitize_label('-'.join(match['team2']))}"
        )
        match_dir = run_dir / sanitize_label(target) / match_label
        if progress:
            print(
                f"  [{int(match['target_match_index'])}/{matches_per_hero}] starting; "
                f"team1={','.join(match['team1'])}; team2={','.join(match['team2'])}; "
                f"live-progress={match_dir / 'match_progress.json'}",
                flush=True,
            )
        try:
            manifest = (
                completed_audit_manifest(
                    match_dir,
                    team1=match["team1"],
                    team2=match["team2"],
                    seed=int(match["seed"]),
                    difficulty=difficulty,
                    max_steps=max_steps,
                )
                if resume
                else None
            )
            resumed = manifest is not None
            findings_file = match_dir / "findings.jsonl"
            if manifest is not None:
                findings = read_jsonl(findings_file)
                winner = manifest.get("winner")
                steps = int(manifest.get("steps_executed") or 0)
                finding_count = len(findings)
                report_file = match_dir / "battle_report.md"
                findings_markdown_file = match_dir / "findings.md"
            else:
                manifest = run_match_audit_guarded(
                    match_dir, team1=match["team1"], team2=match["team2"],
                    seed=int(match["seed"]), difficulty=difficulty, max_steps=max_steps,
                )
                findings = read_jsonl(findings_file)
                winner = manifest.get("winner")
                steps = int(manifest.get("steps_executed") or 0)
                finding_count = len(findings)
                report_file = match_dir / "battle_report.md"
                findings_markdown_file = match_dir / "findings.md"
            match_summary = {
                **match,
                "output_dir": str(match_dir),
                "battle_report": str(report_file),
                "findings_markdown": str(findings_markdown_file),
                "winner": winner,
                "steps": steps,
                "finding_count": finding_count,
                "high_signal_count": count_high_signal(findings),
                "resumed": resumed,
            }
        except Exception as exc:
            failure_traceback = traceback.format_exc()
            live_progress = read_match_progress(match_dir)
            progress_actor = live_progress.get("actor") if isinstance(live_progress.get("actor"), dict) else None
            findings = [
                {
                    "id": "PER-HERO",
                    "severity": "error",
                    "category": "match_error",
                    "source": "per_hero_ai_debug",
                    "message": f"Match failed before writing a complete audit: {type(exc).__name__}: {exc}",
                    "step": int(live_progress.get("step") or 0),
                    "actor": progress_actor,
                    "payload": None,
                    "evidence": {
                        "match": match, "traceback": failure_traceback,
                        "live_progress": live_progress,
                        "partial_trace": str(match_dir / "trace.jsonl"),
                        "slow_step_stack": str(match_dir / "slow_step_stack.log"),
                        "worker_stderr": str(match_dir / "worker_stderr.log"),
                    },
                }
            ]
            match_summary = {
                **match,
                "output_dir": str(match_dir),
                "battle_report": None,
                "findings_markdown": None,
                "winner": None,
                "steps": int(live_progress.get("step") or 0),
                "finding_count": len(findings),
                "high_signal_count": count_high_signal(findings),
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": failure_traceback,
            }

        if progress:
            outcome = "reused" if match_summary.get("resumed") else "finished"
            if match_summary.get("error"):
                outcome = "error"
            print(
                f"  [{int(match['target_match_index'])}/{matches_per_hero}] {outcome}; "
                f"winner={match_summary.get('winner')}; steps={match_summary.get('steps')}; "
                f"findings={match_summary.get('finding_count')}; high-signal={match_summary.get('high_signal_count')}",
                flush=True,
            )
            if match_summary.get("error"):
                print(
                    f"    {match_summary['error']}; progress={match_dir / 'match_progress.json'}; "
                    f"stack={match_dir / 'slow_step_stack.log'}; partial-trace={match_dir / 'trace.jsonl'}",
                    flush=True,
                )

        for finding in findings:
            actor = finding.get("actor") if isinstance(finding.get("actor"), dict) else {}
            actor_code = str(actor.get("hero_code") or "")
            attributed_code = actor_code or target
            annotated = {
                **finding,
                "target": target,
                "attributed_code": attributed_code,
                "attributed_name": actor.get("name") or heroes_by_code.get(attributed_code, {}).get("name") or attributed_code,
                "target_match_index": match["target_match_index"],
                "global_match_index": match["global_match_index"],
                "match_seed": match["seed"],
                "team1": list(match["team1"]),
                "team2": list(match["team2"]),
                "match_output_dir": str(match_dir),
                "battle_report": match_summary.get("battle_report"),
            }
            all_findings.append(annotated)
            category_counts[str(finding.get("category"))] += 1
            severity_counts[str(finding.get("severity"))] += 1
            if is_high_signal(finding):
                target_high_signal[attributed_code] += 1
                suspected_defects.append(defect_stub(annotated, heroes_by_code.get(attributed_code, {})))
        match_summaries.append(match_summary)

    summary = {
        "seed": seed,
        "difficulty": difficulty,
        "matches_per_hero": matches_per_hero,
        "max_steps": max_steps,
        "target_count": len(target_codes),
        "match_count": len(plan),
        "finding_count": len(all_findings),
        "high_signal_count": count_high_signal(all_findings),
        "category_counts": dict(sorted(category_counts.items())),
        "severity_counts": dict(sorted(severity_counts.items())),
        "target_match_counts": dict(sorted(target_counts.items())),
        "target_high_signal_counts": dict(sorted(target_high_signal.items())),
        "questionnaire": questionnaire_status(),
        "targets": [target_context(code, heroes_by_code.get(code, {})) for code in target_codes],
        "matches": match_summaries,
    }

    summary_path = run_dir / "summary.json"
    findings_path = run_dir / "findings.jsonl"
    suspected_defects_path = run_dir / "suspected_defects.jsonl"
    report_path = run_dir / "summary.md"
    write_json(summary_path, summary)
    write_jsonl(findings_path, all_findings)
    write_jsonl(suspected_defects_path, suspected_defects)
    report_path.write_text(render_markdown(summary, all_findings, suspected_defects), encoding="utf-8")

    return PerHeroDebugResult(
        output_dir=run_dir,
        summary_path=summary_path,
        findings_path=findings_path,
        suspected_defects_path=suspected_defects_path,
        report_path=report_path,
        target_count=len(target_codes),
        match_count=len(plan),
        finding_count=len(all_findings),
        high_signal_count=count_high_signal(all_findings),
    )


def read_match_progress(match_dir: Path) -> dict[str, Any]:
    try:
        value = json.loads((match_dir / "match_progress.json").read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def run_match_audit_guarded(
    match_dir: Path,
    *,
    team1: Iterable[str],
    team2: Iterable[str],
    seed: int,
    difficulty: str,
    max_steps: int,
) -> dict[str, Any]:
    """Keep one slow AI decision from blocking all remaining match reports."""
    team1 = list(team1)
    team2 = list(team2)
    match_dir.mkdir(parents=True, exist_ok=True)
    for name in ("manifest.json", "match_progress.json", "slow_step_stack.log"):
        (match_dir / name).unlink(missing_ok=True)
    project_root = Path(__file__).resolve().parents[3]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(project_root / "src"), environment.get("PYTHONPATH", "")) if part
    )
    command = [
        sys.executable, "-u", "-m", "wujiang.tools.match_audit",
        "--team1", ",".join(team1), "--team2", ",".join(team2),
        "--seed", str(seed), "--difficulty", difficulty,
        "--max-steps", str(max_steps), "--out", str(match_dir.resolve()),
    ]
    progress_path = match_dir / "match_progress.json"
    with (match_dir / "worker_stdout.log").open("w", encoding="utf-8") as stdout, \
            (match_dir / "worker_stderr.log").open("w", encoding="utf-8") as stderr:
        process = subprocess.Popen(command, cwd=project_root, env=environment, stdout=stdout, stderr=stderr)
        last_progress_stamp: int | None = None
        last_progress_at = time.monotonic()
        try:
            while process.poll() is None:
                try:
                    stamp = progress_path.stat().st_mtime_ns
                except OSError:
                    stamp = None
                if stamp is not None and stamp != last_progress_stamp:
                    last_progress_stamp = stamp
                    last_progress_at = time.monotonic()
                if time.monotonic() - last_progress_at >= MATCH_PROGRESS_STALL_SECONDS:
                    live = read_match_progress(match_dir)
                    raise TimeoutError(
                        f"No match progress for {MATCH_PROGRESS_STALL_SECONDS}s; "
                        f"step={live.get('step')}, stage={live.get('stage')}, "
                        f"actor={live.get('actor')}, action={live.get('skill_code') or live.get('action_type')}; "
                        f"stack={match_dir / 'slow_step_stack.log'}"
                    )
                time.sleep(0.5)
            if process.returncode != 0:
                raise RuntimeError(
                    f"Match worker exited with code {process.returncode}; "
                    f"details={match_dir / 'worker_stderr.log'}"
                )
        except BaseException:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            raise
    manifest = completed_audit_manifest(
        match_dir, team1=team1, team2=team2, seed=seed,
        difficulty=difficulty, max_steps=max_steps,
    )
    if manifest is None:
        raise RuntimeError(f"Match worker did not write a complete audit; details={match_dir}")
    return manifest


def completed_audit_manifest(
    match_dir: Path,
    *,
    team1: Iterable[str],
    team2: Iterable[str],
    seed: int,
    difficulty: str,
    max_steps: int,
) -> Optional[dict[str, Any]]:
    """Return a reusable manifest only when the entire match output matches this plan."""
    required_files = (
        match_dir / "manifest.json",
        match_dir / "trace.jsonl",
        match_dir / "battle_report.md",
        match_dir / "findings.jsonl",
        match_dir / "findings.md",
    )
    if not all(path.is_file() for path in required_files):
        return None
    try:
        manifest = json.loads(required_files[0].read_text(encoding="utf-8"))
        expected = {
            "team1": [str(code) for code in team1],
            "team2": [str(code) for code in team2],
            "seed": int(seed),
            "difficulty": str(difficulty),
            "max_steps": int(max_steps),
        }
        if any(manifest.get(key) != value for key, value in expected.items()):
            return None
        steps = int(manifest.get("steps_executed"))
        if steps < 0 or steps > int(max_steps):
            return None
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return manifest


def default_output_dir(*, seed: int, label: Optional[str]) -> Path:
    suffix = sanitize_label(label) if label else "per-hero"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return DEFAULT_PER_HERO_DIR / f"{stamp}-seed{seed}-{suffix}"


def questionnaire_status() -> dict[str, Any]:
    path = Path("docs") / "武将实现问题清单.xlsx"
    return {
        "path": str(path),
        "exists": path.exists(),
        "note": "Use saved answers when available; older handwritten heroes may not have questionnaire answers.",
    }


def target_context(code: str, hero: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": code,
        "name": hero.get("name"),
        "raw_skill_text": hero.get("raw_skill_text"),
        "raw_trait_text": hero.get("raw_trait_text"),
        "questionnaire_status": questionnaire_status(),
    }


def is_high_signal(finding: dict[str, Any]) -> bool:
    return str(finding.get("category")) in HIGH_SIGNAL_CATEGORIES


def count_high_signal(findings: Iterable[dict[str, Any]]) -> int:
    return sum(1 for finding in findings if is_high_signal(finding))


def defect_stub(finding: dict[str, Any], hero: dict[str, Any]) -> dict[str, Any]:
    actor = finding.get("actor") if isinstance(finding.get("actor"), dict) else {}
    return {
        "status": "needs_manual_rule_review",
        "severity": finding.get("severity"),
        "category": finding.get("category"),
        "target": finding.get("target"),
        "attributed_code": finding.get("attributed_code"),
        "attributed_name": finding.get("attributed_name"),
        "target_name": hero.get("name"),
        "actor_code": actor.get("hero_code"),
        "actor_name": actor.get("name"),
        "seed": finding.get("match_seed"),
        "teams": {"team1": finding.get("team1"), "team2": finding.get("team2")},
        "battle_report": finding.get("battle_report"),
        "observed_behavior": finding.get("message"),
        "expected_behavior": "",
        "source_evidence": {
            "raw_skill_text": hero.get("raw_skill_text"),
            "raw_trait_text": hero.get("raw_trait_text"),
            "questionnaire": questionnaire_status(),
        },
        "proposed_fix": "",
    }


def findings_by_target(findings: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for finding in findings:
        counts[str(finding.get("target") or "unknown")] += 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def render_markdown(
    summary: dict[str, Any],
    findings: list[dict[str, Any]],
    suspected_defects: list[dict[str, Any]],
) -> str:
    lines = [
        "# Per-Hero AI Debug Summary",
        "",
        f"- Targets: `{summary['target_count']}`",
        f"- Matches: `{summary['match_count']}`",
        f"- Matches per hero: `{summary['matches_per_hero']}`",
        f"- Findings: `{summary['finding_count']}`",
        f"- High-signal findings: `{summary['high_signal_count']}`",
        f"- Questionnaire file: `{summary['questionnaire']['path']}` exists `{summary['questionnaire']['exists']}`",
        "",
        "## Category Counts",
        "",
    ]
    for category, count in summary["category_counts"].items():
        lines.append(f"- `{category}`: `{count}`")
    lines.extend(["", "## Findings By Target", ""])
    for target, count in findings_by_target(findings).items():
        lines.append(f"- `{target}`: `{count}`")
    lines.extend(["", "## Suspected Defects Requiring Manual Rule Review", ""])
    if not suspected_defects:
        lines.append("- No high-signal suspected defects recorded by the automated audit.")
    else:
        for defect in suspected_defects[:50]:
            lines.append(
                f"- `{defect['target']}` seed `{defect['seed']}` `{defect['category']}` "
                f"actor `{defect.get('actor_code')}`: {defect.get('observed_behavior')}"
            )
    lines.extend(["", "## Matches", ""])
    for match in summary["matches"]:
        lines.append(
            f"- `{match['target']}` #{match['target_match_index']}: "
            f"`{', '.join(match['team1'])}` vs `{', '.join(match['team2'])}`, "
            f"seed `{match['seed']}`, findings `{match['finding_count']}`, "
            f"high-signal `{match['high_signal_count']}`, report `{match.get('battle_report')}`"
        )
    return "\n".join(lines).rstrip() + "\n"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the 10-match per-hero AI debug workflow.")
    parser.add_argument("--targets", default=None, help="Comma-separated target hero codes. Defaults to all public heroes.")
    parser.add_argument("--limit", type=int, default=None, help="Limit targets after resolving --targets/default roster.")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--matches-per-hero", type=int, default=DEFAULT_MATCHES_PER_HERO)
    parser.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS)
    parser.add_argument("--difficulty", default="standard", choices=sorted(ai_policy.AI_DIFFICULTIES))
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--label", default=None)
    parser.add_argument("--resume", action="store_true", help="Reuse complete match folders already present under --out.")
    args = parser.parse_args(argv)

    targets = parse_roster(args.targets) if args.targets else public_hero_codes()
    if args.limit is not None:
        targets = targets[: max(0, int(args.limit))]
    result = run_per_hero_ai_debug(
        targets,
        seed=args.seed,
        matches_per_hero=args.matches_per_hero,
        max_steps=args.max_steps,
        difficulty=args.difficulty,
        output_dir=args.out,
        label=args.label,
        resume=args.resume,
        progress=True,
    )
    print(f"wrote per-hero AI debug: {result.output_dir}")
    print(
        f"targets={result.target_count} matches={result.match_count} "
        f"findings={result.finding_count} high_signal={result.high_signal_count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
