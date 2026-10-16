"""Turn an intent into one legal action from the unit's own list."""
from __future__ import annotations

import random
from typing import Any, Iterable, Optional

from wujiang.tactical.engine.core import Battle, Position, Unit
from wujiang.tactical.rooms.tendency.log import record_decision
from wujiang.tactical.rooms.tendency.situation import Situation, build_situation

CLEAR_GAP = 0.75
# Direction and reaction skills. A preview that already lists patterns is cast before this set applies.
UNSAFE_SKILLS = frozenset({
    "mana_pull",
    "fate_kick",
    "pierce",
    "machine_gun",
    "knockback",
})
# Dashes whose preview is one destination cell. They are a subset of the bare-cell
# cast: any other skill with a single-destination cell list uses the same x,y.
# This set does not block skills that are absent from it.
SAFE_CELL_SKILLS = frozenset({
    "medusa",
    "medusa_blink",
    "thunder_god",
    "rending",
    "mounted_leap",
    "leap",
    "fly_leap",
    "plasma_thruster",
    "paralysis_card",
    "poison_card",
    "drain_card",
})
# A missing selection mode is one destination. These modes need a richer payload.
_NOT_ONE_CELL = frozenset({
    "pattern_cells",
    "choice_pattern",
    "revive_unit_cell",
    "body_direction",
    "unit_direction",
    "direction",
    "multi_unit",
    "stat_cells",
    "mimic_skill",
    "agency_contract",
    "ancient_fusion",
})
# Recognized only when a preview already lists the word. Never used to invent a direction.
_COMPASS_STEPS = {
    "north": (0, -1),
    "northeast": (1, -1),
    "east": (1, 0),
    "southeast": (1, 1),
    "south": (0, 1),
    "southwest": (-1, 1),
    "west": (-1, 0),
    "northwest": (-1, -1),
}
_SAMPLE_LIMIT = 12

from wujiang.tactical.rooms.tendency.heroes import INTENTS, skills_for, weights_for  # noqa: E402


def pick_intent(weights: dict[str, float], rng: random.Random) -> tuple[str, bool]:
    ordered = sorted(INTENTS, key=lambda name: (-float(weights.get(name, 0.0)), name))
    top = ordered[0]
    second = ordered[1]
    gap = float(weights.get(top, 0.0)) - float(weights.get(second, 0.0))
    if gap >= CLEAR_GAP:
        return top, False
    ceiling = float(weights.get(top, 0.0))
    pool = [name for name in INTENTS if ceiling - float(weights.get(name, 0.0)) < CLEAR_GAP]
    scored = [max(0.05, float(weights.get(name, 0.0))) for name in pool]
    return rng.choices(pool, weights=scored, k=1)[0], True


def _is_companion(unit: Unit) -> bool:
    return bool(unit.is_summon or unit.is_clone or getattr(unit, "is_mount", False))


def choose_turn_bundle_action(
    battle: Battle,
    units: Iterable[Unit],
    difficulty: str,
) -> tuple[dict[str, Any], Optional[Unit]]:
    del difficulty
    ready = [unit for unit in units if unit.can_take_turn_actions(battle)]
    if not ready:
        return {"type": "end_turn"}, None
    slot_id = battle.current_turn_slot_unit_id()
    companions = [unit for unit in ready if unit.unit_id != slot_id and _is_companion(unit)]
    ordered = companions + [unit for unit in ready if unit not in companions]
    rng = random.Random()
    for unit in ordered:
        snapshot = battle.action_snapshot_for(unit)
        situation = build_situation(battle, unit, snapshot)
        weights = weights_for(situation)
        intent, sampled = pick_intent(weights, rng)
        payload = _payload_for_intent(battle, unit, snapshot, situation, intent)
        if payload is None:
            payload = {"type": "end_turn", "unit_id": unit.unit_id}
        others_ready = any(other.unit_id != unit.unit_id for other in ready)
        if payload.get("type") == "end_turn" and others_ready:
            payload = {"type": "pass_unit", "unit_id": unit.unit_id}
        record_decision({
            "hero_code": situation.hero_code,
            "intent": intent,
            "sampled": sampled,
            "intent_weights": {name: round(float(weights.get(name, 0.0)), 3) for name in INTENTS},
            "situation": situation.as_dict(),
            "action_type": payload.get("type"),
            "skill_code": payload.get("skill_code"),
        })
        return payload, unit
    return {"type": "end_turn"}, None


def choose_instant_action(battle: Battle, units: Iterable[Unit], difficulty: str) -> Optional[dict[str, Any]]:
    del difficulty
    for unit in list(units)[:4]:
        snapshot = battle.action_snapshot_for(unit)
        payload = _first_skill(battle, unit, snapshot, ("survive",), timing="instant")
        if payload is not None:
            return payload
    return None


def choose_chain_reaction(
    battle: Battle,
    reactor: Unit,
    options: list[dict[str, Any]],
    difficulty: str,
) -> Optional[dict[str, Any]]:
    del difficulty
    window = getattr(battle, "pending_chain", None)
    queued = getattr(window, "queued_action", None) if window is not None else None
    if queued is None or not options:
        return {"type": "chain_skip"}
    threatened = _incoming_hits(battle, reactor, queued)
    ranked: list[tuple[int, dict[str, Any]]] = []
    for option in options:
        if not isinstance(option, dict):
            continue
        payload = _reaction_payload(battle, reactor, queued, option, threatened)
        if payload is None:
            continue
        code = str(option.get("action_code") or "")
        score = _reaction_score(queued, code, threatened)
        if score <= 0 and not threatened and ("shield" in code or code in {"protection", "block"}):
            score = 45
        if score > 0:
            ranked.append((score, payload))
    if not ranked:
        return {"type": "chain_skip"}
    return max(ranked, key=lambda item: item[0])[1]


def _incoming_hits(battle: Battle, reactor: Unit, queued: Any) -> bool:
    targets = {str(unit_id) for unit_id in (getattr(queued, "target_unit_ids", None) or [])}
    if str(getattr(reactor, "unit_id", "")) in targets:
        return True
    danger = set(_positions(getattr(queued, "target_cells", None)))
    if not danger:
        return False
    reader = getattr(battle, "unit_cells", None)
    cells = list(reader(reactor)) if callable(reader) else [getattr(reactor, "position", None)]
    return any(cell in danger for cell in (_as_position(cell) for cell in cells) if cell is not None)


def _reaction_score(queued: Any, code: str, threatened: bool) -> int:
    if not threatened:
        return 0
    payload = getattr(queued, "payload", None) or {}
    kind = str(getattr(queued, "action_type", "") or "")
    cannot_evade = bool(payload.get("cannot_evade"))
    ignore_shield = bool(payload.get("ignore_shield"))
    if code in {"evasion", "ghost_step", "backstep_shot"}:
        return 0 if cannot_evade else (90 if kind in {"attack", "skill"} else 20)
    if code in {"protection", "block", "harden"} or "shield" in code:
        if ignore_shield:
            return 0
        return 70 if kind == "attack" else (45 if kind == "skill" else 0)
    if code in {"counter", "knockback"}:
        return 60 if kind == "attack" else 0
    return 30 if kind in {"attack", "skill"} else 0


def _reaction_payload(
    battle: Battle,
    reactor: Unit,
    queued: Any,
    option: dict[str, Any],
    threatened: bool,
) -> Optional[dict[str, Any]]:
    code = str(option.get("action_code") or "")
    preview = option.get("preview") or {}
    listed = [str(unit_id) for unit_id in (preview.get("target_unit_ids") or [])]
    covers_ally = any(unit_id in {str(item) for item in (getattr(queued, "target_unit_ids", None) or [])} for unit_id in listed)
    if not code or not (threatened or covers_ally):
        return None
    base = {"type": "chain_react", "unit_id": reactor.unit_id, "action_code": code}
    escape = code in {"evasion", "ghost_step", "backstep_shot"}
    if escape:
        cell = _retreat_cell(battle, reactor, queued, _positions(preview.get("cells")))
        if cell is None:
            return None
        base["x"] = cell.x
        base["y"] = cell.y
    elif listed:
        own = str(getattr(reactor, "unit_id", ""))
        base["target_unit_id"] = own if own in listed else listed[0]
        if code in {"protection", "block"} or "shield" in code:
            base["target_unit_ids"] = [base["target_unit_id"]]
    if not _reaction_payload_legal(battle, reactor, queued, base):
        return None
    return base


def _retreat_cell(battle: Battle, reactor: Unit, queued: Any, cells: list[Position]) -> Optional[Position]:
    if not cells:
        return None
    danger = set(_positions(getattr(queued, "target_cells", None)))
    if not danger:
        reader = getattr(battle, "unit_cells", None)
        own = list(reader(reactor)) if callable(reader) else [getattr(reactor, "position", None)]
        danger = {cell for cell in (_as_position(item) for item in own) if cell is not None}
    source = battle.units.get(str(getattr(queued, "actor_id", "") or "")) if hasattr(battle, "units") else None
    origin = _as_position(getattr(source, "position", None)) if source is not None else None

    def rank(cell: Position) -> tuple[int, int]:
        outside = 0 if cell in danger else 1
        away = cell.distance_to(origin) if origin is not None else 0
        return (outside, away)

    chosen = max(cells, key=rank)
    if chosen in danger:
        return None
    return chosen


def _reaction_payload_legal(battle: Battle, reactor: Unit, queued: Any, payload: dict[str, Any]) -> bool:
    getter = getattr(reactor, "get_skill", None)
    if not callable(getter):
        return True
    try:
        skill = getter(str(payload.get("action_code") or ""))
    except Exception:
        return True
    if skill is None:
        return True
    cost = float(getattr(skill, "mana_cost", 0.0) or 0.0)
    if cost > float(getattr(reactor, "current_mana", cost) or 0.0):
        return False
    checker = getattr(skill, "can_react_with_payload", None)
    if not callable(checker):
        return True
    try:
        ok, _reason = checker(battle, reactor, queued, payload)
    except Exception:
        return False
    return bool(ok)


def choose_respawn_action(
    battle: Battle,
    unit: Unit,
    options: list[Position],
    difficulty: str,
) -> Optional[dict[str, Any]]:
    del difficulty
    if not options:
        return None
    anchor = unit.position or getattr(unit, "last_position", None)
    allies = [
        other
        for other in battle.units.values()
        if other.alive and not other.banished and other.position is not None and other.player_id == unit.player_id and other.unit_id != unit.unit_id
    ]
    sample = list(options)
    if len(sample) > 24:
        sample = sample[:: max(1, len(sample) // 24)][:24]

    def rank(cell: Position) -> tuple[int, int, int]:
        if anchor is not None:
            return (cell.distance_to(anchor), cell.x, cell.y)
        if allies:
            nearest = min(cell.distance_to(other.position) for other in allies if other.position is not None)
            return (nearest, cell.x, cell.y)
        return (0, cell.x, cell.y)

    destination = min(sample, key=rank)
    return {"type": "respawn_select", "unit_id": unit.unit_id, "x": destination.x, "y": destination.y}


def choose_damage_choice_action(battle: Battle) -> dict[str, Any]:
    prompt = battle.pending_damage_choice or {}
    unit_id = str(prompt.get("unit_id") or "")
    kind = str(prompt.get("kind") or "")
    options = [str(item) for item in (prompt.get("options") or [])]
    if kind == "hell_death_strike" and options:
        owner = battle.units.get(unit_id)
        if owner is None:
            owner = next((unit for unit in getattr(battle, "destroyed_units", []) if unit.unit_id == unit_id), None)
        enemies = [
            option for option in options
            if (target := battle.units.get(option)) is not None
            and target.alive
            and (owner is None or target.player_id != owner.player_id)
        ]
        return {
            "type": "damage_choice",
            "unit_id": unit_id,
            "target_unit_id": enemies[0] if enemies else options[0],
        }
    return {
        "type": "damage_choice",
        "unit_id": unit_id,
        "stat_name": "decline",
        "target_unit_id": "decline",
    }


def _payload_for_intent(
    battle: Battle,
    actor: Unit,
    snapshot: dict[str, Any],
    situation: Situation,
    intent: str,
) -> Optional[dict[str, Any]]:
    if intent == "survive":
        return (
            _first_skill(battle, actor, snapshot, skills_for(situation, intent), timing="active", modes={"self", "ally", "none"})
            or _move(battle, actor, snapshot, away=True)
            or _attack(battle, actor, snapshot)
        )
    if intent == "spacing":
        distance = situation.nearest_enemy_distance
        in_range = distance is not None and distance <= situation.attack_range
        melee_threat = distance is not None and distance <= 2 and situation.attack_range >= 4
        if melee_threat:
            return _move(battle, actor, snapshot, away=True) or _attack(battle, actor, snapshot)
        if not in_range:
            return _first_skill(battle, actor, snapshot, skills_for(situation, intent), timing="active") or _move(battle, actor, snapshot, away=False)
        return _attack(battle, actor, snapshot) or _first_skill(battle, actor, snapshot, skills_for(situation, intent), timing="active")
    if intent == "pressure":
        return _attack(battle, actor, snapshot) or _first_skill(battle, actor, snapshot, skills_for(situation, intent), timing="active") or _move(battle, actor, snapshot, away=False)
    if intent == "trade":
        return (
            _first_skill(battle, actor, snapshot, skills_for(situation, intent), timing="active", modes={"enemy", "unit", "cell", "self"})
            or _attack(battle, actor, snapshot)
            or _move(battle, actor, snapshot, away=False)
        )
    return (
        _first_skill(battle, actor, snapshot, skills_for(situation, intent), timing="active")
        or _attack(battle, actor, snapshot)
        or _move(battle, actor, snapshot, away=False)
    )


def _first_skill(
    battle: Battle,
    actor: Unit,
    snapshot: dict[str, Any],
    preferred: tuple[str, ...],
    *,
    timing: str,
    modes: set[str] | None = None,
) -> Optional[dict[str, Any]]:
    actions = [
        action
        for action in snapshot.get("actions", [])
        if action.get("kind") == "skill" and action.get("available") and str(action.get("timing") or "") == timing
    ]
    ordered = sorted(actions, key=lambda action: _skill_rank(action, preferred))
    for action in ordered:
        code = str(action.get("code") or "")
        preview = action.get("preview") or {}
        if code in UNSAFE_SKILLS and not _selection_patterns(preview):
            continue
        mode = str(action.get("target_mode") or "none")
        if modes is not None and mode not in modes:
            continue
        payload = _skill_payload(battle, actor, action)
        if payload is not None:
            return payload
    return None


def _skill_rank(action: dict[str, Any], preferred: tuple[str, ...]) -> tuple[int, int, int]:
    preview = action.get("preview") or {}
    # A drawn area or listed choice is aimed. Bare casts stay in the second group.
    shaped = 0 if (_selection_patterns(preview) or _selection_choices(preview)) else 1
    code = str(action.get("code") or "")
    if code in preferred:
        return (shaped, 0, preferred.index(code))
    return (shaped, 1, 0)


def _skill_payload(battle: Battle, actor: Unit, action: dict[str, Any]) -> Optional[dict[str, Any]]:
    preview = action.get("preview") or {}
    directions = _listed_directions(preview)
    direction_required = str(action.get("direction_mode") or "none") == "required"
    if direction_required and not directions:
        return None

    def finish(payload: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        if payload is None or not direction_required:
            return payload
        token = _direction_toward_enemy(battle, actor, directions)
        if token is None:
            return None
        payload["direction"] = token
        payload["choice_code"] = token
        return payload

    mode = str(action.get("target_mode") or "none")
    code = str(action.get("code") or "")
    listed = [str(item) for item in (preview.get("target_unit_ids") or [])]
    if mode in {"enemy", "unit"}:
        if code == "recover_mana":
            payload = finish(_recover_mana_payload(battle, actor, code, listed))
            return payload if _payload_matters(battle, actor, payload) else None
        allies_ok = mode == "unit"
        for target in _listed_units(battle, actor, listed, allies=False) or (
            _listed_units(battle, actor, listed, allies=True) if allies_ok else []
        ):
            payload = finish(_at_unit(actor, code, target))
            if _payload_matters(battle, actor, payload):
                return payload
        return None
    if mode == "ally":
        targets = _listed_units(battle, actor, listed, allies=True)
        if actor.unit_id in listed and actor.position is not None:
            targets = [actor, *[target for target in targets if target.unit_id != actor.unit_id]]
        for target in targets:
            payload = finish(_at_unit(actor, code, target))
            if _payload_matters(battle, actor, payload):
                return payload
        return None
    if mode in {"none", "self"} and not preview.get("requires_target"):
        payload = finish({"type": "skill", "unit_id": actor.unit_id, "skill_code": code})
        return payload if _payload_matters(battle, actor, payload) else None
    if mode == "cell":
        patterns = _selection_patterns(preview)
        if patterns:
            return finish(_pattern_payload(battle, actor, code, patterns))
        choices = _selection_choices(preview)
        if choices:
            payload = _choice_payload(battle, actor, code, choices)
            if direction_required:
                token = _direction_toward_enemy(battle, actor, directions)
                directed = _choice_payload_for_code(battle, actor, code, choices, token)
                if directed is not None:
                    payload = directed
            return finish(payload)
        selection_mode = str(_selection(preview).get("mode") or "")
        # An undrawn pattern needs several cells; one coordinate raises ActionError.
        if selection_mode == "pattern_cells" or not _one_cell_ok(selection_mode, code):
            return None
        cells = _stride(_positions(preview.get("cells")))
        if not cells:
            return None
        enemies = _enemies(battle, actor.player_id)
        ordered = sorted(
            cells,
            key=lambda cell: min(
                (cell.distance_to(enemy.position) for enemy in enemies if enemy.position is not None),
                default=0,
            ),
        )
        for chosen in ordered[:8]:
            payload = finish({"type": "skill", "unit_id": actor.unit_id, "skill_code": code, "x": chosen.x, "y": chosen.y})
            if _payload_matters(battle, actor, payload):
                return payload
        return None
    return None


def _payload_matters(battle: Battle, actor: Unit, payload: Optional[dict[str, Any]]) -> bool:
    """False when casting would leave the board unchanged.

    A legal skill can still do nothing: the ally already has the buff, or the
    area does not touch anyone. That cast must not be chosen again.
    """
    if payload is None:
        return False
    if payload.get("type") != "skill":
        return True
    code = str(payload.get("skill_code") or "")
    skill = None
    if hasattr(actor, "get_skill"):
        try:
            skill = actor.get_skill(code)
        except Exception:
            skill = None
    if skill is None:
        return True
    before = _board_mark(battle)
    from wujiang.tactical.rooms.ai import ai_probe_rollback
    with ai_probe_rollback(battle):
        try:
            skill.execute(battle, actor, payload)
        except Exception:
            return False
        return _board_mark(battle) != before


def _board_mark(battle: Battle) -> tuple[Any, ...]:
    units = []
    for unit in battle.all_units():
        position = getattr(unit, "position", None)
        statuses = tuple(sorted(str(getattr(status, "name", "")) for status in (getattr(unit, "statuses", None) or [])))
        units.append((
            unit.unit_id,
            bool(unit.alive),
            round(float(unit.current_hp), 4),
            round(float(unit.current_mana), 4),
            None if position is None else (int(position.x), int(position.y)),
            statuses,
        ))
    effects = tuple(sorted(str(getattr(effect, "name", type(effect).__name__)) for effect in battle.field_effects))
    return (tuple(sorted(units)), effects)


def _one_cell_ok(mode: str, code: str) -> bool:
    """True when one preview cell is a destination.

    SAFE_CELL_SKILLS is the dash subset of this rule. Other codes use it too.
    pattern_cells, revive, and other multi-part modes are not one destination.
    """
    if code in SAFE_CELL_SKILLS and mode not in _NOT_ONE_CELL:
        return True
    return mode not in _NOT_ONE_CELL


def _selection(preview: Any) -> dict[str, Any]:
    if not isinstance(preview, dict):
        return {}
    selection = preview.get("selection")
    if not isinstance(selection, dict):
        return {}
    return selection


def _selection_patterns(preview: Any) -> list[Any]:
    patterns = _selection(preview).get("patterns")
    if not isinstance(patterns, list) or not patterns:
        return []
    return patterns


def _selection_choices(preview: Any) -> list[Any]:
    choices = _selection(preview).get("choices")
    if not isinstance(choices, list) or not choices:
        return []
    return choices


def _stride(items: list[Any], limit: int = _SAMPLE_LIMIT) -> list[Any]:
    if len(items) <= limit:
        return list(items)
    return list(items)[:: max(1, len(items) // limit)][:limit]


def _as_position(cell: Any) -> Optional[Position]:
    if isinstance(cell, Position):
        return cell
    if isinstance(cell, dict):
        raw_x, raw_y = cell.get("x"), cell.get("y")
    else:
        raw_x, raw_y = getattr(cell, "x", None), getattr(cell, "y", None)
    if raw_x is None or raw_y is None:
        return None
    return Position(int(raw_x), int(raw_y))


def _read_occupied_cells(battle: Battle, enemy: Any) -> list[Position]:
    reader = getattr(battle, "unit_cells", None)
    if not callable(reader):
        anchor = _as_position(getattr(enemy, "position", None))
        return [anchor] if anchor is not None else []
    return [cell for cell in (_as_position(item) for item in (reader(enemy) or [])) if cell is not None]


def _enemy_occupied_cells(battle: Battle, enemies: Iterable[Any]) -> set[Position]:
    occupied: set[Position] = set()
    for enemy in enemies:
        occupied.update(_read_occupied_cells(battle, enemy))
    return occupied


def _pattern_overlaps(pattern: Any, occupied: set[Position]) -> bool:
    return any(cell in occupied for cell in _positions(pattern))


def _choice_overlaps(choice: dict[str, Any], occupied: set[Position]) -> bool:
    patterns = choice.get("patterns")
    if not isinstance(patterns, list):
        return False
    return any(_pattern_overlaps(pattern, occupied) for pattern in patterns)


def _stride_covering(items: list[Any], occupied: set[Position], overlaps: Any) -> list[Any]:
    covering = [item for item in items if overlaps(item, occupied)]
    if not covering:
        return []
    return _stride(covering)


def _pattern_payload(
    battle: Battle,
    actor: Unit,
    code: str,
    patterns: list[Any],
) -> Optional[dict[str, Any]]:
    enemies = [enemy for enemy in _enemies(battle, actor.player_id) if enemy.position is not None]
    occupied = _enemy_occupied_cells(battle, enemies)
    sampled = _stride_covering(patterns, occupied, _pattern_overlaps)
    if not sampled:
        return None

    def rank(pattern: Any) -> tuple[int, int]:
        cells = _positions(pattern)
        if not cells:
            return (1, 10**9)
        if not enemies:
            return (0, 0)
        nearest = min(min(cell.distance_to(enemy.position) for enemy in enemies) for cell in cells)
        return (0, nearest)

    for pattern in sorted(sampled, key=rank)[:6]:
        chosen = _positions(pattern)
        if not chosen:
            continue
        anchor = min(chosen, key=lambda cell: min(cell.distance_to(enemy.position) for enemy in enemies)) if enemies else chosen[0]
        shaped = [{"x": cell.x, "y": cell.y} for cell in chosen]
        payload = {
            "type": "skill",
            "unit_id": actor.unit_id,
            "skill_code": code,
            "cells": shaped,
            "declared_area_cells": shaped,
            "x": anchor.x,
            "y": anchor.y,
        }
        if _payload_matters(battle, actor, payload):
            return payload
    return None


def _choice_payload(
    battle: Battle,
    actor: Unit,
    code: str,
    choices: list[Any],
) -> Optional[dict[str, Any]]:
    viable = [choice for choice in choices if isinstance(choice, dict)]
    enemies = [enemy for enemy in _enemies(battle, actor.player_id) if enemy.position is not None]
    occupied = _enemy_occupied_cells(battle, enemies)
    sampled = _stride_covering(viable, occupied, _choice_overlaps)
    ranked: list[tuple[int, int, str, list[Any]]] = []
    for index, choice in enumerate(sampled):
        patterns = choice.get("patterns")
        if not isinstance(patterns, list) or not patterns:
            continue
        covering = [pattern for pattern in patterns if _pattern_overlaps(pattern, occupied)]
        if not covering:
            continue
        focus = covering
        cells = [cell for pattern in focus for cell in _positions(pattern)]
        if not cells:
            continue
        if enemies:
            nearest = min(min(cell.distance_to(enemy.position) for enemy in enemies) for cell in cells)
        else:
            nearest = 0
        ranked.append((nearest, index, str(choice.get("code") or ""), focus))
    if not ranked:
        return None
    _, _, choice_code, focus = min(ranked, key=lambda item: (item[0], item[1]))
    payload = _pattern_payload(battle, actor, code, focus)
    if payload is None:
        return None
    payload["choice_code"] = choice_code
    return payload


def _choice_payload_for_code(
    battle: Battle,
    actor: Unit,
    code: str,
    choices: list[Any],
    token: Any,
) -> Optional[dict[str, Any]]:
    if not isinstance(token, str):
        return None
    for choice in choices:
        if not isinstance(choice, dict) or str(choice.get("code") or "") != token:
            continue
        patterns = choice.get("patterns")
        if not isinstance(patterns, list) or not patterns:
            return None
        payload = _pattern_payload(battle, actor, code, [patterns[0]])
        if payload is None:
            return None
        payload["choice_code"] = token
        return payload
    return None


def _listed_directions(preview: Any) -> list[Any]:
    selection = _selection(preview)
    compass: list[Any] = []
    choices = selection.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            code = str(choice.get("code") or "")
            if code in _COMPASS_STEPS:
                compass.append(code)
    if compass:
        return compass
    raw = selection.get("directions")
    if not isinstance(raw, list) or not raw:
        raw = preview.get("directions") if isinstance(preview, dict) else None
    if not isinstance(raw, list):
        return []
    return [item for item in raw if _direction_step(item) is not None]


def _chebyshev(left: Any, right: Any) -> int:
    return max(abs(int(left.x) - int(right.x)), abs(int(left.y) - int(right.y)))


def _direction_step(item: Any) -> Optional[tuple[int, int]]:
    if isinstance(item, str):
        return _COMPASS_STEPS.get(item)
    if not isinstance(item, dict):
        return None
    code = str(item.get("code") or item.get("direction") or "")
    if code in _COMPASS_STEPS:
        return _COMPASS_STEPS[code]
    if item.get("dx") is None or item.get("dy") is None:
        return None
    return (int(item["dx"]), int(item["dy"]))


def _direction_token(item: Any) -> Any:
    if isinstance(item, str) and item in _COMPASS_STEPS:
        return item
    if not isinstance(item, dict):
        return None
    code = item.get("code") or item.get("direction")
    if isinstance(code, str) and code in _COMPASS_STEPS:
        return code
    if item.get("dx") is None or item.get("dy") is None:
        return None
    return {"dx": int(item["dx"]), "dy": int(item["dy"])}


def _direction_toward_enemy(battle: Battle, actor: Unit, listed: list[Any]) -> Any:
    sampled = _stride(listed)
    if not sampled:
        return None
    origin = getattr(actor, "position", None)
    enemies = [enemy for enemy in _enemies(battle, actor.player_id) if enemy.position is not None]
    enemy = None
    if origin is not None and getattr(origin, "x", None) is not None and enemies:
        enemy = min(enemies, key=lambda unit: _chebyshev(origin, unit.position))

    def rank(item: Any) -> tuple[int, int]:
        step = _direction_step(item)
        if step is None or origin is None or enemy is None or enemy.position is None:
            return (1 if step is None else 0, 0)
        landed = Position(origin.x + step[0], origin.y + step[1])
        return (0, landed.distance_to(enemy.position))

    return _direction_token(min(sampled, key=rank))


def _attack(battle: Battle, actor: Unit, snapshot: dict[str, Any]) -> Optional[dict[str, Any]]:
    for action in snapshot.get("actions", []):
        if action.get("kind") != "attack" or not action.get("available"):
            continue
        listed = [str(item) for item in (action.get("preview", {}).get("target_unit_ids") or [])]
        target = _listed_unit(battle, actor, listed, allies=False, skip_hopeless=True)
        if target is None or target.position is None:
            continue
        payload: dict[str, Any] = {"type": "attack", "unit_id": actor.unit_id}
        payload.update(dict(action.get("attack_payload") or {}))
        payload["target_unit_id"] = target.unit_id
        payload["x"] = target.position.x
        payload["y"] = target.position.y
        return payload
    return None


def _move(battle: Battle, actor: Unit, snapshot: dict[str, Any], *, away: bool) -> Optional[dict[str, Any]]:
    if actor.position is None:
        return None
    cells: list[Position] = []
    for action in snapshot.get("actions", []):
        if action.get("kind") == "move" and action.get("available"):
            cells = _positions(action.get("preview", {}).get("cells"))
            break
    if not cells:
        return None
    if len(cells) > 12:
        cells = cells[:: max(1, len(cells) // 12)][:12]
    enemies = _focus_enemies(battle, actor)
    origin = actor.position

    def rank(cell: Position) -> tuple[float, int]:
        if not enemies:
            return (float(cell.distance_to(origin)), 0)
        distance = min(cell.distance_to(enemy.position) for enemy in enemies if enemy.position is not None)
        return ((-distance if away else distance), cell.distance_to(origin))

    chosen = min(cells, key=rank)
    return {"type": "move", "unit_id": actor.unit_id, "x": chosen.x, "y": chosen.y}


def defense_is_hopeless(attack: float, defense: float) -> bool:
    """Damage falls off as 1/2^(defense-attack+1). A gap of 8 is already noise."""
    return float(defense) >= float(attack) + 8


def _hopeless_target(actor: Unit, target: Unit) -> bool:
    return defense_is_hopeless(float(actor.stat("attack")), float(target.stat("defense")))


def _listed_units(
    battle: Battle,
    actor: Unit,
    raw_ids: list[str],
    *,
    allies: bool,
    skip_hopeless: bool = False,
) -> list[Unit]:
    found: list[Unit] = []
    for raw in raw_ids:
        unit = battle.units.get(str(raw))
        if unit is None or not unit.alive or unit.banished or unit.position is None:
            continue
        same_side = unit.player_id == actor.player_id
        if allies and not same_side:
            continue
        if not allies and same_side:
            continue
        found.append(unit)
    if skip_hopeless:
        useful = [unit for unit in found if not _hopeless_target(actor, unit)]
        if useful:
            found = useful
        elif found:
            return []
    if actor.position is None:
        return []
    return sorted(found, key=lambda unit: (_ratio(unit), unit.position.distance_to(actor.position)))


def _listed_unit(
    battle: Battle,
    actor: Unit,
    raw_ids: list[str],
    *,
    allies: bool,
    skip_hopeless: bool = False,
) -> Optional[Unit]:
    found = _listed_units(battle, actor, raw_ids, allies=allies, skip_hopeless=skip_hopeless)
    return found[0] if found else None


def _focus_enemies(battle: Battle, actor: Unit) -> list[Unit]:
    enemies = _enemies(battle, actor.player_id)
    useful = [unit for unit in enemies if not _hopeless_target(actor, unit)]
    return useful or enemies


def _enemies(battle: Battle, player_id: int) -> list[Unit]:
    return [
        unit
        for unit in battle.units.values()
        if unit.alive and not unit.banished and unit.position is not None and unit.player_id != player_id
    ]


def _positions(raw_cells: Any) -> list[Position]:
    cells: list[Position] = []
    for cell in raw_cells or []:
        if isinstance(cell, dict) and cell.get("x") is not None and cell.get("y") is not None:
            cells.append(Position(int(cell["x"]), int(cell["y"])))
    return cells


def _recover_mana_payload(
    battle: Battle,
    actor: Unit,
    code: str,
    listed: list[str],
) -> Optional[dict[str, Any]]:
    skill = actor.get_skill(code) if hasattr(actor, "get_skill") else None
    if skill is not None and int(getattr(skill, "uses_this_turn", 0) or 0) >= 1:
        return None
    target = _listed_unit(battle, actor, listed, allies=True)
    if target is None:
        return None
    room = max(0.0, float(target.max_mana()) - float(target.current_mana)) if hasattr(target, "max_mana") else 1.0
    if target.unit_id == actor.unit_id:
        cost = float(getattr(skill, "mana_cost", 1) or 0)
        if room <= cost:
            return None
    elif room <= 0:
        return None
    return _at_unit(actor, code, target)


def _at_unit(actor: Unit, code: str, target: Optional[Unit]) -> Optional[dict[str, Any]]:
    if target is None or target.position is None:
        return None
    return {
        "type": "skill",
        "unit_id": actor.unit_id,
        "skill_code": code,
        "target_unit_id": target.unit_id,
        "x": target.position.x,
        "y": target.position.y,
    }


def _ratio(unit: Unit) -> float:
    return float(unit.current_hp) / max(float(unit.max_health), 0.001)
