from __future__ import annotations

import copy
import math
from collections import deque
from typing import Any

from wujiang.strategy.administration import REGISTERED_UNIT_TYPES
from wujiang.strategy.models import EventLogEntry, StrategicArmy, StrategyError, WorldState


MIN_INITIAL_SUPPLY = 50
ARMY_MORALE_AT_FORMATION = 70
ARMY_SUPPLY_DISTANCE_COST = 5
ARMY_MARCH_SUPPLY_COST = 5
ARMY_STARVATION_MORALE_LOSS = 12
ARMY_SEVERED_MORALE_LOSS = 3


def _clone_world(world: WorldState) -> WorldState:
    return WorldState.from_dict(copy.deepcopy(world.to_dict()))


def _general(world: WorldState, faction_id: str, office_id: str):
    office = next((item for item in world.offices if item.office_id == str(office_id)), None)
    if (
        office is None
        or office.faction_id != faction_id
        or office.office_type != "general"
        or office.status != "active"
        or not office.holder_id
    ):
        raise StrategyError("只有在职将军可以编组军队。")
    return office


def _owned_city(world: WorldState, faction_id: str, city_id: str):
    city = next((item for item in world.cities if item.city_id == str(city_id)), None)
    if city is None or city.owner_faction_id != faction_id:
        raise StrategyError("只能在己方城市编组军队。")
    return city


def _active_army_for_general(world: WorldState, office_id: str) -> StrategicArmy | None:
    return next(
        (
            army
            for army in world.armies
            if army.commander_office_id == office_id and army.status not in {"disbanded", "destroyed"}
        ),
        None,
    )


def _commanded_army(
    world: WorldState,
    *,
    faction_id: str,
    army_id: str,
    issuer_office_id: str,
) -> tuple[Any, StrategicArmy]:
    general = _general(world, faction_id, issuer_office_id)
    army = next((item for item in world.armies if item.army_id == str(army_id)), None)
    if army is None or army.faction_id != faction_id or army.commander_office_id != general.office_id:
        raise StrategyError("只能指挥自己统率的军队。")
    if army.status in {"disbanded", "destroyed", "engaged", "besieging", "retreating"}:
        raise StrategyError("军队当前状态不能改变行军命令。")
    return general, army


def shortest_army_route(world: WorldState, source_node_id: str, destination_node_id: str) -> list[str]:
    source_id = str(source_node_id or "")
    destination_id = str(destination_node_id or "")
    nodes_by_id = {node.node_id: node for node in world.nodes}
    if source_id not in nodes_by_id or destination_id not in nodes_by_id:
        raise StrategyError("行军起点或目的节点不存在。")
    if source_id == destination_id:
        raise StrategyError("军队已经位于该节点。")
    pending: deque[str] = deque([source_id])
    previous: dict[str, str | None] = {source_id: None}
    while pending:
        current_id = pending.popleft()
        for next_id in sorted(nodes_by_id[current_id].connected_node_ids):
            if next_id not in nodes_by_id or next_id in previous:
                continue
            previous[next_id] = current_id
            if next_id == destination_id:
                pending.clear()
                break
            pending.append(next_id)
    if destination_id not in previous:
        raise StrategyError("当前节点与目的节点之间没有可通行路线。")
    route = [destination_id]
    while route[-1] != source_id:
        parent = previous[route[-1]]
        if parent is None:
            raise StrategyError("无法重建行军路线。")
        route.append(parent)
    route.reverse()
    return route


def _supply_line_status(world: WorldState, faction_id: str, route: list[str]) -> str:
    distance = max(0, len(route) - 1)
    if distance == 0:
        return "local"
    cities_by_node = {city.node_id: city for city in world.cities}
    if any(
        (city := cities_by_node.get(node_id)) is not None and city.owner_faction_id != faction_id
        for node_id in route[1:-1]
    ):
        return "severed"
    return "strained" if distance >= 3 else "open"


def _route_allow_same(world: WorldState, source_node_id: str, destination_node_id: str) -> list[str]:
    if str(source_node_id) == str(destination_node_id):
        return [str(source_node_id)]
    return shortest_army_route(world, source_node_id, destination_node_id)


def army_supply_plan(world: WorldState, army: StrategicArmy) -> dict[str, Any]:
    owned_cities = sorted(
        (city for city in world.cities if city.owner_faction_id == army.faction_id),
        key=lambda city: city.city_id,
    )
    candidates: list[tuple[int, int, str, Any, list[str], str]] = []
    status_rank = {"local": 0, "open": 1, "strained": 2, "severed": 3}
    for city in owned_cities:
        try:
            route = _route_allow_same(world, army.location_node_id, city.node_id)
        except StrategyError:
            continue
        status = _supply_line_status(world, army.faction_id, route)
        candidates.append((status_rank[status], len(route) - 1, city.city_id, city, route, status))
    base_need = max(10, math.ceil(max(0, army.manpower) / 20))
    marched_this_month = army.status == "marching" or (
        army.estimated_arrival_month == world.current_month
        and army.departure_month is not None
        and army.departure_month < world.current_month
        and len(army.route_node_ids) >= 2
        and army.route_progress_index == len(army.route_node_ids) - 1
    )
    if not candidates:
        return {
            "source_city": None,
            "route": [],
            "status": "none",
            "distance": None,
            "need": base_need + (ARMY_MARCH_SUPPLY_COST if marched_this_month else 0),
            "delivery_limit": 0,
        }
    _, distance, _, city, route, status = min(candidates, key=lambda item: item[:3])
    need = base_need + distance * ARMY_SUPPLY_DISTANCE_COST
    if marched_this_month:
        need += ARMY_MARCH_SUPPLY_COST
    if status in {"local", "open"}:
        delivery_limit = need
    elif status == "strained":
        delivery_limit = max(1, need // 2)
    else:
        delivery_limit = 0
    return {
        "source_city": city,
        "route": route,
        "status": status,
        "distance": distance,
        "need": need,
        "delivery_limit": delivery_limit,
    }


def refresh_army_supply_intel(world: WorldState, army: StrategicArmy) -> None:
    plan = army_supply_plan(world, army)
    source_city = plan["source_city"]
    army.supply_source_city_id = source_city.city_id if source_city is not None else None
    army.supply_line_node_ids = list(plan["route"])
    army.supply_line_status = str(plan["status"])
    army.supply_distance = plan["distance"]
    army.monthly_supply_need = int(plan["need"])


def load_army_supply(
    world: WorldState,
    *,
    faction_id: str,
    army_id: str,
    supply: int,
    issuer_office_id: str,
) -> WorldState:
    next_world = _clone_world(world)
    _, army = _commanded_army(
        next_world,
        faction_id=faction_id,
        army_id=army_id,
        issuer_office_id=issuer_office_id,
    )
    if army.status != "garrisoned":
        raise StrategyError("只有驻扎在己方城市的军队可以手动装粮。")
    city = next(
        (
            item
            for item in next_world.cities
            if item.node_id == army.location_node_id and item.owner_faction_id == faction_id
        ),
        None,
    )
    if city is None:
        raise StrategyError("军队必须位于己方城市才能装粮。")
    amount = int(supply)
    if amount <= 0:
        raise StrategyError("装粮数量必须大于 0。")
    if city.resources.food < amount:
        raise StrategyError("当前城市没有足够粮食。")
    if army.supply + amount > army.supply_capacity:
        raise StrategyError(f"军队粮草容量为 {army.supply_capacity}，本次装载会超出上限。")
    city.resources.food -= amount
    army.supply += amount
    refresh_army_supply_intel(next_world, army)
    next_world.event_log.append(EventLogEntry(
        month=next_world.current_month,
        category="strategy_army_supply_loaded",
        message=f"军队在{city.name}装载粮草 {amount}，现有 {army.supply}/{army.supply_capacity}。",
        related_ids=[faction_id, army.army_id, issuer_office_id, city.city_id],
    ))
    next_world.validate()
    return next_world


def _apply_starvation_attrition(world: WorldState, army: StrategicArmy) -> tuple[str | None, int]:
    if army.starvation_months < 2 or not army.unit_inventory:
        return None, 0
    unit_type = min(
        army.unit_inventory,
        key=lambda item: (int(REGISTERED_UNIT_TYPES[item]["troop_cost"]), item),
    )
    army.unit_inventory[unit_type] -= 1
    if army.unit_inventory[unit_type] <= 0:
        army.unit_inventory.pop(unit_type, None)
    lost_manpower = int(REGISTERED_UNIT_TYPES[unit_type]["troop_cost"])
    army.manpower = army_manpower(army.unit_inventory)
    army.supply_capacity = army_supply_capacity(army.manpower) if army.manpower > 0 else 0
    army.supply = min(army.supply, army.supply_capacity)
    if not army.unit_inventory:
        army.status = "destroyed"
        army.current_order = "hold"
        army.route_node_ids = [army.location_node_id]
        army.route_progress_index = 0
        army.destination_node_id = army.location_node_id
        army.estimated_arrival_month = world.current_month
    return unit_type, lost_manpower


def advance_army_supply(world: WorldState) -> WorldState:
    next_world = _clone_world(world)
    nodes_by_id = {node.node_id: node for node in next_world.nodes}
    for army in sorted(next_world.armies, key=lambda item: item.army_id):
        if army.status in {"disbanded", "destroyed"}:
            continue
        plan = army_supply_plan(next_world, army)
        source_city = plan["source_city"]
        refresh_army_supply_intel(next_world, army)
        delivery = 0
        if source_city is not None and int(plan["delivery_limit"]) > 0:
            delivery = min(
                int(plan["delivery_limit"]),
                max(0, army.supply_capacity - army.supply),
                source_city.resources.food,
            )
            source_city.resources.food -= delivery
            army.supply += delivery
        consumed = min(army.supply, army.monthly_supply_need)
        army.supply -= consumed
        army.last_supply_received = delivery
        army.last_supply_consumed = consumed
        shortage = consumed < army.monthly_supply_need
        if shortage:
            army.starvation_months += 1
            army.morale = max(0, army.morale - ARMY_STARVATION_MORALE_LOSS)
        else:
            army.starvation_months = 0
            if army.supply_line_status in {"local", "open"}:
                army.morale = min(100, army.morale + 2)
            elif army.supply_line_status in {"severed", "none"}:
                army.morale = max(0, army.morale - ARMY_SEVERED_MORALE_LOSS)
        lost_unit, lost_manpower = _apply_starvation_attrition(next_world, army)
        location_name = nodes_by_id[army.location_node_id].name
        source_name = source_city.name if source_city is not None else "无"
        category = "strategy_army_supply_shortage" if shortage else "strategy_army_supplied"
        message = (
            f"军队在{location_name}结算补给：需求 {army.monthly_supply_need}，"
            f"从{source_name}接收 {delivery}，消耗 {consumed}，余粮 {army.supply}，士气 {army.morale}。"
        )
        if lost_unit:
            category = "strategy_army_destroyed" if army.status == "destroyed" else "strategy_army_attrition"
            message += f" 连续断粮导致 1 个{lost_unit}单位、{lost_manpower} 兵员损失。"
        next_world.event_log.append(EventLogEntry(
            month=next_world.current_month,
            category=category,
            message=message,
            related_ids=[
                army.faction_id,
                army.army_id,
                army.location_node_id,
                *([source_city.city_id] if source_city is not None else []),
            ],
        ))
    next_world.validate()
    return next_world


def order_army_march(
    world: WorldState,
    *,
    faction_id: str,
    army_id: str,
    destination_node_id: str,
    issuer_office_id: str,
) -> WorldState:
    next_world = _clone_world(world)
    _, army = _commanded_army(
        next_world,
        faction_id=faction_id,
        army_id=army_id,
        issuer_office_id=issuer_office_id,
    )
    route = shortest_army_route(next_world, army.location_node_id, destination_node_id)
    army.status = "marching"
    army.current_order = "march"
    army.march_origin_node_id = route[0]
    army.destination_node_id = route[-1]
    army.route_node_ids = route
    army.route_progress_index = 0
    army.departure_month = next_world.current_month
    army.estimated_arrival_month = next_world.current_month + len(route) - 1
    refresh_army_supply_intel(next_world, army)
    names_by_id = {node.node_id: node.name for node in next_world.nodes}
    next_world.event_log.append(EventLogEntry(
        month=next_world.current_month,
        category="strategy_army_march_ordered",
        message=(
            f"军队从{names_by_id.get(route[0], route[0])}出发，沿 "
            f"{' → '.join(names_by_id.get(node_id, node_id) for node_id in route)} 行军，"
            f"预计第 {army.estimated_arrival_month} 月抵达。"
        ),
        related_ids=[faction_id, army.army_id, issuer_office_id, *route],
    ))
    next_world.validate()
    return next_world


def halt_army_march(
    world: WorldState,
    *,
    faction_id: str,
    army_id: str,
    issuer_office_id: str,
) -> WorldState:
    next_world = _clone_world(world)
    _, army = _commanded_army(
        next_world,
        faction_id=faction_id,
        army_id=army_id,
        issuer_office_id=issuer_office_id,
    )
    if army.status != "marching":
        raise StrategyError("只有行军中的军队可以停止行军。")
    city = next((item for item in next_world.cities if item.node_id == army.location_node_id), None)
    army.status = "garrisoned" if city is not None and city.owner_faction_id == faction_id else "deployed"
    army.current_order = "hold"
    if army.route_node_ids:
        army.route_node_ids = army.route_node_ids[:army.route_progress_index + 1]
        army.route_progress_index = len(army.route_node_ids) - 1
    else:
        army.route_node_ids = [army.location_node_id]
        army.route_progress_index = 0
    army.destination_node_id = army.location_node_id
    army.estimated_arrival_month = next_world.current_month
    refresh_army_supply_intel(next_world, army)
    node = next(item for item in next_world.nodes if item.node_id == army.location_node_id)
    next_world.event_log.append(EventLogEntry(
        month=next_world.current_month,
        category="strategy_army_march_halted",
        message=f"军队在{node.name}停止行军，等待新的命令。",
        related_ids=[faction_id, army.army_id, issuer_office_id, node.node_id],
    ))
    next_world.validate()
    return next_world


def advance_army_movements(world: WorldState) -> WorldState:
    next_world = _clone_world(world)
    nodes_by_id = {node.node_id: node for node in next_world.nodes}
    cities_by_node = {city.node_id: city for city in next_world.cities}
    heroes_by_code = {hero.hero_code: hero for hero in next_world.strategic_heroes}
    for army in sorted(next_world.armies, key=lambda item: item.army_id):
        if army.status != "marching":
            continue
        next_index = army.route_progress_index + 1
        if next_index >= len(army.route_node_ids):
            raise StrategyError(f"军队 {army.army_id} 没有下一段合法路线。")
        previous_node_id = army.location_node_id
        next_node_id = army.route_node_ids[next_index]
        if next_node_id not in nodes_by_id[previous_node_id].connected_node_ids:
            raise StrategyError(f"军队 {army.army_id} 的下一段路线已经失效。")
        army.location_node_id = next_node_id
        army.route_progress_index = next_index
        hero = heroes_by_code.get(army.commander_hero_code)
        city = cities_by_node.get(next_node_id)
        if hero is not None:
            hero.city_id = city.city_id if city is not None else None
        arrived = next_index == len(army.route_node_ids) - 1
        if arrived:
            army.status = "garrisoned" if city is not None and city.owner_faction_id == army.faction_id else "deployed"
            army.current_order = "hold"
            category = "strategy_army_arrived"
            message = f"军队抵达{nodes_by_id[next_node_id].name}，结束本次行军。"
        else:
            category = "strategy_army_marched"
            message = (
                f"军队从{nodes_by_id[previous_node_id].name}行至{nodes_by_id[next_node_id].name}，"
                f"预计第 {army.estimated_arrival_month} 月抵达{nodes_by_id[army.destination_node_id].name}。"
            )
        next_world.event_log.append(EventLogEntry(
            month=next_world.current_month,
            category=category,
            message=message,
            related_ids=[army.faction_id, army.army_id, previous_node_id, next_node_id],
        ))
        refresh_army_supply_intel(next_world, army)
    next_world.validate()
    return next_world


def _normalized_units(raw: dict[str, Any] | None) -> dict[str, int]:
    if not isinstance(raw, dict):
        raise StrategyError("编军单位必须是兵种数量对象。")
    units: dict[str, int] = {}
    for unit_type, raw_count in raw.items():
        normalized_type = str(unit_type or "").strip()
        count = int(raw_count or 0)
        if normalized_type not in REGISTERED_UNIT_TYPES:
            raise StrategyError("编军包含不存在的兵种。")
        if count < 0:
            raise StrategyError("编军数量不能为负数。")
        if count:
            units[normalized_type] = count
    if not units:
        raise StrategyError("至少要向军队转入一个已注册单位。")
    return units


def army_manpower(unit_inventory: dict[str, int]) -> int:
    return sum(
        int(config["troop_cost"]) * max(0, int(unit_inventory.get(unit_type, 0)))
        for unit_type, config in REGISTERED_UNIT_TYPES.items()
    )


def army_supply_capacity(manpower: int) -> int:
    return max(200, int(manpower))


def form_or_reinforce_army(
    world: WorldState,
    *,
    faction_id: str,
    city_id: str,
    unit_inventory: dict[str, Any],
    supply: int,
    issuer_office_id: str,
) -> WorldState:
    next_world = _clone_world(world)
    general = _general(next_world, faction_id, issuer_office_id)
    city = _owned_city(next_world, faction_id, city_id)
    hero = next(
        (item for item in next_world.strategic_heroes if item.office_id == general.office_id),
        None,
    )
    if hero is None or hero.hero_code != general.holder_id:
        raise StrategyError("将军职位没有绑定一致的武将。")
    if hero.city_id not in {None, city.city_id}:
        raise StrategyError("将军必须亲自在编军城市。")

    units = _normalized_units(unit_inventory)
    for unit_type, count in units.items():
        if int(general.unit_inventory.get(unit_type, 0)) < count:
            raise StrategyError("将军库存没有足够的已注册单位。")
    supply_amount = int(supply)
    if supply_amount < MIN_INITIAL_SUPPLY:
        raise StrategyError(f"编军至少需要 {MIN_INITIAL_SUPPLY} 粮草。")
    if city.resources.food < supply_amount:
        raise StrategyError("编军城市没有足够粮食装载军队。")

    army = _active_army_for_general(next_world, general.office_id)
    if army is not None and (army.status != "garrisoned" or army.location_node_id != city.node_id):
        raise StrategyError("只能在军队当前驻扎的己方城市补充编制。")
    combined_units = dict(army.unit_inventory if army is not None else {})
    for unit_type, count in units.items():
        combined_units[unit_type] = combined_units.get(unit_type, 0) + count
    manpower = army_manpower(combined_units)
    capacity = army_supply_capacity(manpower)
    current_supply = int(army.supply if army is not None else 0)
    if current_supply + supply_amount > capacity:
        raise StrategyError(f"军队粮草容量为 {capacity}，本次装载会超出上限。")

    for unit_type, count in units.items():
        general.unit_inventory[unit_type] -= count
        if general.unit_inventory[unit_type] <= 0:
            general.unit_inventory.pop(unit_type, None)
    city.resources.food -= supply_amount
    if army is None:
        army = StrategicArmy(
            army_id=f"army:{faction_id}:{general.office_id.split(':')[-1]}:{next_world.current_month}:{len(next_world.armies) + 1}",
            faction_id=faction_id,
            commander_office_id=general.office_id,
            commander_hero_code=hero.hero_code,
            location_node_id=city.node_id,
            home_city_id=city.city_id,
            unit_inventory=combined_units,
            manpower=manpower,
            supply=supply_amount,
            supply_capacity=capacity,
            morale=ARMY_MORALE_AT_FORMATION,
            created_month=next_world.current_month,
        )
        next_world.armies.append(army)
        category = "strategy_army_formed"
        verb = "编成"
    else:
        army.unit_inventory = combined_units
        army.manpower = manpower
        army.supply_capacity = capacity
        army.supply += supply_amount
        category = "strategy_army_reinforced"
        verb = "补充"
    refresh_army_supply_intel(next_world, army)
    next_world.event_log.append(EventLogEntry(
        month=next_world.current_month,
        category=category,
        message=f"{hero.hero_code}在{city.name}{verb}军队：兵员 {army.manpower}，粮草 {army.supply}/{army.supply_capacity}。",
        related_ids=[faction_id, army.army_id, general.office_id, city.city_id, *sorted(units)],
    ))
    next_world.validate()
    return next_world


def disband_army(
    world: WorldState,
    *,
    faction_id: str,
    army_id: str,
    issuer_office_id: str,
) -> WorldState:
    next_world = _clone_world(world)
    general = _general(next_world, faction_id, issuer_office_id)
    army = next((item for item in next_world.armies if item.army_id == str(army_id)), None)
    if army is None or army.faction_id != faction_id or army.commander_office_id != general.office_id:
        raise StrategyError("只能解散自己指挥的军队。")
    if army.status != "garrisoned":
        raise StrategyError("只有驻扎状态的军队可以解散。")
    city = next(
        (item for item in next_world.cities if item.node_id == army.location_node_id and item.owner_faction_id == faction_id),
        None,
    )
    if city is None:
        raise StrategyError("军队必须位于己方城市才能解散。")
    for unit_type, count in army.unit_inventory.items():
        general.unit_inventory[unit_type] = general.unit_inventory.get(unit_type, 0) + count
    city.resources.food += army.supply
    returned_supply = army.supply
    army.unit_inventory = {}
    army.manpower = 0
    army.supply = 0
    army.supply_capacity = 0
    army.status = "disbanded"
    army.current_order = "hold"
    next_world.event_log.append(EventLogEntry(
        month=next_world.current_month,
        category="strategy_army_disbanded",
        message=f"军队在{city.name}解散，单位归还将军库存，粮草返还 {returned_supply}。",
        related_ids=[faction_id, army.army_id, general.office_id, city.city_id],
    ))
    next_world.validate()
    return next_world
