from __future__ import annotations

import copy
import hashlib
from typing import Any

from wujiang.strategic.models import EventLogEntry, OfficeOrder, StrategyError, WorldState


FIELD_LEVY = {"population": 120, "food": 60, "money": 40, "troops": 140}
GARRISON_LEVY = {"population": 80, "food": 40, "money": 25, "troops": 90, "defense": 1}
BUILDING_PROJECTS: dict[str, dict[str, Any]] = {
    "fields": {
        "name": "农业区",
        "money": 80,
        "food": 20,
        "effect": "每月粮食 +60 / 级",
        "monthly_food": 60,
    },
    "barracks": {
        "name": "军营",
        "money": 100,
        "food": 30,
        "effect": "每月兵力 +15 / 级",
        "monthly_troops": 15,
    },
    "ritual_site": {
        "name": "祭坛",
        "money": 140,
        "food": 10,
        "effect": "每月以太 +8 / 级；可安放圣物",
        "monthly_ether": 8,
    },
    "academy": {
        "name": "学院",
        "money": 120,
        "food": 10,
        "effect": "每月以太 +6 / 级",
        "monthly_ether": 6,
    },
    "market": {
        "name": "商业区",
        "money": 110,
        "food": 15,
        "effect": "每月金钱 +50 / 级",
        "monthly_money": 50,
    },
    "industrial": {
        "name": "工业区",
        "money": 130,
        "food": 25,
        "effect": "每月金钱 +35 / 级、兵力 +8 / 级",
        "monthly_money": 35,
        "monthly_troops": 8,
    },
    "city_defense": {
        "name": "城防",
        "money": 100,
        "food": 20,
        "effect": "攻城战在城门两侧部署两座箭塔",
        "max_level": 1,
    },
    "walls": {
        "name": "城墙",
        "money": 120,
        "food": 20,
        "defense": 2,
        "effect": "每级城防 +2",
        "fortress_only": True,
    },
    "castle": {
        "name": "城堡",
        "money": 160,
        "food": 30,
        "defense": 1,
        "effect": "每级城防 +1，每月兵力 +20 / 级",
        "monthly_troops": 20,
        "fortress_only": True,
    },
    "stables": {
        "name": "马厩",
        "money": 130,
        "food": 35,
        "effect": "注册骑兵",
        "visible": False,
    },
    "archery_range": {
        "name": "靶场",
        "money": 115,
        "food": 25,
        "effect": "注册弓兵",
        "visible": False,
    },
}

SETTLEMENT_LABELS = {
    "village": "村庄",
    "town": "城镇",
    "city": "城市",
    "fortress": "要塞",
}
SETTLEMENT_BUILDING_RANK = {
    "village": 1,
    "town": 2,
    "city": 3,
    "fortress": 3,
}
SETTLEMENT_UPGRADES: dict[str, dict[str, Any]] = {
    "town": {
        "from_settlement": "village",
        "name": "城镇",
        "population": 1400,
        "food": 700,
        "money": 180,
        "level": 2,
        "defense": 0,
    },
    "city": {
        "from_settlement": "town",
        "name": "城市",
        "population": 2600,
        "food": 1200,
        "money": 320,
        "level": 3,
        "defense": 1,
    },
    "fortress": {
        "from_settlement": "town",
        "name": "要塞",
        "population": 2000,
        "food": 900,
        "money": 260,
        "level": 2,
        "defense": 4,
    },
}

CANNON_STOCK_CAPS = {
    "village": 0,
    "town": 2,
    "city": 4,
    "fortress": 8,
}
ECONOMY_GROWTH = {
    "village": 1.00,
    "town": 1.12,
    "city": 1.35,
}
CITY_WORKS: dict[str, dict[str, Any]] = {
    "forge_cannon": {
        "name": "铸造火炮",
        "money": 90,
        "food": 40,
        "required_tech": "cannon_foundry",
        "kind": "forge",
        "effect": "本城火炮库存 +1；城镇最多 2 门，城市 4 门，要塞 8 门。",
    },
    "convert_to_fortress": {
        "name": "改建要塞",
        "from_settlement": "city",
        "to_settlement": "fortress",
        "money": 220,
        "food": 120,
        "defense": 3,
        "kind": "convert",
        "effect": "城市改为要塞。钱粮人口增速保持城市档。",
    },
    "convert_to_city": {
        "name": "改建城市",
        "from_settlement": "fortress",
        "to_settlement": "city",
        "money": 180,
        "food": 80,
        "kind": "convert",
        "effect": "要塞改为城市。经济档不变。",
    },
}

REGISTERED_UNIT_TYPES: dict[str, dict[str, Any]] = {
    "infantry": {"name": "步兵", "troop_cost": 100, "building_id": "barracks"},
    "archer": {"name": "弓兵", "troop_cost": 140, "building_id": "archery_range"},
    "cavalry": {"name": "骑兵", "troop_cost": 180, "building_id": "stables"},
    "snow_ghost": {
        "name": "雪鬼",
        "troop_cost": 100,
        "building_id": None,
        "player_trainable": False,
    },
}


def _clone_world(world: WorldState) -> WorldState:
    return WorldState.from_dict(copy.deepcopy(world.to_dict()))


def _office(world: WorldState, office_id: str, faction_id: str, office_type: str):
    office = next((item for item in world.offices if item.office_id == str(office_id)), None)
    if office is None or office.faction_id != faction_id or office.office_type != office_type:
        raise StrategyError("当前职位无权执行这项行动。")
    return office


def _building_office(world: WorldState, office_id: str, faction_id: str, city, verb: str = "建设"):
    office = next((item for item in world.offices if item.office_id == str(office_id)), None)
    if office is None or office.faction_id != faction_id:
        raise StrategyError("当前职位无权执行这项行动。")
    if office.office_type == "lord":
        return office
    if office.office_type == "governor":
        if city.city_id not in office.managed_entity_ids:
            raise StrategyError(f"城主只能{verb}所辖城市。")
        return office
    raise StrategyError("当前职位无权执行这项行动。")


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
    from wujiang.strategic.tactics import unlocked_registered_unit_types

    faction = next(item for item in world.factions if item.faction_id == faction_id)
    unlocked = unlocked_registered_unit_types(faction)
    weighted: list[str] = []
    for unit_type, config in REGISTERED_UNIT_TYPES.items():
        if config.get("player_trainable", True) is False:
            continue
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


def cannon_stock_cap(city) -> int:
    return int(CANNON_STOCK_CAPS.get(str(getattr(city, "settlement", "") or "village"), 0))


def city_economy_class(city) -> str:
    current = str(getattr(city, "economy_class", "") or "")
    if current in ECONOMY_GROWTH:
        return current
    from wujiang.strategic.models import infer_economy_class

    return infer_economy_class(str(getattr(city, "settlement", "") or ""), int(getattr(city, "level", 1) or 1))


def city_economy_growth(city) -> float:
    return float(ECONOMY_GROWTH.get(city_economy_class(city), 1.0))


def city_has_city_defense(city) -> bool:
    levels = getattr(city, "building_levels", None) or {}
    return int(levels.get("city_defense", 0) or 0) > 0


def city_building_max_level(city, building_id: str) -> int:
    project = BUILDING_PROJECTS.get(str(building_id))
    if project is None:
        return 0
    if project.get("fortress_only") and str(getattr(city, "settlement", "") or "") != "fortress":
        return 0
    settlement_cap = min(3, int(SETTLEMENT_BUILDING_RANK.get(str(getattr(city, "settlement", "") or "village"), 1)))
    project_cap = int(project.get("max_level") or 3)
    return min(settlement_cap, project_cap)


def construct_city_building(
    world: WorldState,
    *,
    faction_id: str,
    city_id: str,
    building_id: str,
    issuer_office_id: str,
) -> WorldState:
    next_world = _clone_world(world)
    city = _owned_city(next_world, city_id, faction_id)
    office = _building_office(next_world, issuer_office_id, faction_id, city, "建设")
    project_id = str(building_id or "").strip()
    project = BUILDING_PROJECTS.get(project_id)
    if project is None:
        raise StrategyError("建筑项目不存在。")
    current_level = int(city.building_levels.get(project_id, 0))
    maximum_level = city_building_max_level(city, project_id)
    if maximum_level <= 0:
        raise StrategyError("只有要塞可以建造这座建筑。")
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
    if project_id == "ritual_site":
        from wujiang.strategic.relics import ensure_city_relic_altar

        ensure_city_relic_altar(next_world, city)
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


def settlement_upgrade_options(city) -> list[dict[str, Any]]:
    current = str(getattr(city, "settlement", "") or "")
    options: list[dict[str, Any]] = []
    for target, spec in SETTLEMENT_UPGRADES.items():
        if spec["from_settlement"] != current:
            continue
        population = int(spec["population"])
        food = int(spec["food"])
        money = int(spec["money"])
        options.append(
            {
                "id": target,
                "name": spec["name"],
                "from_settlement": spec["from_settlement"],
                "population": population,
                "food": food,
                "money": money,
                "available": (
                    int(city.resources.population) >= population
                    and int(city.resources.food) >= food
                    and int(city.resources.money) >= money
                ),
            }
        )
    return options


def settlement_upgrades_public() -> list[dict[str, Any]]:
    return [{"id": target, **spec} for target, spec in SETTLEMENT_UPGRADES.items()]


def city_works_public() -> list[dict[str, Any]]:
    return [{"id": work_id, **spec} for work_id, spec in CITY_WORKS.items()]


def city_work_options(world: WorldState, city, faction) -> list[dict[str, Any]]:
    from wujiang.strategic.tactics import siege_tech_bonuses

    bonuses = siege_tech_bonuses(faction)
    options: list[dict[str, Any]] = []
    for work_id, spec in CITY_WORKS.items():
        required_tech = str(spec.get("required_tech") or "")
        from_settlement = str(spec.get("from_settlement") or "")
        cap = cannon_stock_cap(city)
        stock = int(getattr(city, "cannon_stock", 0) or 0)
        available = True
        reason = ""
        if required_tech and not int(bonuses.get("can_forge_cannon", 0) or 0):
            available = False
            reason = "需要先研究火炮铸造"
        if from_settlement and str(city.settlement) != from_settlement:
            available = False
            reason = "当前城市类型不符"
        if work_id == "forge_cannon":
            if cap <= 0:
                available = False
                reason = "村庄不能储存火炮"
            elif stock >= cap:
                available = False
                reason = f"库存已达上限 {cap}"
        money = int(spec.get("money") or 0)
        food = int(spec.get("food") or 0)
        if available and (int(city.resources.money) < money or int(city.resources.food) < food):
            available = False
            reason = f"资源不足：钱 {money} / 粮 {food}"
        options.append(
            {
                "id": work_id,
                "name": spec["name"],
                "effect": spec.get("effect") or "",
                "money": money,
                "food": food,
                "required_tech": required_tech,
                "kind": spec.get("kind") or "",
                "cannon_stock": stock,
                "cannon_stock_cap": cap,
                "available": available,
                "reason": reason,
            }
        )
    return options


def start_city_work(
    world: WorldState,
    *,
    faction_id: str,
    city_id: str,
    work_id: str,
    issuer_office_id: str,
) -> WorldState:
    next_world = _clone_world(world)
    city = _owned_city(next_world, city_id, faction_id)
    office = _building_office(next_world, issuer_office_id, faction_id, city, "开工")
    work_id = str(work_id or "").strip()
    spec = CITY_WORKS.get(work_id)
    if spec is None:
        raise StrategyError("城市工程项目不存在。")
    faction = next((item for item in next_world.factions if item.faction_id == faction_id), None)
    if faction is None:
        raise StrategyError("势力不存在。")
    from wujiang.strategic.tactics import siege_tech_bonuses

    bonuses = siege_tech_bonuses(faction)
    required_tech = str(spec.get("required_tech") or "")
    if required_tech and not int(bonuses.get("can_forge_cannon", 0) or 0) and required_tech == "cannon_foundry":
        raise StrategyError("需要先研究火炮铸造。")
    from_settlement = str(spec.get("from_settlement") or "")
    if from_settlement and city.settlement != from_settlement:
        from_name = SETTLEMENT_LABELS.get(from_settlement, from_settlement)
        raise StrategyError(f"只有{from_name}可以执行{spec['name']}。")
    if work_id == "forge_cannon":
        cap = cannon_stock_cap(city)
        if cap <= 0:
            raise StrategyError("村庄不能储存火炮。")
        if int(city.cannon_stock) >= cap:
            raise StrategyError(f"{SETTLEMENT_LABELS.get(city.settlement, city.settlement)}最多储存 {cap} 门火炮。")
    _spend_city_resources(city, population=0, food=int(spec.get("food") or 0), money=int(spec.get("money") or 0))
    if work_id == "forge_cannon":
        city.cannon_stock += 1
        message = f"{city.name}铸造火炮，库存 {city.cannon_stock}/{cannon_stock_cap(city)}。"
    else:
        target = str(spec.get("to_settlement") or "")
        city.settlement = target
        if int(spec.get("defense") or 0):
            city.defense += int(spec["defense"])
        if target == "city":
            city.level = max(int(city.level), 3)
        cap = cannon_stock_cap(city)
        if city.cannon_stock > cap:
            city.cannon_stock = cap
        message = f"{city.name}完成{spec['name']}，现为{SETTLEMENT_LABELS.get(target, target)}。"
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="city_work",
            message=message,
            related_ids=[faction_id, office.office_id, city.city_id, work_id],
        )
    )
    next_world.validate()
    return next_world


def upgrade_city_settlement(
    world: WorldState,
    *,
    faction_id: str,
    city_id: str,
    settlement: str,
    issuer_office_id: str,
) -> WorldState:
    next_world = _clone_world(world)
    city = _owned_city(next_world, city_id, faction_id)
    office = _building_office(next_world, issuer_office_id, faction_id, city, "升级")
    target = str(settlement or "").strip()
    spec = SETTLEMENT_UPGRADES.get(target)
    if spec is None:
        raise StrategyError("未知的城市升级目标。")
    if city.settlement != spec["from_settlement"]:
        from_name = SETTLEMENT_LABELS.get(str(spec["from_settlement"]), str(spec["from_settlement"]))
        raise StrategyError(f"只有{from_name}可以升级为{spec['name']}。")
    if city.resources.population < int(spec["population"]):
        raise StrategyError(f"人口不足：升级为{spec['name']}需要人口 {spec['population']}。")
    _spend_city_resources(city, population=0, food=int(spec["food"]), money=int(spec["money"]))
    city.settlement = target
    city.level = max(int(city.level), int(spec["level"]))
    if target in {"village", "town", "city"}:
        city.economy_class = target
    if int(spec.get("defense") or 0):
        city.defense += int(spec["defense"])
    cap = cannon_stock_cap(city)
    if city.cannon_stock > cap:
        city.cannon_stock = cap
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="city_settlement_upgraded",
            message=f"{city.name}升级为{spec['name']}。",
            related_ids=[faction_id, office.office_id, city.city_id, target],
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


def city_building_monthly_bonus(city) -> dict[str, int]:
    food = money = ether = troops = 0
    for building_id, level in dict(getattr(city, "building_levels", {}) or {}).items():
        project = BUILDING_PROJECTS.get(str(building_id))
        if project is None:
            continue
        grade = max(0, int(level or 0))
        food += int(project.get("monthly_food", 0)) * grade
        money += int(project.get("monthly_money", 0)) * grade
        ether += int(project.get("monthly_ether", 0)) * grade
        troops += int(project.get("monthly_troops", 0)) * grade
    return {"food": food, "money": money, "ether": ether, "troops": troops}


def building_projects_public() -> list[dict[str, Any]]:
    return [
        {
            "id": project_id,
            "name": project["name"],
            "money": int(project["money"]),
            "food": int(project["food"]),
            "effect": str(project.get("effect") or ""),
            "defense": int(project.get("defense") or 0),
            "monthly_food": int(project.get("monthly_food") or 0),
            "monthly_money": int(project.get("monthly_money") or 0),
            "monthly_ether": int(project.get("monthly_ether") or 0),
            "monthly_troops": int(project.get("monthly_troops") or 0),
            "fortress_only": bool(project.get("fortress_only")),
            "visible": project.get("visible", True) is not False,
            "max_level": int(project.get("max_level") or 3),
        }
        for project_id, project in BUILDING_PROJECTS.items()
    ]


def registered_unit_types_public() -> list[dict[str, Any]]:
    return [{"id": unit_type, **config} for unit_type, config in REGISTERED_UNIT_TYPES.items()]
