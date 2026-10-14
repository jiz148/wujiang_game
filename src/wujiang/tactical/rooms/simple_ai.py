"""Bounded battle AI.

A unit already publishes what it can do through action_snapshot_for.
This picker only reads that list and keeps a fixed number of options.
The detailed scorer in ai.py stays in the tree. The room calls ai_router,
which uses this picker only while the simplified AI setting is on.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from wujiang.tactical.engine.core import Battle, Position, Unit

MOVE_LIMIT = 12


def choose_turn_bundle_action(
    battle: Battle,
    units: Iterable[Unit],
    difficulty: str,
) -> tuple[dict[str, Any], Optional[Unit]]:
    del difficulty
    for unit in units:
        if not unit.can_take_turn_actions(battle):
            continue
        snapshot = battle.action_snapshot_for(unit)
        skill = _skill_payload(battle, unit, snapshot, timing="active")
        if skill is not None:
            return skill, unit
        attack = _attack_payload(battle, unit, snapshot)
        if attack is not None:
            return attack, unit
        move = _move_payload(battle, unit, snapshot)
        if move is not None:
            return move, unit
    return {"type": "end_turn"}, None


def choose_instant_action(battle: Battle, units: Iterable[Unit], difficulty: str) -> Optional[dict[str, Any]]:
    del difficulty
    for unit in list(units)[:4]:
        snapshot = battle.action_snapshot_for(unit)
        payload = _skill_payload(battle, unit, snapshot, timing="instant")
        if payload is not None:
            return payload
    return None


def choose_chain_reaction(
    battle: Battle,
    reactor: Unit,
    options: list[dict[str, Any]],
    difficulty: str,
) -> Optional[dict[str, Any]]:
    del battle, reactor, options, difficulty
    return {"type": "chain_skip"}


def choose_respawn_action(
    battle: Battle,
    unit: Unit,
    options: list[Position],
    difficulty: str,
) -> Optional[dict[str, Any]]:
    del difficulty
    if not options:
        return None
    enemies = _enemies(battle, unit.player_id)
    sample = list(options)[:24]

    def rank(cell: Position) -> tuple[int, int, int]:
        if not enemies:
            return (0, cell.x, cell.y)
        distance = min(
            cell.distance_to(enemy.position)
            for enemy in enemies
            if enemy.position is not None
        )
        return (distance, cell.x, cell.y)

    destination = min(sample, key=rank)
    return {"type": "respawn_select", "unit_id": unit.unit_id, "x": destination.x, "y": destination.y}


def choose_damage_choice_action(battle: Battle) -> dict[str, Any]:
    prompt = battle.pending_damage_choice or {}
    unit_id = str(prompt.get("unit_id") or "")
    kind = str(prompt.get("kind") or "")
    options = prompt.get("options") or []
    if kind in {"attack_swap", "formation"} and options:
        return {"type": "damage_choice", "unit_id": unit_id, "target_unit_id": options[0]}
    return {
        "type": "damage_choice",
        "unit_id": unit_id,
        "stat_name": "decline",
        "target_unit_id": "decline",
    }


def _attack_payload(battle: Battle, actor: Unit, snapshot: dict[str, Any]) -> Optional[dict[str, Any]]:
    for action in snapshot.get("actions", []):
        if action.get("kind") == "attack" and action.get("available"):
            payload = _targeted_payload(battle, actor, action, "attack")
            if payload is not None:
                return payload
    return None


def _skill_payload(
    battle: Battle,
    actor: Unit,
    snapshot: dict[str, Any],
    *,
    timing: str,
) -> Optional[dict[str, Any]]:
    """One shared rule for every hero: use a skill the unit already listed.

    Only the first few usable skills are considered. A skill is usable when
    its own preview already names a legal unit or cell, or when it needs no
    target. Pattern and forced-direction skills are left alone.
    """
    seen = 0
    for action in snapshot.get("actions", []):
        if action.get("kind") != "skill" or not action.get("available"):
            continue
        if str(action.get("timing") or "") != timing:
            continue
        if str(action.get("direction_mode") or "none") == "required":
            continue
        if action.get("max_uses_per_turn") is None and int(action.get("uses_this_turn") or 0) >= 1:
            continue
        seen += 1
        if seen > 4:
            break
        payload = _skill_from_preview(battle, actor, action)
        if payload is not None:
            return payload
    return None


def _skill_from_preview(battle: Battle, actor: Unit, action: dict[str, Any]) -> Optional[dict[str, Any]]:
    preview = action.get("preview") or {}
    mode = str(action.get("target_mode") or "none")
    listed = [str(item) for item in (preview.get("target_unit_ids") or [])]
    if mode in {"enemy", "unit"}:
        target = _listed_unit(battle, actor, listed, allies=False)
        if target is None and mode == "unit":
            target = _listed_unit(battle, actor, listed, allies=True)
        return _skill_at_unit(actor, action, target) if target is not None else None
    if mode == "ally":
        target = _listed_unit(battle, actor, listed, allies=True)
        return _skill_at_unit(actor, action, target) if target is not None else None
    if mode in {"none", "self"} and not preview.get("requires_target"):
        return {"type": "skill", "unit_id": actor.unit_id, "skill_code": str(action.get("code") or "")}
    if mode == "cell":
        cells = _positions(preview.get("cells"))[:8]
        if not cells or actor.position is None:
            return None
        enemies = _enemies(battle, actor.player_id)
        chosen = min(
            cells,
            key=lambda cell: min(
                (cell.distance_to(enemy.position) for enemy in enemies if enemy.position is not None),
                default=0,
            ),
        )
        return {
            "type": "skill",
            "unit_id": actor.unit_id,
            "skill_code": str(action.get("code") or ""),
            "x": chosen.x,
            "y": chosen.y,
        }
    return None


def _skill_at_unit(actor: Unit, action: dict[str, Any], target: Unit) -> Optional[dict[str, Any]]:
    if target.position is None:
        return None
    return {
        "type": "skill",
        "unit_id": actor.unit_id,
        "skill_code": str(action.get("code") or ""),
        "target_unit_id": target.unit_id,
        "x": target.position.x,
        "y": target.position.y,
    }


def _listed_unit(battle: Battle, actor: Unit, raw_ids: list[str], *, allies: bool) -> Optional[Unit]:
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
    if not found or actor.position is None:
        return None
    return min(found, key=lambda unit: (unit.current_hp, unit.position.distance_to(actor.position)))


def _targeted_payload(
    battle: Battle,
    actor: Unit,
    action: dict[str, Any],
    kind: str,
) -> Optional[dict[str, Any]]:
    preview = action.get("preview") or {}
    target = _preferred_enemy(battle, actor, preview.get("target_unit_ids") or [])
    if target is None or target.position is None:
        return None
    payload: dict[str, Any] = {"type": kind, "unit_id": actor.unit_id}
    if kind == "attack":
        payload.update(dict(action.get("attack_payload") or {}))
    else:
        payload["skill_code"] = str(action.get("code") or "")
    payload["target_unit_id"] = target.unit_id
    payload["x"] = target.position.x
    payload["y"] = target.position.y
    return payload


def _move_payload(battle: Battle, actor: Unit, snapshot: dict[str, Any]) -> Optional[dict[str, Any]]:
    if actor.position is None:
        return None
    cells: list[Position] = []
    for action in snapshot.get("actions", []):
        if action.get("kind") == "move" and action.get("available"):
            cells = _positions(action.get("preview", {}).get("cells"))
            break
    if not cells:
        return None
    chosen = _shortlist(battle, actor, cells)[0]
    return {"type": "move", "unit_id": actor.unit_id, "x": chosen.x, "y": chosen.y}


def _shortlist(battle: Battle, actor: Unit, cells: list[Position]) -> list[Position]:
    origin = actor.position
    enemies = _enemies(battle, actor.player_id)
    if origin is None or not enemies or len(cells) <= MOVE_LIMIT:
        return cells[:MOVE_LIMIT] or cells
    preferred = max(1, int(actor.targeting_range()))

    def rank(cell: Position) -> tuple[float, int]:
        enemy_distance = min(
            cell.distance_to(enemy.position)
            for enemy in enemies
            if enemy.position is not None
        )
        step = cell.distance_to(origin)
        if preferred <= 1:
            return (float(enemy_distance), step)
        return (abs(enemy_distance - preferred), step)

    return sorted(cells, key=rank)[:MOVE_LIMIT]


def _preferred_enemy(battle: Battle, actor: Unit, raw_ids: list[Any]) -> Optional[Unit]:
    enemies: list[Unit] = []
    for raw in raw_ids:
        unit = battle.units.get(str(raw))
        if unit is None or not unit.alive or unit.banished or unit.position is None:
            continue
        if unit.player_id == actor.player_id:
            continue
        enemies.append(unit)
    if not enemies or actor.position is None:
        return None
    return min(
        enemies,
        key=lambda unit: (unit.current_hp, unit.position.distance_to(actor.position)),
    )


def _enemies(battle: Battle, player_id: int) -> list[Unit]:
    return [
        unit
        for unit in battle.units.values()
        if unit.alive
        and not unit.banished
        and unit.position is not None
        and unit.player_id != player_id
        and not bool(getattr(unit, "is_siege_structure", False))
    ]


def _positions(raw_cells: Any) -> list[Position]:
    cells: list[Position] = []
    for cell in raw_cells or []:
        if isinstance(cell, dict) and cell.get("x") is not None and cell.get("y") is not None:
            cells.append(Position(int(cell["x"]), int(cell["y"])))
        elif isinstance(cell, Position):
            cells.append(cell)
    return cells
