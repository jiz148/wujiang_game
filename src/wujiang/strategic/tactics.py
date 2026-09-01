from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from wujiang.strategic.models import City, EventLogEntry, Faction, StrategyError, WorldState
from wujiang.strategic.simulation import POLICIES


@dataclass(frozen=True, slots=True)
class TacticTech:
    tech_id: str
    name: str
    description: str
    money_cost: int
    ether_cost: int
    branch: str = "military"
    special_ratio_bonus: int = 0
    garrison_ratio_bonus: int = 0
    hero_deployment_limit_bonus: int = 0
    office_capacity_effects: dict[str, int] = field(default_factory=dict)
    unit_unlocks: tuple[str, ...] = field(default_factory=tuple)
    building_level_effects: dict[str, int] = field(default_factory=dict)
    prerequisites: tuple[str, ...] = field(default_factory=tuple)
    required_building: str = ""
    required_building_level: int = 0
    required_settlement: str = ""
    siege_effects: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.tech_id,
            "name": self.name,
            "description": self.description,
            "money_cost": self.money_cost,
            "ether_cost": self.ether_cost,
            "branch": self.branch,
            "special_ratio_bonus": self.special_ratio_bonus,
            "garrison_ratio_bonus": self.garrison_ratio_bonus,
            "hero_deployment_limit_bonus": self.hero_deployment_limit_bonus,
            "office_capacity_effects": dict(self.office_capacity_effects),
            "unit_unlocks": list(self.unit_unlocks),
            "building_level_effects": dict(self.building_level_effects),
            "prerequisites": list(self.prerequisites),
            "required_building": self.required_building,
            "required_building_level": int(self.required_building_level or 0),
            "required_settlement": self.required_settlement,
            "siege_effects": dict(self.siege_effects),
        }


TACTIC_TECH_TREE: tuple[TacticTech, ...] = (
    TacticTech(
        tech_id="local_militia",
        name="乡勇编练",
        description="提高城市特色士兵在出战兵力中的基础占比。",
        money_cost=80,
        ether_cost=0,
        special_ratio_bonus=10,
    ),
    TacticTech(
        tech_id="city_doctrine",
        name="城邦战术",
        description="让每座城市更稳定地把本地兵力转化为特色单位。",
        money_cost=140,
        ether_cost=15,
        special_ratio_bonus=20,
        prerequisites=("local_militia",),
    ),
    TacticTech(
        tech_id="combined_arms",
        name="合成兵制",
        description="进一步扩大特色单位比例，并保留普通部队作为骨架。",
        money_cost=200,
        ether_cost=30,
        special_ratio_bonus=15,
        prerequisites=("city_doctrine",),
    ),
    TacticTech(
        tech_id="fortified_garrison",
        name="城防军制",
        description="提高守备兵比例，适合防守城市与围城战。",
        money_cost=120,
        ether_cost=10,
        garrison_ratio_bonus=10,
        prerequisites=("local_militia",),
    ),
    TacticTech(
        tech_id="hero_command",
        name="英灵军议",
        description="扩展城市战中可同时投入的战略英灵上限。",
        money_cost=180,
        ether_cost=35,
        hero_deployment_limit_bonus=1,
        prerequisites=("city_doctrine",),
    ),
    TacticTech(
        tech_id="command_staff_1",
        name="参谋制度 I",
        description="每名大将军可辖将军数量 +1。",
        money_cost=150,
        ether_cost=10,
        branch="office",
        office_capacity_effects={"general_per_grand_general": 1},
        prerequisites=("local_militia",),
    ),
    TacticTech(
        tech_id="command_staff_2",
        name="参谋制度 II",
        description="每名大将军可辖将军数量再 +1。",
        money_cost=260,
        ether_cost=25,
        branch="office",
        office_capacity_effects={"general_per_grand_general": 1},
        prerequisites=("command_staff_1",),
    ),
    TacticTech(
        tech_id="archery_corps",
        name="弓兵军制",
        description="允许有靶场的城市注册弓兵；每单位需要 140 兵力。",
        money_cost=120,
        ether_cost=10,
        branch="unit",
        unit_unlocks=("archer",),
        prerequisites=("local_militia",),
    ),
    TacticTech(
        tech_id="cavalry_corps",
        name="骑兵军制",
        description="允许有马厩的城市注册骑兵；每单位需要 180 兵力。",
        money_cost=180,
        ether_cost=15,
        branch="unit",
        unit_unlocks=("cavalry",),
        prerequisites=("local_militia",),
    ),
    TacticTech(
        tech_id="civic_architecture_2",
        name="城市营造 II",
        description="学院与田地可升级至 2 级。",
        money_cost=120,
        ether_cost=5,
        branch="building",
        building_level_effects={"academy": 1, "fields": 1},
    ),
    TacticTech(
        tech_id="military_architecture_2",
        name="军用营造 II",
        description="兵营、马厩与靶场可升级至 2 级。",
        money_cost=150,
        ether_cost=5,
        branch="building",
        building_level_effects={"barracks": 1, "stables": 1, "archery_range": 1},
        prerequisites=("local_militia",),
    ),
    TacticTech(
        tech_id="sacred_architecture_2",
        name="祭祀营造 II",
        description="祭祀场可升级至 2 级，提高每月以太产出。",
        money_cost=140,
        ether_cost=20,
        branch="building",
        building_level_effects={"ritual_site": 1},
    ),
    TacticTech(
        tech_id="architecture_3",
        name="城邦营造 III",
        description="全部六类核心建筑可升级至 3 级。",
        money_cost=280,
        ether_cost=35,
        branch="building",
        building_level_effects={
            "academy": 1,
            "fields": 1,
            "barracks": 1,
            "stables": 1,
            "archery_range": 1,
            "ritual_site": 1,
        },
        prerequisites=("civic_architecture_2", "military_architecture_2", "sacred_architecture_2"),
    ),
    TacticTech(
        tech_id="military_reform_1",
        name="军制改革 I",
        description="扩充国家军事指挥体系，大将军职位容量 +1。",
        money_cost=160,
        ether_cost=10,
        branch="office",
        office_capacity_effects={"grand_general": 1},
        prerequisites=("local_militia",),
    ),
    TacticTech(
        tech_id="military_reform_2",
        name="军制改革 II",
        description="继续扩充战区指挥体系，大将军职位容量 +1。",
        money_cost=240,
        ether_cost=25,
        branch="office",
        office_capacity_effects={"grand_general": 1},
        prerequisites=("military_reform_1",),
    ),
    TacticTech(
        tech_id="military_reform_3",
        name="军制改革 III",
        description="建立成熟的多战区参谋制度，大将军职位容量 +2。",
        money_cost=360,
        ether_cost=45,
        branch="office",
        office_capacity_effects={"grand_general": 2},
        prerequisites=("military_reform_2",),
    ),
    TacticTech(
        tech_id="civic_edict",
        name="律令布告",
        description="统一城内法令口径，降低治理摩擦。",
        money_cost=70,
        ether_cost=0,
        branch="office",
    ),
    TacticTech(
        tech_id="census_roll",
        name="编户齐民",
        description="清查户口，方便后续征收与征发。",
        money_cost=90,
        ether_cost=0,
        branch="office",
    ),
    TacticTech(
        tech_id="militia_drill",
        name="教阅法",
        description="定期校场教阅，提高乡勇出战效率。",
        money_cost=70,
        ether_cost=0,
    ),
    TacticTech(
        tech_id="market_levy",
        name="市集课税",
        description="规范市集抽成，提高每回合金钱收入。",
        money_cost=80,
        ether_cost=0,
        branch="building",
    ),
    TacticTech(
        tech_id="caravan_charter",
        name="商路特许",
        description="发放商队路引，拓宽城际贸易。",
        money_cost=100,
        ether_cost=0,
        branch="building",
    ),
    TacticTech(
        tech_id="wall_mason",
        name="夯土城垣",
        description="加固城垣夯土，提高基础城防。",
        money_cost=80,
        ether_cost=0,
        branch="building",
    ),
    TacticTech(
        tech_id="irrigation",
        name="沟渠灌溉",
        description="整修沟渠，提高田地产出。",
        money_cost=70,
        ether_cost=0,
        branch="building",
        building_level_effects={"fields": 0},
    ),
    TacticTech(
        tech_id="granary",
        name="常平仓",
        description="设置常平仓，缓和粮价与缺粮冲击。",
        money_cost=90,
        ether_cost=0,
        branch="building",
    ),
    TacticTech(
        tech_id="envoy_office",
        name="行人制度",
        description="设立行人官，方便遣使与回访。",
        money_cost=80,
        ether_cost=0,
        branch="office",
    ),
    TacticTech(
        tech_id="guest_rite",
        name="宾礼",
        description="完善宾礼，改善与邻邦的交涉余地。",
        money_cost=90,
        ether_cost=0,
        branch="office",
    ),
    TacticTech(
        tech_id="spirit_rite",
        name="社稷常祀",
        description="按时祭祀社稷，稳定城内以太来源。",
        money_cost=80,
        ether_cost=10,
        branch="building",
        building_level_effects={"ritual_site": 0},
    ),
    TacticTech(
        tech_id="talent_call",
        name="求贤令",
        description="广发求贤令，提高在野武将投效意愿。",
        money_cost=80,
        ether_cost=0,
        branch="office",
        hero_deployment_limit_bonus=0,
    ),
    TacticTech(
        tech_id="cannon_foundry",
        name="火炮铸造",
        description="解锁城市工程项目「铸造火炮」。需要至少一座城市拥有 2 级学院。",
        money_cost=180,
        ether_cost=20,
        branch="siege",
        prerequisites=("civic_architecture_2",),
        required_building="academy",
        required_building_level=2,
        siege_effects={"can_forge_cannon": 1},
    ),
    TacticTech(
        tech_id="cannon_attack_1",
        name="火炮威力 I",
        description="全部火炮攻击力 +1。",
        money_cost=140,
        ether_cost=15,
        branch="siege",
        prerequisites=("cannon_foundry",),
        siege_effects={"cannon_attack": 1},
    ),
    TacticTech(
        tech_id="cannon_attack_2",
        name="火炮威力 II",
        description="全部火炮攻击力再 +1。",
        money_cost=220,
        ether_cost=25,
        branch="siege",
        prerequisites=("cannon_attack_1",),
        siege_effects={"cannon_attack": 1},
    ),
    TacticTech(
        tech_id="cannon_range_1",
        name="火炮射程 I",
        description="全部火炮攻击距离 +1。",
        money_cost=140,
        ether_cost=15,
        branch="siege",
        prerequisites=("cannon_foundry",),
        siege_effects={"cannon_range": 1},
    ),
    TacticTech(
        tech_id="cannon_range_2",
        name="火炮射程 II",
        description="全部火炮攻击距离再 +1。",
        money_cost=220,
        ether_cost=25,
        branch="siege",
        prerequisites=("cannon_range_1",),
        siege_effects={"cannon_range": 1},
    ),
    TacticTech(
        tech_id="cannon_splash_1",
        name="火炮范围 I",
        description="解锁重型炮弹，火炮可配置溅射 1。溅射不分敌我。",
        money_cost=160,
        ether_cost=20,
        branch="siege",
        prerequisites=("cannon_foundry",),
        siege_effects={"cannon_splash": 1},
    ),
    TacticTech(
        tech_id="cannon_splash_2",
        name="火炮范围 II",
        description="解锁超重型炮弹，火炮可配置溅射 2。溅射极易误伤己方。",
        money_cost=240,
        ether_cost=30,
        branch="siege",
        prerequisites=("cannon_splash_1",),
        siege_effects={"cannon_splash": 1},
    ),
    TacticTech(
        tech_id="tower_attack_1",
        name="箭塔火力 I",
        description="全部箭塔攻击力 +1。",
        money_cost=120,
        ether_cost=10,
        branch="siege",
        prerequisites=("fortified_garrison",),
        siege_effects={"tower_attack": 1},
    ),
    TacticTech(
        tech_id="tower_attack_2",
        name="箭塔火力 II",
        description="全部箭塔攻击力再 +1。",
        money_cost=200,
        ether_cost=20,
        branch="siege",
        prerequisites=("tower_attack_1",),
        siege_effects={"tower_attack": 1},
    ),
    TacticTech(
        tech_id="tower_range_1",
        name="箭塔射程 I",
        description="全部箭塔攻击距离 +1。",
        money_cost=120,
        ether_cost=10,
        branch="siege",
        prerequisites=("fortified_garrison",),
        siege_effects={"tower_range": 1},
    ),
    TacticTech(
        tech_id="tower_range_2",
        name="箭塔射程 II",
        description="全部箭塔攻击距离再 +1。",
        money_cost=200,
        ether_cost=20,
        branch="siege",
        prerequisites=("tower_range_1",),
        siege_effects={"tower_range": 1},
    ),
    TacticTech(
        tech_id="tower_defense_1",
        name="箭塔防御 I",
        description="全部箭塔防御力 +1。",
        money_cost=120,
        ether_cost=10,
        branch="siege",
        prerequisites=("fortified_garrison",),
        siege_effects={"tower_defense": 1},
    ),
    TacticTech(
        tech_id="tower_defense_2",
        name="箭塔防御 II",
        description="全部箭塔防御力再 +1。",
        money_cost=200,
        ether_cost=20,
        branch="siege",
        prerequisites=("tower_defense_1",),
        siege_effects={"tower_defense": 1},
    ),
)

TACTIC_TECHS_BY_ID = {tech.tech_id: tech for tech in TACTIC_TECH_TREE}

TECH_CATEGORY_LABELS = {
    "politics": "政治",
    "military": "军事",
    "economy": "经济",
    "construction": "建设",
    "agriculture": "农业",
    "diplomacy": "外交",
    "ritual": "祭祀",
    "heroes": "武将",
    "siege": "攻城",
}

# 父类只负责分组展示；研究费用、回合和前置都挂在子科技上。
# 既有科技暂定为 1 回合，避免打断现有月结测试；新编占位科技用 2～3 回合。
TECH_PRESENTATION: dict[str, tuple[str, int]] = {
    "civic_edict": ("politics", 2),
    "census_roll": ("politics", 2),
    "command_staff_1": ("politics", 1),
    "command_staff_2": ("politics", 1),
    "local_militia": ("military", 1),
    "militia_drill": ("military", 2),
    "city_doctrine": ("military", 1),
    "combined_arms": ("military", 1),
    "fortified_garrison": ("military", 1),
    "archery_corps": ("military", 1),
    "cavalry_corps": ("military", 1),
    "military_reform_1": ("military", 1),
    "military_reform_2": ("military", 1),
    "military_reform_3": ("military", 1),
    "market_levy": ("economy", 2),
    "caravan_charter": ("economy", 3),
    "civic_architecture_2": ("construction", 1),
    "military_architecture_2": ("construction", 1),
    "architecture_3": ("construction", 1),
    "wall_mason": ("construction", 2),
    "irrigation": ("agriculture", 2),
    "granary": ("agriculture", 2),
    "envoy_office": ("diplomacy", 2),
    "guest_rite": ("diplomacy", 3),
    "sacred_architecture_2": ("ritual", 1),
    "spirit_rite": ("ritual", 2),
    "hero_command": ("heroes", 1),
    "talent_call": ("heroes", 2),
    "cannon_foundry": ("siege", 2),
    "cannon_attack_1": ("siege", 2),
    "cannon_attack_2": ("siege", 3),
    "cannon_range_1": ("siege", 2),
    "cannon_range_2": ("siege", 3),
    "cannon_splash_1": ("siege", 2),
    "cannon_splash_2": ("siege", 3),
    "tower_attack_1": ("siege", 2),
    "tower_attack_2": ("siege", 3),
    "tower_range_1": ("siege", 2),
    "tower_range_2": ("siege", 3),
    "tower_defense_1": ("siege", 2),
    "tower_defense_2": ("siege", 3),
}

BASE_UNIT_UNLOCKS = {"infantry"}


def unlocked_registered_unit_types(faction: Faction) -> set[str]:
    unlocked = set(BASE_UNIT_UNLOCKS)
    for tech_id in faction.tactic_techs:
        tech = TACTIC_TECHS_BY_ID.get(tech_id)
        if tech is not None:
            unlocked.update(tech.unit_unlocks)
    return unlocked


def building_max_level(faction: Faction, building_id: str) -> int:
    maximum = 1
    for tech_id in faction.tactic_techs:
        tech = TACTIC_TECHS_BY_ID.get(tech_id)
        if tech is not None:
            maximum += int(tech.building_level_effects.get(str(building_id), 0))
    return max(1, min(3, maximum))


def _clone_world(world: WorldState) -> WorldState:
    return WorldState.from_dict(copy.deepcopy(world.to_dict()))


def _faction(world: WorldState, faction_id: str) -> Faction:
    for faction in world.factions:
        if faction.faction_id == faction_id:
            return faction
    raise StrategyError("势力不存在。")


def _city(world: WorldState, city_id: str) -> City:
    for city in world.cities:
        if city.city_id == city_id:
            return city
    raise StrategyError("城市不存在。")


def _settlement_rank(settlement: str) -> int:
    from wujiang.strategic.administration import SETTLEMENT_BUILDING_RANK

    return int(SETTLEMENT_BUILDING_RANK.get(str(settlement or ""), 0) or 0)


def faction_meets_tech_requirements(world: WorldState | None, faction: Faction, tech: TacticTech) -> bool:
    if world is None:
        return not tech.required_building and not tech.required_settlement
    cities = [city for city in world.cities if city.owner_faction_id == faction.faction_id]
    if tech.required_building:
        needed = max(1, int(tech.required_building_level or 1))
        if not any(int(city.building_levels.get(tech.required_building, 0) or 0) >= needed for city in cities):
            return False
    if tech.required_settlement:
        needed_rank = _settlement_rank(tech.required_settlement)
        if not any(_settlement_rank(city.settlement) >= needed_rank for city in cities):
            return False
    return True


def siege_tech_bonuses(faction: Faction) -> dict[str, int]:
    bonuses = {
        "cannon_attack": 0,
        "cannon_range": 0,
        "cannon_splash": 0,
        "tower_attack": 0,
        "tower_range": 0,
        "tower_defense": 0,
        "can_forge_cannon": 0,
    }
    for tech_id in faction.tactic_techs:
        tech = TACTIC_TECHS_BY_ID.get(tech_id)
        if tech is None:
            continue
        for key, value in tech.siege_effects.items():
            bonuses[key] = min(2, int(bonuses.get(key, 0)) + int(value or 0))
    return bonuses


def tactic_tech_tree_public(faction: Faction, world: WorldState | None = None) -> list[dict[str, Any]]:
    from wujiang.strategic.administration import BUILDING_PROJECTS, SETTLEMENT_LABELS

    unlocked = set(faction.tactic_techs)
    payload: list[dict[str, Any]] = []
    for tech in TACTIC_TECH_TREE:
        item = tech.to_dict()
        category, research_months = TECH_PRESENTATION.get(tech.tech_id, ("military", 1))
        item["category"] = category
        item["category_label"] = TECH_CATEGORY_LABELS.get(category, "军事")
        item["research_months"] = research_months
        item["required_building_label"] = (
            (BUILDING_PROJECTS.get(tech.required_building) or {}).get("name") or tech.required_building
        )
        item["required_settlement_label"] = SETTLEMENT_LABELS.get(tech.required_settlement, tech.required_settlement)
        item["requirement_met"] = faction_meets_tech_requirements(world, faction, tech)
        researching = faction.researching or {}
        researching_id = str(researching.get("tech_id") or "")
        months_done = int(researching.get("months_done") or 0)
        item["unlocked"] = tech.tech_id in unlocked
        item["available"] = (
            tech.tech_id not in unlocked
            and all(prereq in unlocked for prereq in tech.prerequisites)
            and item["requirement_met"]
        )
        item["researching"] = researching_id == tech.tech_id
        item["research_progress"] = months_done if researching_id == tech.tech_id else 0
        payload.append(item)
    return payload


def special_unit_ratio(faction: Faction) -> int:
    unlocked = set(faction.tactic_techs)
    ratio = 10
    for tech_id in unlocked:
        tech = TACTIC_TECHS_BY_ID.get(tech_id)
        if tech is not None:
            ratio += tech.special_ratio_bonus
    return max(0, min(70, ratio))


def garrison_ratio(faction: Faction) -> int:
    unlocked = set(faction.tactic_techs)
    ratio = 0
    for tech_id in unlocked:
        tech = TACTIC_TECHS_BY_ID.get(tech_id)
        if tech is not None:
            ratio += tech.garrison_ratio_bonus
    return max(0, min(20, ratio))


def city_troop_conversion(city: City, faction: Faction) -> list[dict[str, Any]]:
    total_troops = city.resources.troops
    if total_troops <= 0:
        return []

    feature_names = city.troop_features or ["守备兵"]
    special_ratio = special_unit_ratio(faction)
    forced_garrison_ratio = garrison_ratio(faction)
    default_ratio = max(0, 100 - special_ratio - forced_garrison_ratio)
    rows: list[dict[str, Any]] = []

    if forced_garrison_ratio:
        rows.append(
            {
                "unit_type": "守备兵",
                "source": "tactic_tech",
                "ratio": forced_garrison_ratio,
                "troops": total_troops * forced_garrison_ratio // 100,
            }
        )

    per_feature_ratio = special_ratio // len(feature_names)
    remainder = special_ratio - per_feature_ratio * len(feature_names)
    for index, feature in enumerate(feature_names):
        ratio = per_feature_ratio + (1 if index < remainder else 0)
        if ratio <= 0:
            continue
        rows.append(
            {
                "unit_type": feature,
                "source": "city_feature",
                "ratio": ratio,
                "troops": total_troops * ratio // 100,
            }
        )

    rows.append(
        {
            "unit_type": "普通步兵",
            "source": "default",
            "ratio": default_ratio,
            "troops": max(0, total_troops - sum(row["troops"] for row in rows)),
        }
    )
    return rows


def enrich_world_public_state(world: WorldState) -> dict[str, Any]:
    from wujiang.strategic.battles import BATTLE_RESOLUTION_MODES, BATTLE_UNIT_TROOP_COSTS
    from wujiang.strategic.campaign_runtime import CITY_MONTHLY_ORDER_LIMIT
    from wujiang.strategic.exile import exile_action_choices_public
    from wujiang.strategic.heroes import (
        hero_ritual_capacity,
        strategic_hero_deployment_limit,
        strategic_hero_pool_public,
        strategic_heroes_for_faction_public,
    )
    from wujiang.strategic.objectives import evaluate_strategic_status
    from wujiang.strategic.command import monthly_briefings_public
    from wujiang.strategic.rebellion import rebellion_action_choices_public
    from wujiang.strategic.story import scheduled_consequences_public, story_events_public
    from wujiang.strategic.offices import office_system_public
    from wujiang.strategic.administration import (
        SETTLEMENT_LABELS,
        building_projects_public,
        cannon_stock_cap,
        city_building_max_level,
        city_economy_class,
        city_work_options,
        city_works_public,
        registered_unit_types_public,
        settlement_upgrade_options,
        settlement_upgrades_public,
    )
    from wujiang.strategic.neutral_politics import neutral_city_state_profiles_public
    from wujiang.strategic.diplomacy import diplomacy_cooldown_until, diplomatic_memory_public, faction_diplomacy_public, neutral_diplomatic_agreements_public, neutral_diplomacy_options_public
    from wujiang.strategic.peaceful_integration import peaceful_integration_option
    from wujiang.strategic.occupation import occupation_status_public
    from wujiang.strategic.rebellion import rebellion_funding_option
    from wujiang.strategic.relics import relic_system_public
    from wujiang.strategic.world_crisis import world_crises_public

    payload = world.to_dict()
    # Monthly reports are persisted as the authoritative audit trail. The campaign
    # serializer exposes only the faction-filtered monthly_cycle view.
    payload.pop("monthly_reports", None)
    payload.pop("campaign_tutorial", None)
    payload.pop("relics", None)
    payload.pop("relic_altars", None)
    factions_by_id = {faction.faction_id: faction for faction in world.factions}
    neutral_profiles = neutral_city_state_profiles_public(world)
    for faction_payload, faction in zip(payload["factions"], world.factions):
        if faction.is_world_crisis:
            faction_payload["is_world_crisis"] = True
            faction_payload["tactic_tech_tree"] = []
            faction_payload["strategic_heroes"] = []
            faction_payload["strategic_hero_deployment_limit"] = 0
            faction_payload["hero_ritual_capacity"] = {
                "used": 0,
                "maximum": 0,
                "remaining": 0,
            }
            continue
        faction_payload["tactic_tech_tree"] = tactic_tech_tree_public(faction, world)
        faction_payload["siege_tech"] = siege_tech_bonuses(faction)
        faction_payload["strategic_heroes"] = strategic_heroes_for_faction_public(world, faction.faction_id)
        faction_payload["strategic_hero_deployment_limit"] = strategic_hero_deployment_limit(world, faction.faction_id)
        faction_payload["hero_ritual_capacity"] = hero_ritual_capacity(world, faction.faction_id)
        if faction.is_neutral_city_state:
            profile = neutral_profiles[faction.faction_id]
            for relationship in profile.get("relationships", []):
                relationship["diplomacy_options"] = neutral_diplomacy_options_public(
                    world,
                    actor_faction_id=str(relationship.get("faction_id") or ""),
                    neutral_faction_id=faction.faction_id,
                )
                relationship["incitement_cooldown_until_month"] = diplomacy_cooldown_until(
                    world,
                    str(relationship.get("faction_id") or ""),
                    faction.faction_id,
                    "incite",
                )
                relationship["peaceful_integration"] = peaceful_integration_option(
                    world,
                    actor_faction_id=str(relationship.get("faction_id") or ""),
                    neutral_faction_id=faction.faction_id,
                )
            profile["agreements"] = neutral_diplomatic_agreements_public(world, faction.faction_id)
            profile["diplomatic_memory"] = diplomatic_memory_public(world, faction.faction_id)
            faction_payload["neutral_politics"] = profile
        if faction.is_major:
            faction_payload["faction_diplomacy"] = {
                other.faction_id: faction_diplomacy_public(
                    world,
                    actor_faction_id=faction.faction_id,
                    target_faction_id=other.faction_id,
                )
                for other in world.factions
                if other.faction_id != faction.faction_id and other.is_major
            }
    for city_payload, city in zip(payload["cities"], world.cities):
        faction = factions_by_id[city.owner_faction_id]
        city_payload["troop_conversion"] = city_troop_conversion(city, faction)
        city_payload["settlement_label"] = SETTLEMENT_LABELS.get(str(city.settlement or ""), str(city.settlement or ""))
        city_payload["building_limits"] = {
            project["id"]: city_building_max_level(city, project["id"])
            for project in building_projects_public()
        }
        city_payload["settlement_upgrades"] = settlement_upgrade_options(city)
        city_payload["cannon_stock"] = int(getattr(city, "cannon_stock", 0) or 0)
        city_payload["cannon_stock_cap"] = cannon_stock_cap(city)
        city_payload["economy_class"] = city_economy_class(city)
        city_payload["economy_class_label"] = SETTLEMENT_LABELS.get(city_economy_class(city), city_economy_class(city))
        city_payload["city_works"] = city_work_options(world, city, faction)
        city_payload["occupation_governance"] = occupation_status_public(world, city.city_id)
        city_payload["rebellion_funding_options"] = {
            major.faction_id: rebellion_funding_option(
                world,
                sponsor_faction_id=major.faction_id,
                city_id=city.city_id,
            )
            for major in world.factions
            if major.is_major
        }
    payload["policy_choices"] = sorted(POLICIES)
    payload["battle_resolution_modes"] = sorted(mode for mode in BATTLE_RESOLUTION_MODES if mode != "pending_choice")
    payload["city_monthly_order_limit"] = CITY_MONTHLY_ORDER_LIMIT
    payload["battle_unit_costs"] = dict(BATTLE_UNIT_TROOP_COSTS)
    payload["exile_action_choices"] = exile_action_choices_public()
    payload["rebellion_action_choices"] = rebellion_action_choices_public()
    payload["strategic_hero_pool"] = strategic_hero_pool_public(world)
    payload["strategic_status"] = evaluate_strategic_status(world)
    payload["monthly_briefings"] = monthly_briefings_public(world)
    payload["story_events"] = story_events_public(world)
    payload["scheduled_consequences"] = scheduled_consequences_public(world)
    payload["office_system"] = office_system_public(world)
    payload["building_projects"] = building_projects_public()
    payload["city_works"] = city_works_public()
    payload["settlement_upgrades"] = settlement_upgrades_public()
    payload["registered_unit_types"] = registered_unit_types_public()
    payload["relic_system"] = relic_system_public(world)
    payload["world_crises"] = world_crises_public(world)
    return payload


def set_city_policy(world: WorldState, *, faction_id: str, city_id: str, policy: str) -> WorldState:
    if policy not in POLICIES:
        raise StrategyError(f"未知城市方针：{policy}")
    next_world = _clone_world(world)
    city = _city(next_world, city_id)
    if city.owner_faction_id != faction_id:
        raise StrategyError("只能调整本势力控制城市的方针。")
    if city.policy == policy:
        return next_world
    city.policy = policy
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="city_policy",
            message=f"{city.name}方针调整为{policy}。",
            related_ids=[city.city_id, faction_id],
        )
    )
    next_world.validate()
    return next_world


def _research_months(tech_id: str) -> int:
    return int(TECH_PRESENTATION.get(tech_id, ("military", 1))[1])


def _complete_tactic_tech(world: WorldState, faction: Faction, tech: TacticTech) -> WorldState:
    if tech.tech_id not in faction.tactic_techs:
        faction.tactic_techs.append(tech.tech_id)
    faction.researching = {}
    world.event_log.append(
        EventLogEntry(
            month=world.current_month,
            category="tactic_tech",
            message=f"{faction.name}解锁战术科技：{tech.name}。",
            related_ids=[faction.faction_id, tech.tech_id],
        )
    )
    if tech.office_capacity_effects:
        from wujiang.strategic.offices import ensure_office_system

        return ensure_office_system(world)
    return world


def unlock_tactic_tech(world: WorldState, *, faction_id: str, tech_id: str) -> WorldState:
    tech = TACTIC_TECHS_BY_ID.get(tech_id)
    if tech is None:
        raise StrategyError("战术科技不存在。")
    next_world = _clone_world(world)
    faction = _faction(next_world, faction_id)
    unlocked = set(faction.tactic_techs)
    if tech_id in unlocked:
        raise StrategyError("战术科技已经解锁。")
    missing = [prereq for prereq in tech.prerequisites if prereq not in unlocked]
    if missing:
        raise StrategyError("战术科技前置条件未满足。")
    if not faction_meets_tech_requirements(next_world, faction, tech):
        if tech.required_building:
            from wujiang.strategic.administration import BUILDING_PROJECTS

            building_name = (BUILDING_PROJECTS.get(tech.required_building) or {}).get("name") or tech.required_building
            raise StrategyError(f"研究该科技需要至少一座城市拥有 {tech.required_building_level} 级{building_name}。")
        raise StrategyError("战术科技的城市或建筑条件未满足。")
    current = faction.researching or {}
    current_id = str(current.get("tech_id") or "")
    if current_id and current_id != tech_id:
        raise StrategyError("已有科技正在研究，取消后才能改研其他。")
    if current_id == tech_id:
        raise StrategyError("这门科技已经在研究。")
    if faction.resources.money < tech.money_cost:
        raise StrategyError("资源不足，无法研究战术科技。")

    faction.resources.money -= tech.money_cost
    months = max(1, _research_months(tech_id))
    if months <= 1:
        next_world = _complete_tactic_tech(next_world, faction, tech)
        next_world.validate()
        return next_world

    faction.researching = {
        "tech_id": tech_id,
        "months_done": 1,
        "months_total": months,
        "monthly_money": tech.money_cost,
    }
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="tactic_tech",
            message=f"{faction.name}开始研究：{tech.name}（1/{months}）。",
            related_ids=[faction_id, tech_id],
        )
    )
    next_world.validate()
    return next_world


def cancel_tactic_research(world: WorldState, *, faction_id: str) -> WorldState:
    next_world = _clone_world(world)
    faction = _faction(next_world, faction_id)
    current = faction.researching or {}
    tech_id = str(current.get("tech_id") or "")
    if not tech_id:
        next_world.validate()
        return next_world
    tech = TACTIC_TECHS_BY_ID.get(tech_id)
    faction.researching = {}
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="tactic_tech",
            message=f"{faction.name}取消研究：{tech.name if tech else tech_id}，进度清零。",
            related_ids=[faction_id, tech_id],
        )
    )
    next_world.validate()
    return next_world


def advance_tactic_research(world: WorldState) -> WorldState:
    next_world = _clone_world(world)
    for faction in next_world.factions:
        current = faction.researching or {}
        tech_id = str(current.get("tech_id") or "")
        if not tech_id:
            continue
        tech = TACTIC_TECHS_BY_ID.get(tech_id)
        if tech is None:
            faction.researching = {}
            continue
        monthly = int(current.get("monthly_money") or tech.money_cost)
        if faction.resources.money < monthly:
            next_world.event_log.append(
                EventLogEntry(
                    month=next_world.current_month,
                    category="tactic_tech",
                    message=f"{faction.name}研究{tech.name}因资金不足暂停。",
                    related_ids=[faction.faction_id, tech_id],
                )
            )
            continue
        faction.resources.money -= monthly
        months_done = int(current.get("months_done") or 0) + 1
        months_total = max(1, int(current.get("months_total") or _research_months(tech_id)))
        if months_done >= months_total:
            next_world = _complete_tactic_tech(next_world, faction, tech)
            continue
        faction.researching = {
            "tech_id": tech_id,
            "months_done": months_done,
            "months_total": months_total,
            "monthly_money": monthly,
        }
        next_world.event_log.append(
            EventLogEntry(
                month=next_world.current_month,
                category="tactic_tech",
                message=f"{faction.name}研究{tech.name}（{months_done}/{months_total}）。",
                related_ids=[faction.faction_id, tech_id],
            )
        )
    next_world.validate()
    return next_world
