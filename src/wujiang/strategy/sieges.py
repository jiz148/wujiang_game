from __future__ import annotations

import copy
import math

from wujiang.strategy.armies import _armies_hostile, refresh_army_supply_intel
from wujiang.strategy.models import EventLogEntry, StrategicArmy, StrategicSiege, StrategyError, WorldState
from wujiang.strategy.occupation import mark_city_captured


ACTIVE_SIEGE_STATUSES = {"active", "contested", "breached", "battle_pending"}
ATTACKER_STANCES = {"blockade", "starve", "assault", "withdraw"}
DEFENDER_STANCES = {"hold", "breakout", "await_relief", "surrender"}


def _clone_world(world: WorldState) -> WorldState:
    return WorldState.from_dict(copy.deepcopy(world.to_dict()))


def _siege(world: WorldState, siege_id: str) -> StrategicSiege:
    siege = next(
        (item for item in world.sieges if item.siege_id == str(siege_id) and item.status in ACTIVE_SIEGE_STATUSES),
        None,
    )
    if siege is None:
        raise StrategyError("围城不存在或已经结束。")
    return siege


def _active_general(world: WorldState, faction_id: str, office_id: str):
    office = next((item for item in world.offices if item.office_id == str(office_id)), None)
    if (
        office is None
        or office.faction_id != faction_id
        or office.office_type != "general"
        or office.status != "active"
        or not office.holder_id
    ):
        raise StrategyError("只有参围军队的在职将军可以调整围城方针。")
    return office


def _active_governor(world: WorldState, faction_id: str, office_id: str, city_id: str):
    office = next((item for item in world.offices if item.office_id == str(office_id)), None)
    if (
        office is None
        or office.faction_id != faction_id
        or office.office_type != "governor"
        or office.status != "active"
        or not office.holder_id
        or city_id not in office.managed_entity_ids
    ):
        raise StrategyError("只有管理被围城市的在职城主可以调整守城方针。")
    return office


def _army(world: WorldState, army_id: str) -> StrategicArmy | None:
    return next((item for item in world.armies if item.army_id == str(army_id)), None)


def _factions_hostile(world: WorldState, first_id: str, second_id: str) -> bool:
    if first_id == second_id:
        return False
    factions = {item.faction_id: item for item in world.factions}
    first = factions[first_id]
    second = factions[second_id]
    if first.is_world_crisis or second.is_world_crisis:
        return True
    if first.is_neutral_city_state == second.is_neutral_city_state:
        return True
    major_id = second_id if first.is_neutral_city_state else first_id
    neutral_id = first_id if first.is_neutral_city_state else second_id
    return not any(
        agreement.status == "active"
        and agreement.agreement_type == "non_aggression"
        and agreement.major_faction_id == major_id
        and agreement.neutral_faction_id == neutral_id
        for agreement in world.diplomatic_agreements
    )


def _safe_withdrawal_destination(world: WorldState, army: StrategicArmy, destination_node_id: str) -> str:
    destination_id = str(destination_node_id or "")
    node = next(item for item in world.nodes if item.node_id == army.location_node_id)
    if destination_id not in node.connected_node_ids:
        raise StrategyError("撤围目的地必须是围城节点的相邻节点。")
    if any(
        other.army_id != army.army_id
        and other.status not in {"disbanded", "destroyed"}
        and other.location_node_id == destination_id
        and _armies_hostile(world, army, other)
        for other in world.armies
    ):
        raise StrategyError("撤围目的地已有敌军，不能作为安全退路。")
    return destination_id


def order_siege_attacker_stance(
    world: WorldState,
    *,
    faction_id: str,
    siege_id: str,
    stance: str,
    issuer_office_id: str,
    destination_node_id: str = "",
) -> WorldState:
    next_world = _clone_world(world)
    siege = _siege(next_world, siege_id)
    general = _active_general(next_world, faction_id, issuer_office_id)
    if siege.attacker_faction_id != faction_id:
        raise StrategyError("只能调整己方参与的围城。")
    commanded = next(
        (
            _army(next_world, army_id)
            for army_id in siege.attacker_army_ids
            if (_army(next_world, army_id) is not None)
            and _army(next_world, army_id).commander_office_id == general.office_id
        ),
        None,
    )
    if commanded is None:
        raise StrategyError("该将军没有指挥参围军队。")
    chosen = str(stance or "")
    if chosen not in ATTACKER_STANCES:
        raise StrategyError("围城方针无效。")
    siege.attacker_stance = chosen
    related_ids = [siege.siege_id, siege.city_id, faction_id, general.office_id]
    if chosen == "withdraw":
        destination_id = _safe_withdrawal_destination(next_world, commanded, destination_node_id)
        for army_id in siege.attacker_army_ids:
            army = _army(next_world, army_id)
            if army is None or army.status in {"disbanded", "destroyed"}:
                continue
            _safe_withdrawal_destination(next_world, army, destination_id)
            army.status = "retreating"
            army.current_order = "retreat"
            army.target_army_id = None
            army.target_encounter_id = None
            army.retreat_destination_node_id = destination_id
        related_ids.append(destination_id)
    next_world.event_log.append(EventLogEntry(
        month=next_world.current_month,
        category="strategy_siege_attacker_stance",
        message=f"围城 {siege.siege_id} 的攻方方针调整为 {chosen}。",
        related_ids=related_ids,
    ))
    next_world.validate()
    return next_world


def _finish_siege(world: WorldState, siege: StrategicSiege, outcome: str) -> None:
    siege.status = "ended"
    siege.ended_month = world.current_month
    siege.outcome = outcome
    siege.battle_trigger = None


def _apply_surrender(world: WorldState, siege: StrategicSiege) -> None:
    city = next(item for item in world.cities if item.city_id == siege.city_id)
    previous_owner_id = city.owner_faction_id
    city.owner_faction_id = siege.attacker_faction_id
    city.resources.troops = max(50, city.resources.troops // 2)
    city.support_by_faction[siege.attacker_faction_id] = max(
        30, city.support_by_faction.get(siege.attacker_faction_id, 0)
    )
    mark_city_captured(
        world,
        city_id=city.city_id,
        previous_owner_faction_id=previous_owner_id,
        occupier_faction_id=siege.attacker_faction_id,
    )
    from wujiang.strategy.relics import apply_city_control_change_consequences

    _, control_change = apply_city_control_change_consequences(
        world,
        city_id=city.city_id,
        previous_faction_id=previous_owner_id,
        new_faction_id=siege.attacker_faction_id,
        cause="siege_surrender",
    )
    for army_id in siege.attacker_army_ids:
        army = _army(world, army_id)
        if army is None or army.status in {"disbanded", "destroyed"}:
            continue
        army.status = "garrisoned"
        army.current_order = "hold"
        army.target_army_id = None
        army.target_encounter_id = None
        army.retreat_destination_node_id = None
        refresh_army_supply_intel(world, army)
    _finish_siege(world, siege, "surrendered")
    world.event_log.append(EventLogEntry(
        month=world.current_month,
        category="strategy_siege_surrendered",
        message=f"{city.name}开城投降，城市转入占领治理。",
        related_ids=[siege.siege_id, city.city_id, previous_owner_id, siege.attacker_faction_id],
    ))
    if control_change["captured_relic_ids"]:
        world.event_log.append(EventLogEntry(
            month=world.current_month,
            category="strategy_siege_relics_captured",
            message=f"{city.name}投降时有 {len(control_change['captured_relic_ids'])} 件圣物转归攻方。",
            related_ids=[siege.siege_id, *control_change["captured_relic_ids"]],
        ))


def order_siege_defender_stance(
    world: WorldState,
    *,
    faction_id: str,
    siege_id: str,
    stance: str,
    issuer_office_id: str,
) -> WorldState:
    next_world = _clone_world(world)
    siege = _siege(next_world, siege_id)
    _active_governor(next_world, faction_id, issuer_office_id, siege.city_id)
    if siege.defender_faction_id != faction_id:
        raise StrategyError("只能调整己方城市的守城方针。")
    chosen = str(stance or "")
    if chosen not in DEFENDER_STANCES:
        raise StrategyError("守城方针无效。")
    siege.defender_stance = chosen
    next_world.event_log.append(EventLogEntry(
        month=next_world.current_month,
        category="strategy_siege_defender_stance",
        message=f"围城 {siege.siege_id} 的守方方针调整为 {chosen}。",
        related_ids=[siege.siege_id, siege.city_id, faction_id, issuer_office_id],
    ))
    if chosen == "surrender":
        _apply_surrender(next_world, siege)
    next_world.validate()
    return next_world


def reconcile_sieges(world: WorldState) -> None:
    active_encounters = [item for item in world.encounters if item.status == "active"]
    for siege in sorted(world.sieges, key=lambda item: item.siege_id):
        if siege.status not in ACTIVE_SIEGE_STATUSES:
            continue
        city = next(item for item in world.cities if item.city_id == siege.city_id)
        if city.owner_faction_id != siege.defender_faction_id:
            _finish_siege(world, siege, "city_changed")
            continue
        valid_ids = [
            army_id
            for army_id in siege.attacker_army_ids
            if (army := _army(world, army_id)) is not None
            and army.status not in {"disbanded", "destroyed"}
            and army.faction_id == siege.attacker_faction_id
            and army.location_node_id == siege.node_id
        ]
        siege.attacker_army_ids = valid_ids
        if not valid_ids:
            _finish_siege(world, siege, "withdrawn")
            continue
        encounter = next(
            (
                item for item in active_encounters
                if item.node_id == siege.node_id
                and any(army_id in ids for ids in item.faction_army_ids.values() for army_id in valid_ids)
            ),
            None,
        )
        if encounter is not None:
            siege.status = "contested"
            continue
        if siege.status == "contested":
            siege.status = "breached" if siege.fortification_remaining <= 0 else "active"
        for army in sorted(world.armies, key=lambda item: item.army_id):
            if (
                army.faction_id == siege.attacker_faction_id
                and army.location_node_id == siege.node_id
                and army.status not in {"disbanded", "destroyed", "engaged", "retreating"}
                and not any(
                    other.status in ACTIVE_SIEGE_STATUSES and army.army_id in other.attacker_army_ids
                    for other in world.sieges if other.siege_id != siege.siege_id
                )
            ):
                if army.army_id not in siege.attacker_army_ids:
                    siege.attacker_army_ids.append(army.army_id)
                army.status = "besieging"
                army.current_order = "besiege"


def _new_sieges(world: WorldState) -> list[StrategicSiege]:
    created: list[StrategicSiege] = []
    active_nodes = {item.node_id for item in world.encounters if item.status == "active"}
    active_cities = {item.city_id for item in world.sieges if item.status in ACTIVE_SIEGE_STATUSES}
    for city in sorted(world.cities, key=lambda item: item.city_id):
        if city.city_id in active_cities or city.node_id in active_nodes:
            continue
        candidates = [
            army for army in world.armies
            if army.location_node_id == city.node_id
            and army.faction_id != city.owner_faction_id
            and army.status in {"garrisoned", "deployed", "besieging"}
        ]
        if not candidates:
            continue
        hostile = next(
            (
                army for army in sorted(candidates, key=lambda item: item.army_id)
                if _factions_hostile(world, army.faction_id, city.owner_faction_id)
            ),
            None,
        )
        if hostile is None:
            continue
        faction_id = hostile.faction_id
        attackers = [army for army in candidates if army.faction_id == faction_id]
        fortification = max(20, city.defense * 10)
        siege = StrategicSiege(
            siege_id=f"siege:{city.city_id}:{world.current_month}:{len(world.sieges) + len(created) + 1}",
            city_id=city.city_id,
            node_id=city.node_id,
            attacker_faction_id=faction_id,
            defender_faction_id=city.owner_faction_id,
            attacker_army_ids=[army.army_id for army in attackers],
            started_month=world.current_month,
            fortification_initial=fortification,
            fortification_remaining=fortification,
        )
        for army in attackers:
            army.status = "besieging"
            army.current_order = "besiege"
        created.append(siege)
        world.event_log.append(EventLogEntry(
            month=world.current_month,
            category="strategy_siege_started",
            message=f"{city.name}被围；抵达当月只建立包围，不立即消耗城防与粮草。",
            related_ids=[siege.siege_id, city.city_id, faction_id, city.owner_faction_id, *siege.attacker_army_ids],
        ))
    return created


def _ai_defender_stance(world: WorldState, siege: StrategicSiege) -> str:
    city = next(item for item in world.cities if item.city_id == siege.city_id)
    attacker_manpower = sum((_army(world, army_id).manpower for army_id in siege.attacker_army_ids if _army(world, army_id)), 0)
    support = city.support_by_faction.get(siege.defender_faction_id, 50)
    if city.resources.food <= 0 and city.resources.troops * 2 < attacker_manpower and support < 35:
        return "surrender"
    if siege.fortification_remaining <= max(5, siege.fortification_initial // 5):
        return "await_relief"
    return "hold"


def _tick_siege(world: WorldState, siege: StrategicSiege) -> None:
    city = next(item for item in world.cities if item.city_id == siege.city_id)
    defender = next(item for item in world.factions if item.faction_id == siege.defender_faction_id)
    if defender.is_ai:
        siege.defender_stance = _ai_defender_stance(world, siege)
    if siege.defender_stance == "surrender":
        _apply_surrender(world, siege)
        return
    siege.last_city_food_consumed = 0
    siege.last_garrison_lost = 0
    siege.last_fortification_damage = 0
    if siege.defender_stance == "breakout":
        siege.status = "battle_pending"
        siege.battle_trigger = "breakout"
        return
    if siege.status in {"contested", "breached", "battle_pending"}:
        return
    food_need = max(20, math.ceil(city.resources.troops / 20))
    if siege.attacker_stance == "starve":
        food_need *= 2
    if siege.defender_stance == "hold":
        food_need += 10
    consumed = min(city.resources.food, food_need)
    city.resources.food -= consumed
    siege.last_city_food_consumed = consumed
    shortage = consumed < food_need
    if shortage:
        lost = min(city.resources.troops, max(20, math.ceil(city.resources.troops * 0.10)))
        city.resources.troops -= lost
        siege.last_garrison_lost = lost
        city.support_by_faction[siege.defender_faction_id] = max(
            0, city.support_by_faction.get(siege.defender_faction_id, 50) - 5
        )
    if siege.attacker_stance == "blockade":
        damage = 2
    elif siege.attacker_stance == "starve":
        damage = 4 if shortage else 1
    elif siege.attacker_stance == "assault":
        manpower = sum((_army(world, army_id).manpower for army_id in siege.attacker_army_ids if _army(world, army_id)), 0)
        damage = max(8, math.ceil(manpower / 100))
        for army_id in siege.attacker_army_ids:
            army = _army(world, army_id)
            if army is None:
                continue
            army.supply = max(0, army.supply - min(10, army.supply))
            army.morale = max(0, army.morale - 8)
    else:
        damage = 0
    if siege.defender_stance == "hold":
        damage = max(0, damage - 2)
    siege.last_fortification_damage = min(siege.fortification_remaining, damage)
    siege.fortification_remaining -= siege.last_fortification_damage
    if siege.fortification_remaining <= 0:
        siege.status = "breached"
        siege.battle_trigger = "assault"
    world.event_log.append(EventLogEntry(
        month=world.current_month,
        category="strategy_siege_advanced",
        message=(
            f"{city.name}围城结算：耗粮 {consumed}，守军损失 {siege.last_garrison_lost}，"
            f"城防损失 {siege.last_fortification_damage}，剩余 {siege.fortification_remaining}。"
        ),
        related_ids=[siege.siege_id, city.city_id, siege.attacker_faction_id, siege.defender_faction_id],
    ))


def advance_sieges(world: WorldState) -> WorldState:
    next_world = _clone_world(world)
    reconcile_sieges(next_world)
    created = _new_sieges(next_world)
    next_world.sieges.extend(created)
    created_ids = {item.siege_id for item in created}
    for siege in sorted(next_world.sieges, key=lambda item: item.siege_id):
        if (
            siege.siege_id in created_ids
            or siege.status == "ended"
            or next_world.current_month <= siege.started_month
        ):
            continue
        _tick_siege(next_world, siege)
    reconcile_sieges(next_world)
    next_world.validate()
    return next_world
