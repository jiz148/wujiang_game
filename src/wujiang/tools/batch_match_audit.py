from __future__ import annotations

import argparse
import json
import random
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from wujiang.heroes.registry import list_heroes
from wujiang.tools.match_audit import DEFAULT_MAX_STEPS, parse_roster, run_match_audit, sanitize_label, write_json, write_jsonl
from wujiang.web import ai as ai_policy


DEFAULT_BATCH_DIR = Path("reports") / "batch-audit"
HIGH_SIGNAL_CATEGORIES = {
    "action_error",
    "fallback_error",
    "decision_error",
    "ai_candidate_builder_error",
    "ai_payload_generation_gap",
    "ai_candidate_filtered",
    "match_error",
}


@dataclass(slots=True)
class BatchAuditResult:
    output_dir: Path
    summary_path: Path
    findings_path: Path
    report_path: Path
    match_count: int
    finding_count: int
    high_signal_count: int


def public_hero_codes() -> list[str]:
    return [str(hero["code"]) for hero in list_heroes()]


def build_match_plan(
    codes: Iterable[str],
    *,
    seed: int,
    rounds: int,
    team_size: int = 2,
    max_matches: Optional[int] = None,
) -> list[dict[str, Any]]:
    roster = [str(code).strip() for code in codes if str(code).strip()]
    if len(roster) < team_size * 2:
        raise ValueError(f"Need at least {team_size * 2} hero codes for {team_size}v{team_size} audits.")
    matches: list[dict[str, Any]] = []
    group_size = team_size * 2
    for round_index in range(max(1, int(rounds))):
        shuffled = list(roster)
        random.Random(seed + round_index).shuffle(shuffled)
        offset = (round_index * team_size) % len(shuffled)
        rotated = shuffled[offset:] + shuffled[:offset]
        for start in range(0, len(rotated), group_size):
            group = rotated[start : start + group_size]
            if len(group) < group_size:
                fill_index = 0
                while len(group) < group_size:
                    candidate = rotated[fill_index % len(rotated)]
                    fill_index += 1
                    if candidate in group:
                        continue
                    group.append(candidate)
            match = {
                "index": len(matches) + 1,
                "round": round_index + 1,
                "team1": group[:team_size],
                "team2": group[team_size:group_size],
                "seed": seed + len(matches),
            }
            matches.append(match)
            if max_matches is not None and len(matches) >= max_matches:
                return matches
    return matches


def run_batch_audit(
    codes: Optional[Iterable[str]] = None,
    *,
    seed: int = 1,
    rounds: int = 1,
    max_matches: Optional[int] = None,
    max_steps: int = DEFAULT_MAX_STEPS,
    difficulty: str = "standard",
    output_dir: Optional[Path | str] = None,
    label: Optional[str] = None,
) -> BatchAuditResult:
    roster = list(codes) if codes is not None else public_hero_codes()
    planned_matches = build_match_plan(roster, seed=seed, rounds=rounds, max_matches=max_matches)
    run_dir = Path(output_dir) if output_dir is not None else default_batch_output_dir(seed=seed, label=label)
    run_dir.mkdir(parents=True, exist_ok=True)

    all_findings: list[dict[str, Any]] = []
    match_summaries: list[dict[str, Any]] = []
    hero_coverage: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    severity_counts: Counter[str] = Counter()

    for match in planned_matches:
        team1 = list(match["team1"])
        team2 = list(match["team2"])
        for code in team1 + team2:
            hero_coverage[code] += 1
        match_label = f"match-{match['index']:03d}-{sanitize_label('-'.join(team1))}-vs-{sanitize_label('-'.join(team2))}"
        match_dir = run_dir / match_label
        try:
            result = run_match_audit(
                team1,
                team2,
                seed=int(match["seed"]),
                difficulty=difficulty,
                max_steps=max_steps,
                output_dir=match_dir,
                label=match_label,
            )
            findings = read_jsonl(result.findings_jsonl_path)
            match_summary = {
                **match,
                "output_dir": str(match_dir),
                "winner": result.winner,
                "steps": result.step_count,
                "finding_count": result.finding_count,
                "high_signal_count": count_high_signal(findings),
            }
        except Exception as exc:
            findings = [
                {
                    "id": "BATCH",
                    "severity": "error",
                    "category": "match_error",
                    "source": "batch_match_audit",
                    "message": f"Match failed before writing a complete audit: {type(exc).__name__}: {exc}",
                    "step": None,
                    "actor": None,
                    "payload": None,
                    "evidence": {"match": match},
                }
            ]
            match_summary = {
                **match,
                "output_dir": str(match_dir),
                "winner": None,
                "steps": 0,
                "finding_count": len(findings),
                "high_signal_count": count_high_signal(findings),
                "error": f"{type(exc).__name__}: {exc}",
            }
        for finding in findings:
            annotated = {
                **finding,
                "match_index": match["index"],
                "match_seed": match["seed"],
                "team1": team1,
                "team2": team2,
                "match_output_dir": str(match_dir),
            }
            all_findings.append(annotated)
            category_counts[str(finding.get("category"))] += 1
            severity_counts[str(finding.get("severity"))] += 1
        match_summaries.append(match_summary)

    summary = {
        "seed": seed,
        "difficulty": difficulty,
        "rounds": rounds,
        "max_matches": max_matches,
        "max_steps": max_steps,
        "hero_count": len(roster),
        "match_count": len(planned_matches),
        "finding_count": len(all_findings),
        "high_signal_count": count_high_signal(all_findings),
        "category_counts": dict(sorted(category_counts.items())),
        "severity_counts": dict(sorted(severity_counts.items())),
        "hero_coverage": dict(sorted(hero_coverage.items())),
        "matches": match_summaries,
    }

    summary_path = run_dir / "summary.json"
    findings_path = run_dir / "findings.jsonl"
    report_path = run_dir / "summary.md"
    write_json(summary_path, summary)
    write_jsonl(findings_path, all_findings)
    report_path.write_text(render_batch_markdown(summary, all_findings), encoding="utf-8")

    return BatchAuditResult(
        output_dir=run_dir,
        summary_path=summary_path,
        findings_path=findings_path,
        report_path=report_path,
        match_count=len(planned_matches),
        finding_count=len(all_findings),
        high_signal_count=count_high_signal(all_findings),
    )


def default_batch_output_dir(*, seed: int, label: Optional[str]) -> Path:
    suffix = sanitize_label(label) if label else "implemented-roster"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return DEFAULT_BATCH_DIR / f"{stamp}-seed{seed}-{suffix}"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def count_high_signal(findings: Iterable[dict[str, Any]]) -> int:
    return sum(1 for finding in findings if str(finding.get("category")) in HIGH_SIGNAL_CATEGORIES)


def top_high_signal_findings(findings: list[dict[str, Any]], *, limit: int = 25) -> list[dict[str, Any]]:
    return [
        finding
        for finding in findings
        if str(finding.get("category")) in HIGH_SIGNAL_CATEGORIES
    ][:limit]


def findings_by_hero(findings: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts: defaultdict[str, int] = defaultdict(int)
    for finding in findings:
        actor = finding.get("actor")
        if isinstance(actor, dict):
            key = str(actor.get("hero_code") or actor.get("name") or "unknown")
        else:
            key = "match"
        counts[key] += 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def render_batch_markdown(summary: dict[str, Any], findings: list[dict[str, Any]]) -> str:
    lines = [
        "# Batch Match Audit Summary",
        "",
        f"- Matches: `{summary['match_count']}`",
        f"- Heroes covered: `{summary['hero_count']}`",
        f"- Findings: `{summary['finding_count']}`",
        f"- High-signal findings: `{summary['high_signal_count']}`",
        f"- Max steps per match: `{summary['max_steps']}`",
        "",
        "## Category Counts",
        "",
    ]
    for category, count in summary["category_counts"].items():
        lines.append(f"- `{category}`: `{count}`")
    lines.extend(["", "## Findings By Hero", ""])
    for hero, count in findings_by_hero(findings).items():
        lines.append(f"- `{hero}`: `{count}`")
    lines.extend(["", "## High-Signal Findings", ""])
    high_signal = top_high_signal_findings(findings)
    if not high_signal:
        lines.append("- No high-signal findings recorded.")
    else:
        for finding in high_signal:
            actor = finding.get("actor") if isinstance(finding.get("actor"), dict) else {}
            lines.append(
                f"- Match `{finding.get('match_index')}` `{finding.get('category')}` "
                f"{actor.get('hero_code') or actor.get('name') or ''}: {finding.get('message')}"
            )
    lines.extend(["", "## Matches", ""])
    for match in summary["matches"]:
        lines.append(
            f"- `{match['index']:03d}` team1 `{', '.join(match['team1'])}` vs "
            f"team2 `{', '.join(match['team2'])}`: findings `{match['finding_count']}`, "
            f"high-signal `{match['high_signal_count']}`, steps `{match['steps']}`"
        )
    return "\n".join(lines).rstrip() + "\n"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run deterministic batch AI match audits over implemented heroes.")
    parser.add_argument("--codes", default=None, help="Optional comma-separated hero codes. Defaults to public selectable heroes.")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--max-matches", type=int, default=None)
    parser.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS)
    parser.add_argument("--difficulty", default="standard", choices=sorted(ai_policy.AI_DIFFICULTIES))
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--label", default=None)
    args = parser.parse_args(argv)

    codes = parse_roster(args.codes) if args.codes else None
    result = run_batch_audit(
        codes,
        seed=args.seed,
        rounds=args.rounds,
        max_matches=args.max_matches,
        max_steps=args.max_steps,
        difficulty=args.difficulty,
        output_dir=args.out,
        label=args.label,
    )
    print(f"wrote batch audit: {result.output_dir}")
    print(
        f"matches={result.match_count} findings={result.finding_count} "
        f"high_signal={result.high_signal_count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
