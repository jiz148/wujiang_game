from __future__ import annotations

import copy

from wujiang.strategy.battle_adapter import MAX_GRID_UNITS_PER_SIDE, TROOPS_PER_GRID_UNIT
from wujiang.strategy.heroes import normalize_strategic_hero_deployment, record_strategic_hero_battle_losses
from wujiang.strategy.models import City, EventLogEntry, PendingBattle, StrategyError, WorldState
from wujiang.strategy.simulation import clamp, owner_support


BATTLE_RESOLUTION_MODES = {"manual", "ai_auto", "watch_ai", "quick"}
MIN_ATTACK_TROOPS = 50
ATTACK_COMMITMENT_NUMERATOR = 3
ATTACK_COMMITMENT_DENOMINATOR = 4
REGISTERED_UNIT_TROOP_VALUES = {"infantry": 100, "archer": 140, "cavalry": 180}


def city_attack_commitment(troops: int) -> int:
    available = max(0, int(troops))
    if available < MIN_ATTACK_TROOPS:
        return 0
    return max(MIN_ATTACK_TROOPS, available * ATTACK_COMMITMENT_NUMERATOR // ATTACK_COMMITMENT_DENOMINATOR)


def _clone_world(world: WorldState) -> WorldState:
    return WorldState.from_dict(copy.deepcopy(world.to_dict()))


def _city(world: WorldState, city_id: str) -> City:
    for city in world.cities:
        if city.city_id == city_id:
            return city
    raise StrategyError("City does not exist.")


def _cities_are_connected(world: WorldState, source: City, target: City) -> bool:
    nodes = {node.node_id: node for node in world.nodes}
    source_node = nodes.get(source.node_id)
    return bool(source_node and target.node_id in source_node.connected_node_ids)


def _battle_id(world: WorldState, source_city_id: str, target_city_id: str) -> str:
    return f"battle_{world.current_month}_{source_city_id}_{target_city_id}_{len(world.pending_battles) + 1}"


def _grid_unit_count_for_troops(troop_count: int) -> int:
    troops = max(0, int(troop_count))
    if troops <= 0:
        return 0
    return max(1, min(MAX_GRID_UNITS_PER_SIDE, (troops + TROOPS_PER_GRID_UNIT - 1) // TROOPS_PER_GRID_UNIT))


def _registered_unit_count(units: dict[str, int]) -> int:
    return sum(max(0, int(count)) for count in units.values())


def _registered_unit_power(units: dict[str, int]) -> int:
    return sum(REGISTERED_UNIT_TROOP_VALUES.get(unit_type, 100) * max(0, int(count)) for unit_type, count in units.items())


def _commit_registered_units(inventory: dict[str, int]) -> dict[str, int]:
    committed: dict[str, int] = {}
    remaining = MAX_GRID_UNITS_PER_SIDE
    for unit_type in ("cavalry", "archer", "infantry"):
        count = min(remaining, max(0, int(inventory.get(unit_type, 0))))
        if count <= 0:
            continue
        committed[unit_type] = count
        inventory[unit_type] -= count
        if inventory[unit_type] <= 0:
            inventory.pop(unit_type, None)
        remaining -= count
        if remaining <= 0:
            break
    return committed


def _surviving_registered_units(units: dict[str, int], surviving_count: int) -> dict[str, int]:
    remaining = max(0, min(int(surviving_count), _registered_unit_count(units)))
    survivors: dict[str, int] = {}
    for unit_type in ("cavalry", "archer", "infantry"):
        count = min(remaining, max(0, int(units.get(unit_type, 0))))
        if count > 0:
            survivors[unit_type] = count
            remaining -= count
    return survivors


def _add_registered_units(inventory: dict[str, int], units: dict[str, int]) -> None:
    for unit_type, count in units.items():
        if int(count) > 0:
            inventory[unit_type] = inventory.get(unit_type, 0) + int(count)


def _remaining_troops_from_grid_units(
    initial_troops: int,
    *,
    initial_grid_units: int,
    surviving_grid_units: int,
) -> int:
    if initial_troops <= 0 or initial_grid_units <= 0:
        return 0
    survivors = max(0, min(int(surviving_grid_units), int(initial_grid_units)))
    return max(0, min(int(initial_troops), round(int(initial_troops) * survivors / int(initial_grid_units))))


def _battle_result_payload(
    battle: PendingBattle,
    *,
    winner_side: str,
    city_captured: bool,
    attacker_losses: int,
    defender_losses: int,
    attacker_remaining: int,
    defender_remaining: int,
    attacker_initial_grid_units: int,
    defender_initial_grid_units: int,
    surviving_grid_units_by_team: dict[int, int] | None,
    report_summary: str,
) -> dict[str, object]:
    loser_side = "defender" if winner_side == "attacker" else "attacker"
    winner_faction_id = battle.attacker_faction_id if winner_side == "attacker" else battle.defender_faction_id
    loser_faction_id = battle.defender_faction_id if winner_side == "attacker" else battle.attacker_faction_id
    return {
        "winner_faction_id": winner_faction_id,
        "loser_faction_id": loser_faction_id,
        "winner_side": winner_side,
        "loser_side": loser_side,
        "city_captured": bool(city_captured),
        "resolution_mode": battle.resolution_mode,
        "resolution_source": "real_grid" if surviving_grid_units_by_team is not None else "sandbox",
        "lost_troops_by_side": {
            "attacker": max(0, int(attacker_losses)),
            "defender": max(0, int(defender_losses)),
        },
        "remaining_troops_by_side": {
            "attacker": max(0, int(attacker_remaining)),
            "defender": max(0, int(defender_remaining)),
        },
        "initial_troops_by_side": {
            "attacker": max(0, int(battle.attacker_troops)),
            "defender": max(0, int(battle.defender_troops)),
        },
        "initial_grid_units_by_side": {
            "attacker": max(0, int(attacker_initial_grid_units)),
            "defender": max(0, int(defender_initial_grid_units)),
        },
        "surviving_grid_units_by_side": (
            {
                "attacker": max(0, int(surviving_grid_units_by_team.get(1, 0))),
                "defender": max(0, int(surviving_grid_units_by_team.get(2, 0))),
            }
            if surviving_grid_units_by_team is not None
            else {}
        ),
        "battle_log_summary": report_summary,
    }


def declare_city_attack(
    world: WorldState,
    *,
    faction_id: str,
    source_city_id: str,
    target_city_id: str,
    resolution_mode: str,
    auto_resolve: bool = True,
    attacker_hero_codes: list[str] | tuple[str, ...] | set[str] | None = None,
    attacker_office_id: str = "",
) -> WorldState:
    if resolution_mode not in BATTLE_RESOLUTION_MODES:
        raise StrategyError("Unknown battle resolution mode.")
    next_world = _clone_world(world)
    source = _city(next_world, source_city_id)
    target = _city(next_world, target_city_id)
    if source.owner_faction_id != faction_id:
        raise StrategyError("Only cities controlled by your faction can launch attacks.")
    if target.owner_faction_id == faction_id:
        raise StrategyError("Cannot attack a city controlled by your own faction.")
    if not _cities_are_connected(next_world, source, target):
        raise StrategyError("Only adjacent city nodes can be attacked.")
    attacker_office = next(
        (
            item
            for item in next_world.offices
            if item.office_id == str(attacker_office_id) and item.faction_id == faction_id
        ),
        None,
    )
    available_registered_power = _registered_unit_power(attacker_office.unit_inventory) if attacker_office else 0
    if source.resources.troops < MIN_ATTACK_TROOPS and available_registered_power < MIN_ATTACK_TROOPS:
        raise StrategyError("Source city does not have enough troops.")
    selected_attacker_hero_codes = normalize_strategic_hero_deployment(
        next_world,
        faction_id,
        [] if attacker_hero_codes is None else attacker_hero_codes,
    )

    attacker_registered_units = _commit_registered_units(attacker_office.unit_inventory) if attacker_office else {}
    defender_registered_units = _commit_registered_units(target.registered_units)
    attacker_troops = city_attack_commitment(source.resources.troops)
    defender_troops = target.resources.troops
    source.resources.troops -= attacker_troops
    battle = PendingBattle(
        battle_id=_battle_id(next_world, source_city_id, target_city_id),
        month=next_world.current_month,
        attacker_faction_id=faction_id,
        defender_faction_id=target.owner_faction_id,
        source_city_id=source_city_id,
        target_city_id=target_city_id,
        resolution_mode=resolution_mode,
        attacker_troops=attacker_troops,
        defender_troops=defender_troops,
        attacker_hero_codes=selected_attacker_hero_codes,
        defender_hero_codes=None,
        attacker_office_id=attacker_office.office_id if attacker_office is not None else None,
        attacker_registered_units=attacker_registered_units,
        defender_registered_units=defender_registered_units,
        report=[f"{source.name} sends {attacker_troops} troops to attack {target.name}."],
    )
    next_world.pending_battles.append(battle)
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="battle_declared",
            message=f"{source.name} attacks {target.name}; mode: {resolution_mode}.",
            related_ids=[battle.battle_id, source_city_id, target_city_id],
        )
    )
    if auto_resolve and resolution_mode == "quick":
        return resolve_pending_battle(next_world, battle_id=battle.battle_id)
    next_world.validate()
    return next_world


def attach_battle_room(
    world: WorldState,
    *,
    battle_id: str,
    room_id: str,
    invite_path: str,
) -> WorldState:
    next_world = _clone_world(world)
    battle = next((item for item in next_world.pending_battles if item.battle_id == battle_id), None)
    if battle is None:
        raise StrategyError("Strategy battle does not exist.")
    if battle.status != "pending":
        raise StrategyError("Only pending strategy battles can bind a real battle room.")
    battle.battle_room_id = str(room_id or "")
    battle.battle_room_invite_path = str(invite_path or "")
    battle.report.append(f"Real grid battle room created: {battle.battle_room_id}.")
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="battle_room_created",
            message=f"Strategy battle {battle.battle_id} created real grid room {battle.battle_room_id}.",
            related_ids=[battle.battle_id, battle.battle_room_id],
        )
    )
    from wujiang.strategy.objectives import record_strategic_status_events

    next_world = record_strategic_status_events(next_world)
    next_world.validate()
    return next_world


def set_battle_defender_hero(
    world: WorldState,
    *,
    faction_id: str,
    battle_id: str,
    hero_code: str | list[str] | tuple[str, ...] | set[str],
) -> WorldState:
    next_world = _clone_world(world)
    battle = next((item for item in next_world.pending_battles if item.battle_id == battle_id), None)
    if battle is None:
        raise StrategyError("Strategy battle does not exist.")
    if battle.status != "pending":
        raise StrategyError("Only pending strategy battles can change defender hero deployment.")
    if battle.defender_faction_id != faction_id:
        raise StrategyError("Only the defending faction can set this battle's defender hero.")
    if battle.battle_room_id:
        raise StrategyError("This strategy battle already has a real grid room; defender hero deployment is locked.")

    if isinstance(hero_code, (list, tuple, set)):
        raw_codes = hero_code
    else:
        code = str(hero_code or "").strip()
        raw_codes = [code] if code else []
    defender_hero_codes = normalize_strategic_hero_deployment(next_world, faction_id, raw_codes)
    battle.defender_hero_codes = defender_hero_codes
    if defender_hero_codes:
        battle.report.append(f"Defender commits strategic heroes {', '.join(defender_hero_codes)} to this battle.")
    else:
        battle.report.append("Defender commits no strategic hero to this battle.")
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="battle_defender_hero_set",
            message=f"Defender hero deployment updated for strategy battle {battle.battle_id}.",
            related_ids=[battle.battle_id, faction_id, *defender_hero_codes],
        )
    )
    next_world.validate()
    return next_world


def _apply_battle_outcome(
    next_world: WorldState,
    battle: PendingBattle,
    *,
    attacker_wins: bool,
    preface: str = "",
    surviving_grid_units_by_team: dict[int, int] | None = None,
    surviving_hero_codes_by_team: dict[int, set[str] | list[str] | tuple[str, ...]] | None = None,
) -> WorldState:
    target = _city(next_world, battle.target_city_id)
    source = _city(next_world, battle.source_city_id)
    support = owner_support(target)
    defender_score = (
        battle.defender_troops
        + _registered_unit_power(battle.defender_registered_units)
        + target.defense * 80
        + support * 3
    )
    if preface:
        battle.report.append(preface)
    attacker_initial_grid_units = min(
        MAX_GRID_UNITS_PER_SIDE,
        _registered_unit_count(battle.attacker_registered_units) + _grid_unit_count_for_troops(battle.attacker_troops),
    )
    defender_initial_grid_units = min(
        MAX_GRID_UNITS_PER_SIDE,
        _registered_unit_count(battle.defender_registered_units) + _grid_unit_count_for_troops(battle.defender_troops),
    )
    attacker_remaining_from_room: int | None = None
    defender_remaining_from_room: int | None = None
    if surviving_grid_units_by_team is not None:
        attacker_remaining_from_room = _remaining_troops_from_grid_units(
            battle.attacker_troops,
            initial_grid_units=attacker_initial_grid_units,
            surviving_grid_units=surviving_grid_units_by_team.get(1, 0),
        )
        defender_remaining_from_room = _remaining_troops_from_grid_units(
            battle.defender_troops,
            initial_grid_units=defender_initial_grid_units,
            surviving_grid_units=surviving_grid_units_by_team.get(2, 0),
        )
        battle.report.append(
            "Real grid survivors: "
            f"attacker {surviving_grid_units_by_team.get(1, 0)}/{attacker_initial_grid_units}, "
            f"defender {surviving_grid_units_by_team.get(2, 0)}/{defender_initial_grid_units}."
        )
    if attacker_wins:
        previous_owner_faction_id = target.owner_faction_id
        if attacker_remaining_from_room is None:
            attacker_losses = min(battle.attacker_troops, max(10, defender_score // 3))
            defender_losses = min(target.resources.troops, max(20, battle.attacker_troops // 2))
            survivors = max(0, battle.attacker_troops - attacker_losses)
            defender_remaining = max(0, battle.defender_troops - defender_losses)
        else:
            survivors = attacker_remaining_from_room
            attacker_losses = max(0, battle.attacker_troops - survivors)
            defender_remaining = int(defender_remaining_from_room or 0)
            defender_losses = max(0, battle.defender_troops - defender_remaining)
        target.owner_faction_id = battle.attacker_faction_id
        target.resources.troops = survivors
        target.support_by_faction[battle.attacker_faction_id] = clamp(
            target.support_by_faction.get(battle.attacker_faction_id, 35) + 12,
            0,
            100,
        )
        target.support_by_faction[battle.defender_faction_id] = clamp(
            target.support_by_faction.get(battle.defender_faction_id, 50) - 18,
            0,
            100,
        )
        from wujiang.strategy.occupation import mark_city_captured

        mark_city_captured(
            next_world,
            city_id=target.city_id,
            previous_owner_faction_id=previous_owner_faction_id,
            occupier_faction_id=battle.attacker_faction_id,
        )
        battle.winner_faction_id = battle.attacker_faction_id
        battle.report.extend(
            [
                f"Attacker wins, attacker losses {attacker_losses}, defender losses {defender_losses}.",
                f"{target.name} changes owner; occupying troops {survivors}.",
            ]
        )
        battle.battle_result = _battle_result_payload(
            battle,
            winner_side="attacker",
            city_captured=True,
            attacker_losses=attacker_losses,
            defender_losses=defender_losses,
            attacker_remaining=survivors,
            defender_remaining=defender_remaining,
            attacker_initial_grid_units=attacker_initial_grid_units,
            defender_initial_grid_units=defender_initial_grid_units,
            surviving_grid_units_by_team=surviving_grid_units_by_team,
            report_summary=" ".join(battle.report[-3:]),
        )
    else:
        if attacker_remaining_from_room is None:
            attacker_losses = max(10, battle.attacker_troops * 2 // 3)
            defender_losses = min(target.resources.troops, max(10, battle.attacker_troops // 4))
            defender_survivors = max(0, target.resources.troops - defender_losses)
            attacker_survivors = max(0, battle.attacker_troops - attacker_losses)
        else:
            attacker_survivors = attacker_remaining_from_room
            defender_survivors = int(defender_remaining_from_room or 0)
            attacker_losses = max(0, battle.attacker_troops - attacker_survivors)
            defender_losses = max(0, battle.defender_troops - defender_survivors)
        target.resources.troops = defender_survivors
        source.resources.troops += attacker_survivors
        target.support_by_faction[battle.defender_faction_id] = clamp(
            target.support_by_faction.get(battle.defender_faction_id, 50) + 4,
            0,
            100,
        )
        battle.winner_faction_id = battle.defender_faction_id
        battle.report.extend(
            [
                f"Defender wins, attacker losses {attacker_losses}, defender losses {defender_losses}.",
                f"{source.name} gathers routed troops {attacker_survivors}.",
            ]
        )
        battle.battle_result = _battle_result_payload(
            battle,
            winner_side="defender",
            city_captured=False,
            attacker_losses=attacker_losses,
            defender_losses=defender_losses,
            attacker_remaining=attacker_survivors,
            defender_remaining=defender_survivors,
            attacker_initial_grid_units=attacker_initial_grid_units,
            defender_initial_grid_units=defender_initial_grid_units,
            surviving_grid_units_by_team=surviving_grid_units_by_team,
            report_summary=" ".join(battle.report[-3:]),
        )
    attacker_registered_count = _registered_unit_count(battle.attacker_registered_units)
    defender_registered_count = _registered_unit_count(battle.defender_registered_units)
    if surviving_grid_units_by_team is None:
        attacker_registered_survivor_count = round(attacker_registered_count * (2 / 3 if attacker_wins else 1 / 3))
        defender_registered_survivor_count = round(defender_registered_count * (0 if attacker_wins else 2 / 3))
    else:
        attacker_rate = max(0, int(surviving_grid_units_by_team.get(1, 0))) / max(1, attacker_initial_grid_units)
        defender_rate = max(0, int(surviving_grid_units_by_team.get(2, 0))) / max(1, defender_initial_grid_units)
        attacker_registered_survivor_count = round(attacker_registered_count * attacker_rate)
        defender_registered_survivor_count = round(defender_registered_count * defender_rate)
        if attacker_wins:
            defender_registered_survivor_count = 0
    attacker_registered_survivors = _surviving_registered_units(
        battle.attacker_registered_units,
        attacker_registered_survivor_count,
    )
    defender_registered_survivors = _surviving_registered_units(
        battle.defender_registered_units,
        defender_registered_survivor_count,
    )
    attacker_office = next(
        (item for item in next_world.offices if item.office_id == battle.attacker_office_id),
        None,
    )
    _add_registered_units(
        attacker_office.unit_inventory if attacker_office is not None else source.registered_units,
        attacker_registered_survivors,
    )
    if not attacker_wins:
        _add_registered_units(target.registered_units, defender_registered_survivors)

    next_world, strategic_heroes_by_side = record_strategic_hero_battle_losses(
        next_world,
        attacker_faction_id=battle.attacker_faction_id,
        defender_faction_id=battle.defender_faction_id,
        surviving_hero_codes_by_team=surviving_hero_codes_by_team,
        committed_hero_codes_by_team={1: battle.attacker_hero_codes, 2: battle.defender_hero_codes},
    )
    battle = next(item for item in next_world.pending_battles if item.battle_id == battle.battle_id)
    source = _city(next_world, battle.source_city_id)
    target = _city(next_world, battle.target_city_id)
    if battle.battle_result is not None:
        battle.battle_result["strategic_heroes_by_side"] = strategic_heroes_by_side
        battle.battle_result["registered_units_by_side"] = {
            "attacker_initial": dict(battle.attacker_registered_units),
            "defender_initial": dict(battle.defender_registered_units),
            "attacker_surviving": attacker_registered_survivors,
            "defender_surviving": defender_registered_survivors,
        }
    if attacker_wins:
        from wujiang.strategy.heroes import release_ritual_bindings_for_captured_city

        next_world = release_ritual_bindings_for_captured_city(
            next_world,
            city_id=battle.target_city_id,
            previous_faction_id=battle.defender_faction_id,
        )
        battle = next(item for item in next_world.pending_battles if item.battle_id == battle.battle_id)
    battle.status = "resolved"
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="battle_resolved",
            message=f"{target.name} battle resolved; winner: {battle.winner_faction_id}; mode: {battle.resolution_mode}.",
            related_ids=[battle.battle_id, source.city_id, target.city_id],
        )
    )
    next_world.validate()
    return next_world


def resolve_pending_battle(world: WorldState, *, battle_id: str) -> WorldState:
    next_world = _clone_world(world)
    battle = next((item for item in next_world.pending_battles if item.battle_id == battle_id), None)
    if battle is None:
        raise StrategyError("Strategy battle does not exist.")
    if battle.status != "pending":
        raise StrategyError("Strategy battle has already been resolved.")

    target = _city(next_world, battle.target_city_id)
    support = owner_support(target)
    attacker_score = battle.attacker_troops + _registered_unit_power(battle.attacker_registered_units)
    defender_score = (
        battle.defender_troops
        + _registered_unit_power(battle.defender_registered_units)
        + target.defense * 80
        + support * 3
    )
    return _apply_battle_outcome(next_world, battle, attacker_wins=attacker_score >= defender_score)


def resolve_battle_room_result(
    world: WorldState,
    *,
    battle_room_id: str,
    winner_team_id: int,
    battle_summary: str = "",
    surviving_grid_units_by_team: dict[int, int] | None = None,
    surviving_hero_codes_by_team: dict[int, set[str] | list[str] | tuple[str, ...]] | None = None,
) -> WorldState:
    room_id = str(battle_room_id or "").strip().upper()
    if not room_id:
        raise StrategyError("Real grid battle room id cannot be empty.")
    if int(winner_team_id) not in {1, 2}:
        raise StrategyError("Real grid battle winner team must be 1 or 2.")
    next_world = _clone_world(world)
    battle = next(
        (
            item
            for item in next_world.pending_battles
            if str(item.battle_room_id or "").strip().upper() == room_id
        ),
        None,
    )
    if battle is None:
        raise StrategyError("Real grid battle room is not bound to a strategy battle.")
    if battle.status != "pending":
        return next_world
    attacker_wins = int(winner_team_id) == 1
    side_name = "attacker" if attacker_wins else "defender"
    detail = f"Real grid room {room_id} finished; winning side: {side_name}."
    if battle_summary:
        detail = f"{detail} {str(battle_summary).strip()}"
    return _apply_battle_outcome(
        next_world,
        battle,
        attacker_wins=attacker_wins,
        preface=detail,
        surviving_grid_units_by_team=surviving_grid_units_by_team,
        surviving_hero_codes_by_team=surviving_hero_codes_by_team,
    )
