from __future__ import annotations

import copy

from wujiang.strategic.battle_adapter import MAX_COMPOSED_UNITS, MAX_GRID_UNITS_PER_SIDE, TROOPS_PER_GRID_UNIT
from wujiang.strategic.heroes import (
    normalize_strategic_hero_deployment,
    record_strategic_hero_battle_losses,
    station_heroes_in_city,
    strategic_defender_hero_codes_for_faction,
    strategic_hero_deployment_limit,
)
from wujiang.strategic.models import City, EventLogEntry, PendingBattle, StrategicArmy, StrategicSiege, StrategyError, WorldState
from wujiang.strategic.simulation import clamp, owner_support


def _attacker_heroes_to_station(
    hero_result: dict[str, dict[str, list[str]]] | None,
    fallback_codes: list[str] | tuple[str, ...] | None,
) -> list[str]:
    attacker = (hero_result or {}).get("attacker") or {}
    codes: list[str] = []
    for code in list(attacker.get("surviving") or []) + list(attacker.get("sleeping") or []):
        text = str(code or "")
        if text and text not in codes:
            codes.append(text)
    if codes:
        return codes
    return [str(code) for code in (fallback_codes or []) if code]


BATTLE_RESOLUTION_MODES = {"manual", "ai_auto", "watch_ai", "quick", "formula", "pending_choice"}
BATTLE_PLAYER_CHOICES = {"manual", "ai_auto", "formula", "siege", "retreat"}
BATTLE_UNIT_TROOP_COSTS = {"infantry": 10, "archer": 20, "cavalry": 50}
AUTO_COMPOSED_UNITS = 30
MIN_ATTACK_TROOPS = 50
ATTACK_COMMITMENT_NUMERATOR = 3
ATTACK_COMMITMENT_DENOMINATOR = 4
REGISTERED_UNIT_TROOP_VALUES = {"infantry": 100, "archer": 140, "cavalry": 180}
COMPOSITION_COMBAT_POWER = {"infantry": 12, "archer": 24, "cavalry": 56}
FORMULA_HERO_POWER = 90
RESOLUTION_MODE_LABELS = {
    "manual": "手动开战",
    "ai_auto": "AI 推演",
    "watch_ai": "观看 AI",
    "quick": "快速结算",
    "formula": "快速结算",
    "pending_choice": "待决",
    "siege": "围城",
    "retreat": "撤退",
}


FEATURE_COMPOSITION_BIAS = {
    "弓兵": {"archer": 18},
    "以太侦察兵": {"archer": 10},
    "骑兵": {"cavalry": 16},
    "守备兵": {"infantry": 10},
    "山地兵": {"infantry": 8},
    "城墙工兵": {"infantry": 8},
}


def auto_battle_composition(
    troop_budget: int,
    *,
    city: City | None = None,
    limit: int = AUTO_COMPOSED_UNITS,
) -> dict[str, int]:
    budget = max(0, int(troop_budget))
    infantry_cost = BATTLE_UNIT_TROOP_COSTS["infantry"]
    archer_cost = BATTLE_UNIT_TROOP_COSTS["archer"]
    cavalry_cost = BATTLE_UNIT_TROOP_COSTS["cavalry"]
    if budget < infantry_cost:
        return {}
    slots = min(max(1, int(limit)), budget // infantry_cost)
    infantry_weight = 50
    archer_weight = 35
    cavalry_weight = 15
    for feature in getattr(city, "troop_features", None) or []:
        bias = FEATURE_COMPOSITION_BIAS.get(str(feature) or "")
        if not bias:
            continue
        infantry_weight += int(bias.get("infantry") or 0)
        archer_weight += int(bias.get("archer") or 0)
        cavalry_weight += int(bias.get("cavalry") or 0)
    infantry_weight = max(20, infantry_weight)
    archer_weight = max(10, archer_weight)
    cavalry_weight = max(0, cavalry_weight)
    total_weight = infantry_weight + archer_weight + cavalry_weight
    cavalry_budget = budget * cavalry_weight // total_weight
    archer_budget = budget * archer_weight // total_weight
    infantry_budget = budget - cavalry_budget - archer_budget
    cavalry = min(slots, cavalry_budget // cavalry_cost)
    archer = min(slots - cavalry, archer_budget // archer_cost)
    infantry = min(slots - cavalry - archer, infantry_budget // infantry_cost)
    remaining_slots = slots - cavalry - archer - infantry
    remaining_budget = budget - cavalry * cavalry_cost - archer * archer_cost - infantry * infantry_cost
    extra_infantry = min(remaining_slots, remaining_budget // infantry_cost)
    infantry += extra_infantry
    remaining_slots -= extra_infantry
    remaining_budget -= extra_infantry * infantry_cost
    extra_archer = min(remaining_slots, remaining_budget // archer_cost)
    archer += extra_archer
    remaining_budget -= extra_archer * archer_cost
    extra_cavalry = min(remaining_slots - extra_archer, remaining_budget // cavalry_cost)
    cavalry += extra_cavalry
    composition = {
        "infantry": infantry,
        "archer": archer,
        "cavalry": cavalry,
    }
    return {unit_type: count for unit_type, count in composition.items() if count > 0}


def _composition_unit_count(composition: dict[str, int] | None) -> int:
    raw = composition if isinstance(composition, dict) else {}
    return sum(max(0, int(count or 0)) for count in raw.values())


def _side_initial_grid_units(battle: PendingBattle, side: str) -> int:
    composition = getattr(battle, f"{side}_composition", None)
    if _composition_unit_count(composition) > 0:
        return min(MAX_COMPOSED_UNITS, _composition_unit_count(composition))
    registered = getattr(battle, f"{side}_registered_units", {}) or {}
    troops = battle.attacker_troops if side == "attacker" else battle.defender_troops
    return min(
        MAX_GRID_UNITS_PER_SIDE,
        _registered_unit_count(registered) + _grid_unit_count_for_troops(troops),
    )


def normalize_battle_composition(
    composition: dict[str, object] | None,
    troop_budget: int,
    *,
    limit: int = MAX_COMPOSED_UNITS,
) -> dict[str, int]:
    raw = composition if isinstance(composition, dict) else {}
    counts = {unit_type: max(0, int(raw.get(unit_type, 0) or 0)) for unit_type in BATTLE_UNIT_TROOP_COSTS}
    spent = sum(counts[unit_type] * BATTLE_UNIT_TROOP_COSTS[unit_type] for unit_type in counts)
    total_units = sum(counts.values())
    if total_units <= 0:
        raise StrategyError("至少配置一个作战单位。")
    if total_units > limit:
        raise StrategyError(f"战场最多投入 {limit} 个单位。")
    if spent > max(0, int(troop_budget)):
        raise StrategyError(f"配兵超过可用兵力 {troop_budget}。")
    return {unit_type: count for unit_type, count in counts.items() if count > 0}


def hero_is_stationed_in_city(world: WorldState, hero_code: str, city_id: str) -> bool:
    hero = next((item for item in world.strategic_heroes if item.hero_code == str(hero_code)), None)
    if hero is None:
        return False
    stationed = str(hero.city_id or hero.assignment_target_id or "")
    return stationed == str(city_id)


def city_attack_commitment(troops: int) -> int:
    available = max(0, int(troops))
    if available < MIN_ATTACK_TROOPS:
        return 0
    return max(MIN_ATTACK_TROOPS, available * ATTACK_COMMITMENT_NUMERATOR // ATTACK_COMMITMENT_DENOMINATOR)


def pending_battle_hero_codes(world: WorldState, *, exclude_battle_id: str = "") -> set[str]:
    codes: set[str] = set()
    excluded = str(exclude_battle_id or "")
    for battle in world.pending_battles:
        if battle.status != "pending":
            continue
        if excluded and battle.battle_id == excluded:
            continue
        codes.update(str(code) for code in (battle.attacker_hero_codes or []) if code)
        codes.update(str(code) for code in (battle.defender_hero_codes or []) if code)
    return codes


def normalize_committed_attack_troops(value: object, available: int) -> int:
    if value in {None, ""}:
        committed = city_attack_commitment(available)
    else:
        committed = int(value)
    if committed < MIN_ATTACK_TROOPS:
        raise StrategyError(f"出征至少需要带走 {MIN_ATTACK_TROOPS} 兵力。")
    if committed > max(0, int(available)):
        raise StrategyError(f"出发城可带走兵力不足：可用 {available}，本次要带走 {committed}。")
    return committed


def _clone_world(world: WorldState) -> WorldState:
    return WorldState.from_dict(copy.deepcopy(world.to_dict()))


def _mode_label(mode: str) -> str:
    return RESOLUTION_MODE_LABELS.get(str(mode or ""), str(mode or "待决"))


def _faction_label(world: WorldState, faction_id: str) -> str:
    faction = next((item for item in world.factions if item.faction_id == faction_id), None)
    return faction.name if faction is not None else str(faction_id or "未知势力")


def _maybe_city(world: WorldState, city_id: str) -> City | None:
    if not city_id:
        return None
    try:
        return _city(world, city_id)
    except StrategyError:
        return None


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


def _army_snapshot(army: StrategicArmy) -> dict[str, object]:
    return {
        "faction_id": army.faction_id,
        "manpower": army.manpower,
        "unit_inventory": dict(army.unit_inventory),
        "supply": army.supply,
        "supply_capacity": army.supply_capacity,
        "morale": army.morale,
        "location_node_id": army.location_node_id,
    }


def _aggregate_army_units(armies: list[StrategicArmy]) -> dict[str, int]:
    result: dict[str, int] = {}
    for army in armies:
        for unit_type, count in army.unit_inventory.items():
            result[unit_type] = result.get(unit_type, 0) + max(0, int(count))
    return result


def _representative_city_id(world: WorldState, armies: list[StrategicArmy], faction_id: str) -> str:
    for army in armies:
        if any(city.city_id == army.home_city_id for city in world.cities):
            return army.home_city_id
    owned = next((city for city in world.cities if city.owner_faction_id == faction_id), None)
    if owned is None:
        raise StrategyError("参战势力没有可用于战斗记录的城市。")
    return owned.city_id


def declare_strategic_battle(
    world: WorldState,
    *,
    faction_id: str,
    source_kind: str,
    source_entity_id: str,
    resolution_mode: str,
    auto_resolve: bool = True,
) -> WorldState:
    """Create a battle from a persistent encounter or siege without consuming armies."""
    if resolution_mode not in BATTLE_RESOLUTION_MODES:
        raise StrategyError("Unknown battle resolution mode.")
    if source_kind not in {"encounter", "siege"}:
        raise StrategyError("战略战斗来源无效。")
    next_world = _clone_world(world)
    if any(
        battle.status == "pending"
        and battle.source_kind == source_kind
        and battle.source_entity_id == str(source_entity_id)
        for battle in next_world.pending_battles
    ):
        raise StrategyError("该战略接战已有一场待处理战斗。")

    battle_trigger = "encounter"
    target_city_id: str
    if source_kind == "encounter":
        encounter = next(
            (item for item in next_world.encounters if item.encounter_id == str(source_entity_id) and item.status == "active"),
            None,
        )
        if encounter is None:
            raise StrategyError("遭遇不存在或已经结束。")
        sides = [(side_id, army_ids) for side_id, army_ids in encounter.faction_army_ids.items() if army_ids]
        if len(sides) != 2:
            raise StrategyError("三方及以上遭遇必须先通过撤退或外交拆分为两方。")
        side_ids = {side_id for side_id, _ in sides}
        if faction_id not in side_ids:
            raise StrategyError("只能处理己方参与的遭遇战。")
        attacker_faction_id = faction_id
        defender_faction_id = next(side_id for side_id in side_ids if side_id != faction_id)
        attacker_ids = list(encounter.faction_army_ids[attacker_faction_id])
        defender_ids = list(encounter.faction_army_ids[defender_faction_id])
        battle_node_id = encounter.node_id
    else:
        siege = next(
            (
                item for item in next_world.sieges
                if item.siege_id == str(source_entity_id)
                and item.status in {"breached", "battle_pending"}
                and item.battle_trigger in {"assault", "breakout"}
            ),
            None,
        )
        if siege is None:
            raise StrategyError("围城尚未产生可处理的强攻或突围战斗。")
        if faction_id not in {siege.attacker_faction_id, siege.defender_faction_id}:
            raise StrategyError("只能处理己方参与的围城战。")
        attacker_faction_id = siege.attacker_faction_id
        defender_faction_id = siege.defender_faction_id
        attacker_ids = list(siege.attacker_army_ids)
        defender_ids = []
        battle_node_id = siege.node_id
        target_city_id = siege.city_id
        battle_trigger = str(siege.battle_trigger)

    armies_by_id = {army.army_id: army for army in next_world.armies}
    attacker_armies = [armies_by_id[army_id] for army_id in attacker_ids if army_id in armies_by_id]
    defender_armies = [armies_by_id[army_id] for army_id in defender_ids if army_id in armies_by_id]
    if not attacker_armies or (source_kind == "encounter" and not defender_armies):
        raise StrategyError("参战军队已经不存在，无法创建战斗。")
    source_city_id = _representative_city_id(next_world, attacker_armies, attacker_faction_id)
    if source_kind == "encounter":
        target_city_id = _representative_city_id(next_world, defender_armies, defender_faction_id)
    target_city = _city(next_world, target_city_id)
    attacker_units = _aggregate_army_units(attacker_armies)
    defender_units = (
        _aggregate_army_units(defender_armies)
        if source_kind == "encounter"
        else dict(target_city.registered_units)
    )
    attacker_troops = sum(army.manpower for army in attacker_armies)
    defender_troops = (
        sum(army.manpower for army in defender_armies)
        if source_kind == "encounter"
        else target_city.resources.troops
    )
    if not attacker_units or attacker_troops <= 0 or (source_kind == "encounter" and (not defender_units or defender_troops <= 0)):
        raise StrategyError("参战军队没有可投入的现役单位。")
    snapshots = {
        army.army_id: _army_snapshot(army)
        for army in [*attacker_armies, *defender_armies]
    }
    attacker_commander_codes = [army.commander_hero_code for army in attacker_armies if army.commander_hero_code]
    defender_commander_codes = [army.commander_hero_code for army in defender_armies if army.commander_hero_code]
    attacker_faction = next(item for item in next_world.factions if item.faction_id == attacker_faction_id)
    defender_faction = next(item for item in next_world.factions if item.faction_id == defender_faction_id)
    attacker_commander_codes = (
        []
        if attacker_faction.is_world_crisis
        else normalize_strategic_hero_deployment(
            next_world,
            attacker_faction_id,
            attacker_commander_codes[:strategic_hero_deployment_limit(next_world, attacker_faction_id)],
        )
    )
    defender_deployment = (
        normalize_strategic_hero_deployment(
            next_world,
            defender_faction_id,
            defender_commander_codes[:strategic_hero_deployment_limit(next_world, defender_faction_id)],
        )
        if source_kind == "encounter" and not defender_faction.is_world_crisis
        else None
    )
    battle = PendingBattle(
        battle_id=f"battle_{next_world.current_month}_{source_kind}_{len(next_world.pending_battles) + 1}",
        month=next_world.current_month,
        attacker_faction_id=attacker_faction_id,
        defender_faction_id=defender_faction_id,
        source_city_id=source_city_id,
        target_city_id=target_city_id,
        resolution_mode=resolution_mode,
        attacker_troops=attacker_troops,
        defender_troops=defender_troops,
        attacker_hero_codes=attacker_commander_codes,
        defender_hero_codes=defender_deployment,
        attacker_registered_units=attacker_units,
        defender_registered_units=defender_units,
        source_kind=source_kind,
        source_entity_id=str(source_entity_id),
        battle_trigger=battle_trigger,
        battle_node_id=battle_node_id,
        attacker_army_ids=attacker_ids,
        defender_army_ids=defender_ids,
        army_snapshots=snapshots,
        report=[f"{source_kind} {source_entity_id} enters {battle_trigger} battle."],
    )
    next_world.pending_battles.append(battle)
    next_world.event_log.append(EventLogEntry(
        month=next_world.current_month,
        category="strategic_battle_declared",
        message=f"战略接战 {source_entity_id} 已选择 {resolution_mode} 处理。",
        related_ids=[battle.battle_id, str(source_entity_id), *attacker_ids, *defender_ids],
    ))
    if auto_resolve and resolution_mode == "quick":
        return resolve_pending_battle(next_world, battle_id=battle.battle_id)
    next_world.validate()
    return next_world


def _grid_unit_count_for_troops(troop_count: int) -> int:
    troops = max(0, int(troop_count))
    if troops <= 0:
        return 0
    return max(1, min(MAX_GRID_UNITS_PER_SIDE, (troops + TROOPS_PER_GRID_UNIT - 1) // TROOPS_PER_GRID_UNIT))


def _registered_unit_count(units: dict[str, int]) -> int:
    return sum(max(0, int(count)) for count in units.values())


def _registered_unit_power(units: dict[str, int]) -> int:
    return sum(REGISTERED_UNIT_TROOP_VALUES.get(unit_type, 100) * max(0, int(count)) for unit_type, count in units.items())


def _clamp_rate(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _composition_combat_power(composition: dict[str, int] | None, troops: int) -> int:
    raw = composition if isinstance(composition, dict) else {}
    quality = 0
    spent = 0
    for unit_type, cost in BATTLE_UNIT_TROOP_COSTS.items():
        count = max(0, int(raw.get(unit_type, 0) or 0))
        quality += count * COMPOSITION_COMBAT_POWER.get(unit_type, cost)
        spent += count * cost
    leftover = max(0, int(troops) - spent)
    return quality + leftover


def _hero_combat_power(codes: list[str] | tuple[str, ...] | set[str] | None) -> int:
    return FORMULA_HERO_POWER * len([code for code in (codes or []) if str(code or "").strip()])


def _hero_skill_combat_power(
    world: WorldState,
    codes: list[str] | tuple[str, ...] | set[str] | None,
    *,
    assignment_type: str,
) -> int:
    from wujiang.strategic.hero_personal import hero_skill_battle_bonus

    bonus = 0
    wanted = {str(code) for code in (codes or []) if str(code or "").strip()}
    if not wanted:
        return 0
    for hero in world.strategic_heroes:
        if hero.hero_code not in wanted:
            continue
        bonus += hero_skill_battle_bonus(hero, assignment_type=assignment_type)
    return bonus


def simulate_formula_city_attack(world: WorldState, battle: PendingBattle) -> dict[str, object]:
    target = _city(world, battle.target_city_id)
    support = owner_support(target)
    attacker_power = (
        _composition_combat_power(getattr(battle, "attacker_composition", None), battle.attacker_troops)
        + _registered_unit_power(battle.attacker_registered_units)
        + _hero_combat_power(battle.attacker_hero_codes)
        + _hero_skill_combat_power(world, battle.attacker_hero_codes, assignment_type="campaign")
    )
    defender_codes = battle.defender_hero_codes
    if defender_codes is None:
        defender_codes = strategic_defender_hero_codes_for_faction(world, battle.defender_faction_id)
    defender_power = (
        _composition_combat_power(getattr(battle, "defender_composition", None), battle.defender_troops)
        + _registered_unit_power(battle.defender_registered_units)
        + target.defense * 80
        + support * 3
        + _hero_combat_power(defender_codes)
        + _hero_skill_combat_power(world, defender_codes, assignment_type="garrison")
    )
    attacker_wins = attacker_power >= defender_power
    ratio = attacker_power / max(1, defender_power)
    if attacker_wins:
        attacker_rate = _clamp_rate(0.20 + 0.40 / max(ratio, 0.55), 0.18, 0.68)
        defender_rate = _clamp_rate(0.55 + 0.25 * ratio, 0.55, 0.97)
    else:
        attacker_rate = _clamp_rate(0.50 + 0.30 / max(ratio, 0.35), 0.48, 0.88)
        defender_rate = _clamp_rate(0.14 + 0.28 * ratio, 0.12, 0.52)
    attacker_losses = min(int(battle.attacker_troops), max(0, round(int(battle.attacker_troops) * attacker_rate)))
    defender_losses = min(int(battle.defender_troops), max(0, round(int(battle.defender_troops) * defender_rate)))
    if attacker_losses <= 0 and battle.attacker_troops > 0:
        attacker_losses = min(int(battle.attacker_troops), 1)
    if defender_losses <= 0 and battle.defender_troops > 0:
        defender_losses = min(int(battle.defender_troops), 1)
    return {
        "attacker_wins": attacker_wins,
        "attacker_power": attacker_power,
        "defender_power": defender_power,
        "attacker_losses": attacker_losses,
        "defender_losses": defender_losses,
        "defender_hero_codes": list(defender_codes or []),
    }


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
    committed_troops: int | None = None,
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
    from wujiang.strategic.vision import city_is_visible

    if not city_is_visible(next_world, faction_id, target.city_id):
        raise StrategyError("尚未探明目标，不能出征。")
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
    if attacker_hero_codes is None:
        selected_attacker_hero_codes = []
    else:
        selected_attacker_hero_codes = normalize_strategic_hero_deployment(
            next_world,
            faction_id,
            attacker_hero_codes,
        )
        remote_heroes = [
            code
            for code in selected_attacker_hero_codes
            if not hero_is_stationed_in_city(next_world, code, source.city_id)
        ]
        if remote_heroes:
            raise StrategyError("只能投入驻扎在出发城的武将。请先把武将调集到出发城。")
        busy_heroes = pending_battle_hero_codes(next_world) & set(selected_attacker_hero_codes)
        if busy_heroes:
            raise StrategyError("这些武将已参加其他出征，不能同时出现在两场进攻里。")

    attacker_registered_units = _commit_registered_units(attacker_office.unit_inventory) if attacker_office else {}
    defender_registered_units = _commit_registered_units(target.registered_units)
    if (
        committed_troops in {None, ""}
        and source.resources.troops < MIN_ATTACK_TROOPS
        and available_registered_power >= MIN_ATTACK_TROOPS
    ):
        attacker_troops = max(0, int(source.resources.troops))
    else:
        attacker_troops = normalize_committed_attack_troops(committed_troops, source.resources.troops)
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
        attacker_composition=auto_battle_composition(attacker_troops, city=source),
        defender_composition=auto_battle_composition(defender_troops, city=target),
        report=[f"{source.name}派出 {attacker_troops} 兵力进攻{target.name}。"],
    )
    next_world.pending_battles.append(battle)
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="battle_declared",
            message=f"{source.name}进攻{target.name}，处理方式：{_mode_label(resolution_mode)}。",
            related_ids=[battle.battle_id, source_city_id, target_city_id],
        )
    )
    if auto_resolve and resolution_mode in {"quick", "formula"}:
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
    source = _maybe_city(next_world, battle.source_city_id)
    target = _maybe_city(next_world, battle.target_city_id)
    battle_label = (
        f"{source.name}进攻{target.name}"
        if source is not None and target is not None
        else "这场战斗"
    )
    battle.report.append(f"已创建真实战场，房间 {battle.battle_room_id}。")
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="battle_room_created",
            message=f"{battle_label}已创建真实战场，房间 {battle.battle_room_id}。",
            related_ids=[battle.battle_id, battle.battle_room_id],
        )
    )
    from wujiang.strategic.objectives import record_strategic_status_events

    next_world = record_strategic_status_events(next_world)
    next_world.validate()
    return next_world


def retreat_pending_battle(world: WorldState, *, battle_id: str, faction_id: str) -> WorldState:
    next_world = _clone_world(world)
    battle = next((item for item in next_world.pending_battles if item.battle_id == battle_id), None)
    if battle is None or battle.status != "pending":
        raise StrategyError("没有可撤退的待处理战斗。")
    if faction_id != battle.attacker_faction_id:
        raise StrategyError("只有攻方可以下令撤退。")
    source = _city(next_world, battle.source_city_id)
    returned = (battle.attacker_troops * 7) // 10
    lost = max(0, battle.attacker_troops - returned)
    source.resources.troops += returned
    battle.status = "resolved"
    battle.resolution_mode = "retreat"
    battle.winner_faction_id = None
    battle.battle_result = {"outcome": "retreat", "troops_returned": returned, "troops_lost": lost}
    battle.report.append(f"攻方撤退，收回 {returned} 兵力，折损 {lost}。")
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="battle_retreated",
            message=f"{source.name}撤出对{_city(next_world, battle.target_city_id).name}的进攻，收回 {returned}、折损 {lost}。",
            related_ids=[battle.battle_id, source.city_id, battle.target_city_id],
        )
    )
    next_world.validate()
    return next_world


def convert_pending_battle_to_siege(world: WorldState, *, battle_id: str, faction_id: str) -> WorldState:
    next_world = _clone_world(world)
    battle = next((item for item in next_world.pending_battles if item.battle_id == battle_id), None)
    if battle is None or battle.status != "pending":
        raise StrategyError("没有可转入围城的待处理战斗。")
    if faction_id != battle.attacker_faction_id:
        raise StrategyError("只有攻方可以转入围城。")
    if battle.source_kind not in {"legacy_city_attack", ""}:
        raise StrategyError("只有攻城战可以转入围城。")
    target = _city(next_world, battle.target_city_id)
    source = _city(next_world, battle.source_city_id)
    if any(item.status in {"active", "contested", "breached", "battle_pending"} and item.city_id == target.city_id for item in next_world.sieges):
        raise StrategyError("该城已经处于围城之中。")
    general = next(
        (
            office
            for office in next_world.offices
            if office.faction_id == battle.attacker_faction_id
            and office.office_type == "general"
            and office.status == "active"
            and not any(
                army.commander_office_id == office.office_id and army.status not in {"disbanded", "destroyed"}
                for army in next_world.armies
            )
        ),
        None,
    )
    if general is None:
        raise StrategyError("没有空闲将军接管围城。请先任命将军，或选择交战。")
    commander = str(general.holder_id or (battle.attacker_hero_codes or [""])[0] or "")
    army = StrategicArmy(
        army_id=f"army:siege:{battle.battle_id}",
        faction_id=battle.attacker_faction_id,
        commander_office_id=general.office_id,
        commander_hero_code=commander,
        location_node_id=target.node_id,
        home_city_id=source.city_id,
        unit_inventory={"infantry": max(1, battle.attacker_troops // 100)},
        manpower=max(1, battle.attacker_troops),
        supply=max(50, battle.attacker_troops // 2),
        supply_capacity=max(80, battle.attacker_troops),
        status="besieging",
        current_order="besiege",
        created_month=next_world.current_month,
    )
    next_world.armies.append(army)
    fortification = max(20, target.defense * 10)
    siege = StrategicSiege(
        siege_id=f"siege:{target.city_id}:{next_world.current_month}:{len(next_world.sieges) + 1}",
        city_id=target.city_id,
        node_id=target.node_id,
        attacker_faction_id=battle.attacker_faction_id,
        defender_faction_id=battle.defender_faction_id,
        attacker_army_ids=[army.army_id],
        started_month=next_world.current_month,
        fortification_initial=fortification,
        fortification_remaining=fortification,
        attacker_stance="blockade",
    )
    next_world.sieges.append(siege)
    battle.status = "resolved"
    battle.resolution_mode = "siege"
    battle.battle_result = {"outcome": "siege", "siege_id": siege.siege_id}
    battle.report.append(f"转入围城，围困{target.name}。")
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="strategy_siege_started",
            message=f"{source.name}对{target.name}转入围城，不再强攻。",
            related_ids=[siege.siege_id, battle.battle_id, target.city_id],
        )
    )
    next_world.validate()
    return next_world


def set_pending_battle_composition(
    world: WorldState,
    *,
    battle_id: str,
    composition: dict[str, object] | None,
) -> WorldState:
    next_world = _clone_world(world)
    battle = next((item for item in next_world.pending_battles if item.battle_id == battle_id), None)
    if battle is None or battle.status != "pending":
        raise StrategyError("没有可配置的待处理战斗。")
    battle.attacker_composition = normalize_battle_composition(composition, battle.attacker_troops)
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
        battle.report.append(f"守方投入武将：{'、'.join(defender_hero_codes)}。")
    else:
        battle.report.append("守方未投入武将。")
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="battle_defender_hero_set",
            message=f"{_faction_label(next_world, faction_id)}调整了守城武将。",
            related_ids=[battle.battle_id, faction_id, *defender_hero_codes],
        )
    )
    next_world.validate()
    return next_world


def _army_by_id(world: WorldState, army_id: str) -> StrategicArmy | None:
    return next((army for army in world.armies if army.army_id == str(army_id)), None)


def _set_army_destroyed(army: StrategicArmy) -> None:
    army.unit_inventory = {}
    army.manpower = 0
    army.supply = 0
    army.status = "destroyed"
    army.current_order = "hold"
    army.target_army_id = None
    army.target_encounter_id = None
    army.retreat_destination_node_id = None


def _set_stationary_after_battle(world: WorldState, army: StrategicArmy) -> None:
    owned_city = next(
        (
            city for city in world.cities
            if city.node_id == army.location_node_id and city.owner_faction_id == army.faction_id
        ),
        None,
    )
    army.status = "garrisoned" if owned_city is not None else "deployed"
    army.current_order = "hold"
    army.target_army_id = None
    army.target_encounter_id = None
    army.retreat_destination_node_id = None
    army.march_origin_node_id = army.location_node_id
    army.destination_node_id = army.location_node_id
    army.route_node_ids = [army.location_node_id]
    army.route_progress_index = 0
    army.departure_month = world.current_month
    army.estimated_arrival_month = world.current_month
    from wujiang.strategic.armies import refresh_army_supply_intel

    refresh_army_supply_intel(world, army)


def _retreat_after_battle(world: WorldState, army: StrategicArmy, enemy_faction_id: str) -> str | None:
    node = next(item for item in world.nodes if item.node_id == army.location_node_id)
    candidates: list[tuple[int, str]] = []
    for destination_id in node.connected_node_ids:
        hostile_present = any(
            other.army_id != army.army_id
            and other.status not in {"disbanded", "destroyed"}
            and other.location_node_id == destination_id
            and other.faction_id == enemy_faction_id
            for other in world.armies
        )
        if hostile_present:
            continue
        owned_city = any(
            city.node_id == destination_id and city.owner_faction_id == army.faction_id
            for city in world.cities
        )
        on_supply_line = destination_id in army.supply_line_node_ids
        candidates.append((0 if owned_city else 1 if on_supply_line else 2, destination_id))
    if not candidates:
        _set_army_destroyed(army)
        return None
    destination_id = min(candidates)[1]
    army.location_node_id = destination_id
    _set_stationary_after_battle(world, army)
    return destination_id


def _apply_army_survival(
    army: StrategicArmy,
    snapshot: dict[str, object],
    *,
    survival_rate: float,
    won: bool,
) -> None:
    initial_units = {
        str(unit_type): max(0, int(count))
        for unit_type, count in dict(snapshot.get("unit_inventory") or {}).items()
    }
    remaining_units = {
        unit_type: min(count, max(0, round(count * survival_rate)))
        for unit_type, count in initial_units.items()
    }
    remaining_units = {unit_type: count for unit_type, count in remaining_units.items() if count > 0}
    initial_manpower = max(0, int(snapshot.get("manpower") or 0))
    remaining_manpower = min(initial_manpower, max(0, round(initial_manpower * survival_rate)))
    if survival_rate > 0 and initial_units and not remaining_units:
        unit_type = max(initial_units, key=lambda key: (initial_units[key], key))
        remaining_units[unit_type] = 1
        remaining_manpower = max(1, remaining_manpower)
    army.unit_inventory = remaining_units
    army.manpower = remaining_manpower
    initial_supply = max(0, int(snapshot.get("supply") or 0))
    supply_loss = min(initial_supply, max(5, (initial_manpower + 99) // 100))
    army.supply = max(0, initial_supply - supply_loss)
    initial_morale = max(0, min(100, int(snapshot.get("morale") or 0)))
    army.morale = max(0, min(100, initial_morale + (5 if won else -15)))
    if not army.unit_inventory or army.manpower <= 0:
        _set_army_destroyed(army)


def _apply_strategic_army_outcome(
    next_world: WorldState,
    battle: PendingBattle,
    *,
    attacker_wins: bool,
    surviving_grid_units_by_team: dict[int, int],
    surviving_hero_codes_by_team: dict[int, set[str] | list[str] | tuple[str, ...]] | None,
    resolution_source: str,
    preface: str = "",
) -> WorldState:
    target = _city(next_world, battle.target_city_id)
    attacker_initial_grid = max(1, min(MAX_GRID_UNITS_PER_SIDE, _registered_unit_count(battle.attacker_registered_units)))
    defender_initial_grid = max(
        1,
        min(
            MAX_GRID_UNITS_PER_SIDE,
            _registered_unit_count(battle.defender_registered_units)
            + (0 if battle.source_kind == "encounter" else _grid_unit_count_for_troops(battle.defender_troops)),
        ),
    )
    attacker_grid_survivors = max(0, min(attacker_initial_grid, int(surviving_grid_units_by_team.get(1, 0))))
    defender_grid_survivors = max(0, min(defender_initial_grid, int(surviving_grid_units_by_team.get(2, 0))))
    attacker_rate = attacker_grid_survivors / attacker_initial_grid
    defender_rate = defender_grid_survivors / defender_initial_grid
    if preface:
        battle.report.append(preface)

    for army_id in battle.attacker_army_ids:
        army = _army_by_id(next_world, army_id)
        snapshot = battle.army_snapshots.get(army_id, {})
        if army is not None:
            _apply_army_survival(army, snapshot, survival_rate=attacker_rate, won=attacker_wins)
    for army_id in battle.defender_army_ids:
        army = _army_by_id(next_world, army_id)
        snapshot = battle.army_snapshots.get(army_id, {})
        if army is not None:
            _apply_army_survival(army, snapshot, survival_rate=defender_rate, won=not attacker_wins)

    retreat_destinations: dict[str, str | None] = {}
    city_captured = False
    captured_previous_owner: str | None = None
    if battle.source_kind == "encounter":
        encounter = next(item for item in next_world.encounters if item.encounter_id == battle.source_entity_id)
        encounter.status = "ended"
        encounter.ended_month = next_world.current_month
        encounter.outcome = "battle_resolved"
        winner_ids = battle.attacker_army_ids if attacker_wins else battle.defender_army_ids
        loser_ids = battle.defender_army_ids if attacker_wins else battle.attacker_army_ids
        loser_enemy = battle.attacker_faction_id if attacker_wins else battle.defender_faction_id
        for army_id in winner_ids:
            army = _army_by_id(next_world, army_id)
            if army is not None and army.status != "destroyed":
                _set_stationary_after_battle(next_world, army)
        for army_id in loser_ids:
            army = _army_by_id(next_world, army_id)
            if army is not None and army.status != "destroyed":
                retreat_destinations[army_id] = _retreat_after_battle(next_world, army, loser_enemy)
    else:
        siege = next(item for item in next_world.sieges if item.siege_id == battle.source_entity_id)
        trigger = str(battle.battle_trigger or "assault")
        target.resources.troops = max(0, round(battle.defender_troops * defender_rate))
        target.registered_units = _surviving_registered_units(
            battle.defender_registered_units,
            round(_registered_unit_count(battle.defender_registered_units) * defender_rate),
        )
        if trigger == "assault" and attacker_wins:
            previous_owner = target.owner_faction_id
            captured_previous_owner = previous_owner
            target.owner_faction_id = battle.attacker_faction_id
            target.resources.troops = 0
            target.registered_units = {}
            target.support_by_faction[battle.attacker_faction_id] = clamp(
                target.support_by_faction.get(battle.attacker_faction_id, 35) + 12, 0, 100
            )
            target.support_by_faction[battle.defender_faction_id] = clamp(
                target.support_by_faction.get(battle.defender_faction_id, 50) - 18, 0, 100
            )
            from wujiang.strategic.occupation import mark_city_captured

            mark_city_captured(
                next_world,
                city_id=target.city_id,
                previous_owner_faction_id=previous_owner,
                occupier_faction_id=battle.attacker_faction_id,
            )
            siege.status = "ended"
            siege.ended_month = next_world.current_month
            siege.outcome = "captured"
            siege.battle_trigger = None
            city_captured = True
            for army_id in battle.attacker_army_ids:
                army = _army_by_id(next_world, army_id)
                if army is not None and army.status != "destroyed":
                    _set_stationary_after_battle(next_world, army)
        elif trigger == "breakout" and attacker_wins:
            siege.status = "breached"
            siege.fortification_remaining = 0
            siege.battle_trigger = "assault"
            siege.defender_stance = "hold"
            for army_id in battle.attacker_army_ids:
                army = _army_by_id(next_world, army_id)
                if army is not None and army.status != "destroyed":
                    army.status = "besieging"
                    army.current_order = "besiege"
        else:
            siege.status = "ended"
            siege.ended_month = next_world.current_month
            siege.outcome = "breakout_succeeded" if trigger == "breakout" else "repelled"
            siege.battle_trigger = None
            for army_id in battle.attacker_army_ids:
                army = _army_by_id(next_world, army_id)
                if army is not None and army.status != "destroyed":
                    retreat_destinations[army_id] = _retreat_after_battle(
                        next_world, army, battle.defender_faction_id
                    )

    attacker_remaining = max(0, round(battle.attacker_troops * attacker_rate))
    defender_remaining = max(0, round(battle.defender_troops * defender_rate))
    battle.winner_faction_id = battle.attacker_faction_id if attacker_wins else battle.defender_faction_id
    battle.report.append(
        f"战况回写：攻方剩余 {attacker_remaining}/{battle.attacker_troops}，"
        f"守方剩余 {defender_remaining}/{battle.defender_troops}。"
    )
    battle.battle_result = _battle_result_payload(
        battle,
        winner_side="attacker" if attacker_wins else "defender",
        city_captured=city_captured,
        attacker_losses=battle.attacker_troops - attacker_remaining,
        defender_losses=battle.defender_troops - defender_remaining,
        attacker_remaining=attacker_remaining,
        defender_remaining=defender_remaining,
        attacker_initial_grid_units=attacker_initial_grid,
        defender_initial_grid_units=defender_initial_grid,
        surviving_grid_units_by_team={1: attacker_grid_survivors, 2: defender_grid_survivors},
        report_summary=" ".join(battle.report[-3:]),
    )
    battle.battle_result["resolution_source"] = resolution_source
    battle.battle_result["source_kind"] = battle.source_kind
    battle.battle_result["source_entity_id"] = battle.source_entity_id
    battle.battle_result["battle_trigger"] = battle.battle_trigger
    battle.battle_result["retreat_destinations"] = retreat_destinations
    battle.battle_result["army_ids_by_side"] = {
        "attacker": list(battle.attacker_army_ids),
        "defender": list(battle.defender_army_ids),
    }
    next_world, hero_result = record_strategic_hero_battle_losses(
        next_world,
        attacker_faction_id=battle.attacker_faction_id,
        defender_faction_id=battle.defender_faction_id,
        surviving_hero_codes_by_team=surviving_hero_codes_by_team,
        committed_hero_codes_by_team={1: battle.attacker_hero_codes, 2: battle.defender_hero_codes},
    )
    battle = next(item for item in next_world.pending_battles if item.battle_id == battle.battle_id)
    battle.battle_result["strategic_heroes_by_side"] = hero_result
    if city_captured and captured_previous_owner is not None:
        from wujiang.strategic.relics import apply_city_control_change_consequences

        next_world, control_change = apply_city_control_change_consequences(
            next_world,
            city_id=target.city_id,
            previous_faction_id=captured_previous_owner,
            new_faction_id=battle.attacker_faction_id,
            cause=f"strategic_siege_{battle.battle_trigger or 'assault'}",
        )
        battle = next(item for item in next_world.pending_battles if item.battle_id == battle.battle_id)
        battle.battle_result["city_control_change"] = control_change
    if city_captured:
        station_heroes_in_city(
            next_world,
            hero_codes=_attacker_heroes_to_station(hero_result, battle.attacker_hero_codes),
            city_id=battle.target_city_id,
        )
    battle.status = "resolved"
    next_world.event_log.append(EventLogEntry(
        month=next_world.current_month,
        category="strategic_battle_resolved",
        message=f"战略接战 {battle.source_entity_id} 已结算，胜方为 {battle.winner_faction_id}。",
        related_ids=[battle.battle_id, str(battle.source_entity_id), battle.winner_faction_id],
    ))
    next_world.validate()
    return next_world


def _apply_world_crisis_outcome(
    next_world: WorldState,
    battle: PendingBattle,
    *,
    attacker_wins: bool,
    surviving_grid_units_by_team: dict[int, int],
    surviving_hero_codes_by_team: dict[int, set[str] | list[str] | tuple[str, ...]] | None,
    resolution_source: str,
    preface: str = "",
) -> WorldState:
    attacker_initial = max(
        1,
        min(MAX_GRID_UNITS_PER_SIDE, _registered_unit_count(battle.attacker_registered_units)),
    )
    defender_initial = max(
        1,
        min(MAX_GRID_UNITS_PER_SIDE, _registered_unit_count(battle.defender_registered_units)),
    )
    attacker_survivors = max(
        0, min(attacker_initial, int(surviving_grid_units_by_team.get(1, 0)))
    )
    defender_survivors = max(
        0, min(defender_initial, int(surviving_grid_units_by_team.get(2, 0)))
    )
    attacker_remaining = _remaining_troops_from_grid_units(
        battle.attacker_troops,
        initial_grid_units=attacker_initial,
        surviving_grid_units=attacker_survivors,
    )
    defender_remaining = _remaining_troops_from_grid_units(
        battle.defender_troops,
        initial_grid_units=defender_initial,
        surviving_grid_units=defender_survivors,
    )
    if preface:
        battle.report.append(preface)
    battle.winner_faction_id = (
        battle.attacker_faction_id if attacker_wins else battle.defender_faction_id
    )
    battle.report.append(
        f"北境决战回写：攻方存活 {attacker_survivors}/{attacker_initial}，"
        f"守方存活 {defender_survivors}/{defender_initial}；城市归属不变。"
    )
    battle.battle_result = _battle_result_payload(
        battle,
        winner_side="attacker" if attacker_wins else "defender",
        city_captured=False,
        attacker_losses=battle.attacker_troops - attacker_remaining,
        defender_losses=battle.defender_troops - defender_remaining,
        attacker_remaining=attacker_remaining,
        defender_remaining=defender_remaining,
        attacker_initial_grid_units=attacker_initial,
        defender_initial_grid_units=defender_initial,
        surviving_grid_units_by_team={
            1: attacker_survivors,
            2: defender_survivors,
        },
        report_summary=" ".join(battle.report[-3:]),
    )
    battle.battle_result.update(
        {
            "resolution_source": resolution_source,
            "source_kind": "world_crisis",
            "source_entity_id": battle.source_entity_id,
            "battle_trigger": battle.battle_trigger,
        }
    )
    next_world, hero_result = record_strategic_hero_battle_losses(
        next_world,
        attacker_faction_id=battle.attacker_faction_id,
        defender_faction_id=battle.defender_faction_id,
        surviving_hero_codes_by_team=surviving_hero_codes_by_team,
        committed_hero_codes_by_team={
            1: battle.attacker_hero_codes,
            2: battle.defender_hero_codes,
        },
    )
    battle = next(
        item for item in next_world.pending_battles if item.battle_id == battle.battle_id
    )
    battle.battle_result["strategic_heroes_by_side"] = hero_result
    battle.status = "resolved"
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="world_crisis_battle_resolved",
            message=(
                f"北境决战已结算，胜方为 {battle.winner_faction_id}；"
                "本次结算不直接改变任何城市归属。"
            ),
            related_ids=[
                battle.battle_id,
                str(battle.source_entity_id),
                battle.winner_faction_id,
            ],
        )
    )
    from wujiang.strategic.world_crisis import resolve_world_crisis_showdown_outcome

    resolved_world = resolve_world_crisis_showdown_outcome(
        next_world,
        crisis_id=str(battle.source_entity_id),
        attacker_wins=attacker_wins,
    )
    from wujiang.strategic.objectives import record_strategic_status_events

    return record_strategic_status_events(resolved_world)


def _apply_battle_outcome(
    next_world: WorldState,
    battle: PendingBattle,
    *,
    attacker_wins: bool,
    preface: str = "",
    surviving_grid_units_by_team: dict[int, int] | None = None,
    surviving_hero_codes_by_team: dict[int, set[str] | list[str] | tuple[str, ...]] | None = None,
    formula_outcome: dict[str, object] | None = None,
) -> WorldState:
    target = _city(next_world, battle.target_city_id)
    source = _city(next_world, battle.source_city_id)
    support = owner_support(target)
    defender_score = (
        _composition_combat_power(getattr(battle, "defender_composition", None), battle.defender_troops)
        + _registered_unit_power(battle.defender_registered_units)
        + target.defense * 80
        + support * 3
    )
    if preface:
        battle.report.append(preface)
    attacker_initial_grid_units = _side_initial_grid_units(battle, "attacker")
    defender_initial_grid_units = _side_initial_grid_units(battle, "defender")
    attacker_remaining_from_room: int | None = None
    defender_remaining_from_room: int | None = None
    control_change: dict | None = None
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
            "真实战场存活："
            f"攻方 {surviving_grid_units_by_team.get(1, 0)}/{attacker_initial_grid_units}，"
            f"守方 {surviving_grid_units_by_team.get(2, 0)}/{defender_initial_grid_units}。"
        )
    if attacker_wins:
        previous_owner_faction_id = target.owner_faction_id
        if attacker_remaining_from_room is None:
            if formula_outcome:
                attacker_losses = min(battle.attacker_troops, max(0, int(formula_outcome.get("attacker_losses") or 0)))
                defender_losses = min(target.resources.troops, max(0, int(formula_outcome.get("defender_losses") or 0)))
            else:
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
        from wujiang.strategic.occupation import mark_city_captured

        mark_city_captured(
            next_world,
            city_id=target.city_id,
            previous_owner_faction_id=previous_owner_faction_id,
            occupier_faction_id=battle.attacker_faction_id,
        )
        battle.winner_faction_id = battle.attacker_faction_id
        battle.report.extend(
            [
                f"攻方胜利，攻方损失 {attacker_losses}，守方损失 {defender_losses}。",
                f"{target.name}易主，占领军 {survivors}。",
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
        from wujiang.strategic.relics import apply_city_control_change_consequences

        next_world, control_change = apply_city_control_change_consequences(
            next_world,
            city_id=battle.target_city_id,
            previous_faction_id=battle.defender_faction_id,
            new_faction_id=battle.attacker_faction_id,
            cause="legacy_city_battle",
        )
        battle = next(item for item in next_world.pending_battles if item.battle_id == battle.battle_id)
        source = _city(next_world, battle.source_city_id)
        target = _city(next_world, battle.target_city_id)
        if battle.battle_result is not None:
            battle.battle_result["city_control_change"] = control_change
    else:
        if attacker_remaining_from_room is None:
            if formula_outcome:
                attacker_losses = min(battle.attacker_troops, max(0, int(formula_outcome.get("attacker_losses") or 0)))
                defender_losses = min(target.resources.troops, max(0, int(formula_outcome.get("defender_losses") or 0)))
            else:
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
                f"守方胜利，攻方损失 {attacker_losses}，守方损失 {defender_losses}。",
                f"{source.name}收容溃军 {attacker_survivors}。",
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
    if attacker_wins:
        station_heroes_in_city(
            next_world,
            hero_codes=_attacker_heroes_to_station(strategic_heroes_by_side, battle.attacker_hero_codes),
            city_id=target.city_id,
        )
    if battle.battle_result is not None:
        battle.battle_result["strategic_heroes_by_side"] = strategic_heroes_by_side
        battle.battle_result["registered_units_by_side"] = {
            "attacker_initial": dict(battle.attacker_registered_units),
            "defender_initial": dict(battle.defender_registered_units),
            "attacker_surviving": attacker_registered_survivors,
            "defender_surviving": defender_registered_survivors,
        }
    battle.status = "resolved"
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="battle_resolved",
            message=(
                f"{target.name}的战斗已结算，胜方为{_faction_label(next_world, battle.winner_faction_id)}，"
                f"处理方式：{_mode_label(battle.resolution_mode)}。"
            ),
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

    if battle.source_kind == "world_crisis":
        attacker_score = (
            battle.attacker_troops
            + _registered_unit_power(battle.attacker_registered_units)
        )
        defender_score = (
            battle.defender_troops
            + _registered_unit_power(battle.defender_registered_units)
        )
        attacker_wins = attacker_score >= defender_score
        attacker_initial = max(
            1,
            min(MAX_GRID_UNITS_PER_SIDE, _registered_unit_count(battle.attacker_registered_units)),
        )
        defender_initial = max(
            1,
            min(MAX_GRID_UNITS_PER_SIDE, _registered_unit_count(battle.defender_registered_units)),
        )
        survivors = {
            1: max(1, round(attacker_initial * (0.60 if attacker_wins else 0.25))),
            2: max(1, round(defender_initial * (0.25 if attacker_wins else 0.60))),
        }
        hero_survivors = {
            1: set(battle.attacker_hero_codes or []) if attacker_wins else set(),
            2: set(),
        }
        return _apply_world_crisis_outcome(
            next_world,
            battle,
            attacker_wins=attacker_wins,
            surviving_grid_units_by_team=survivors,
            surviving_hero_codes_by_team=hero_survivors,
            resolution_source="quick",
        )

    if battle.source_kind in {"encounter", "siege"}:
        target = _city(next_world, battle.target_city_id)

        def side_score(army_ids: list[str], troops: int) -> int:
            snapshots = [battle.army_snapshots.get(army_id, {}) for army_id in army_ids]
            if not snapshots:
                return max(0, int(troops))
            average_morale = sum(int(row.get("morale") or 0) for row in snapshots) / len(snapshots)
            supply_rates = [
                int(row.get("supply") or 0) / max(1, int(row.get("supply_capacity") or 0))
                for row in snapshots
            ]
            average_supply = sum(supply_rates) / len(supply_rates)
            return round(max(0, int(troops)) * (0.70 + average_morale / 200 + average_supply * 0.30))

        attacker_score = side_score(battle.attacker_army_ids, battle.attacker_troops)
        defender_score = side_score(battle.defender_army_ids, battle.defender_troops)
        if battle.source_kind == "siege":
            siege = next(item for item in next_world.sieges if item.siege_id == battle.source_entity_id)
            defender_score += siege.fortification_remaining * 10 + owner_support(target) * 3
        attacker_wins = attacker_score >= defender_score
        attacker_initial = max(1, min(MAX_GRID_UNITS_PER_SIDE, _registered_unit_count(battle.attacker_registered_units)))
        defender_initial = max(
            1,
            min(
                MAX_GRID_UNITS_PER_SIDE,
                _registered_unit_count(battle.defender_registered_units)
                + (0 if battle.source_kind == "encounter" else _grid_unit_count_for_troops(battle.defender_troops)),
            ),
        )
        survivors = {
            1: max(1, round(attacker_initial * (0.60 if attacker_wins else 0.25))),
            2: max(1, round(defender_initial * (0.25 if attacker_wins else 0.60))),
        }
        hero_survivors = {
            1: set(battle.attacker_hero_codes or []) if attacker_wins else set(),
            2: (
                set(
                    strategic_defender_hero_codes_for_faction(next_world, battle.defender_faction_id)
                    if battle.defender_hero_codes is None
                    else battle.defender_hero_codes
                )
                if not attacker_wins
                else set()
            ),
        }
        return _apply_strategic_army_outcome(
            next_world,
            battle,
            attacker_wins=attacker_wins,
            surviving_grid_units_by_team=survivors,
            surviving_hero_codes_by_team=hero_survivors,
            resolution_source="quick",
        )

    if battle.resolution_mode == "formula":
        outcome = simulate_formula_city_attack(next_world, battle)
        defender_codes = [str(code) for code in (outcome.get("defender_hero_codes") or [])]
        hero_survivors = {
            1: set(battle.attacker_hero_codes or []) if outcome["attacker_wins"] else set(),
            2: set(defender_codes) if not outcome["attacker_wins"] else set(),
        }
        resolved = _apply_battle_outcome(
            next_world,
            battle,
            attacker_wins=bool(outcome["attacker_wins"]),
            preface=(
                f"快速结算：攻方战力 {int(outcome['attacker_power'])}，"
                f"守方战力 {int(outcome['defender_power'])}。"
            ),
            surviving_hero_codes_by_team=hero_survivors,
            formula_outcome=outcome,
        )
        settled = next(item for item in resolved.pending_battles if item.battle_id == battle.battle_id)
        if settled.battle_result is not None:
            settled.battle_result["resolution_source"] = "formula"
        return resolved

    target = _city(next_world, battle.target_city_id)
    support = owner_support(target)
    attacker_score = (
        _composition_combat_power(getattr(battle, "attacker_composition", None), battle.attacker_troops)
        + _registered_unit_power(battle.attacker_registered_units)
        + _hero_combat_power(battle.attacker_hero_codes)
        + _hero_skill_combat_power(next_world, battle.attacker_hero_codes, assignment_type="campaign")
    )
    defender_codes = battle.defender_hero_codes
    if defender_codes is None:
        defender_codes = strategic_defender_hero_codes_for_faction(next_world, battle.defender_faction_id)
    defender_score = (
        _composition_combat_power(getattr(battle, "defender_composition", None), battle.defender_troops)
        + _registered_unit_power(battle.defender_registered_units)
        + target.defense * 80
        + support * 3
        + _hero_combat_power(defender_codes)
        + _hero_skill_combat_power(next_world, defender_codes, assignment_type="garrison")
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
    side_name = "攻方" if attacker_wins else "守方"
    detail = f"真实战场 {room_id} 已结束，胜方为{side_name}。"
    if battle_summary:
        detail = f"{detail} {str(battle_summary).strip()}"
    if battle.source_kind == "world_crisis":
        return _apply_world_crisis_outcome(
            next_world,
            battle,
            attacker_wins=attacker_wins,
            preface=detail,
            surviving_grid_units_by_team=surviving_grid_units_by_team or {1: 0, 2: 0},
            surviving_hero_codes_by_team=surviving_hero_codes_by_team,
            resolution_source="real_grid",
        )
    if battle.source_kind in {"encounter", "siege"}:
        return _apply_strategic_army_outcome(
            next_world,
            battle,
            attacker_wins=attacker_wins,
            preface=detail,
            surviving_grid_units_by_team=surviving_grid_units_by_team or {1: 0, 2: 0},
            surviving_hero_codes_by_team=surviving_hero_codes_by_team,
            resolution_source="real_grid",
        )
    return _apply_battle_outcome(
        next_world,
        battle,
        attacker_wins=attacker_wins,
        preface=detail,
        surviving_grid_units_by_team=surviving_grid_units_by_team,
        surviving_hero_codes_by_team=surviving_hero_codes_by_team,
    )
