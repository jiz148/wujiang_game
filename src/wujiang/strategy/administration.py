from __future__ import annotations

import copy
import hashlib
from typing import Any

from wujiang.strategy.models import EventLogEntry, OfficeOrder, StrategyError, WorldState


FIELD_LEVY = {"population": 120, "food": 60, "money": 40, "troops": 140}
GARRISON_LEVY = {"population": 80, "food": 40, "money": 25, "troops": 90, "defense": 1}
BUILDING_PROJECTS: dict[str, dict[str, Any]] = {
    "academy": {"name": "学院", "money": 120, "food": 10, "effect": "研究与高级建筑前置"},
    "fields": {"name": "田地", "money": 80, "food": 20, "effect": "每级每月粮食 +60"},
    "barracks": {"name": "兵营", "money": 100, "food": 30, "effect": "注册步兵"},
    "stables": {"name": "马厩", "money": 130, "food": 35, "effect": "注册骑兵"},
    "archery_range": {"name": "靶场", "money": 115, "food": 25, "effect": "注册弓兵"},
    "ritual_site": {"name": "祭祀场", "money": 140, "food": 10, "effect": "每级每月以太 +8；允许祭祀"},
    "walls": {"name": "城墙", "money": 120, "food": 20, "defense": 2, "effect": "每级城防 +2"},
}

REGISTERED_UNIT_TYPES: dict[str, dict[str, Any]] = {
    "infantry": {"name": "步兵", "troop_cost": 100, "building_id": "barracks"},
    "archer": {"name": "弓兵", "troop_cost": 140, "building_id": "archery_range"},
    "cavalry": {"name": "骑兵", "troop_cost": 180, "building_id": "stables"},
}


def _clone_world(world: WorldState) -> WorldState:
    return WorldState.from_dict(copy.deepcopy(world.to_dict()))


def _office(world: WorldState, office_id: str, faction_id: str, office_type: str):
    office = next((item for item in world.offices if item.office_id == str(office_id)), None)
    if office is None or office.faction_id != faction_id or office.office_type != office_type:
        raise StrategyError("当前职位无权执行这项行动。")
    return office


def _owned_city(world: WorldState, city_id: str, faction_id: str):
    city = next((item for item in world.cities if item.city_id == str(city_id)), None)
    if city is None or city.owner_faction_id != faction_id:
        raise StrategyError("只能管理本势力城市。")
    return city


def _spend_city_resources(city, *, population: int, food: int, money: int) -> None:
    if city.resources.population < population or city.resources.food < food or city.resources.money < money:
        raise StrategyError(f"资源不足：需要人口 {population}、粮食 {food}、金钱 {money}。")
    city.resources.population -= population
    city.resources.food -= food
    city.resources.money -= money


def levy_field_troops(
    world: WorldState,
    *,
    faction_id: str,
    city_id: str,
    issuer_office_id: str,
) -> WorldState:
    next_world = _clone_world(world)
    office = _office(next_world, issuer_office_id, faction_id, "grand_general")
    city = _owned_city(next_world, city_id, faction_id)
    _spend_city_resources(city, **{key: FIELD_LEVY[key] for key in ("population", "food", "money")})
    city.resources.troops += FIELD_LEVY["troops"]
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="field_troops_levied",
            message=f"大将军在{city.name}征募野战兵 {FIELD_LEVY['troops']}。",
            related_ids=[faction_id, office.office_id, city.city_id],
        )
    )
    next_world.validate()
    return next_world


def levy_city_garrison(
    world: WorldState,
    *,
    faction_id: str,
    city_id: str,
    issuer_office_id: str,
) -> WorldState:
    next_world = _clone_world(world)
    office = _office(next_world, issuer_office_id, faction_id, "governor")
    city = _owned_city(next_world, city_id, faction_id)
    if city.city_id not in office.managed_entity_ids:
        raise StrategyError("城主只能征集所辖城市的守军。")
    _spend_city_resources(city, **{key: GARRISON_LEVY[key] for key in ("population", "food", "money")})
    city.resources.troops += GARRISON_LEVY["troops"]
    city.defense += GARRISON_LEVY["defense"]
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="city_garrison_levied",
            message=f"{city.name}征集守军 {GARRISON_LEVY['troops']}，城防 +{GARRISON_LEVY['defense']}。",
            related_ids=[faction_id, office.office_id, city.city_id],
        )
    )
    next_world.validate()
    return next_world


def increase_city_troops(
    world: WorldState,
    *,
    faction_id: str,
    city_id: str,
    issuer_office_id: str,
) -> WorldState:
    return levy_city_garrison(
        world,
        faction_id=faction_id,
        city_id=city_id,
        issuer_office_id=issuer_office_id,
    )


def _eligible_registration_types(world: WorldState, city, faction_id: str) -> list[str]:
    from wujiang.strategy.tactics import unlocked_registered_unit_types

    faction = next(item for item in world.factions if item.faction_id == faction_id)
    unlocked = unlocked_registered_unit_types(faction)
    weighted: list[str] = []
    for unit_type, config in REGISTERED_UNIT_TYPES.items():
        building_level = int(city.building_levels.get(str(config["building_id"]), 0))
        if unit_type in unlocked and building_level > 0:
            weighted.extend([unit_type] * building_level)
    return weighted


def register_city_soldiers(
    world: WorldState,
    *,
    faction_id: str,
    city_id: str,
    issuer_office_id: str,
    unit_count: int = 1,
) -> WorldState:
    next_world = _clone_world(world)
    office = _office(next_world, issuer_office_id, faction_id, "governor")
    city = _owned_city(next_world, city_id, faction_id)
    if city.city_id not in office.managed_entity_ids:
        raise StrategyError("城主只能注册所辖城市的士兵。")
    requested = max(1, min(3, int(unit_count)))
    weighted_types = _eligible_registration_types(next_world, city, faction_id)
    if not weighted_types:
        raise StrategyError("本城没有可用的训练建筑，或对应兵种科技尚未解锁。")
    registration_number = 1 + sum(
        1
        for event in next_world.event_log
        if event.category == "city_soldiers_registered" and city.city_id in event.related_ids
    )
    created: dict[str, int] = {}
    for index in range(requested):
        digest = hashlib.sha256(
            f"{next_world.seed}:{next_world.current_month}:{city.city_id}:{registration_number}:{index}".encode("utf-8")
        ).digest()
        start = int.from_bytes(digest[:4], "big") % len(weighted_types)
        affordable = [
            weighted_types[(start + offset) % len(weighted_types)]
            for offset in range(len(weighted_types))
            if city.resources.troops >= int(REGISTERED_UNIT_TYPES[weighted_types[(start + offset) % len(weighted_types)]]["troop_cost"])
        ]
        if not affordable:
            break
        unit_type = affordable[0]
        city.resources.troops -= int(REGISTERED_UNIT_TYPES[unit_type]["troop_cost"])
        city.registered_units[unit_type] = city.registered_units.get(unit_type, 0) + 1
        created[unit_type] = created.get(unit_type, 0) + 1
    if not created:
        minimum = min(int(REGISTERED_UNIT_TYPES[item]["troop_cost"]) for item in set(weighted_types))
        raise StrategyError(f"城市兵力不足；至少需要 {minimum} 兵力才能注册一个单位。")
    summary = "、".join(f"{REGISTERED_UNIT_TYPES[key]['name']} {value}" for key, value in sorted(created.items()))
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="city_soldiers_registered",
            message=f"{city.name}完成士兵注册：{summary}。",
            related_ids=[faction_id, office.office_id, city.city_id, *sorted(created)],
        )
    )
    next_world.validate()
    return next_world


def construct_city_building(
    world: WorldState,
    *,
    faction_id: str,
    city_id: str,
    building_id: str,
    issuer_office_id: str,
) -> WorldState:
    next_world = _clone_world(world)
    office = _office(next_world, issuer_office_id, faction_id, "governor")
    city = _owned_city(next_world, city_id, faction_id)
    if city.city_id not in office.managed_entity_ids:
        raise StrategyError("城主只能建设所辖城市。")
    project_id = str(building_id or "").strip()
    project = BUILDING_PROJECTS.get(project_id)
    if project is None:
        raise StrategyError("建筑项目不存在。")
    from wujiang.strategy.tactics import building_max_level

    faction = next(item for item in next_world.factions if item.faction_id == faction_id)
    current_level = int(city.building_levels.get(project_id, 0))
    maximum_level = building_max_level(faction, project_id)
    if current_level >= maximum_level:
        raise StrategyError(f"该建筑当前最高只能达到 {maximum_level} 级。")
    next_level = current_level + 1
    _spend_city_resources(
        city,
        population=0,
        food=int(project["food"]) * next_level,
        money=int(project["money"]) * next_level,
    )
    city.building_levels[project_id] = next_level
    if project_id not in city.buildings:
        city.buildings.append(project_id)
    city.defense += int(project.get("defense", 0))
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="city_building_constructed",
            message=f"{city.name}的{project['name']}升至 {next_level} 级。",
            related_ids=[faction_id, office.office_id, city.city_id, project_id],
        )
    )
    next_world.validate()
    return next_world


def _transfer_units(city, receiver, unit_type: str, count: int) -> None:
    normalized_type = str(unit_type or "").strip()
    amount = max(1, int(count))
    if normalized_type not in REGISTERED_UNIT_TYPES:
        raise StrategyError("调兵请求的兵种不存在。")
    if int(city.registered_units.get(normalized_type, 0)) < amount:
        raise StrategyError("城市没有足够的已注册单位。")
    city.registered_units[normalized_type] -= amount
    if city.registered_units[normalized_type] <= 0:
        city.registered_units.pop(normalized_type, None)
    receiver.unit_inventory[normalized_type] = receiver.unit_inventory.get(normalized_type, 0) + amount


def transfer_registered_units(
    world: WorldState,
    *,
    faction_id: str,
    city_id: str,
    general_office_id: str,
    unit_type: str,
    count: int,
    issuer_office_id: str,
) -> WorldState:
    next_world = _clone_world(world)
    issuer = _office(next_world, issuer_office_id, faction_id, "grand_general")
    receiver = _office(next_world, general_office_id, faction_id, "general")
    city = _owned_city(next_world, city_id, faction_id)
    if receiver.parent_office_id != issuer.office_id:
        raise StrategyError("大将军只能向直属将军调拨单位。")
    normalized_type = str(unit_type or "").strip()
    _transfer_units(city, receiver, normalized_type, count)
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="registered_units_transferred",
            message=f"大将军从{city.name}向直属将军调拨{REGISTERED_UNIT_TYPES[normalized_type]['name']} {int(count)}。",
            related_ids=[faction_id, issuer.office_id, receiver.office_id, city.city_id, normalized_type],
        )
    )
    next_world.validate()
    return next_world


def request_registered_units(
    world: WorldState,
    *,
    faction_id: str,
    city_id: str,
    unit_type: str,
    count: int,
    issuer_office_id: str,
) -> WorldState:
    next_world = _clone_world(world)
    general = _office(next_world, issuer_office_id, faction_id, "general")
    city = _owned_city(next_world, city_id, faction_id)
    if general.parent_office_id is None:
        raise StrategyError("该将军没有直属大将军。")
    normalized_type = str(unit_type or "").strip()
    amount = max(1, int(count))
    if normalized_type not in REGISTERED_UNIT_TYPES:
        raise StrategyError("调兵请求的兵种不存在。")
    order_id = f"unit-request:{next_world.current_month}:{len(next_world.office_orders) + 1}:{general.office_id}"
    next_world.office_orders.append(
        OfficeOrder(
            order_id=order_id,
            issuer_office_id=general.office_id,
            receiver_office_id=general.parent_office_id,
            order_type="unit_request",
            target_entity_id=city.city_id,
            objective=f"请求{REGISTERED_UNIT_TYPES[normalized_type]['name']} {amount}",
            issued_month=next_world.current_month,
            details={"city_id": city.city_id, "unit_type": normalized_type, "count": amount},
        )
    )
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="registered_units_requested",
            message=f"将军向直属大将军申请从{city.name}抽调{REGISTERED_UNIT_TYPES[normalized_type]['name']} {amount}。",
            related_ids=[order_id, general.office_id, general.parent_office_id, city.city_id, normalized_type],
        )
    )
    next_world.validate()
    return next_world


def approve_registered_unit_request(
    world: WorldState,
    *,
    faction_id: str,
    request_id: str,
    issuer_office_id: str,
) -> WorldState:
    next_world = _clone_world(world)
    grand_general = _office(next_world, issuer_office_id, faction_id, "grand_general")
    request = next((item for item in next_world.office_orders if item.order_id == str(request_id)), None)
    if request is None or request.order_type != "unit_request" or request.status != "pending":
        raise StrategyError("调兵请求不存在或已经处理。")
    if request.receiver_office_id != grand_general.office_id:
        raise StrategyError("只能批准提交给本职位的调兵请求。")
    general = _office(next_world, request.issuer_office_id, faction_id, "general")
    city = _owned_city(next_world, str(request.details.get("city_id") or request.target_entity_id or ""), faction_id)
    unit_type = str(request.details.get("unit_type") or "")
    count = max(1, int(request.details.get("count", 1)))
    _transfer_units(city, general, unit_type, count)
    request.status = "completed"
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="registered_unit_request_approved",
            message=f"大将军批准调兵：{REGISTERED_UNIT_TYPES[unit_type]['name']} {count}进入将军军团。",
            related_ids=[request.order_id, grand_general.office_id, general.office_id, city.city_id, unit_type],
        )
    )
    next_world.validate()
    return next_world


def building_projects_public() -> list[dict[str, Any]]:
    return [{"id": project_id, **project, "max_level": 3} for project_id, project in BUILDING_PROJECTS.items()]


def registered_unit_types_public() -> list[dict[str, Any]]:
    return [{"id": unit_type, **config} for unit_type, config in REGISTERED_UNIT_TYPES.items()]
