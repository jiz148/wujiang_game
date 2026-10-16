"""Turn spectator replay steps into situation vectors and action labels.

A spectator step stores the board after an action, not the legal-action list.
The label is inferred by comparing that board with the previous step.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

LABELS = ("move", "attack", "skill", "end")
FEATURE_COUNT = 14


def load_replay(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def iter_replay_files(directory: Path, *, max_bytes: int) -> Iterable[Path]:
    if not directory.is_dir():
        return
    for path in sorted(directory.glob("*.json")):
        try:
            if path.stat().st_size <= max_bytes:
                yield path
        except OSError:
            continue


def _units(battle: dict[str, Any]) -> list[dict[str, Any]]:
    return [unit for unit in (battle.get("units") or []) if isinstance(unit, dict)]


def _living(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        unit for unit in units
        if unit.get("alive") and not unit.get("banished") and isinstance(unit.get("position"), dict)
    ]


def _find(units: list[dict[str, Any]], unit_id: str) -> dict[str, Any] | None:
    for unit in units:
        if str(unit.get("id") or "") == unit_id:
            return unit
    return None


def _distance(left: dict[str, Any], right: dict[str, Any]) -> int:
    return max(abs(int(left["x"]) - int(right["x"])), abs(int(left["y"]) - int(right["y"])))


def _ratio(current: Any, maximum: Any) -> float:
    return float(current or 0) / max(float(maximum or 0), 0.001)


def situation_vector(battle: dict[str, Any], actor_id: str) -> list[float] | None:
    units = _living(_units(battle))
    actor = _find(units, actor_id)
    if actor is None:
        return None
    player = actor.get("player_id")
    enemies = [unit for unit in units if unit.get("player_id") != player]
    allies = [unit for unit in units if unit.get("player_id") == player]
    position = actor["position"]
    nearest = min((_distance(position, unit["position"]) for unit in enemies), default=None)
    weakest = min((_ratio(unit.get("hp"), unit.get("max_hp")) for unit in enemies), default=None)
    stats = actor.get("stats") or {}
    attack_range = float(stats.get("attack_range") or 0)
    stealthed = any(str(status.get("name") or "") == "隐身" for status in (actor.get("statuses") or []) if isinstance(status, dict))
    return [
        _ratio(actor.get("hp"), actor.get("max_hp")),
        _ratio(actor.get("mana"), actor.get("max_mana")),
        float(stats.get("attack") or 0) / 10.0,
        float(stats.get("defense") or 0) / 10.0,
        float(stats.get("speed") or 0) / 10.0,
        attack_range / 10.0,
        len(enemies) / 8.0,
        len(allies) / 8.0,
        1.0 if nearest is None else min(nearest, 20) / 20.0,
        1.0 if len(enemies) > len(allies) else 0.0,
        1.0 if stealthed else 0.0,
        min(float(battle.get("round_number") or 1), 30.0) / 30.0,
        1.0 if nearest is not None and nearest <= attack_range else 0.0,
        0.0 if weakest is None else weakest,
    ]


def _label(before: dict[str, Any], after: dict[str, Any], reason: str, actor_id: str) -> str | None:
    normalized = str(reason or "")
    if normalized in {"turn_end", "turn_timeout", "surrender", "match_end"}:
        return "end"
    if normalized not in {"ai_turn", "player_action", "ai_instant"}:
        return None
    before_units = _units(before)
    after_units = _units(after)
    actor_before = _find(before_units, actor_id)
    actor_after = _find(after_units, actor_id)
    if actor_before is None or actor_after is None:
        return "end"
    before_pos = actor_before.get("position")
    after_pos = actor_after.get("position")
    moved = (
        isinstance(before_pos, dict) and isinstance(after_pos, dict)
        and (before_pos.get("x"), before_pos.get("y")) != (after_pos.get("x"), after_pos.get("y"))
    )
    player = actor_before.get("player_id")
    hurt_enemy = False
    for unit in before_units:
        if unit.get("player_id") == player:
            continue
        later = _find(after_units, str(unit.get("id") or ""))
        if later is None or not later.get("alive"):
            hurt_enemy = True
            break
        if float(later.get("hp") or 0) < float(unit.get("hp") or 0) - 0.001:
            hurt_enemy = True
            break
    mana_spent = float(actor_after.get("mana") or 0) < float(actor_before.get("mana") or 0) - 0.001
    if hurt_enemy and not moved:
        return "attack"
    if mana_spent:
        return "skill"
    if moved:
        return "move"
    if hurt_enemy:
        return "attack"
    return "end"


def examples_from_replay(payload: dict[str, Any]) -> list[tuple[list[float], str]]:
    steps = payload.get("steps") or []
    rows: list[tuple[list[float], str]] = []
    previous: dict[str, Any] | None = None
    for step in steps:
        battle = step.get("spectator_battle") if isinstance(step, dict) else None
        if not isinstance(battle, dict) or not battle.get("units"):
            previous = battle if isinstance(battle, dict) else previous
            continue
        if previous and previous.get("units"):
            actor_id = str(previous.get("active_turn_unit_id") or "")
            features = situation_vector(previous, actor_id) if actor_id else None
            label = _label(previous, battle, str(step.get("reason") or ""), actor_id) if features else None
            if features is not None and label in LABELS:
                rows.append((features, label))
        previous = battle
    return rows


def collect_examples(directory: Path, *, max_bytes: int = 4_000_000) -> list[tuple[str, list[float], str]]:
    rows: list[tuple[str, list[float], str]] = []
    for path in iter_replay_files(directory, max_bytes=max_bytes):
        try:
            payload = load_replay(path)
        except (OSError, json.JSONDecodeError):
            continue
        for features, label in examples_from_replay(payload):
            rows.append((path.name, features, label))
    return rows
