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
        if unit.hero_code == "excel_r373" and unit.current_mana > 0:
            overclock = _skill_payload(battle, unit, snapshot, timing="instant")
            if overclock is not None:
                return overclock, unit
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
    ordered = list(units)
    ordered.sort(key=lambda unit: unit.hero_code != "excel_r373")
    for unit in ordered[:4]:
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
    del difficulty
    if reactor.hero_code == "excel_r373" and battle.pending_chain is not None:
        queued = battle.pending_chain.queued_action
        for option in options:
            code = str(option.get("action_code") or "")
            if code not in {"ion_shield", "infinity_instant__ion_shield"}:
                continue
            if queued.payload.get("ignore_shield"):
                continue
            preview = option.get("preview") or {}
            affected = set(queued.target_unit_ids)
            affected.update(unit.unit_id for unit in battle.effect_units_at_cells(queued.target_cells))
            target_id = next((str(item) for item in preview.get("target_unit_ids", [])
                              if str(item) in affected), None)
            if target_id is None:
                continue
            payload = {"type": "chain_react", "unit_id": reactor.unit_id,
                       "action_code": code, "target_unit_ids": [target_id]}
            if reactor.get_skill(code).can_react_with_payload(battle, reactor, queued, payload)[0]:
                return payload
    if reactor.hero_code in {"excel_r328", "excel_r329", "flying_dragon_ship_atlanta"}:
        queued = battle.pending_chain.queued_action if battle.pending_chain is not None else None
        if queued is not None and not queued.payload.get("ignore_shield"):
            affected_ids = set(queued.target_unit_ids)
            affected_ids.update(unit.unit_id for unit in battle.effect_units_at_cells(queued.target_cells))
            for option in options:
                code = str(option.get("action_code") or "")
                if code.rsplit("__", 1)[-1] not in {"protection", "light_wall"}:
                    continue
                preview = option.get("preview") or {}
                target_ids = [str(item) for item in preview.get("target_unit_ids") or []]
                target_id = next((item for item in target_ids if item in affected_ids), None)
                if target_id is None:
                    continue
                payload = {"type": "chain_react", "unit_id": reactor.unit_id,
                           "action_code": code, "target_unit_id": target_id,
                           "target_unit_ids": [target_id]}
                if reactor.get_skill(code).can_react_with_payload(battle, reactor, queued, payload)[0]:
                    return payload
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
    if kind == "gentleman_guard":
        protected = battle.units.get(str(prompt.get("protected_unit_id") or ""))
        damage = float(prompt.get("damage") or 0)
        use = protected is not None and not protected.is_clone and damage >= protected.current_hp
        return {"type": "damage_choice", "unit_id": unit_id,
                "target_unit_id": "protect" if use else "decline"}
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
    actions = list(snapshot.get("actions", []))
    if actor.hero_code == "excel_r324":
        priority = {"eight_dragon_crow_array": 0, "square_crow_array": 1,
                    "hexagram_crow_array": 2, "triangle_crow_array": 3,
                    "super_messenger_crow": 4}
        actions.sort(key=lambda item: priority.get(str(item.get("code") or ""), 5))
    if actor.hero_code in {"excel_r328", "excel_r329"}:
        priority = {"flying_dragon_ship": 0, "departure_signal": 1,
                    "dragon_breath": 2, "add_health": 2, "add_mana": 3,
                    "chain_pull": 4, "defense_one": 5, "silver_signal_flare": 6}
        actions.sort(key=lambda item: priority.get(str(item.get("code") or ""), 7))
    for action in actions:
        if action.get("kind") != "skill" or not action.get("available"):
            continue
        if str(action.get("timing") or "") != timing:
            continue
        if str(action.get("direction_mode") or "none") == "required":
            continue
        repeatable_crow = actor.hero_code == "excel_r324" and str(action.get("code") or "") in {
            "super_messenger_crow", "triangle_crow_array", "square_crow_array",
            "hexagram_crow_array", "eight_dragon_crow_array"}
        if action.get("max_uses_per_turn") is None and int(action.get("uses_this_turn") or 0) >= (3 if repeatable_crow else 1):
            continue
        seen += 1
        if seen > (7 if actor.hero_code in {"excel_r328", "excel_r329", "flying_dragon_ship_atlanta"} else 4):
            break
        code = str(action.get("code") or "").rsplit("__", 1)[-1]
        if code == "silver_signal_flare":
            signal = actor.get_skill(str(action.get("code")))
            signal = getattr(signal, "inner", signal)
            targets = [unit for unit in signal.beneficiaries(battle, actor)
                       if not unit.has_status("银制信号弹攻势")]
            if any(unit.is_clone and not unit.direct_effects_blocked() for unit in targets):
                continue
            if not any(not unit.is_clone and battle.unit_belongs_to_current_turn(unit)
                       and unit.attacks_used < unit.attack_actions_per_turn()
                       for unit in targets):
                continue
        payload = _skill_from_preview(battle, actor, action)
        if payload is not None:
            return payload
    return None


def _skill_from_preview(battle: Battle, actor: Unit, action: dict[str, Any]) -> Optional[dict[str, Any]]:
    preview = action.get("preview") or {}
    mode = str(action.get("target_mode") or "none")
    listed = [str(item) for item in (preview.get("target_unit_ids") or [])]
    source_code = str(action.get("code") or "").rsplit("__", 1)[-1]
    if actor.hero_code == "excel_r373" and source_code in {"machine_gun", "missile"}:
        patterns = (preview.get("selection") or {}).get("patterns") or []
        ranked: list[tuple[float, list[Position]]] = []
        for pattern in patterns:
            positions = _positions(pattern)
            targets = [target for target in battle.effect_units_at_cells(positions)
                       if not target.magic_immunity]
            net = sum((1.0 if target.player_id != actor.player_id else -1.6)
                      * max(1, battle.unit_hit_count_for_cells(target, positions))
                      for target in targets)
            if str(action.get("code") or "").startswith("infinity_instant__") and actor.player_id == battle.active_player:
                premium = sum((1.0 if target.player_id != actor.player_id else -1.6)
                              * (battle.damage_rule.calculate_damage(actor.stat("attack") + 2, target.stat("defense"))
                                 - battle.damage_rule.calculate_damage(actor.stat("attack"), target.stat("defense")))
                              for target in targets)
                if premium <= 0:
                    continue
            if net > 0:
                ranked.append((net, positions))
        if not ranked:
            return None
        chosen = max(ranked, key=lambda entry: entry[0])[1]
        return {"type": "skill", "unit_id": actor.unit_id,
                "skill_code": str(action.get("code") or ""),
                "cells": [{"x": cell.x, "y": cell.y} for cell in chosen]}
    if source_code in {"add_mana", "defense_one"}:
        allies = [battle.units.get(unit_id) for unit_id in listed]
        allies = [unit for unit in allies if unit is not None and unit.player_id == actor.player_id]
        if source_code == "add_mana":
            allies = [unit for unit in allies if unit.current_mana < unit.max_mana()
                      and (unit is not actor or actor.get_skill(str(action.get("code"))).mana_cost == 0)]
        else:
            allies = [unit for unit in allies if not any(
                status.name == "守+1" and getattr(status, "source_id", None) == actor.unit_id
                for status in unit.statuses)]
        target = min(allies, key=lambda unit: (unit.current_hp, -unit.stat("attack")), default=None)
        return _skill_at_unit(actor, action, target) if target is not None else None
    if mode in {"enemy", "unit"}:
        target = _listed_unit(battle, actor, listed, allies=False)
        if target is None and mode == "unit":
            target = _listed_unit(battle, actor, listed, allies=True)
        payload = _skill_at_unit(actor, action, target) if target is not None else None
        if payload is not None and preview.get("selection", {}).get("mode") == "crow_formation":
            ravens = preview.get("raven_ids_by_target", {}).get(target.unit_id, [])
            payload["raven_ids"] = ravens[:int(preview.get("raven_count") or 0)]
        return payload
    if mode == "ally":
        target = _listed_unit(battle, actor, listed, allies=True)
        return _skill_at_unit(actor, action, target) if target is not None else None
    if mode in {"none", "self"} and not preview.get("requires_target"):
        return {"type": "skill", "unit_id": actor.unit_id, "skill_code": str(action.get("code") or "")}
    if mode == "cell":
        if source_code in {"dragon_breath", "chain_pull"}:
            patterns = preview.get("selection", {}).get("patterns") or []
            ranked = []
            for pattern in patterns:
                positions = _positions(pattern)
                recipients = battle.effect_units_at_cells(positions, ignore=actor)
                if source_code == "chain_pull":
                    recipients = recipients[:1]
                score = sum(1 if unit.player_id != actor.player_id else -2 for unit in recipients)
                if score > 0:
                    ranked.append((score, positions))
            if ranked:
                chosen_pattern = max(ranked, key=lambda item: item[0])[1]
                return {"type": "skill", "unit_id": actor.unit_id,
                        "skill_code": str(action.get("code") or ""),
                        "cells": [{"x": cell.x, "y": cell.y} for cell in chosen_pattern]}
            return None
        cells = _positions(preview.get("cells"))
        if source_code not in {"flying_dragon_ship", "departure_signal"}:
            cells = cells[:8]
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
    if actor.hero_code in {"excel_r328", "excel_r329"} and battle.mounted_unit_for(actor) is None:
        from wujiang.tactical.heroes.flying_dragon_ship import allied_ship
        ship = allied_ship(battle, actor.player_id)
        if ship is not None:
            boarding = [cell for cell in cells if cell in battle.unit_cells(ship)]
            if boarding:
                return boarding[:MOVE_LIMIT]
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
