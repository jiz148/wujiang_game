from __future__ import annotations

from typing import Any

from wujiang.strategy.command import strategy_action_command_cost
from wujiang.strategy.diplomacy import apply_neutral_diplomacy_action, neutral_diplomacy_options_public
from wujiang.strategy.models import City, EventLogEntry, Faction, StrategyError, WorldState
from wujiang.strategy.occupation import apply_occupation_policy, occupation_policy_option
from wujiang.strategy.offices import ai_office_for_action
from wujiang.strategy.peaceful_integration import apply_peaceful_integration, peaceful_integration_option
from wujiang.strategy.rebellion import (
    apply_rebellion_action,
    apply_rebellion_funding,
    rebellion_force_troops,
    rebellion_funding_option,
    validate_rebellion_action,
)
from wujiang.strategy.simulation import rebellion_risk


def _faction(world: WorldState, faction_id: str) -> Faction:
    return next(item for item in world.factions if item.faction_id == faction_id)


def _owned_cities(world: WorldState, faction_id: str) -> list[City]:
    return [city for city in world.cities if city.owner_faction_id == faction_id]


def _neutral_city(world: WorldState, neutral_faction_id: str) -> City | None:
    return next((city for city in world.cities if city.owner_faction_id == neutral_faction_id), None)


def choose_ai_occupation_policy(world: WorldState, faction_id: str) -> tuple[str, str, str] | None:
    faction = _faction(world, faction_id)
    pending = sorted(
        (city for city in _owned_cities(world, faction_id) if city.occupation.get("status") == "pending"),
        key=lambda city: (int(city.occupation.get("captured_month") or 0), city.city_id),
    )
    if not pending:
        return None
    city = pending[0]
    support = int(city.support_by_faction.get(faction_id, 35))
    autonomy = int(city.support_by_faction.get("local_autonomy", 45))
    options = {
        policy_id: occupation_policy_option(world, faction_id=faction_id, city_id=city.city_id, policy_id=policy_id)
        for policy_id in ("autonomy", "integration", "garrison", "plunder")
    }
    priorities: list[tuple[str, str]] = []
    if support <= 35 or autonomy >= 65:
        priorities.append(("autonomy", "当地支持薄弱，优先以自治降低叛乱风险"))
    if faction.resources.money < 80 or faction.resources.food < 80:
        priorities.append(("plunder", "国家资源濒临枯竭，选择掠夺换取即时资源"))
    if city.resources.troops >= 300 and support < 50:
        priorities.append(("garrison", "守军充足但统治不稳，选择驻军压低风险"))
    if faction.resources.money >= 220 and faction.resources.food >= 180 and support >= 40:
        priorities.append(("integration", "资源充足且当地基础尚可，选择整合建立长期统治"))
    priorities.extend([
        ("autonomy", "以最低政治风险完成占领过渡"),
        ("garrison", "依靠现有守军维持占领秩序"),
        ("integration", "投入资源推进长期整合"),
        ("plunder", "缺少其他合法方案，抽取资源后承担高风险"),
    ])
    for policy_id, reason in priorities:
        if options[policy_id]["can_choose"]:
            return city.city_id, policy_id, reason
    return None


def choose_ai_rebellion_action(world: WorldState, faction_id: str) -> tuple[str, str, str] | None:
    candidates: list[tuple[int, str, City]] = []
    for city in _owned_cities(world, faction_id):
        force = rebellion_force_troops(city)
        risk = rebellion_risk(city, food_shortage=city.resources.food <= 0)
        if force <= 0 and risk < 45:
            continue
        candidates.append((force * 2 + risk, city.city_id, city))
    if not candidates:
        return None
    _, _, city = max(candidates, key=lambda item: (item[0], item[1]))
    faction = _faction(world, faction_id)
    force = rebellion_force_troops(city)
    owner_support = int(city.support_by_faction.get(faction_id, 50))
    priorities: list[tuple[str, str]] = []
    if force > 0 and owner_support <= 20 and faction.resources.money >= 60:
        priorities.append(("negotiate", "统治支持已接近倒戈门槛，优先谈判止损"))
    if force > 0 and city.resources.troops >= 120 and city.resources.troops >= max(120, force // 2):
        priorities.append(("suppress", "守军足以削弱叛军，选择镇压恢复短期秩序"))
    if force > 0 and faction.resources.money >= 60:
        priorities.append(("negotiate", "兵力不足以稳妥清剿，支付金钱换取停火"))
    if force > 0:
        priorities.append(("grant_autonomy", "资源不足，授予自治避免叛乱继续扩大"))
    else:
        if city.resources.troops >= max(150, city.resources.population // 25):
            return None
        if faction.resources.money >= 80:
            priorities.append(("appease", "危机尚未形成叛军，先投入金钱安抚"))
        if city.resources.food >= 120:
            priorities.append(("relief_grain", "以本地粮食缓和尚未武装化的不满"))
        if city.resources.troops < max(150, city.resources.population // 25):
            priorities.append(("grant_autonomy", "守军与安抚资源都不足，以地方自治降低危机"))
    for action_id, reason in priorities:
        try:
            validate_rebellion_action(world, faction_id=faction_id, action_id=action_id, city_id=city.city_id)
        except StrategyError:
            continue
        return city.city_id, action_id, reason
    return None


def choose_ai_peaceful_integration(world: WorldState, faction_id: str) -> tuple[str, str] | None:
    candidates: list[tuple[int, str]] = []
    for neutral in world.factions:
        if not neutral.is_neutral_city_state or _neutral_city(world, neutral.faction_id) is None:
            continue
        option = peaceful_integration_option(world, actor_faction_id=faction_id, neutral_faction_id=neutral.faction_id)
        if not option["can_integrate"]:
            continue
        city = _neutral_city(world, neutral.faction_id)
        assert city is not None
        score = (
            int(neutral.relations.get(faction_id, 0))
            + int(neutral.influence_by_faction.get(faction_id, 0))
            + int(city.support_by_faction.get(faction_id, 35))
        )
        candidates.append((score, neutral.faction_id))
    if not candidates:
        return None
    _, neutral_faction_id = max(candidates, key=lambda item: (item[0], item[1]))
    return neutral_faction_id, "关系、影响力、当地支持与履约记录均已成熟，完成和平整合"


def choose_ai_neutral_diplomacy(
    world: WorldState,
    faction_id: str,
    strategic_goal: dict[str, Any] | None,
) -> tuple[str, str, str] | None:
    targets: list[tuple[int, str, Faction, City, dict[str, dict[str, Any]]]] = []
    for neutral in world.factions:
        if not neutral.is_neutral_city_state:
            continue
        city = _neutral_city(world, neutral.faction_id)
        if city is None:
            continue
        options = {
            option["id"]: option
            for option in neutral_diplomacy_options_public(
                world,
                actor_faction_id=faction_id,
                neutral_faction_id=neutral.faction_id,
            )
        }
        if not any(option["can_propose"] for option in options.values()):
            continue
        relation = int(neutral.relations.get(faction_id, 0))
        influence = int(neutral.influence_by_faction.get(faction_id, 0))
        support = int(city.support_by_faction.get(faction_id, 35))
        active_or_fulfilled = any(
            agreement.major_faction_id == faction_id
            and agreement.neutral_faction_id == neutral.faction_id
            and (agreement.status == "active" or agreement.end_reason == "fulfilled")
            for agreement in world.diplomatic_agreements
        )
        focus_score = relation * 2 + influence * 2 + support + (40 if active_or_fulfilled else 0)
        targets.append((focus_score, neutral.faction_id, neutral, city, options))
    if not targets:
        return None
    goal_type = str((strategic_goal or {}).get("goal_type") or "")
    ordered_targets = sorted(targets, key=lambda item: (-item[0], item[1]))
    actor = _faction(world, faction_id)
    peace_focus = next((
        item
        for item in ordered_targets
        if int(item[2].relations.get(faction_id, 0)) >= 50
        and int(item[2].influence_by_faction.get(faction_id, 0)) >= 50
        and int(item[3].support_by_faction.get(faction_id, 35)) >= 60
    ), None)
    if peace_focus is not None and actor.resources.money < 100 and actor.diplomatic_reputation >= 52:
        for _, neutral_id, _, _, options in reversed(ordered_targets):
            if neutral_id == peace_focus[1]:
                continue
            if options["demand_tribute"]["can_propose"] and options["demand_tribute"]["expected_accepted"]:
                return neutral_id, "demand_tribute", "和平整合所需国库不足，向已受威慑的外围城邦索取贡金"
            if options["intimidate"]["can_propose"] and options["intimidate"]["expected_accepted"]:
                return neutral_id, "intimidate", "和平整合所需国库不足，以边境优势威慑外围城邦筹备资源"
    for _, neutral_id, neutral, city, options in ordered_targets:
        relation = int(neutral.relations.get(faction_id, 0))
        influence = int(neutral.influence_by_faction.get(faction_id, 0))
        local_support = int(city.support_by_faction.get(faction_id, 35))
        active_agreement = any(
            agreement.major_faction_id == faction_id
            and agreement.neutral_faction_id == neutral_id
            and agreement.status == "active"
            for agreement in world.diplomatic_agreements
        )
        agreement_history = any(
            agreement.major_faction_id == faction_id and agreement.neutral_faction_id == neutral_id
            for agreement in world.diplomatic_agreements
        )
        priorities: list[tuple[str, str]] = []
        if relation >= 50 and influence >= 50 and local_support >= 60 and actor.resources.money < 160:
            priorities.append(("non_aggression", "和平整合已接近门槛，保留国库并用无成本协议补足信任"))
        if goal_type in {"border_defense", "stabilize_unrest"} and relation < -20:
            priorities.append(("non_aggression", "边境压力较高，即使关系恶化仍尝试最低限度停战"))
        if goal_type == "capture_city" and options["intimidate"]["pressure_ratio"] >= 0.9 and relation < 15:
            priorities.append(("intimidate", "扩张目标与边境优势使威慑成为当前最直接方案"))
        if goal_type == "stabilize_food":
            priorities.append(("trade", "当前战略目标需要粮食，优先尝试互市"))
        if relation < 15:
            priorities.append(("aid", "先用援助建立关系、影响力与当地支持"))
        if not agreement_history and relation >= 15:
            priorities.append(("non_aggression", "已有基本信任，先建立可履约的互不侵犯协议"))
            priorities.append(("protection", "城邦面临威胁，尝试建立保护协议"))
        if active_agreement or agreement_history:
            priorities.append(("aid", "围绕既有承诺继续积累关系与当地支持"))
            priorities.append(("trade", "在承诺期内通过互市继续扩大影响"))
        priorities.extend([
            ("aid", "以援助推进长期和平路线"),
            ("trade", "以互市交换资源并改善关系"),
            ("non_aggression", "尝试降低边境冲突风险"),
            ("protection", "尝试回应城邦安全需求"),
            ("intimidate", "缺少互利方案，转用可信边境压力"),
            ("demand_tribute", "利用既有威慑索取短期资源"),
        ])
        for action_id, reason in priorities:
            if options[action_id]["can_propose"]:
                return neutral_id, action_id, reason
    return None


def choose_ai_rebellion_funding(
    world: WorldState,
    faction_id: str,
    strategic_goal: dict[str, Any] | None,
) -> tuple[str, str] | None:
    goal_type = str((strategic_goal or {}).get("goal_type") or "")
    candidates: list[tuple[int, str]] = []
    for city in world.cities:
        if city.owner_faction_id == faction_id:
            continue
        owner = _faction(world, city.owner_faction_id)
        if owner.is_neutral_city_state:
            continue
        option = rebellion_funding_option(world, sponsor_faction_id=faction_id, city_id=city.city_id)
        if not option["can_fund"]:
            continue
        sponsor_support = int(city.support_by_faction.get(faction_id, 35))
        owner_support = int(city.support_by_faction.get(city.owner_faction_id, 50))
        force = rebellion_force_troops(city)
        occupation_active = city.occupation.get("status") in {"pending", "active"}
        if not (force > 0 or owner_support <= 30 or sponsor_support >= 45 or (occupation_active and goal_type == "capture_city")):
            continue
        score = force + sponsor_support * 3 - owner_support * 2 + (80 if occupation_active else 0)
        candidates.append((score, city.city_id))
    if not candidates:
        return None
    _, city_id = max(candidates, key=lambda item: (item[0], item[1]))
    return city_id, "敌城占领或叛乱危机已成熟，资助反抗以争取未来倒戈"


def ai_peace_treasury_reserve(world: WorldState, faction_id: str) -> int:
    """Keep enough treasury to finish a focused diplomacy route instead of stranding it behind tech spending."""
    for neutral in world.factions:
        if not neutral.is_neutral_city_state or _neutral_city(world, neutral.faction_id) is None:
            continue
        if int(neutral.relations.get(faction_id, 0)) > 0 or int(neutral.influence_by_faction.get(faction_id, 0)) > 0:
            return 200
    return 0


def _record_decision(
    world: WorldState,
    *,
    faction_id: str,
    action: str,
    reason: str,
    related_ids: list[str],
) -> None:
    faction = _faction(world, faction_id)
    world.event_log.append(EventLogEntry(
        month=world.current_month,
        category="strategy_ai_political_decision",
        message=f"{faction.name}作出政治决策：{action}。原因：{reason}。",
        related_ids=[faction_id, *related_ids],
    ))


def apply_major_political_ai_actions(
    world: WorldState,
    *,
    faction_id: str,
    command_remaining: int,
    attack_reserve: int,
    strategic_goal: dict[str, Any] | None,
) -> tuple[WorldState, int, list[str], list[str]]:
    next_world = world
    actions: list[str] = []
    office_actions: list[str] = []

    occupation = choose_ai_occupation_policy(next_world, faction_id)
    if occupation is not None and command_remaining >= 1:
        city_id, policy_id, reason = occupation
        office = ai_office_for_action(next_world, faction_id=faction_id, action_type="choose_occupation_policy", payload={"city_id": city_id})
        if office is not None:
            next_world = apply_occupation_policy(next_world, faction_id=faction_id, city_id=city_id, policy_id=policy_id)
            action = f"occupation:{city_id}:{policy_id}"
            actions.append(action)
            office_actions.append(f"{office.office_id}:{action}")
            command_remaining -= 1
            _record_decision(next_world, faction_id=faction_id, action=action, reason=reason, related_ids=[city_id, policy_id])

    rebellion = choose_ai_rebellion_action(next_world, faction_id)
    if rebellion is not None:
        city_id, action_id, reason = rebellion
        cost = strategy_action_command_cost("rebellion_action", {"rebellion_action_id": action_id})
        office = ai_office_for_action(next_world, faction_id=faction_id, action_type="rebellion_action", payload={"city_id": city_id})
        if office is not None and command_remaining >= cost:
            next_world = apply_rebellion_action(next_world, faction_id=faction_id, action_id=action_id, city_id=city_id)
            action = f"rebellion:{city_id}:{action_id}"
            actions.append(action)
            office_actions.append(f"{office.office_id}:{action}")
            command_remaining -= cost
            _record_decision(next_world, faction_id=faction_id, action=action, reason=reason, related_ids=[city_id, action_id])

    integration = choose_ai_peaceful_integration(next_world, faction_id)
    if integration is not None and command_remaining >= 2:
        neutral_id, reason = integration
        office = ai_office_for_action(next_world, faction_id=faction_id, action_type="peaceful_integration", payload={"neutral_faction_id": neutral_id})
        if office is not None:
            next_world = apply_peaceful_integration(next_world, actor_faction_id=faction_id, neutral_faction_id=neutral_id)
            action = f"peaceful_integration:{neutral_id}"
            actions.append(action)
            office_actions.append(f"{office.office_id}:{action}")
            command_remaining -= 2
            _record_decision(next_world, faction_id=faction_id, action=action, reason=reason, related_ids=[neutral_id])

    diplomacy = choose_ai_neutral_diplomacy(next_world, faction_id, strategic_goal)
    if diplomacy is not None and command_remaining - 1 >= attack_reserve:
        neutral_id, action_id, reason = diplomacy
        office = ai_office_for_action(next_world, faction_id=faction_id, action_type="neutral_diplomacy", payload={"neutral_faction_id": neutral_id})
        if office is not None:
            next_world = apply_neutral_diplomacy_action(
                next_world,
                actor_faction_id=faction_id,
                neutral_faction_id=neutral_id,
                action_id=action_id,
            )
            action = f"diplomacy:{neutral_id}:{action_id}"
            actions.append(action)
            office_actions.append(f"{office.office_id}:{action}")
            command_remaining -= 1
            _record_decision(next_world, faction_id=faction_id, action=action, reason=reason, related_ids=[neutral_id, action_id])

    funding = choose_ai_rebellion_funding(next_world, faction_id, strategic_goal)
    if funding is not None and command_remaining - 1 >= attack_reserve:
        city_id, reason = funding
        office = ai_office_for_action(next_world, faction_id=faction_id, action_type="fund_rebellion", payload={"city_id": city_id})
        if office is not None:
            next_world = apply_rebellion_funding(next_world, sponsor_faction_id=faction_id, city_id=city_id)
            action = f"fund_rebellion:{city_id}"
            actions.append(action)
            office_actions.append(f"{office.office_id}:{action}")
            command_remaining -= 1
            _record_decision(next_world, faction_id=faction_id, action=action, reason=reason, related_ids=[city_id])

    return next_world, command_remaining, actions, office_actions
