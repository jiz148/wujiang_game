from __future__ import annotations

import copy
from typing import Any

from wujiang.strategy.models import EventLogEntry, Faction, StrategyError, WorldState
from wujiang.strategy.neutral_city_states import adjacent_city_ids, faction_by_id


PEACEFUL_INTEGRATION_MONEY_COST = 100
PEACEFUL_INTEGRATION_FOOD_COST = 80


def _clone_world(world: WorldState) -> WorldState:
    return WorldState.from_dict(copy.deepcopy(world.to_dict()))


def neutral_city(world: WorldState, neutral_faction_id: str):
    return next((city for city in world.cities if city.owner_faction_id == neutral_faction_id), None)


def adjust_neutral_influence(
    world: WorldState,
    *,
    major_faction_id: str,
    neutral_faction_id: str,
    influence_delta: int = 0,
    support_delta: int = 0,
) -> None:
    neutral = faction_by_id(world, neutral_faction_id)
    if not neutral.is_neutral_city_state:
        raise StrategyError("影响力只能作用于中立城邦。")
    neutral.influence_by_faction[major_faction_id] = max(
        0,
        min(100, neutral.influence_by_faction.get(major_faction_id, 0) + int(influence_delta)),
    )
    city = neutral_city(world, neutral_faction_id)
    if city is not None:
        city.support_by_faction[major_faction_id] = max(
            0,
            min(100, city.support_by_faction.get(major_faction_id, 35) + int(support_delta)),
        )


def _fulfilled_agreement(world: WorldState, major_faction_id: str, neutral_faction_id: str) -> bool:
    return any(
        item.major_faction_id == major_faction_id
        and item.neutral_faction_id == neutral_faction_id
        and item.status == "ended"
        and item.end_reason == "fulfilled"
        for item in world.diplomatic_agreements
    )


def peaceful_integration_option(
    world: WorldState,
    *,
    actor_faction_id: str,
    neutral_faction_id: str,
) -> dict[str, Any]:
    actor = faction_by_id(world, actor_faction_id)
    neutral = faction_by_id(world, neutral_faction_id)
    if actor.is_neutral_city_state:
        raise StrategyError("中立城邦不能整合其他城邦。")
    if not neutral.is_neutral_city_state:
        raise StrategyError("和平整合只能以中立城邦为目标。")
    city = neutral_city(world, neutral.faction_id)
    relation = int(neutral.relations.get(actor.faction_id, 0))
    influence = int(neutral.influence_by_faction.get(actor.faction_id, 0))
    support = int(city.support_by_faction.get(actor.faction_id, 35)) if city is not None else 0
    adjacent = bool(city and any(
        other.owner_faction_id == actor.faction_id
        for other in world.cities
        if other.city_id in adjacent_city_ids(world, city.city_id)
    ))
    fulfilled = _fulfilled_agreement(world, actor.faction_id, neutral.faction_id)
    requirements = [
        {"id": "city", "label": "城邦仍保持自治", "met": city is not None, "current": "是" if city else "否", "required": "是"},
        {"id": "adjacent", "label": "与我方接壤", "met": adjacent, "current": "是" if adjacent else "否", "required": "是"},
        {"id": "relation", "label": "关系", "met": relation >= 60, "current": relation, "required": 60},
        {"id": "influence", "label": "影响力", "met": influence >= 60, "current": influence, "required": 60},
        {"id": "support", "label": "当地支持", "met": support >= 60, "current": support, "required": 60},
        {"id": "reputation", "label": "外交信誉", "met": actor.diplomatic_reputation >= 50, "current": actor.diplomatic_reputation, "required": 50},
        {"id": "fulfilled_agreement", "label": "曾完整履行协议", "met": fulfilled, "current": "是" if fulfilled else "否", "required": "是"},
        {"id": "not_incited", "label": "当前未受教唆", "met": neutral.incited_against_faction_id is None, "current": "是" if neutral.incited_against_faction_id is None else "否", "required": "是"},
        {"id": "money", "label": "势力金钱", "met": actor.resources.money >= PEACEFUL_INTEGRATION_MONEY_COST, "current": actor.resources.money, "required": PEACEFUL_INTEGRATION_MONEY_COST},
        {"id": "food", "label": "势力粮食", "met": actor.resources.food >= PEACEFUL_INTEGRATION_FOOD_COST, "current": actor.resources.food, "required": PEACEFUL_INTEGRATION_FOOD_COST},
    ]
    blockers = [f"{item['label']}未达成（当前 {item['current']}，需要 {item['required']}）" for item in requirements if not item["met"]]
    return {
        "id": "peaceful_integration",
        "name": "和平整合",
        "description": "在长期互信与地方支持成熟后，由城主保留治理职责并无战斗并入我方。",
        "command_cost": 2,
        "resource_cost": {"money": PEACEFUL_INTEGRATION_MONEY_COST, "food": PEACEFUL_INTEGRATION_FOOD_COST},
        "can_integrate": not blockers,
        "blocked_reason": "；".join(blockers),
        "requirements": requirements,
    }


def validate_peaceful_integration(world: WorldState, *, actor_faction_id: str, neutral_faction_id: str) -> None:
    option = peaceful_integration_option(
        world,
        actor_faction_id=actor_faction_id,
        neutral_faction_id=neutral_faction_id,
    )
    if not option["can_integrate"]:
        raise StrategyError(str(option["blocked_reason"]))


def apply_peaceful_integration(world: WorldState, *, actor_faction_id: str, neutral_faction_id: str) -> WorldState:
    validate_peaceful_integration(world, actor_faction_id=actor_faction_id, neutral_faction_id=neutral_faction_id)
    next_world = _clone_world(world)
    actor = faction_by_id(next_world, actor_faction_id)
    neutral = faction_by_id(next_world, neutral_faction_id)
    city = neutral_city(next_world, neutral.faction_id)
    assert city is not None
    actor.resources.money -= PEACEFUL_INTEGRATION_MONEY_COST
    actor.resources.food -= PEACEFUL_INTEGRATION_FOOD_COST
    city.owner_faction_id = actor.faction_id
    city.support_by_faction[actor.faction_id] = max(70, city.support_by_faction.get(actor.faction_id, 35))
    if "和平整合" not in city.traits:
        city.traits.append("和平整合")
    neutral.capital_city_id = None
    neutral.relations[actor.faction_id] = 100
    neutral.influence_by_faction[actor.faction_id] = 100
    neutral.diplomacy[actor.faction_id] = "peacefully_integrated"
    neutral.incited_against_faction_id = None
    neutral.incited_by_faction_id = None
    for agreement in next_world.diplomatic_agreements:
        if agreement.major_faction_id == actor.faction_id and agreement.neutral_faction_id == neutral.faction_id and agreement.status == "active":
            agreement.status = "ended"
            agreement.end_reason = "peaceful_integration"
            agreement.ended_month = next_world.current_month

    from wujiang.strategy.diplomacy import record_diplomatic_memory

    record_diplomatic_memory(
        next_world,
        category="peaceful_integration",
        major=actor,
        neutral=neutral,
        summary=f"{neutral.name}在保留城主管理的前提下和平并入{actor.name}。",
        action_id="peaceful_integration",
    )
    next_world.event_log.append(EventLogEntry(
        month=next_world.current_month,
        category="neutral_city_state_peacefully_integrated",
        message=f"{neutral.name}在长期互信与地方支持下和平并入{actor.name}，原城主继续管理{city.name}。",
        related_ids=[actor.faction_id, neutral.faction_id, city.city_id],
    ))
    next_world.validate()
    return next_world
