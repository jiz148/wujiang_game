from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from wujiang.strategy.models import City, EventLogEntry, StrategyError, WorldState


def _clamp(value: int, minimum: int = 0, maximum: int = 100) -> int:
    return max(minimum, min(maximum, int(value)))


@dataclass(frozen=True, slots=True)
class OccupationPolicy:
    policy_id: str
    name: str
    summary: str
    income_percent: int
    rebellion_modifier: int
    money_cost: int = 0
    food_cost: int = 0
    minimum_garrison: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.policy_id,
            "name": self.name,
            "summary": self.summary,
            "income_percent": self.income_percent,
            "rebellion_modifier": self.rebellion_modifier,
            "money_cost": self.money_cost,
            "food_cost": self.food_cost,
            "minimum_garrison": self.minimum_garrison,
            "duration_settlements": 3,
        }


OCCUPATION_POLICIES: tuple[OccupationPolicy, ...] = (
    OccupationPolicy("autonomy", "自治", "让地方保留权力，以较低产出换取快速降压。", 75, -20),
    OccupationPolicy("integration", "整合", "投入国家资源重建统治，逐月提高支持。", 90, 5, money_cost=100, food_cost=80),
    OccupationPolicy("garrison", "驻军", "依靠至少 150 守军维持秩序，但增加粮耗并损伤支持。", 85, -25, minimum_garrison=150),
    OccupationPolicy("plunder", "掠夺", "立即抽取资源，代价是长期低产出、高叛乱和城防受损。", 60, 30),
)
OCCUPATION_POLICIES_BY_ID = {item.policy_id: item for item in OCCUPATION_POLICIES}


def _clone_world(world: WorldState) -> WorldState:
    return WorldState.from_dict(copy.deepcopy(world.to_dict()))


def _city(world: WorldState, city_id: str) -> City:
    city = next((item for item in world.cities if item.city_id == str(city_id)), None)
    if city is None:
        raise StrategyError("城市不存在。")
    return city


def mark_city_captured(
    world: WorldState,
    *,
    city_id: str,
    previous_owner_faction_id: str,
    occupier_faction_id: str,
) -> None:
    city = _city(world, city_id)
    city.occupation = {
        "status": "pending",
        "captured_month": world.current_month,
        "previous_owner_faction_id": previous_owner_faction_id,
        "occupier_faction_id": occupier_faction_id,
        "policy_id": "",
        "selected_month": None,
        "settlements_completed": 0,
        "outcome": "",
    }
    city.support_by_faction["local_autonomy"] = _clamp(city.support_by_faction.get("local_autonomy", 45) + 15)
    tag = f"occupation:pending:{world.current_month}:{city.city_id}:{occupier_faction_id}"
    if tag not in world.memory_tags:
        world.memory_tags.append(tag)
    world.event_log.append(EventLogEntry(
        month=world.current_month,
        category="occupation_policy_required",
        message=f"{city.name}已被武力攻占，必须选择自治、整合、驻军或掠夺；未决期间产出减半且叛乱风险上升。",
        related_ids=[city.city_id, previous_owner_faction_id, occupier_faction_id],
    ))


def occupation_income_multiplier(city: City) -> float:
    status = str(city.occupation.get("status") or "")
    if status == "pending":
        return 0.5
    if status == "active":
        policy = OCCUPATION_POLICIES_BY_ID.get(str(city.occupation.get("policy_id") or ""))
        return (policy.income_percent / 100) if policy else 1.0
    return 1.0


def occupation_rebellion_modifier(city: City) -> int:
    status = str(city.occupation.get("status") or "")
    if status == "pending":
        return 30
    if status == "active":
        policy = OCCUPATION_POLICIES_BY_ID.get(str(city.occupation.get("policy_id") or ""))
        return policy.rebellion_modifier if policy else 0
    return 0


def occupation_policy_option(world: WorldState, *, faction_id: str, city_id: str, policy_id: str) -> dict[str, Any]:
    city = _city(world, city_id)
    policy = OCCUPATION_POLICIES_BY_ID.get(str(policy_id))
    if policy is None:
        raise StrategyError("占领政策不存在。")
    faction = next((item for item in world.factions if item.faction_id == faction_id), None)
    if faction is None:
        raise StrategyError("势力不存在。")
    blocked = ""
    if city.owner_faction_id != faction_id:
        blocked = "只能处理本势力占领的城市。"
    elif str(city.occupation.get("status") or "") != "pending":
        blocked = "该城市没有待处理的武力占领状态。"
    elif faction.resources.money < policy.money_cost:
        blocked = f"势力金钱不足 {policy.money_cost}。"
    elif faction.resources.food < policy.food_cost:
        blocked = f"势力粮食不足 {policy.food_cost}。"
    elif city.resources.troops < policy.minimum_garrison:
        blocked = f"城市守军不足 {policy.minimum_garrison}。"
    return {**policy.to_dict(), "can_choose": not blocked, "blocked_reason": blocked, "command_cost": 1}


def occupation_status_public(world: WorldState, city_id: str) -> dict[str, Any]:
    city = _city(world, city_id)
    if not city.occupation:
        return {}
    policy = OCCUPATION_POLICIES_BY_ID.get(str(city.occupation.get("policy_id") or ""))
    return {
        **dict(city.occupation),
        "policy_label": policy.name if policy else "待选择",
        "income_percent": round(occupation_income_multiplier(city) * 100),
        "rebellion_modifier": occupation_rebellion_modifier(city),
        "remaining_settlements": max(0, 3 - int(city.occupation.get("settlements_completed") or 0)) if city.occupation.get("status") == "active" else None,
        "policy_choices": [
            occupation_policy_option(world, faction_id=city.owner_faction_id, city_id=city.city_id, policy_id=item.policy_id)
            for item in OCCUPATION_POLICIES
        ] if city.occupation.get("status") == "pending" else [],
    }


def validate_occupation_policy(world: WorldState, *, faction_id: str, city_id: str, policy_id: str) -> None:
    option = occupation_policy_option(world, faction_id=faction_id, city_id=city_id, policy_id=policy_id)
    if not option["can_choose"]:
        raise StrategyError(str(option["blocked_reason"]))


def apply_occupation_policy(world: WorldState, *, faction_id: str, city_id: str, policy_id: str) -> WorldState:
    validate_occupation_policy(world, faction_id=faction_id, city_id=city_id, policy_id=policy_id)
    next_world = _clone_world(world)
    city = _city(next_world, city_id)
    faction = next(item for item in next_world.factions if item.faction_id == faction_id)
    policy = OCCUPATION_POLICIES_BY_ID[policy_id]
    faction.resources.money -= policy.money_cost
    faction.resources.food -= policy.food_cost
    support = city.support_by_faction.get(faction_id, 35)
    autonomy = city.support_by_faction.get("local_autonomy", 45)
    if policy_id == "autonomy":
        support, autonomy = support + 8, autonomy + 15
    elif policy_id == "integration":
        support, autonomy = support + 15, autonomy - 10
    elif policy_id == "garrison":
        support, autonomy = support - 5, autonomy - 15
    else:
        money_taken = city.resources.money * 40 // 100
        food_taken = city.resources.food * 25 // 100
        city.resources.money -= money_taken
        city.resources.food -= food_taken
        faction.resources.money += money_taken
        faction.resources.food += food_taken
        city.defense = max(0, city.defense - 1)
        support, autonomy = support - 25, autonomy + 20
        city.occupation["plundered_money"] = money_taken
        city.occupation["plundered_food"] = food_taken
    city.support_by_faction[faction_id] = _clamp(support)
    city.support_by_faction["local_autonomy"] = _clamp(autonomy)
    city.occupation.update({
        "status": "active",
        "policy_id": policy_id,
        "selected_month": next_world.current_month,
        "settlements_completed": 0,
        "outcome": "",
    })
    next_world.event_log.append(EventLogEntry(
        month=next_world.current_month,
        category="occupation_policy_selected",
        message=f"{faction.name}对{city.name}选择{policy.name}：产出 {policy.income_percent}%，叛乱风险修正 {policy.rebellion_modifier:+d}。",
        related_ids=[faction_id, city.city_id, policy_id],
    ))
    next_world.memory_tags.append(f"occupation:{policy_id}:{next_world.current_month}:{city.city_id}:{faction_id}")
    next_world.validate()
    return next_world


def apply_occupation_month_start(city: City, *, month: int, events: list[EventLogEntry]) -> None:
    status = str(city.occupation.get("status") or "")
    if status == "pending":
        city.support_by_faction[city.owner_faction_id] = _clamp(city.support_by_faction.get(city.owner_faction_id, 35) - 2)
        city.support_by_faction["local_autonomy"] = _clamp(city.support_by_faction.get("local_autonomy", 45) + 4)
        return
    if status != "active":
        return
    policy_id = str(city.occupation.get("policy_id") or "")
    support_delta, autonomy_delta = {
        "autonomy": (2, 2),
        "integration": (3, -3),
        "garrison": (-1, -2),
        "plunder": (-3, 3),
    }.get(policy_id, (0, 0))
    city.support_by_faction[city.owner_faction_id] = _clamp(city.support_by_faction.get(city.owner_faction_id, 35) + support_delta)
    city.support_by_faction["local_autonomy"] = _clamp(city.support_by_faction.get("local_autonomy", 45) + autonomy_delta)
    if policy_id == "garrison":
        city.resources.food = max(0, city.resources.food - 20)
    city.occupation["settlements_completed"] = int(city.occupation.get("settlements_completed") or 0) + 1
    events.append(EventLogEntry(
        month=month,
        category="occupation_monthly_effect",
        message=f"{city.name}的{OCCUPATION_POLICIES_BY_ID[policy_id].name}占领政策完成第 {city.occupation['settlements_completed']}/3 次月结。",
        related_ids=[city.city_id, city.owner_faction_id, policy_id],
    ))


def finish_occupation_month(city: City, *, month: int, events: list[EventLogEntry]) -> None:
    if city.occupation.get("status") != "active" or int(city.occupation.get("settlements_completed") or 0) < 3:
        return
    city.occupation["status"] = "settled"
    city.occupation["settled_month"] = month
    events.append(EventLogEntry(
        month=month,
        category="occupation_settled",
        message=f"{city.name}的占领过渡已经结束，城市进入常态治理。",
        related_ids=[city.city_id, city.owner_faction_id, str(city.occupation.get("policy_id") or "")],
    ))
