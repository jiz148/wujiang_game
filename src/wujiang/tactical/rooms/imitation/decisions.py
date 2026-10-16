"""Turn a full replay into one-step decisions the live AI can actually take.

One decision is one entry the battle already considers legal: move to a cell,
attack a unit, or cast one skill at its target. A plan such as "move, then
pierce" is two decisions. The network should learn this step, not a raw click
on the whole board and not only the four words move/attack/skill/end.

The chosen step comes from the new visual event when the replay has one.
Otherwise it is inferred from the board: which skill card increased its use
count, whether the actor moved, and whether an enemy lost health. The skill
card on that unit is the hero database at that moment. The previous step's
seat view is the legal list, when the replay kept it.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

TURN_REASONS = {"ai_turn", "player_action", "ai_instant"}
ACTION_KINDS = {"move", "attack", "skill"}


def decisions_from_replay(replay: dict[str, Any], *, source_name: str = "") -> list[dict[str, Any]]:
    steps = replay.get("steps") if isinstance(replay, dict) else None
    if not isinstance(steps, list) or len(steps) < 2:
        return []
    rows: list[dict[str, Any]] = []
    for index in range(1, len(steps)):
        before = steps[index - 1] if isinstance(steps[index - 1], dict) else {}
        after = steps[index] if isinstance(steps[index], dict) else {}
        reason = str(after.get("reason") or "")
        if reason not in TURN_REASONS and reason not in {"turn_end", "turn_timeout"}:
            continue
        before_battle = _battle(before)
        after_battle = _battle(after)
        chosen = _chosen_actions(before_battle, after_battle)
        if not chosen and reason in {"turn_end", "turn_timeout"}:
            actor_id = str(before_battle.get("active_turn_unit_id") or "")
            chosen = [{"action_type": "end", "action_code": "end", "actor_id": actor_id, "source": "turn"}]
        for action in chosen:
            row = _row(before, before_battle, after_battle, action, reason, source_name, index)
            if row is not None:
                rows.append(row)
    return rows


def collect_decisions(directory: Path, *, max_bytes: int) -> Iterable[dict[str, Any]]:
    if not directory.is_dir():
        return
    for path in sorted(directory.glob("*.json")):
        try:
            if path.stat().st_size > max_bytes:
                continue
            replay = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        yield from decisions_from_replay(replay, source_name=path.name)


def write_decisions(directory: Path, out: Path, *, max_bytes: int) -> dict[str, Any]:
    out.parent.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    sources = {"event": 0, "board": 0, "turn": 0}
    legal_rows = 0
    legal_hits = 0
    card_hits = 0
    skill_rows = 0
    files = 0
    rows = 0
    with out.open("w", encoding="utf-8", newline="\n") as handle:
        seen: set[str] = set()
        if not directory.is_dir():
            directory = directory
        for path in sorted(directory.glob("*.json")):
            try:
                if path.stat().st_size > max_bytes:
                    continue
                replay = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            batch = decisions_from_replay(replay, source_name=path.name)
            if not batch:
                continue
            files += 1
            seen.add(path.name)
            for row in batch:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                rows += 1
                verb = str(row.get("verb") or "")
                family = verb.split(":", 1)[0]
                counts[family] = counts.get(family, 0) + 1
                source = str(row.get("label_source") or "")
                if source in sources:
                    sources[source] += 1
                if row.get("legal_count"):
                    legal_rows += 1
                    if row.get("chosen_was_legal"):
                        legal_hits += 1
                if family == "skill":
                    skill_rows += 1
                    if row.get("on_skill_card"):
                        card_hits += 1
    report = {
        "files": files,
        "rows": rows,
        "families": counts,
        "label_sources": sources,
        "rows_with_legal_list": legal_rows,
        "chosen_found_in_legal_list": legal_hits,
        "skill_rows": skill_rows,
        "skill_rows_on_card": card_hits,
        "out": str(out),
    }
    report_path = out.with_name("decision-report.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _battle(step: dict[str, Any]) -> dict[str, Any]:
    omniscient = step.get("omniscient_battle")
    if isinstance(omniscient, dict) and omniscient.get("units"):
        return omniscient
    spectator = step.get("spectator_battle")
    return spectator if isinstance(spectator, dict) else {}


def _chosen_actions(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    events = _new_events(before, after)
    declared: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for event in events:
        kind = str(event.get("kind") or "")
        action_type = str(event.get("action_type") or "")
        if kind not in ACTION_KINDS or action_type != kind:
            continue
        actor_id = str(event.get("actor_id") or "")
        code = str(event.get("action_code") or action_type)
        key = (actor_id, action_type, code)
        if key in seen:
            continue
        seen.add(key)
        declared.append({
            "action_type": action_type,
            "action_code": code,
            "actor_id": actor_id,
            "target_unit_ids": [str(item) for item in (event.get("target_unit_ids") or [])],
            "target_cells": _cells(event.get("target_cells")),
            "source": "event",
        })
    if declared:
        return declared
    return _board_actions(before, after)


def _new_events(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    previous_ids = {
        int(event.get("id"))
        for event in (before.get("visual_events") or [])
        if isinstance(event, dict) and event.get("id") is not None
    }
    found: list[dict[str, Any]] = []
    for event in after.get("visual_events") or []:
        if not isinstance(event, dict):
            continue
        event_id = event.get("id")
        if event_id is not None and int(event_id) in previous_ids:
            continue
        found.append(event)
    return found


def _board_actions(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    before_units = {str(unit.get("id") or ""): unit for unit in _units(before)}
    actor_id = str(before.get("active_turn_unit_id") or "")
    actor_before = before_units.get(actor_id)
    actor_after = _find(_units(after), actor_id)
    if actor_before is None or actor_after is None:
        return []
    spent = _spent_skill(actor_before, actor_after)
    if spent is not None:
        return [{
            "action_type": "skill",
            "action_code": spent,
            "actor_id": actor_id,
            "target_unit_ids": _hurt_enemies(before, after, actor_before),
            "target_cells": [],
            "source": "board",
        }]
    before_pos = actor_before.get("position") if isinstance(actor_before.get("position"), dict) else None
    after_pos = actor_after.get("position") if isinstance(actor_after.get("position"), dict) else None
    moved = before_pos is not None and after_pos is not None and (
        before_pos.get("x"), before_pos.get("y")
    ) != (after_pos.get("x"), after_pos.get("y"))
    hurt = _hurt_enemies(before, after, actor_before)
    if moved and not hurt:
        return [{
            "action_type": "move",
            "action_code": "move",
            "actor_id": actor_id,
            "target_unit_ids": [],
            "target_cells": [_pair(after_pos)],
            "source": "board",
        }]
    if hurt and not moved:
        return [{
            "action_type": "attack",
            "action_code": "attack",
            "actor_id": actor_id,
            "target_unit_ids": hurt,
            "target_cells": [],
            "source": "board",
        }]
    return []


def _row(
    before_step: dict[str, Any],
    before: dict[str, Any],
    after: dict[str, Any],
    action: dict[str, Any],
    reason: str,
    source_name: str,
    index: int,
) -> dict[str, Any] | None:
    actor_id = str(action.get("actor_id") or "")
    actor = _find(_units(before), actor_id)
    if actor is None or not actor_id:
        return None
    action_type = str(action.get("action_type") or "")
    code = str(action.get("action_code") or action_type)
    skills = [skill for skill in (actor.get("skills") or []) if isinstance(skill, dict)]
    card = next((skill for skill in skills if str(skill.get("code") or "") == code), None)
    legal = _legal_actions(before_step, actor_id)
    matched = _match_legal(legal, action_type, code)
    verb = "end" if action_type == "end" else action_type if action_type != "skill" else f"skill:{code}"
    seen = _visible_battle(before_step, before, actor)
    seen_actor = _find(_units(seen), actor_id) or actor
    from wujiang.tactical.rooms.imitation.visible import merge_candidates, visible_situation
    situation = visible_situation(seen, seen_actor)
    return {
        "replay": source_name,
        "step": index,
        "reason": reason,
        "verb": verb,
        "action_type": action_type,
        "action_code": code,
        "hero_code": str(seen_actor.get("hero_code") or actor.get("hero_code") or ""),
        "actor_id": actor_id,
        "label_source": str(action.get("source") or ""),
        "target_unit_ids": list(action.get("target_unit_ids") or []),
        "target_cells": list(action.get("target_cells") or []),
        "on_skill_card": card is not None,
        "skill_timing": "" if card is None else str(card.get("timing") or ""),
        "skill_target_mode": "" if card is None else str(card.get("target_mode") or ""),
        "legal_count": len(legal),
        "legal_verbs": [_legal_verb(item) for item in legal],
        "candidates": merge_candidates(legal, situation, _legal_verb),
        "chosen_was_legal": matched is not None,
        "situation": situation,
    }


def _visible_battle(step: dict[str, Any], omniscient: dict[str, Any], actor: dict[str, Any]) -> dict[str, Any]:
    seats = step.get("seat_views") if isinstance(step.get("seat_views"), dict) else {}
    view = seats.get(str(actor.get("player_id")))
    if isinstance(view, dict) and view.get("units"):
        return view
    player = actor.get("player_id")
    units = [unit for unit in _units(omniscient) if not _hidden_from(unit, player)]
    copied = dict(omniscient)
    copied["units"] = units
    return copied


def _hidden_from(unit: dict[str, Any], viewer_player: Any) -> bool:
    if unit.get("player_id") == viewer_player:
        return False
    for status in unit.get("statuses") or []:
        name = status.get("name") if isinstance(status, dict) else status
        if name == "隐身":
            return True
    return False


def _legal_actions(step: dict[str, Any], actor_id: str) -> list[dict[str, Any]]:
    seats = step.get("seat_views") if isinstance(step.get("seat_views"), dict) else {}
    found: list[dict[str, Any]] = []
    for view in seats.values():
        if not isinstance(view, dict):
            continue
        for active in view.get("active_units") or []:
            if not isinstance(active, dict) or str(active.get("unit_id") or "") != actor_id:
                continue
            actions = ((active.get("actions") or {}).get("actions") or []) if isinstance(active.get("actions"), dict) else []
            for action in actions:
                if isinstance(action, dict) and action.get("available"):
                    found.append(action)
            if found:
                return found
    return found


def _match_legal(actions: list[dict[str, Any]], action_type: str, code: str) -> dict[str, Any] | None:
    for action in actions:
        if str(action.get("kind") or "") != action_type:
            continue
        if action_type == "skill" and str(action.get("code") or "") != code:
            continue
        return action
    return None


def _legal_verb(action: dict[str, Any]) -> str:
    kind = str(action.get("kind") or "")
    code = str(action.get("code") or "")
    if kind == "skill" and code:
        return f"skill:{code}"
    return kind or code


def action_verb(action: dict[str, Any]) -> str:
    """Token shared by training records and the live model, such as skill:pierce."""
    return _legal_verb(action)


def _situation(battle: dict[str, Any], actor: dict[str, Any]) -> dict[str, Any]:
    units = [unit for unit in _units(battle) if unit.get("alive") and not unit.get("banished") and isinstance(unit.get("position"), dict)]
    position = actor.get("position") if isinstance(actor.get("position"), dict) else {}
    player = actor.get("player_id")
    enemies = [unit for unit in units if unit.get("player_id") != player]
    nearest = None
    if position:
        distances = [_distance(position, unit["position"]) for unit in enemies]
        nearest = min(distances) if distances else None
    attack_range = float(((actor.get("stats") or {}).get("attack_range") or 0))
    skills = []
    mana = float(actor.get("mana") or 0)
    for skill in actor.get("skills") or []:
        if not isinstance(skill, dict) or skill.get("passive"):
            continue
        if str(skill.get("timing") or "") not in {"active", "instant"}:
            continue
        ready = int(skill.get("cooldown_remaining") or 0) <= 0 and mana + 0.001 >= float(skill.get("mana_cost") or 0)
        skills.append({
            "code": str(skill.get("code") or ""),
            "target_mode": str(skill.get("target_mode") or ""),
            "ready": ready,
        })
    return {
        "round": int(battle.get("round_number") or 1),
        "hp_ratio": round(_ratio(actor.get("hp"), actor.get("max_hp")), 3),
        "mana_ratio": round(_ratio(actor.get("mana"), actor.get("max_mana")), 3),
        "nearest_enemy": nearest,
        "enemy_in_attack_range": bool(nearest is not None and nearest <= attack_range),
        "enemy_count": len(enemies),
        "cannot_move": bool(actor.get("cannot_move") or actor.get("cannot_normal_move")),
        "cannot_attack": bool(actor.get("cannot_attack")),
        "cannot_use_skills": bool(actor.get("cannot_use_skills")),
        "ready_skills": [skill["code"] for skill in skills if skill["ready"]],
    }


def _spent_skill(before: dict[str, Any], after: dict[str, Any]) -> str | None:
    after_by_code = {
        str(skill.get("code") or ""): skill
        for skill in (after.get("skills") or [])
        if isinstance(skill, dict)
    }
    spent: list[str] = []
    for skill in before.get("skills") or []:
        if not isinstance(skill, dict):
            continue
        code = str(skill.get("code") or "")
        later = after_by_code.get(code)
        if later is None:
            continue
        if int(later.get("uses_this_battle") or 0) > int(skill.get("uses_this_battle") or 0):
            spent.append(code)
    if len(spent) == 1:
        return spent[0]
    return None


def _hurt_enemies(before: dict[str, Any], after: dict[str, Any], actor: dict[str, Any]) -> list[str]:
    later_units = {str(unit.get("id") or ""): unit for unit in _units(after)}
    hurt: list[str] = []
    for unit in _units(before):
        if unit.get("player_id") == actor.get("player_id"):
            continue
        unit_id = str(unit.get("id") or "")
        later = later_units.get(unit_id)
        if later is None or not later.get("alive") or float(later.get("hp") or 0) < float(unit.get("hp") or 0) - 0.001:
            hurt.append(unit_id)
    return hurt


def _units(battle: dict[str, Any]) -> list[dict[str, Any]]:
    return [unit for unit in (battle.get("units") or []) if isinstance(unit, dict)]


def _find(units: list[dict[str, Any]], unit_id: str) -> dict[str, Any] | None:
    for unit in units:
        if str(unit.get("id") or "") == unit_id:
            return unit
    return None


def _cells(raw: Any) -> list[dict[str, int]]:
    cells: list[dict[str, int]] = []
    for item in raw or []:
        if isinstance(item, dict) and item.get("x") is not None and item.get("y") is not None:
            cells.append({"x": int(item["x"]), "y": int(item["y"])})
    return cells


def _pair(cell: dict[str, Any]) -> dict[str, int]:
    return {"x": int(cell["x"]), "y": int(cell["y"])}


def _distance(left: dict[str, Any], right: dict[str, Any]) -> int:
    return max(abs(int(left["x"]) - int(right["x"])), abs(int(left["y"]) - int(right["y"])))


def _ratio(current: Any, maximum: Any) -> float:
    return float(current or 0) / max(float(maximum or 0), 0.001)


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="把完整录像整理成一步一个的决策样本。")
    parser.add_argument("--replays", type=Path, required=True)
    parser.add_argument("--max-bytes", type=int, default=8_000_000)
    parser.add_argument("--out", type=Path, default=Path("data/nn-lab/decisions.jsonl"))
    args = parser.parse_args()
    report = write_decisions(args.replays, args.out, max_bytes=args.max_bytes)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
