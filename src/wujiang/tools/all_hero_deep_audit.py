from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Optional

from wujiang.tools.audit_review_packet import write_review_packet

from wujiang.tactical.heroes.registry import create_hero
from wujiang.tactical.rooms import ai as ai_policy
from wujiang.tools.match_audit import DEFAULT_MAX_STEPS, parse_roster, sanitize_label, write_json
from wujiang.tools.per_hero_ai_debug import (
    is_high_signal,
    public_hero_catalog,
    public_hero_codes,
    run_per_hero_ai_debug,
)


DEFAULT_OUTPUT_ROOT = Path("reports") / "all-hero-deep-audit"
DEFAULT_CURRENT_OUTPUT_ROOT = Path("reports") / "current-hero-deep-audit"
DEFAULT_DESIGN_PATH = Path("docs") / "武将设计思想.json"
DEFAULT_REVIEW_STATE_PATH = Path("docs") / "武将符合性审查.json"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_design_profiles(path: Path = DEFAULT_DESIGN_PATH) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    payload = read_json(path)
    return {
        str(item["code"]): dict(item)
        for item in payload.get("heroes", [])
        if isinstance(item, dict) and item.get("code")
    }


def load_current_hero_codes(path: Path = DEFAULT_REVIEW_STATE_PATH) -> list[str]:
    """Load the explicit one-hero or small-batch review target without guessing from code changes."""
    if not path.exists():
        raise ValueError(f"Current-hero review state does not exist: {path}")
    payload = read_json(path)
    raw_codes = payload.get("current_hero_codes")
    if raw_codes is None and payload.get("current_hero_code"):
        raw_codes = [payload["current_hero_code"]]
    if not isinstance(raw_codes, list):
        raise ValueError(f"current_hero_codes is missing from {path}")
    codes = list(dict.fromkeys(str(code).strip() for code in raw_codes if str(code).strip()))
    if not codes:
        raise ValueError(f"current_hero_codes is empty in {path}")
    return codes


def default_output_dir(seed: int) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return DEFAULT_OUTPUT_ROOT / f"{stamp}-seed{seed}"


def current_batch_output_dir(codes: Iterable[str]) -> Path:
    label = "--".join(sanitize_label(str(code)) for code in codes)
    return DEFAULT_CURRENT_OUTPUT_ROOT / label


def run_consolidated_regression(run_dir: Path) -> dict[str, Any]:
    """Run the complete ordinary test suite and preserve its output without blocking the AI audit."""
    command = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"]
    print("[regression] running the complete tests directory before hero simulations...", flush=True)
    started = time.perf_counter()
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    try:
        completed = subprocess.run(
            command,
            cwd=Path.cwd(),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        output = completed.stdout or ""
        return_code = int(completed.returncode)
        launch_error = None
    except OSError as exc:
        output = f"Unable to launch consolidated regression: {type(exc).__name__}: {exc}\n"
        return_code = -1
        launch_error = f"{type(exc).__name__}: {exc}"
    duration_seconds = round(time.perf_counter() - started, 3)
    (run_dir / "regression_tests.log").write_text(output, encoding="utf-8")
    ran_match = re.search(r"Ran\s+(\d+)\s+tests?\s+in\s+([0-9.]+)s", output)
    result_lines = re.findall(r"^(OK(?:\s+\([^\n]+\))?|FAILED\s+\([^\n]+\))$", output, flags=re.MULTILINE)
    summary = {
        "command": command,
        "return_code": return_code,
        "passed": return_code == 0,
        "duration_seconds": duration_seconds,
        "test_count": int(ran_match.group(1)) if ran_match else None,
        "unittest_result": result_lines[-1] if result_lines else None,
        "launch_error": launch_error,
        "log": "regression_tests.log",
        "note": "Regression failures do not stop the per-hero AI audit; inspect the log and review queue together.",
    }
    write_json(run_dir / "regression_tests.json", summary)
    print(
        f"[regression] complete; return-code={return_code}; tests={summary['test_count']}; "
        f"details={run_dir / 'regression_tests.log'}",
        flush=True,
    )
    return summary


def action_code(event: dict[str, Any]) -> str:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    action_type = str(payload.get("type") or "unknown")
    if action_type == "skill":
        return str(payload.get("skill_code") or "skill")
    if action_type == "chain_react":
        return str(payload.get("action_code") or "chain_react")
    if action_type == "attack":
        return str(payload.get("attack_variant") or "attack")
    return action_type


def active_skill_codes(hero_code: str) -> list[str]:
    hero = create_hero(hero_code, 1)
    return sorted(
        {
            str(skill.code)
            for skill in hero.skills
            if str(getattr(skill, "timing", "active")) in {"active", "instant"}
        }
    )


def unit_code(state: Any) -> str:
    return str(state.get("hero_code") or "") if isinstance(state, dict) else ""


def position_distance(before: Any, after: Any) -> int:
    if not isinstance(before, dict) or not isinstance(after, dict):
        return 0
    if not all(key in before and key in after for key in ("x", "y")):
        return 0
    return max(abs(int(after["x"]) - int(before["x"])), abs(int(after["y"]) - int(before["y"])))


def accumulate_state_delta(metrics: dict[str, Any], event: dict[str, Any], related_codes: set[str]) -> None:
    for change in (event.get("state_delta") or {}).values():
        if not isinstance(change, dict):
            continue
        before = change.get("before") if isinstance(change.get("before"), dict) else None
        after = change.get("after") if isinstance(change.get("after"), dict) else None
        code = unit_code(after) or unit_code(before)
        if code not in related_codes:
            continue
        metrics["state_change_events"] += 1
        before_hp = float(before.get("hp") or 0) if before else 0.0
        after_hp = float(after.get("hp") or 0) if after else 0.0
        hp_delta = after_hp - before_hp
        if hp_delta < 0:
            metrics["hp_lost"] += -hp_delta
        elif hp_delta > 0:
            metrics["hp_gained"] += hp_delta
        before_mana = float(before.get("mana") or 0) if before else 0.0
        after_mana = float(after.get("mana") or 0) if after else 0.0
        mana_delta = after_mana - before_mana
        if mana_delta < 0:
            metrics["mana_spent_or_lost"] += -mana_delta
        elif mana_delta > 0:
            metrics["mana_gained"] += mana_delta
        before_position = before.get("position") if before else None
        after_position = after.get("position") if after else None
        metrics["movement_distance"] += position_distance(before_position, after_position)
        if before and bool(before.get("alive")) and (after is None or not bool(after.get("alive"))):
            metrics["deaths"] += 1
        if before is None and after is not None:
            metrics["entries"] += 1
        if before is not None and after is None:
            metrics["exits"] += 1


def new_metrics() -> dict[str, Any]:
    return {
        "action_type_counts": Counter(),
        "action_code_counts": Counter(),
        "decision_reason_counts": Counter(),
        "actions_by_actor_code": Counter(),
        "ai_decision_points_by_actor_code": Counter(),
        "available_action_decision_points": Counter(),
        "effective_action_decision_points": Counter(),
        "log_term_counts": Counter(),
        "state_change_events": 0,
        "movement_distance": 0,
        "hp_lost": 0.0,
        "hp_gained": 0.0,
        "mana_spent_or_lost": 0.0,
        "mana_gained": 0.0,
        "deaths": 0,
        "entries": 0,
        "exits": 0,
        "failed_actions": 0,
    }


def event_source_is_related(
    event: dict[str, Any],
    related_codes: set[str],
) -> bool:
    actor = event.get("actor") if isinstance(event.get("actor"), dict) else {}
    if str(actor.get("hero_code") or "") in related_codes:
        return True
    before = event.get("before") if isinstance(event.get("before"), dict) else {}
    pending = before.get("pending_chain") if isinstance(before.get("pending_chain"), dict) else {}
    pending_actor = pending.get("actor") if isinstance(pending.get("actor"), dict) else {}
    if str(pending_actor.get("hero_code") or "") in related_codes:
        return True
    return False


def event_is_related(
    event: dict[str, Any],
    related_codes: set[str],
    related_names: set[str],
) -> bool:
    if event_source_is_related(event, related_codes):
        return True
    for summary_event in event.get("new_summary_events", []) or []:
        if not isinstance(summary_event, dict):
            continue
        for key in ("actor_unit_id", "target_unit_id"):
            unit_id = str(summary_event.get(key) or "")
            if any(unit_id == code or unit_id.startswith(f"{code}-") for code in related_codes):
                return True
    return any(name and any(name in str(line) for line in event.get("new_logs", []) or []) for name in related_names)


def analyze_target(
    target: str,
    hero: dict[str, Any],
    matches: list[dict[str, Any]],
    findings: list[dict[str, Any]],
    design: dict[str, Any] | None,
) -> dict[str, Any]:
    expectations = dict((design or {}).get("audit_expectations") or {})
    related_codes = set(str(code) for code in expectations.get("related_actor_codes", []) if code)
    related_codes.add(target)
    related_names = {str(hero.get("name") or "")}
    metrics = new_metrics()
    match_rows: list[dict[str, Any]] = []

    for match in matches:
        match_dir = Path(str(match["output_dir"]))
        trace = read_jsonl(match_dir / "trace.jsonl")
        for event in trace:
            actor = event.get("actor") if isinstance(event.get("actor"), dict) else {}
            actor_code = str(actor.get("hero_code") or "")
            if actor_code in related_codes:
                if actor.get("name"):
                    related_names.add(str(actor["name"]))
                payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
                action_type = str(payload.get("type") or "unknown")
                metrics["action_type_counts"][action_type] += 1
                metrics["action_code_counts"][action_code(event)] += 1
                metrics["decision_reason_counts"][str(event.get("reason") or "unknown")] += 1
                metrics["actions_by_actor_code"][actor_code] += 1
                if not bool(event.get("success", True)):
                    metrics["failed_actions"] += 1
                decision = event.get("decision") if isinstance(event.get("decision"), dict) else {}
                if str(event.get("reason") or "") == "ai_turn":
                    metrics["ai_decision_points_by_actor_code"][actor_code] += 1
                for diagnostic in decision.get("action_diagnostics", []) or []:
                    if not isinstance(diagnostic, dict) or not bool(diagnostic.get("available")):
                        continue
                    diagnostic_code = str(diagnostic.get("code") or diagnostic.get("kind") or "unknown")
                    metrics["available_action_decision_points"][f"{actor_code}:{diagnostic_code}"] += 1
                    if int(diagnostic.get("effective_payload_count") or 0) > 0:
                        metrics["effective_action_decision_points"][f"{actor_code}:{diagnostic_code}"] += 1
            logs = [str(line) for line in event.get("new_logs") or []]
            tracked_log_terms = [
                *expectations.get("log_terms_to_observe", []),
                *expectations.get("conditional_log_terms_to_track", []),
            ]
            source_related = event_source_is_related(event, related_codes)
            if event_is_related(event, related_codes, related_names):
                for term in dict.fromkeys(str(term) for term in tracked_log_terms):
                    metrics["log_term_counts"][str(term)] += sum(
                        str(term) in line
                        and (source_related or any(name and name in line for name in related_names))
                        for line in logs
                    )
            accumulate_state_delta(metrics, event, related_codes)
        match_rows.append(
            {
                "index": int(match["target_match_index"]),
                "seed": int(match["seed"]),
                "team1": list(match["team1"]),
                "team2": list(match["team2"]),
                "winner": match.get("winner"),
                "steps": int(match.get("steps") or 0),
                "finding_count": int(match.get("finding_count") or 0),
                "high_signal_count": int(match.get("high_signal_count") or 0),
                "battle_report": match.get("battle_report"),
            }
        )

    target_findings = []
    for item in findings:
        actor = item.get("actor") if isinstance(item.get("actor"), dict) else {}
        attributed_code = str(item.get("attributed_code") or actor.get("hero_code") or item.get("target") or "")
        if attributed_code in related_codes:
            target_findings.append(item)
    for match_row in match_rows:
        match_findings = [
            item
            for item in target_findings
            if int(item.get("target_match_index") or -1) == int(match_row["index"])
        ]
        match_row["finding_count"] = len(match_findings)
        match_row["high_signal_count"] = sum(is_high_signal(item) for item in match_findings)
    finding_categories = Counter(str(item.get("category") or "unknown") for item in target_findings)
    high_signal_count = sum(is_high_signal(item) for item in target_findings)
    wins = sum(match.get("winner") == 1 for match in match_rows)
    losses = sum(match.get("winner") == 2 for match in match_rows)
    unresolved = len(match_rows) - wins - losses
    active_codes = active_skill_codes(target)
    never_used_skills = [code for code in active_codes if metrics["action_code_counts"][code] == 0]
    effective_codes = {
        key.split(":", 1)[1]
        for key, count in metrics["effective_action_decision_points"].items()
        if count > 0 and ":" in key
    }
    expected_action_gaps = [
        str(code)
        for code in expectations.get("action_codes_to_observe", [])
        if metrics["action_code_counts"][str(code)] == 0
    ]
    expected_action_missed_opportunities = [code for code in expected_action_gaps if code in effective_codes]
    expected_action_coverage_gaps = [code for code in expected_action_gaps if code not in effective_codes]
    expected_action_codes = {str(code) for code in expectations.get("action_codes_to_observe", [])}
    unlisted_never_used = [code for code in never_used_skills if code not in expected_action_codes]
    never_used_with_effective_opportunity = [code for code in unlisted_never_used if code in effective_codes]
    never_used_without_effective_opportunity = [code for code in unlisted_never_used if code not in effective_codes]
    expected_log_gaps = [
        str(term)
        for term in expectations.get("log_terms_to_observe", [])
        if metrics["log_term_counts"][str(term)] == 0
    ]
    observation_notes: list[str] = []
    if high_signal_count:
        observation_notes.append(f"有 {high_signal_count} 条高信号异常，必须先复现。")
    if metrics["failed_actions"]:
        observation_notes.append(f"目标武将家族出现 {metrics['failed_actions']} 次动作执行失败。")
    if unresolved:
        observation_notes.append(f"有 {unresolved} 场在步数上限前未决出胜负。")
    if expected_action_missed_opportunities:
        observation_notes.append(f"设计关键动作有有效候选但未使用：{', '.join(expected_action_missed_opportunities)}。")
    if expected_action_coverage_gaps:
        observation_notes.append(f"设计关键动作未出现，且本批没有产生有效候选：{', '.join(expected_action_coverage_gaps)}。")
    if never_used_with_effective_opportunity:
        observation_notes.append(f"其他主动/随时技能有有效候选但未使用：{', '.join(never_used_with_effective_opportunity)}。")
    if never_used_without_effective_opportunity:
        observation_notes.append(f"其他主动/随时技能未使用，且本批没有产生有效候选：{', '.join(never_used_without_effective_opportunity)}。")
    if expected_log_gaps:
        observation_notes.append(f"设计基线关键日志未出现：{', '.join(expected_log_gaps)}。")
    if design is None:
        observation_notes.append("尚无结构化武将设计思想，需在人工复核前补写。")
    if not observation_notes:
        observation_notes.append("自动审计未发现明显观察缺口；仍需按设计思想人工查看代表性对局。")

    priority = (
        "P0"
        if high_signal_count or metrics["failed_actions"]
        else "P1"
        if expected_action_missed_opportunities or never_used_with_effective_opportunity
        else "P2"
        if expected_action_coverage_gaps or never_used_without_effective_opportunity or unresolved or expected_log_gaps or design is None
        else "P3"
    )
    return {
        "code": target,
        "name": hero.get("name"),
        "priority": priority,
        "design_baseline_available": design is not None,
        "design": design,
        "related_actor_codes": sorted(related_codes),
        "match_count": len(match_rows),
        "wins": wins,
        "losses": losses,
        "unresolved": unresolved,
        "average_steps": round(sum(item["steps"] for item in match_rows) / max(1, len(match_rows)), 2),
        "high_signal_count": high_signal_count,
        "finding_count": len(target_findings),
        "finding_categories": dict(sorted(finding_categories.items())),
        "active_skill_codes": active_codes,
        "never_used_active_skills": never_used_skills,
        "never_used_active_skills_with_effective_opportunity": never_used_with_effective_opportunity,
        "never_used_active_skills_without_effective_opportunity": never_used_without_effective_opportunity,
        "expected_action_gaps": expected_action_gaps,
        "expected_action_missed_opportunities": expected_action_missed_opportunities,
        "expected_action_coverage_gaps": expected_action_coverage_gaps,
        "expected_log_gaps": expected_log_gaps,
        "metrics": {
            key: dict(sorted(value.items())) if isinstance(value, Counter) else round(value, 4) if isinstance(value, float) else value
            for key, value in metrics.items()
        },
        "observation_notes": observation_notes,
        "matches": match_rows,
    }


def render_hero_review(review: dict[str, Any]) -> str:
    design = review.get("design") or {}
    metrics = review["metrics"]
    lines = [
        f"# {review['name']}（{review['code']}）深度审计",
        "",
        f"- 人工复核优先级：`{review['priority']}`",
        f"- 对局：`{review['match_count']}`；胜/负/未决：`{review['wins']}/{review['losses']}/{review['unresolved']}`；平均步骤：`{review['average_steps']}`",
        f"- Findings：`{review['finding_count']}`；高信号：`{review['high_signal_count']}`；动作失败：`{metrics['failed_actions']}`",
        f"- 设计思想基线：`{'有' if review['design_baseline_available'] else '缺失'}`",
        "",
        "## 自动观察结论",
        "",
    ]
    lines.extend(f"- {note}" for note in review["observation_notes"])
    lines.extend(["", "## 设计思想对照", ""])
    if design:
        for label, key in (
            ("战场定位", "battlefield_role"),
            ("核心设计意图", "design_intent"),
            ("操作节奏", "operation_sequence"),
            ("AI操作原则", "ai_policy"),
            ("通用AI启发", "general_ai_lesson"),
            ("关键规则", "key_rules"),
            ("预期信号", "expected_signals"),
            ("风险反模式", "risk_patterns"),
        ):
            lines.extend([f"### {label}", "", str(design.get(key) or "（未记录）"), ""])
    else:
        lines.extend(["该武将尚无结构化设计思想。人工复核前先补写定位、操作顺序、AI原则、预期信号和风险反模式。", ""])
    lines.extend(
        [
            "## 行为统计",
            "",
            f"- 动作类型：`{json.dumps(metrics['action_type_counts'], ensure_ascii=False)}`",
            f"- 动作/技能代码：`{json.dumps(metrics['action_code_counts'], ensure_ascii=False)}`",
            f"- 家族单位行动：`{json.dumps(metrics['actions_by_actor_code'], ensure_ascii=False)}`",
            f"- AI决策点（按家族单位）：`{json.dumps(metrics['ai_decision_points_by_actor_code'], ensure_ascii=False)}`",
            f"- 动作可用决策点：`{json.dumps(metrics['available_action_decision_points'], ensure_ascii=False)}`",
            f"- 有有效候选的决策点：`{json.dumps(metrics['effective_action_decision_points'], ensure_ascii=False)}`",
            f"- 条件/关键日志计数：`{json.dumps(metrics['log_term_counts'], ensure_ascii=False)}`",
            f"- 位移距离合计：`{metrics['movement_distance']}`",
            f"- HP 减少/回复：`{metrics['hp_lost']}/{metrics['hp_gained']}`",
            f"- 魔力减少/增加：`{metrics['mana_spent_or_lost']}/{metrics['mana_gained']}`",
            f"- 死亡/入场/离场：`{metrics['deaths']}/{metrics['entries']}/{metrics['exits']}`",
            f"- 未观察到使用的主动/随时技能：`{', '.join(review['never_used_active_skills']) or '无'}`",
            "",
            "## 对局索引",
            "",
        ]
    )
    for match in review["matches"]:
        lines.append(
            f"- #{match['index']} seed `{match['seed']}`：`{', '.join(match['team1'])}` vs "
            f"`{', '.join(match['team2'])}`；winner `{match['winner']}`；steps `{match['steps']}`；"
            f"high-signal `{match['high_signal_count']}`；report `{match['battle_report']}`"
        )
    return "\n".join(lines).rstrip() + "\n"


def render_review_queue(reviews: list[dict[str, Any]]) -> str:
    lines = [
        "# 全武将人工复核队列",
        "",
        "优先级说明：P0=动作错误/高信号缺陷；P1=有有效候选却未采用的动作；P2=没有形成有效候选的覆盖缺口、设计基线缺失或对局未决问题；P3=自动观察无明显缺口。",
        "",
    ]
    for review in sorted(reviews, key=lambda item: (item["priority"], -item["high_signal_count"], str(item["code"]))):
        lines.append(
            f"- `{review['priority']}` {review['name']} (`{review['code']}`)："
            f"高信号 `{review['high_signal_count']}`，未决 `{review['unresolved']}`，"
            f"未用技能 `{len(review['never_used_active_skills'])}`，设计基线 `{'有' if review['design_baseline_available'] else '缺失'}`。"
        )
        for note in review["observation_notes"][:3]:
            lines.append(f"  - {note}")
    return "\n".join(lines).rstrip() + "\n"


def run_all_hero_deep_audit(
    targets: Iterable[str],
    *,
    seed: int = 1,
    matches_per_hero: int = 10,
    max_steps: int = DEFAULT_MAX_STEPS,
    difficulty: str = "standard",
    output_dir: Path | str | None = None,
    design_path: Path = DEFAULT_DESIGN_PATH,
    resume: bool = False,
) -> Path:
    target_codes = [str(code) for code in targets]
    public_codes = set(public_hero_codes())
    unknown_codes = [code for code in target_codes if code not in public_codes]
    if not target_codes:
        raise ValueError("At least one audit target is required.")
    if unknown_codes:
        raise ValueError(f"Unknown or non-public target hero code(s): {', '.join(unknown_codes)}")
    run_dir = Path(output_dir) if output_dir is not None else default_output_dir(seed)
    run_dir.mkdir(parents=True, exist_ok=True)
    regression = run_consolidated_regression(run_dir)
    base = run_per_hero_ai_debug(
        target_codes,
        seed=seed,
        matches_per_hero=matches_per_hero,
        max_steps=max_steps,
        difficulty=difficulty,
        output_dir=run_dir,
        label="all-hero-deep-audit",
        resume=resume,
        progress=True,
    )
    summary = read_json(base.summary_path)
    findings = read_jsonl(base.findings_path)
    catalog = {str(hero["code"]): dict(hero) for hero in public_hero_catalog()}
    designs = load_design_profiles(design_path)
    reviews = [
        analyze_target(
            code,
            catalog.get(code, {"code": code, "name": code}),
            [match for match in summary["matches"] if str(match.get("target")) == code],
            findings,
            designs.get(code),
        )
        for code in target_codes
    ]
    review_dir = run_dir / "hero_reviews"
    review_dir.mkdir(parents=True, exist_ok=True)
    for review in reviews:
        (review_dir / f"{sanitize_label(review['code'])}.md").write_text(render_hero_review(review), encoding="utf-8")
    write_json(run_dir / "hero_metrics.json", {"heroes": reviews})
    (run_dir / "review_queue.md").write_text(render_review_queue(reviews), encoding="utf-8")
    overview = {
        "output_dir": str(run_dir),
        "target_count": len(reviews),
        "match_count": sum(item["match_count"] for item in reviews),
        "high_signal_count": sum(item["high_signal_count"] for item in reviews),
        "priority_counts": dict(sorted(Counter(item["priority"] for item in reviews).items())),
        "design_baseline_count": sum(bool(item["design_baseline_available"]) for item in reviews),
        "regression": regression,
        "files": {
            "regression_summary": "regression_tests.json",
            "regression_log": "regression_tests.log",
            "review_queue": "review_queue.md",
            "hero_metrics": "hero_metrics.json",
            "per_hero_reviews": "hero_reviews/<hero_code>.md",
            "base_summary": "summary.md",
            "all_findings": "findings.jsonl",
            "suspected_defects": "suspected_defects.jsonl",
        },
    }
    write_json(run_dir / "deep_audit_overview.json", overview)
    write_review_packet(run_dir, root=Path.cwd())
    return run_dir


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit every public hero with detailed AI traces, per-hero metrics, and design-intent comparison."
    )
    parser.add_argument("--targets", default=None, help="Comma-separated hero codes; defaults to every public hero.")
    parser.add_argument(
        "--current",
        action="store_true",
        help="Audit only current_hero_codes from the conformity-review state file.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Optional target limit for a quick local smoke run.")
    parser.add_argument("--seed", type=int, default=20260831)
    parser.add_argument("--matches-per-hero", type=int, default=10)
    parser.add_argument("--max-steps", type=int, default=500)
    parser.add_argument("--difficulty", default="standard", choices=sorted(ai_policy.AI_DIFFICULTIES))
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--design", type=Path, default=DEFAULT_DESIGN_PATH)
    parser.add_argument("--review-state", type=Path, default=DEFAULT_REVIEW_STATE_PATH)
    parser.add_argument("--resume", action="store_true", help="Reuse completed match folders under --out after interruption.")
    parser.add_argument("--summarize-only", action="store_true", help="Index saved results and current Git changes without running tests or matches.")
    args = parser.parse_args(argv)

    if args.current and args.targets:
        parser.error("--current and --targets cannot be used together")
    if args.current and args.limit is not None:
        parser.error("--current already selects the current review batch; do not combine it with --limit")

    targets = load_current_hero_codes(args.review_state) if args.current else (
        parse_roster(args.targets) if args.targets else public_hero_codes()
    )
    if args.limit is not None:
        targets = targets[: max(0, int(args.limit))]
    output_dir = args.out or (
        current_batch_output_dir(targets) if args.current else DEFAULT_OUTPUT_ROOT / "current"
    )
    if args.summarize_only:
        try:
            packet = write_review_packet(output_dir, root=Path.cwd(), existing_results=True)
        except (OSError, ValueError, KeyError) as exc:
            parser.error(f"Cannot summarize saved results: {exc}")
        print(f"saved results indexed (no tests run): {packet}")
        return 0
    run_dir = run_all_hero_deep_audit(
        targets,
        seed=args.seed,
        matches_per_hero=args.matches_per_hero,
        max_steps=args.max_steps,
        difficulty=args.difficulty,
        output_dir=output_dir,
        design_path=args.design,
        resume=args.resume,
    )
    if args.current:
        print(f"current review batch only: {', '.join(targets)}")
    print(f"deep audit complete: {run_dir}")
    print(f"open {run_dir / 'regression_tests.log'} for the complete ordinary test output")
    print(f"open {run_dir / 'review_packet.md'} first")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
