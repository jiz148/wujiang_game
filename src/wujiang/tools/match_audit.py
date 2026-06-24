from __future__ import annotations

import argparse
import json
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from wujiang.engine.core import ActionError, Battle, Position, QueuedAction, Unit
from wujiang.heroes.registry import CLASSIC_BATTLE_MODE, create_battle
from wujiang.web import ai as ai_policy


DEFAULT_AUDIT_DIR = Path("reports") / "match-audit"
DEFAULT_MAX_STEPS = 120
TRACE_CANDIDATE_LIMIT = 12


@dataclass(slots=True)
class AuditResult:
    output_dir: Path
    manifest_path: Path
    trace_path: Path
    report_path: Path
    findings_jsonl_path: Path
    findings_markdown_path: Path
    winner: Optional[int]
    step_count: int
    finding_count: int


class FindingRecorder:
    def __init__(self) -> None:
        self._items: list[dict[str, Any]] = []
        self._dedupe_items: dict[str, dict[str, Any]] = {}

    @property
    def items(self) -> list[dict[str, Any]]:
        return self._items

    def add(
        self,
        *,
        severity: str,
        category: str,
        source: str,
        message: str,
        step: int,
        actor: Optional[Unit] = None,
        payload: Optional[dict[str, Any]] = None,
        evidence: Optional[dict[str, Any]] = None,
        dedupe_key: Optional[str] = None,
    ) -> dict[str, Any]:
        if dedupe_key:
            existing = self._dedupe_items.get(dedupe_key)
            if existing is not None:
                return existing
        item = {
            "id": f"F{len(self._items) + 1:04d}",
            "severity": severity,
            "category": category,
            "source": source,
            "message": message,
            "step": step,
            "actor": unit_ref(actor) if actor is not None else None,
            "payload": payload,
            "evidence": evidence or {},
        }
        self._items.append(item)
        if dedupe_key:
            self._dedupe_items[dedupe_key] = item
        return item


def parse_roster(value: str | Iterable[str]) -> list[str]:
    if isinstance(value, str):
        raw_codes = value.replace(";", ",").split(",")
    else:
        raw_codes = list(value)
    roster = [str(code).strip() for code in raw_codes if str(code).strip()]
    if not roster:
        raise ValueError("Roster must contain at least one hero code.")
    return roster


def run_match_audit(
    team1: Iterable[str],
    team2: Iterable[str],
    *,
    seed: int = 1,
    difficulty: str = "standard",
    max_steps: int = DEFAULT_MAX_STEPS,
    output_dir: Optional[Path | str] = None,
    label: Optional[str] = None,
) -> AuditResult:
    roster1 = parse_roster(team1)
    roster2 = parse_roster(team2)
    random.seed(seed)
    battle = create_battle(roster1, roster2, mode=CLASSIC_BATTLE_MODE)
    run_dir = Path(output_dir) if output_dir is not None else default_output_dir(seed=seed, label=label)
    run_dir.mkdir(parents=True, exist_ok=True)

    findings = FindingRecorder()
    trace_events: list[dict[str, Any]] = []
    report_lines: list[str] = [
        "# Battle Audit Report",
        "",
        f"- Seed: `{seed}`",
        f"- Difficulty: `{difficulty}`",
        f"- Team 1: `{', '.join(roster1)}`",
        f"- Team 2: `{', '.join(roster2)}`",
        "",
        "## Action Log",
        "",
    ]

    step = 0
    while battle.winner is None and step < max_steps:
        decision = next_decision(battle, difficulty, findings, step=step)
        if decision is None:
            findings.add(
                severity="error",
                category="simulation_blocked",
                source="match_audit",
                message="No legal AI decision or fallback could be produced.",
                step=step,
                evidence={"phase": current_phase(battle)},
            )
            break
        event = apply_decision(battle, decision, findings, step)
        trace_events.append(event)
        report_lines.extend(report_lines_for_event(event))
        step += 1

    if battle.winner is None and step >= max_steps:
        findings.add(
            severity="info",
            category="simulation_limit",
            source="match_audit",
            message="Simulation stopped at max_steps before a winner was decided.",
            step=step,
            evidence={"max_steps": max_steps},
        )

    manifest = {
        "seed": seed,
        "difficulty": difficulty,
        "max_steps": max_steps,
        "steps_executed": step,
        "winner": battle.winner,
        "team1": roster1,
        "team2": roster2,
        "board": {"width": battle.width, "height": battle.height},
        "turn_order_unit_ids": list(battle.turn_order_unit_ids),
        "finding_count": len(findings.items),
        "output_files": {
            "trace": "trace.jsonl",
            "battle_report": "battle_report.md",
            "findings_jsonl": "findings.jsonl",
            "findings_markdown": "findings.md",
        },
    }
    report_lines.extend(
        [
            "",
            "## Final State",
            "",
            f"- Winner: `{battle.winner if battle.winner is not None else 'none'}`",
            f"- Steps executed: `{step}`",
            f"- Findings: `{len(findings.items)}`",
            "",
            "### Units",
            "",
        ]
    )
    for unit in sorted(all_known_units(battle), key=lambda item: (item.player_id, item.unit_id)):
        report_lines.append(unit_report_line(battle, unit))
    report_lines.extend(["", "## Findings", ""])
    if findings.items:
        for finding in findings.items:
            report_lines.append(f"- `{finding['id']}` {finding['severity']} / {finding['category']}: {finding['message']}")
    else:
        report_lines.append("- No findings recorded.")

    manifest_path = run_dir / "manifest.json"
    trace_path = run_dir / "trace.jsonl"
    report_path = run_dir / "battle_report.md"
    findings_jsonl_path = run_dir / "findings.jsonl"
    findings_markdown_path = run_dir / "findings.md"

    write_json(manifest_path, manifest)
    write_jsonl(trace_path, trace_events)
    report_path.write_text("\n".join(report_lines).rstrip() + "\n", encoding="utf-8")
    write_jsonl(findings_jsonl_path, findings.items)
    findings_markdown_path.write_text(render_findings_markdown(findings.items), encoding="utf-8")

    return AuditResult(
        output_dir=run_dir,
        manifest_path=manifest_path,
        trace_path=trace_path,
        report_path=report_path,
        findings_jsonl_path=findings_jsonl_path,
        findings_markdown_path=findings_markdown_path,
        winner=battle.winner,
        step_count=step,
        finding_count=len(findings.items),
    )


def default_output_dir(*, seed: int, label: Optional[str]) -> Path:
    suffix = sanitize_label(label) if label else "match"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return DEFAULT_AUDIT_DIR / f"{stamp}-seed{seed}-{suffix}"


def sanitize_label(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in str(value).strip())
    return cleaned.strip("-") or "match"


def next_decision(
    battle: Battle,
    difficulty: str,
    findings: FindingRecorder,
    *,
    step: int,
) -> Optional[dict[str, Any]]:
    try:
        prompt = battle.current_respawn_prompt()
        if prompt is not None:
            unit = battle.get_unit(prompt.unit_id)
            options = sorted(battle.respawn_options_for(unit), key=lambda item: (item.x, item.y))
            return build_respawn_decision(battle, unit, options, difficulty)

        if battle.pending_chain is not None:
            current_unit_id = battle.pending_chain.current_unit_id()
            if not current_unit_id:
                return {
                    "reason": "ai_chain",
                    "phase": "chain",
                    "actor": None,
                    "payload": {"type": "chain_skip"},
                    "summary": "chain window has no current reactor; finalize",
                }
            reactor = battle.get_unit(current_unit_id)
            options = battle.reaction_snapshot_for(reactor).get("actions", [])
            return build_reaction_decision(battle, reactor, options, difficulty, findings, step=step)

        instant = build_instant_decision_for_waiting_side(battle, difficulty, findings, step=step)
        if instant is not None:
            return instant

        actor = battle.current_turn_unit()
        if actor is None:
            return {
                "reason": "ai_turn_fallback",
                "phase": "turn",
                "actor": None,
                "payload": {"type": "end_turn"},
                "summary": "no current turn unit",
            }
        return build_turn_bundle_decision(battle, difficulty, findings, step=step)
    except ActionError as exc:
        return build_fallback_decision(battle, f"decision ActionError: {exc}")
    except Exception as exc:
        findings.add(
            severity="error",
            category="decision_error",
            source="match_audit",
            message=f"Decision builder raised {type(exc).__name__}: {exc}",
            step=step,
            evidence={"phase": current_phase(battle)},
        )
        return build_fallback_decision(battle, f"decision error: {type(exc).__name__}: {exc}")


def build_turn_decision(
    battle: Battle,
    actor: Unit,
    difficulty: str,
    findings: FindingRecorder,
    *,
    step: int,
) -> dict[str, Any]:
    profile = ai_policy.difficulty_profile(difficulty)
    snapshot = battle.action_snapshot_for(actor)
    active_candidates: list[Any] = []
    move_candidates: list[Any] = []
    diagnostics: list[dict[str, Any]] = []

    for action in snapshot.get("actions", []):
        if not action.get("available"):
            continue
        kind = str(action.get("kind") or "")
        diag = action_diagnostic(battle, actor, action, profile, instant_only=False)
        diagnostics.append(diag)
        if diag.get("builder_error"):
            findings.add(
                severity="warning",
                category="ai_candidate_builder_error",
                source="ai_turn",
                message=f"Candidate builder failed for {actor.name} action {diag.get('code')}.",
                step=step,
                actor=actor,
                evidence=diag,
            )
            continue
        if kind == "move":
            move_candidates.extend(diag.pop("_candidates", []))
        else:
            active_candidates.extend(diag.pop("_candidates", []))
        record_candidate_gap(findings, step=step, source="ai_turn", actor=actor, diag=diag)

    best_non_move = ai_policy.best_candidate(active_candidates)
    best_move = ai_policy.best_candidate(move_candidates)
    selected = None
    selection_reason = "end_turn"
    threshold = profile.action_threshold
    if best_non_move is not None and best_non_move.score >= profile.action_threshold:
        selected = best_non_move
        selection_reason = "best_non_move_meets_threshold"
    elif best_move is not None and best_move.score >= 0:
        selected = best_move
        selection_reason = "best_move"
    elif best_non_move is not None and best_non_move.score > 0:
        selected = best_non_move
        selection_reason = "positive_non_move_fallback"

    payload = dict(selected.payload) if selected is not None else {"type": "end_turn"}
    return {
        "reason": "ai_turn",
        "phase": "turn",
        "actor": unit_ref(actor),
        "payload": payload,
        "summary": candidate_summary(selected) if selected is not None else "end_turn",
        "selection_reason": selection_reason,
        "selected_score": selected.score if selected is not None else None,
        "threshold": threshold,
        "top_candidates": top_candidate_dicts([*active_candidates, *move_candidates]),
        "action_diagnostics": strip_internal_diagnostics(diagnostics),
    }


def build_turn_bundle_decision(
    battle: Battle,
    difficulty: str,
    findings: FindingRecorder,
    *,
    step: int,
) -> dict[str, Any]:
    actors = [unit for unit in battle.current_turn_bundle_units(include_banished=False) if unit.can_take_turn_actions(battle)]
    if not actors:
        actor = battle.current_turn_unit()
        return {
            "reason": "ai_turn",
            "phase": "turn",
            "actor": unit_ref(actor) if actor is not None else None,
            "payload": {"type": "end_turn"},
            "summary": "end_turn",
            "selection_reason": "no_bundle_actor_can_act",
            "top_candidates": [],
            "action_diagnostics": [],
        }
    decisions = [build_turn_decision(battle, actor, difficulty, findings, step=step) for actor in actors]
    actionable = [decision for decision in decisions if dict(decision.get("payload") or {}).get("type") != "end_turn"]
    if not actionable:
        return decisions[0]
    return max(actionable, key=lambda decision: float(decision.get("selected_score") or 0.0))


def build_reaction_decision(
    battle: Battle,
    reactor: Unit,
    options: list[dict[str, Any]],
    difficulty: str,
    findings: FindingRecorder,
    *,
    step: int,
) -> dict[str, Any]:
    queued_action = battle.pending_chain.queued_action if battle.pending_chain is not None else None
    if queued_action is None:
        return {
            "reason": "ai_chain",
            "phase": "chain",
            "actor": unit_ref(reactor),
            "payload": {"type": "chain_skip"},
            "summary": "no queued action",
            "top_candidates": [],
            "action_diagnostics": [],
        }
    profile = ai_policy.difficulty_profile(difficulty)
    candidates: list[Any] = []
    diagnostics: list[dict[str, Any]] = []
    for option in options:
        diag = reaction_diagnostic(battle, reactor, queued_action, option, profile)
        diagnostics.append(diag)
        if diag.get("builder_error"):
            findings.add(
                severity="warning",
                category="ai_candidate_builder_error",
                source="ai_chain",
                message=f"Reaction candidate builder failed for {reactor.name} option {diag.get('code')}.",
                step=step,
                actor=reactor,
                evidence=diag,
            )
            continue
        candidates.extend(diag.pop("_candidates", []))
        record_candidate_gap(findings, step=step, source="ai_chain", actor=reactor, diag=diag)

    chosen = ai_policy.best_candidate(candidates)
    payload = dict(chosen.payload) if chosen is not None and chosen.score >= profile.reaction_threshold else {"type": "chain_skip"}
    return {
        "reason": "ai_chain",
        "phase": "chain",
        "actor": unit_ref(reactor),
        "payload": payload,
        "summary": candidate_summary(chosen) if payload.get("type") != "chain_skip" else "chain_skip",
        "selection_reason": "threshold_met" if payload.get("type") != "chain_skip" else "below_threshold_or_no_candidate",
        "threshold": profile.reaction_threshold,
        "queued_action": queued_action_ref(battle, queued_action),
        "top_candidates": top_candidate_dicts(candidates),
        "action_diagnostics": strip_internal_diagnostics(diagnostics),
    }


def build_instant_decision_for_waiting_side(
    battle: Battle,
    difficulty: str,
    findings: FindingRecorder,
    *,
    step: int,
) -> Optional[dict[str, Any]]:
    active_team = int(battle.active_player or 0)
    waiting_teams = [player_id for player_id in (1, 2) if player_id != active_team]
    for player_id in waiting_teams:
        units = sorted(battle.instant_action_units_for_player(player_id), key=lambda unit: unit.unit_id)
        decision = build_instant_decision(battle, units, difficulty, findings, step=step)
        if decision is not None:
            return decision
    return None


def build_instant_decision(
    battle: Battle,
    units: Iterable[Unit],
    difficulty: str,
    findings: FindingRecorder,
    *,
    step: int,
) -> Optional[dict[str, Any]]:
    profile = ai_policy.difficulty_profile(difficulty)
    candidates: list[Any] = []
    diagnostics: list[dict[str, Any]] = []
    actor_by_payload_unit: dict[str, Unit] = {}
    for unit in units:
        snapshot = battle.action_snapshot_for(unit)
        for action in snapshot.get("actions", []):
            if action.get("kind") != "skill" or not action.get("available"):
                continue
            if str(action.get("timing") or "") != "instant":
                continue
            diag = action_diagnostic(battle, unit, action, profile, instant_only=True)
            diagnostics.append(diag)
            if diag.get("builder_error"):
                findings.add(
                    severity="warning",
                    category="ai_candidate_builder_error",
                    source="ai_instant",
                    message=f"Instant candidate builder failed for {unit.name} action {diag.get('code')}.",
                    step=step,
                    actor=unit,
                    evidence=diag,
                )
                continue
            local_candidates = diag.pop("_candidates", [])
            candidates.extend(local_candidates)
            for candidate in local_candidates:
                actor_by_payload_unit[str(candidate.payload.get("unit_id") or "")] = unit
            record_candidate_gap(findings, step=step, source="ai_instant", actor=unit, diag=diag)

    chosen = ai_policy.best_candidate(candidates)
    if chosen is None or chosen.score < profile.instant_threshold:
        return None
    actor = actor_by_payload_unit.get(str(chosen.payload.get("unit_id") or ""))
    if actor is None:
        try:
            actor = battle.get_unit(str(chosen.payload.get("unit_id") or ""))
        except Exception:
            actor = None
    return {
        "reason": "ai_instant",
        "phase": "instant",
        "actor": unit_ref(actor) if actor is not None else None,
        "payload": dict(chosen.payload),
        "summary": candidate_summary(chosen),
        "selection_reason": "threshold_met",
        "threshold": profile.instant_threshold,
        "top_candidates": top_candidate_dicts(candidates),
        "action_diagnostics": strip_internal_diagnostics(diagnostics),
    }


def build_respawn_decision(
    battle: Battle,
    unit: Unit,
    options: list[Position],
    difficulty: str,
) -> dict[str, Any]:
    payload = ai_policy.choose_respawn_action(battle, unit, options, difficulty)
    if payload is None and options:
        fallback = options[0]
        payload = {"type": "respawn_select", "unit_id": unit.unit_id, "x": fallback.x, "y": fallback.y}
    return {
        "reason": "ai_respawn" if payload is not None else "ai_respawn_fallback",
        "phase": "respawn",
        "actor": unit_ref(unit),
        "payload": payload or {"type": "end_turn"},
        "summary": "respawn",
        "options": [cell.to_dict() for cell in options[:25]],
        "option_count": len(options),
    }


def preview_counts(preview: dict[str, Any], selection: dict[str, Any]) -> dict[str, int]:
    choices = selection.get("choices") if isinstance(selection, dict) else None
    patterns = selection.get("patterns") if isinstance(selection, dict) else None
    candidates = selection.get("candidates") if isinstance(selection, dict) else None
    choice_pattern_count = 0
    if isinstance(choices, list):
        for choice in choices:
            if isinstance(choice, dict) and isinstance(choice.get("patterns"), list):
                choice_pattern_count += len(choice["patterns"])
    return {
        "preview_target_count": len(preview.get("target_unit_ids") or []),
        "preview_cell_count": len(preview.get("cells") or []),
        "preview_pattern_count": len(patterns or []) + choice_pattern_count,
        "preview_candidate_count": len(candidates or []),
    }


def action_diagnostic(
    battle: Battle,
    actor: Unit,
    action: dict[str, Any],
    profile: Any,
    *,
    instant_only: bool,
) -> dict[str, Any]:
    code = str(action.get("code") or "")
    kind = str(action.get("kind") or "")
    diag: dict[str, Any] = {
        "kind": kind,
        "code": code,
        "name": action.get("name"),
        "timing": action.get("timing"),
        "available": bool(action.get("available")),
        "raw_payload_count": None,
        "legal_payload_count": None,
        "effective_payload_count": None,
        "candidate_count": 0,
    }
    try:
        preview = action.get("preview") or {}
        selection = preview.get("selection") or {}
        diag.update(preview_counts(preview, selection))
        if isinstance(selection, dict):
            diag["required_cells"] = int(selection.get("required_cells") or 0)
        if kind == "move":
            raw_payloads = [
                {"type": "move", "unit_id": actor.unit_id, "x": cell.x, "y": cell.y}
                for cell in ai_policy.preview_positions(preview.get("cells"))
            ]
            candidates = ai_policy.build_move_candidates(battle, actor, action, profile)
            legal_payloads = [payload for payload in raw_payloads if ai_policy.payload_is_legal(battle, payload)]
            if battle.mounted_unit_for(actor) is not None:
                diag["expected_filter_reason"] = "mounted_rider_body_move"
            diag.update(
                raw_payload_count=len(raw_payloads),
                legal_payload_count=len(legal_payloads),
                effective_payload_count=len(legal_payloads),
                candidate_count=len(candidates),
            )
            diag["_candidates"] = candidates
            return diag
        if kind == "attack":
            raw_payloads = ai_policy.attack_payloads_for_action(battle, actor, action)
            legal_payloads = [payload for payload in raw_payloads if ai_policy.payload_is_legal(battle, payload)]
            effective_payloads = [
                payload
                for payload in legal_payloads
                if ai_policy.attack_payload_has_effective_enemy_impact(battle, actor, payload)
            ]
            candidates = ai_policy.build_attack_candidates(battle, actor, action, profile)
            diag.update(
                raw_payload_count=len(raw_payloads),
                legal_payload_count=len(legal_payloads),
                effective_payload_count=len(effective_payloads),
                candidate_count=len(candidates),
                sample_payloads=raw_payloads[:3],
            )
            diag["_candidates"] = candidates
            return diag
        if kind == "skill":
            raw_payloads = ai_policy.skill_payloads_for_action(battle, actor, action)
            diagnostic_payloads = ai_policy.trim_skill_payloads_for_ai(battle, actor, raw_payloads, limit=64)
            selection_mode = str(selection.get("mode") or "")
            if (
                battle.mounted_unit_for(actor) is not None
                and str(action.get("code") or "") in ai_policy.MOVE_SKILL_CODES
                and str(action.get("code") or "") != "mounted_leap"
            ):
                diag["expected_filter_reason"] = "mounted_rider_generic_move_skill"
            legal_payloads: list[dict[str, Any]] = []
            effective_payloads: list[dict[str, Any]] = []
            for payload in diagnostic_payloads:
                generated_from_preview = bool(payload.get("cells")) and selection_mode in {"pattern_cells", "choice_pattern", ""}
                legal = generated_from_preview or ai_policy.payload_is_legal(battle, payload)
                if legal:
                    legal_payloads.append(payload)
                    requires_enemy = ai_policy.skill_payload_requires_enemy_impact(battle, actor, action, payload)
                    if not requires_enemy or ai_policy.skill_payload_has_effective_enemy_impact(battle, actor, action, payload):
                        effective_payloads.append(payload)
            candidates = ai_policy.build_skill_candidates(battle, actor, action, profile, instant_only=instant_only)
            if effective_payloads and all(
                ai_policy.should_throttle_unlimited_nonhostile_skill(battle, actor, action, payload)
                for payload in effective_payloads
            ):
                diag["expected_filter_reason"] = "unlimited_nonhostile_repeat_throttle"
            diag.update(
                raw_payload_count=len(raw_payloads),
                diagnostic_payload_count=len(diagnostic_payloads),
                legal_payload_count=len(legal_payloads),
                effective_payload_count=len(effective_payloads),
                candidate_count=len(candidates),
                target_mode=action.get("target_mode"),
                selection_mode=selection_mode,
                sample_payloads=raw_payloads[:3],
            )
            diag["_candidates"] = candidates
            return diag
        diag["_candidates"] = []
        return diag
    except Exception as exc:
        diag["builder_error"] = f"{type(exc).__name__}: {exc}"
        diag["_candidates"] = []
        return diag


def reaction_diagnostic(
    battle: Battle,
    reactor: Unit,
    queued_action: QueuedAction,
    option: dict[str, Any],
    profile: Any,
) -> dict[str, Any]:
    code = str(option.get("action_code") or option.get("code") or "")
    diag: dict[str, Any] = {
        "kind": "reaction",
        "code": code,
        "name": option.get("action_name") or option.get("name"),
        "available": True,
        "raw_payload_count": None,
        "legal_payload_count": None,
        "candidate_count": 0,
    }
    try:
        preview = option.get("preview") or {}
        selection = preview.get("selection") or {}
        diag.update(preview_counts(preview, selection))
        if isinstance(selection, dict):
            diag["required_cells"] = int(selection.get("required_cells") or 0)
        raw_payloads = ai_policy.reaction_payloads_for_option(battle, reactor, queued_action, option)
        legal_payloads = [
            payload
            for payload in raw_payloads
            if ai_policy.reaction_payload_is_legal(battle, reactor, queued_action, payload)
        ]
        candidates = ai_policy.build_reaction_candidates(battle, reactor, queued_action, option, profile)
        diag.update(
            raw_payload_count=len(raw_payloads),
            legal_payload_count=len(legal_payloads),
            effective_payload_count=len(legal_payloads),
            candidate_count=len(candidates),
            sample_payloads=raw_payloads[:3],
        )
        diag["_candidates"] = candidates
        return diag
    except Exception as exc:
        diag["builder_error"] = f"{type(exc).__name__}: {exc}"
        diag["_candidates"] = []
        return diag


def record_candidate_gap(
    findings: FindingRecorder,
    *,
    step: int,
    source: str,
    actor: Unit,
    diag: dict[str, Any],
) -> None:
    raw_count = int(diag.get("raw_payload_count") or 0)
    legal_count = int(diag.get("legal_payload_count") or 0)
    effective_count = int(diag.get("effective_payload_count") or 0)
    candidate_count = int(diag.get("candidate_count") or 0)
    preview_option_count = (
        int(diag.get("preview_target_count") or 0)
        + int(diag.get("preview_cell_count") or 0)
        + int(diag.get("preview_pattern_count") or 0)
        + int(diag.get("preview_candidate_count") or 0)
    )
    if diag.get("expected_filter_reason"):
        return
    if raw_count == 0 and preview_option_count > 0 and diag.get("kind") in {"attack", "skill", "reaction"}:
        if diag.get("kind") == "attack" and int(diag.get("preview_target_count") or 0) == 0:
            return
        if diag.get("code") == "heaven_punishment" and int(diag.get("preview_target_count") or 0) == 0:
            return
        required_cells = int(diag.get("required_cells") or 0)
        if required_cells > 0 and int(diag.get("preview_cell_count") or 0) < required_cells:
            findings.add(
                severity="info",
                category="ai_payload_insufficient_selection",
                source=source,
                message=(
                    f"{actor.name} could not generate payloads for {diag.get('code')} because only "
                    f"{diag.get('preview_cell_count')} selectable cells were available for {required_cells} required cells."
                ),
                step=step,
                actor=actor,
                evidence=diag,
                dedupe_key=f"{source}:{actor.unit_id}:ai_payload_insufficient_selection:{diag.get('code')}",
            )
            return
        findings.add(
            severity="warning",
            category="ai_payload_generation_gap",
            source=source,
            message=f"{actor.name} has available {diag.get('kind')} {diag.get('code')} but generated no payloads.",
            step=step,
            actor=actor,
            evidence=diag,
            dedupe_key=f"{source}:{actor.unit_id}:ai_payload_generation_gap:{diag.get('code')}",
        )
        return
    if raw_count > 0 and legal_count == 0:
        findings.add(
            severity="info",
            category="ai_payload_all_illegal",
            source=source,
            message=f"{actor.name} generated payloads for {diag.get('code')}, but none were legal.",
            step=step,
            actor=actor,
            evidence=diag,
            dedupe_key=f"{source}:{actor.unit_id}:ai_payload_all_illegal:{diag.get('code')}",
        )
        return
    if legal_count > 0 and effective_count == 0 and diag.get("kind") in {"attack", "skill"}:
        findings.add(
            severity="info",
            category="ai_payload_no_effective_impact",
            source=source,
            message=f"{actor.name} generated legal payloads for {diag.get('code')}, but all lacked effective enemy impact.",
            step=step,
            actor=actor,
            evidence=diag,
            dedupe_key=f"{source}:{actor.unit_id}:ai_payload_no_effective_impact:{diag.get('code')}",
        )
        return
    if effective_count > 0 and candidate_count == 0:
        findings.add(
            severity="warning",
            category="ai_candidate_filtered",
            source=source,
            message=f"{actor.name} had effective payloads for {diag.get('code')}, but no candidates survived.",
            step=step,
            actor=actor,
            evidence=diag,
            dedupe_key=f"{source}:{actor.unit_id}:ai_candidate_filtered:{diag.get('code')}",
        )


def build_fallback_decision(battle: Battle, summary: str) -> Optional[dict[str, Any]]:
    if battle.pending_chain is not None:
        current_unit_id = battle.pending_chain.current_unit_id()
        actor = None
        if current_unit_id:
            try:
                actor = battle.get_unit(current_unit_id)
            except Exception:
                actor = None
        return {
            "reason": "ai_chain_fallback",
            "phase": "chain",
            "actor": unit_ref(actor) if actor is not None else None,
            "payload": {"type": "chain_skip"},
            "summary": summary,
        }
    prompt = battle.current_respawn_prompt()
    if prompt is not None:
        unit = battle.get_unit(prompt.unit_id)
        options = sorted(battle.respawn_options_for(unit), key=lambda item: (item.x, item.y))
        if not options:
            return None
        destination = options[0]
        return {
            "reason": "ai_respawn_fallback",
            "phase": "respawn",
            "actor": unit_ref(unit),
            "payload": {"type": "respawn_select", "unit_id": unit.unit_id, "x": destination.x, "y": destination.y},
            "summary": summary,
        }
    actor = battle.current_turn_unit()
    return {
        "reason": "ai_turn_fallback",
        "phase": "turn",
        "actor": unit_ref(actor) if actor is not None else None,
        "payload": {"type": "end_turn"},
        "summary": summary,
    }


def apply_decision(
    battle: Battle,
    decision: dict[str, Any],
    findings: FindingRecorder,
    step: int,
) -> dict[str, Any]:
    before = battle_state_digest(battle)
    before_logs = list(battle.logs)
    event: dict[str, Any] = {
        "step": step,
        "reason": decision.get("reason"),
        "phase": decision.get("phase"),
        "actor": decision.get("actor"),
        "payload": decision.get("payload"),
        "decision": {key: value for key, value in decision.items() if key not in {"payload"}},
        "before": before["meta"],
        "success": True,
    }
    try:
        battle.perform_action(dict(decision.get("payload") or {}))
    except Exception as exc:
        event["success"] = False
        event["error"] = f"{type(exc).__name__}: {exc}"
        findings.add(
            severity="error",
            category="action_error",
            source=str(decision.get("reason") or "ai"),
            message=f"Selected action failed during perform_action: {type(exc).__name__}: {exc}",
            step=step,
            actor=unit_from_ref(battle, decision.get("actor")),
            payload=decision.get("payload"),
            evidence={"decision": decision},
        )
        fallback = build_fallback_decision(battle, f"fallback after action error: {type(exc).__name__}: {exc}")
        if fallback is not None and fallback.get("payload") != decision.get("payload"):
            try:
                battle.perform_action(dict(fallback.get("payload") or {}))
                event["fallback"] = fallback
            except Exception as fallback_exc:
                event["fallback_error"] = f"{type(fallback_exc).__name__}: {fallback_exc}"
                findings.add(
                    severity="error",
                    category="fallback_error",
                    source=str(fallback.get("reason") or "fallback"),
                    message=f"Fallback action failed: {type(fallback_exc).__name__}: {fallback_exc}",
                    step=step,
                    actor=unit_from_ref(battle, fallback.get("actor")),
                    payload=fallback.get("payload"),
                    evidence={"fallback": fallback},
                )

    after = battle_state_digest(battle)
    event["after"] = after["meta"]
    event["state_delta"] = state_delta(before["units"], after["units"])
    event["new_logs"] = new_log_entries(before_logs, battle.logs)
    event["winner"] = battle.winner
    return event


def current_phase(battle: Battle) -> str:
    if battle.current_respawn_prompt() is not None:
        return "respawn"
    if battle.pending_chain is not None:
        return "chain"
    return "turn"


def battle_state_digest(battle: Battle) -> dict[str, Any]:
    units = {unit.unit_id: unit_state(battle, unit) for unit in all_known_units(battle)}
    prompt = battle.current_respawn_prompt()
    queued = battle.pending_chain.queued_action if battle.pending_chain is not None else None
    meta = {
        "active_player": battle.active_player,
        "turn_number": battle.turn_number,
        "round_number": battle.round_number,
        "active_turn_unit_id": battle.current_turn_slot_unit_id(),
        "current_turn_unit_id": battle.current_turn_unit().unit_id if battle.current_turn_unit() is not None else None,
        "phase": current_phase(battle),
        "pending_chain": queued_action_ref(battle, queued) if queued is not None else None,
        "pending_respawn": prompt.to_public_dict() if prompt is not None else None,
        "winner": battle.winner,
    }
    return {"meta": meta, "units": units}


def all_known_units(battle: Battle) -> list[Unit]:
    units: list[Unit] = []
    seen: set[str] = set()
    for unit in list(battle.all_units()) + list(battle.destroyed_units):
        if unit.unit_id in seen:
            continue
        seen.add(unit.unit_id)
        units.append(unit)
    return units


def unit_state(battle: Battle, unit: Unit) -> dict[str, Any]:
    return {
        "id": unit.unit_id,
        "hero_code": getattr(unit, "hero_code", None),
        "name": unit.name,
        "player_id": unit.player_id,
        "alive": unit.alive,
        "banished": unit.banished,
        "position": unit.position.to_dict() if unit.position is not None else None,
        "occupied_cells": [cell.to_dict() for cell in battle.unit_cells(unit)],
        "hp": round(unit.current_hp, 4),
        "max_hp": round(unit.max_health, 4),
        "mana": round(unit.current_mana, 4),
        "mana_points": round(unit.mana_points, 4),
        "shields": unit.shields,
        "temporary_shields": unit.temporary_shields,
        "stats": {
            "attack": unit.stat("attack"),
            "defense": unit.stat("defense"),
            "speed": unit.stat("speed"),
            "attack_range": unit.targeting_range(),
            "mana": unit.stat("mana"),
        },
        "actions": {
            "move_used": unit.move_used,
            "normal_move_steps_used": unit.normal_move_steps_used,
            "normal_move_actions_used": unit.normal_move_actions_used,
            "attacks_used": unit.attacks_used,
            "attacks_per_turn": unit.attack_actions_per_turn(),
            "performed_active_skill": unit.performed_active_skill,
            "turn_ready": unit.turn_ready,
        },
        "statuses": [component_ref(status) for status in unit.statuses],
        "skills": [skill_ref(skill) for skill in unit.skills],
    }


def state_delta(before_units: dict[str, Any], after_units: dict[str, Any]) -> dict[str, Any]:
    changed: dict[str, Any] = {}
    all_ids = sorted(set(before_units) | set(after_units))
    for unit_id in all_ids:
        before = before_units.get(unit_id)
        after = after_units.get(unit_id)
        if before == after:
            continue
        changed[unit_id] = {"before": before, "after": after}
    return changed


def new_log_entries(before: list[str], after: list[str]) -> list[str]:
    if not before:
        return list(after)
    max_overlap = min(len(before), len(after))
    for overlap in range(max_overlap, -1, -1):
        if overlap == 0 or before[-overlap:] == after[:overlap]:
            return list(after[overlap:])
    return list(after)


def unit_ref(unit: Unit | None) -> Optional[dict[str, Any]]:
    if unit is None:
        return None
    return {
        "id": unit.unit_id,
        "hero_code": getattr(unit, "hero_code", None),
        "name": unit.name,
        "player_id": unit.player_id,
        "is_summon": unit.is_summon,
        "is_clone": unit.is_clone,
    }


def unit_from_ref(battle: Battle, ref: Any) -> Optional[Unit]:
    if not isinstance(ref, dict) or not ref.get("id"):
        return None
    try:
        return battle.get_unit(str(ref["id"]))
    except Exception:
        for unit in battle.destroyed_units:
            if unit.unit_id == str(ref["id"]):
                return unit
    return None


def component_ref(component: Any) -> dict[str, Any]:
    data = {"type": type(component).__name__, "name": getattr(component, "name", None)}
    duration = getattr(component, "duration", None)
    if duration is not None:
        data["duration"] = duration
    return data


def skill_ref(skill: Any) -> dict[str, Any]:
    return {
        "code": getattr(skill, "code", None),
        "name": getattr(skill, "name", None),
        "timing": getattr(skill, "timing", None),
        "uses_this_turn": getattr(skill, "uses_this_turn", None),
        "uses_this_battle": getattr(skill, "uses_this_battle", None),
        "cooldown_remaining": getattr(skill, "cooldown_remaining", None),
    }


def queued_action_ref(battle: Battle, queued: QueuedAction | None) -> Optional[dict[str, Any]]:
    if queued is None:
        return None
    actor = None
    try:
        actor = battle.get_unit(queued.actor_id)
    except Exception:
        actor = None
    return {
        "action_type": queued.action_type,
        "actor": unit_ref(actor),
        "display_name": queued.display_name,
        "speed": queued.speed,
        "target_unit_ids": list(queued.target_unit_ids),
        "target_cells": [cell.to_dict() for cell in queued.target_cells],
        "payload": dict(queued.payload),
    }


def candidate_summary(candidate: Any) -> str:
    if candidate is None:
        return ""
    return f"{candidate.summary} score={round(float(candidate.score), 3)}"


def top_candidate_dicts(candidates: Iterable[Any], *, limit: int = TRACE_CANDIDATE_LIMIT) -> list[dict[str, Any]]:
    sorted_candidates = sorted(candidates, key=lambda item: float(item.score), reverse=True)
    return [
        {"summary": candidate.summary, "score": round(float(candidate.score), 3), "payload": candidate.payload}
        for candidate in sorted_candidates[:limit]
    ]


def strip_internal_diagnostics(diagnostics: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    stripped = []
    for diag in diagnostics:
        item = dict(diag)
        item.pop("_candidates", None)
        stripped.append(item)
    return stripped


def report_lines_for_event(event: dict[str, Any]) -> list[str]:
    actor = event.get("actor") or {}
    actor_name = actor.get("name") or "system"
    payload_type = (event.get("payload") or {}).get("type")
    status = "ok" if event.get("success") else "error"
    lines = [
        f"### Step {event.get('step')} - {event.get('reason')} - {status}",
        "",
        f"- Actor: `{actor_name}`",
        f"- Payload: `{payload_type}`",
    ]
    if event.get("error"):
        lines.append(f"- Error: `{event['error']}`")
    if event.get("fallback"):
        lines.append(f"- Fallback: `{event['fallback'].get('reason')}`")
    logs = event.get("new_logs") or []
    if logs:
        lines.append("- Logs:")
        for entry in logs:
            lines.append(f"  - {entry}")
    changed = event.get("state_delta") or {}
    if changed:
        lines.append(f"- Changed units: `{', '.join(changed.keys())}`")
    lines.append("")
    return lines


def unit_report_line(battle: Battle, unit: Unit) -> str:
    state = unit_state(battle, unit)
    pos = state["position"]
    pos_text = f"({pos['x']}, {pos['y']})" if isinstance(pos, dict) else "off-board"
    return (
        f"- P{unit.player_id} `{unit.unit_id}` {unit.name}: "
        f"hp `{state['hp']}/{state['max_hp']}`, mana `{state['mana']}`, "
        f"alive `{state['alive']}`, banished `{state['banished']}`, pos `{pos_text}`"
    )


def render_findings_markdown(findings: list[dict[str, Any]]) -> str:
    lines = ["# Match Audit Findings", ""]
    if not findings:
        lines.append("No findings recorded.")
        return "\n".join(lines) + "\n"
    for item in findings:
        actor = item.get("actor") or {}
        lines.extend(
            [
                f"## {item['id']} - {item['severity']} - {item['category']}",
                "",
                f"- Step: `{item['step']}`",
                f"- Source: `{item['source']}`",
                f"- Actor: `{actor.get('name') or ''}` `{actor.get('id') or ''}`",
                f"- Message: {item['message']}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def write_jsonl(path: Path, payloads: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for payload in payloads:
            handle.write(json.dumps(payload, ensure_ascii=False, default=str))
            handle.write("\n")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run a deterministic 2v2 wujiang AI match audit.")
    parser.add_argument("--team1", required=True, help="Comma-separated hero codes for player 1.")
    parser.add_argument("--team2", required=True, help="Comma-separated hero codes for player 2.")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--difficulty", default="standard", choices=sorted(ai_policy.AI_DIFFICULTIES))
    parser.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS)
    parser.add_argument("--out", type=Path, default=None, help="Output directory. Defaults to reports/match-audit/<run>.")
    parser.add_argument("--label", default=None, help="Optional label for the default output directory name.")
    args = parser.parse_args(argv)

    result = run_match_audit(
        parse_roster(args.team1),
        parse_roster(args.team2),
        seed=args.seed,
        difficulty=args.difficulty,
        max_steps=args.max_steps,
        output_dir=args.out,
        label=args.label,
    )
    print(f"wrote audit: {result.output_dir}")
    print(f"steps={result.step_count} winner={result.winner} findings={result.finding_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
