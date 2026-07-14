from __future__ import annotations

import copy

from wujiang.strategy.models import City, EventLogEntry, StrategyError, WorldState
from wujiang.strategy.objectives import record_strategic_status_events
from wujiang.strategy.rebellion import rebellion_force_troops, set_rebellion_force_troops


POLICIES = {
    "稳定优先",
    "粮食优先",
    "金钱优先",
    "征兵优先",
    "以太优先",
    "城防优先",
    "搜索优先",
    "镇压优先",
    "自治优先",
}


def clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(value)))


def owner_support(city: City) -> int:
    return clamp(city.support_by_faction.get(city.owner_faction_id, 50), 0, 100)


def rebellion_risk(city: City, *, food_shortage: bool = False) -> int:
    support = owner_support(city)
    risk = max(0, 60 - support)
    if food_shortage:
        risk += 20
    if city.resources.troops < max(50, city.resources.population // 30):
        risk += 10
    if city.policy == "镇压优先":
        risk = max(0, risk - 15)
    if city.policy == "自治优先":
        risk = max(0, risk - 10)
    if city.policy == "征兵优先" and support < 50:
        risk += 10
    return clamp(risk, 0, 100)


def _apply_policy(city: City, events: list[EventLogEntry], month: int) -> None:
    if city.policy not in POLICIES:
        raise StrategyError(f"未知城市方针：{city.policy}")

    level = city.level
    support = owner_support(city)
    income_multiplier = 0.75 + support / 200
    food_income = int((80 + city.resources.population // 45) * level * income_multiplier)
    money_income = int((55 + city.resources.population // 60) * level * income_multiplier)
    ether_income = int((8 + level * 4) * income_multiplier)
    troop_growth = int((18 + city.resources.population // 250) * level)
    defense_growth = 0
    food_income += int(city.building_levels.get("fields", 0)) * 60
    ether_income += int(city.building_levels.get("ritual_site", 0)) * 8

    if city.policy == "粮食优先":
        food_income += 80 * level
        money_income = int(money_income * 0.85)
    elif city.policy == "金钱优先":
        money_income += 70 * level
        city.support_by_faction[city.owner_faction_id] = clamp(support - 1, 0, 100)
    elif city.policy == "征兵优先":
        troop_growth += 55 * level
        food_income = int(food_income * 0.9)
        city.support_by_faction[city.owner_faction_id] = clamp(support - 2, 0, 100)
    elif city.policy == "以太优先":
        ether_income += 14 * level
        money_income = int(money_income * 0.9)
    elif city.policy == "城防优先":
        defense_growth = 1
        money_income = int(money_income * 0.85)
    elif city.policy == "稳定优先":
        city.support_by_faction[city.owner_faction_id] = clamp(support + 2, 0, 100)
    elif city.policy == "镇压优先":
        troop_growth = max(0, int(troop_growth * 0.75))
        city.support_by_faction[city.owner_faction_id] = clamp(support - 1, 0, 100)
    elif city.policy == "自治优先":
        money_income = int(money_income * 0.8)
        city.support_by_faction[city.owner_faction_id] = clamp(support + 1, 0, 100)
        city.support_by_faction["local_autonomy"] = clamp(city.support_by_faction.get("local_autonomy", 45) + 2, 0, 100)
    elif city.policy == "搜索优先":
        ether_income += 4 * level

    city.resources.food += food_income
    city.resources.money += money_income
    city.resources.ether += ether_income
    city.resources.troops += troop_growth
    city.defense += defense_growth
    events.append(
        EventLogEntry(
            month=month,
            category="city_income",
            message=(
                f"{city.name}执行{city.policy}：粮食 +{food_income}，金钱 +{money_income}，"
                f"以太 +{ether_income}，兵力 +{troop_growth}。"
            ),
            related_ids=[city.city_id],
        )
    )


def _consume_city_upkeep(city: City, events: list[EventLogEntry], month: int) -> bool:
    food_need = max(1, city.resources.population // 80 + city.resources.troops // 120)
    if city.resources.food >= food_need:
        city.resources.food -= food_need
        return False
    shortage = food_need - city.resources.food
    city.resources.food = 0
    owner_id = city.owner_faction_id
    city.support_by_faction[owner_id] = clamp(city.support_by_faction.get(owner_id, 50) - 5, 0, 100)
    events.append(
        EventLogEntry(
            month=month,
            category="city_crisis",
            message=f"{city.name}粮食不足，缺口 {shortage}，统治支持度下降。",
            related_ids=[city.city_id],
        )
    )
    return True


def _update_rebellion_state(city: City, risk: int, events: list[EventLogEntry], month: int) -> None:
    city.event_states = [
        state
        for state in city.event_states
        if not state.startswith("rebellion_risk:") and not state.startswith("rebellion_crisis:")
    ]
    if risk <= 0:
        return
    stage = "隐患" if risk < 45 else "危机事件" if risk < 75 else "正式叛乱"
    city.event_states.append(f"rebellion_risk:{risk}:{stage}")
    if risk >= 45:
        city.event_states.append(f"rebellion_crisis:month:{month}:risk:{risk}")
        events.append(
            EventLogEntry(
                month=month,
                category="rebellion",
                message=f"{city.name}叛乱风险达到 {risk}，阶段：{stage}。",
                related_ids=[city.city_id],
            )
        )
    if risk >= 75:
        _resolve_rebellion_uprising(city, risk, events, month)


def _resolve_rebellion_uprising(city: City, risk: int, events: list[EventLogEntry], month: int) -> None:
    existing_force = rebellion_force_troops(city)
    rebel_growth = max(60, city.resources.population // 35 + risk * 2 + city.level * 20)
    rebel_cap = max(300, city.resources.population // 4)
    rebel_force = clamp(existing_force + rebel_growth, 0, rebel_cap)
    troop_loss = min(city.resources.troops, max(20, rebel_force // 3))
    food_loss = min(city.resources.food, max(10, rebel_force // 4))
    money_loss = min(city.resources.money, max(10, rebel_force // 5))

    city.resources.troops -= troop_loss
    city.resources.food -= food_loss
    city.resources.money -= money_loss
    city.support_by_faction[city.owner_faction_id] = clamp(
        city.support_by_faction.get(city.owner_faction_id, 50) - 8,
        0,
        100,
    )
    city.support_by_faction["local_autonomy"] = clamp(
        city.support_by_faction.get("local_autonomy", 45) + 8,
        0,
        100,
    )
    set_rebellion_force_troops(city, rebel_force, month=month)
    events.append(
        EventLogEntry(
            month=month,
            category="rebellion_uprising",
            message=(
                f"{city.name}爆发正式叛乱，叛军规模 {rebel_force}，"
                f"守军损失 {troop_loss}，粮食损失 {food_loss}，金钱损失 {money_loss}。"
            ),
            related_ids=[city.city_id, str(rebel_force)],
        )
    )


def advance_month(world: WorldState) -> WorldState:
    next_world = WorldState.from_dict(copy.deepcopy(world.to_dict()))
    from wujiang.strategy.heroes import ensure_strategic_hero_system
    from wujiang.strategy.offices import ensure_office_system

    next_world = ensure_strategic_hero_system(ensure_office_system(next_world))
    next_world.current_month += 1
    month = next_world.current_month
    events: list[EventLogEntry] = [
        EventLogEntry(month=month, category="month", message=f"进入第 {month} 月。")
    ]

    from wujiang.strategy.story import advance_story_events

    next_world = advance_story_events(next_world)

    for city in next_world.cities:
        _apply_policy(city, events, month)
        food_shortage = _consume_city_upkeep(city, events, month)
        risk = rebellion_risk(city, food_shortage=food_shortage)
        _update_rebellion_state(city, risk, events, month)

    next_world.event_log.extend(events)
    next_world.memory_tags.append(f"month_{month}_resolved")
    next_world = record_strategic_status_events(next_world)
    next_world = ensure_strategic_hero_system(ensure_office_system(next_world))
    next_world.validate()
    return next_world
