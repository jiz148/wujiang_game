from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from wujiang.strategy.models import City, EventLogEntry, Faction, StrategyError, WorldState
from wujiang.strategy.simulation import POLICIES


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
)

TACTIC_TECHS_BY_ID = {tech.tech_id: tech for tech in TACTIC_TECH_TREE}

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


def tactic_tech_tree_public(faction: Faction) -> list[dict[str, Any]]:
    unlocked = set(faction.tactic_techs)
    payload: list[dict[str, Any]] = []
    for tech in TACTIC_TECH_TREE:
        item = tech.to_dict()
        item["unlocked"] = tech.tech_id in unlocked
        item["available"] = tech.tech_id not in unlocked and all(prereq in unlocked for prereq in tech.prerequisites)
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
    from wujiang.strategy.battles import BATTLE_RESOLUTION_MODES
    from wujiang.strategy.exile import exile_action_choices_public
    from wujiang.strategy.heroes import (
        hero_ritual_capacity,
        strategic_hero_deployment_limit,
        strategic_hero_pool_public,
        strategic_heroes_for_faction_public,
    )
    from wujiang.strategy.objectives import evaluate_strategic_status
    from wujiang.strategy.command import monthly_briefings_public
    from wujiang.strategy.rebellion import rebellion_action_choices_public
    from wujiang.strategy.story import scheduled_consequences_public, story_events_public
    from wujiang.strategy.offices import office_system_public
    from wujiang.strategy.administration import building_projects_public, registered_unit_types_public
    from wujiang.strategy.neutral_politics import neutral_city_state_profiles_public
    from wujiang.strategy.diplomacy import diplomacy_cooldown_until, diplomatic_memory_public, neutral_diplomatic_agreements_public, neutral_diplomacy_options_public
    from wujiang.strategy.peaceful_integration import peaceful_integration_option
    from wujiang.strategy.occupation import occupation_status_public
    from wujiang.strategy.rebellion import rebellion_funding_option

    payload = world.to_dict()
    # Monthly reports are persisted as the authoritative audit trail. The campaign
    # serializer exposes only the faction-filtered monthly_cycle view.
    payload.pop("monthly_reports", None)
    payload.pop("campaign_tutorial", None)
    factions_by_id = {faction.faction_id: faction for faction in world.factions}
    neutral_profiles = neutral_city_state_profiles_public(world)
    for faction_payload, faction in zip(payload["factions"], world.factions):
        faction_payload["tactic_tech_tree"] = tactic_tech_tree_public(faction)
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
    for city_payload, city in zip(payload["cities"], world.cities):
        faction = factions_by_id[city.owner_faction_id]
        city_payload["troop_conversion"] = city_troop_conversion(city, faction)
        city_payload["building_limits"] = {
            project["id"]: building_max_level(faction, project["id"])
            for project in building_projects_public()
        }
        city_payload["occupation_governance"] = occupation_status_public(world, city.city_id)
        city_payload["rebellion_funding_options"] = {
            major.faction_id: rebellion_funding_option(
                world,
                sponsor_faction_id=major.faction_id,
                city_id=city.city_id,
            )
            for major in world.factions
            if not major.is_neutral_city_state
        }
    payload["policy_choices"] = sorted(POLICIES)
    payload["battle_resolution_modes"] = sorted(BATTLE_RESOLUTION_MODES)
    payload["exile_action_choices"] = exile_action_choices_public()
    payload["rebellion_action_choices"] = rebellion_action_choices_public()
    payload["strategic_hero_pool"] = strategic_hero_pool_public(world)
    payload["strategic_status"] = evaluate_strategic_status(world)
    payload["monthly_briefings"] = monthly_briefings_public(world)
    payload["story_events"] = story_events_public(world)
    payload["scheduled_consequences"] = scheduled_consequences_public(world)
    payload["office_system"] = office_system_public(world)
    payload["building_projects"] = building_projects_public()
    payload["registered_unit_types"] = registered_unit_types_public()
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
    if faction.resources.money < tech.money_cost or faction.resources.ether < tech.ether_cost:
        raise StrategyError("资源不足，无法解锁战术科技。")

    faction.resources.money -= tech.money_cost
    faction.resources.ether -= tech.ether_cost
    faction.tactic_techs.append(tech_id)
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="tactic_tech",
            message=f"{faction.name}解锁战术科技：{tech.name}。",
            related_ids=[faction_id, tech_id],
        )
    )
    if tech.office_capacity_effects:
        from wujiang.strategy.offices import ensure_office_system

        next_world = ensure_office_system(next_world)
    next_world.validate()
    return next_world
