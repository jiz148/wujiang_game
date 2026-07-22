from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from wujiang.strategy.models import DiplomaticAgreement, EventLogEntry, Faction, StrategyError, WorldState
from wujiang.strategy.neutral_city_states import adjacent_city_ids, faction_by_id
from wujiang.strategy.neutral_politics import neutral_city_state_profile


@dataclass(frozen=True, slots=True)
class NeutralDiplomacyAction:
    action_id: str
    name: str
    description: str
    money_cost: int = 0
    food_cost: int = 0
    troop_cost: int = 0


NEUTRAL_DIPLOMACY_ACTIONS: tuple[NeutralDiplomacyAction, ...] = (
    NeutralDiplomacyAction("aid", "提供援助", "转移粮食与金钱，直接改善城邦处境和关系。", money_cost=60, food_cost=80),
    NeutralDiplomacyAction("trade", "互市贸易", "支付金钱换取城邦粮食，双方都得到即时资源。", money_cost=40),
    NeutralDiplomacyAction("protection", "提供保护", "先行调拨金钱与兵力，建立保护协议。", money_cost=40, troop_cost=60),
    NeutralDiplomacyAction("non_aggression", "互不侵犯", "建立最低限度的和平协议，不额外消耗资源。"),
    NeutralDiplomacyAction("demand_tribute", "索取贡金", "以边境压力要求城邦缴纳金钱；可能被拒绝。"),
    NeutralDiplomacyAction("intimidate", "武力威慑", "展示边境实力以影响后续交涉；实力不足会遭到拒绝。"),
)
NEUTRAL_DIPLOMACY_ACTIONS_BY_ID = {item.action_id: item for item in NEUTRAL_DIPLOMACY_ACTIONS}
AGREEMENT_DURATION_MONTHS = 3
FRIENDLY_DIPLOMACY_COOLDOWN_MONTHS = 2
COERCIVE_DIPLOMACY_COOLDOWN_MONTHS = 3


def diplomacy_cooldown_key(major_faction_id: str, neutral_faction_id: str, action_id: str) -> str:
    return f"{major_faction_id}:{neutral_faction_id}:{action_id}"


def diplomacy_cooldown_until(world: WorldState, major_faction_id: str, neutral_faction_id: str, action_id: str) -> int:
    return int(world.diplomatic_cooldowns.get(diplomacy_cooldown_key(major_faction_id, neutral_faction_id, action_id), 0))


def set_diplomacy_cooldown(world: WorldState, major_faction_id: str, neutral_faction_id: str, action_id: str) -> None:
    months = COERCIVE_DIPLOMACY_COOLDOWN_MONTHS if action_id in {"demand_tribute", "intimidate", "incite"} else FRIENDLY_DIPLOMACY_COOLDOWN_MONTHS
    world.diplomatic_cooldowns[diplomacy_cooldown_key(major_faction_id, neutral_faction_id, action_id)] = world.current_month + months


def record_diplomatic_memory(
    world: WorldState, *, category: str, major: Faction, neutral: Faction, summary: str,
    action_id: str = "", agreement_id: str = "", reputation_delta: int = 0, relation_delta: int = 0,
) -> None:
    major.diplomatic_reputation = max(0, min(100, major.diplomatic_reputation + reputation_delta))
    if relation_delta:
        neutral.relations[major.faction_id] = max(-100, min(100, neutral.relations.get(major.faction_id, 0) + relation_delta))
    item = {
        "id": f"diplomatic-memory:{world.current_month}:{len(world.diplomatic_memory) + 1}",
        "month": world.current_month,
        "category": category,
        "major_faction_id": major.faction_id,
        "neutral_faction_id": neutral.faction_id,
        "action_id": action_id,
        "agreement_id": agreement_id,
        "summary": summary,
        "reputation_delta": reputation_delta,
        "relation_delta": relation_delta,
    }
    world.diplomatic_memory.append(item)
    world.diplomatic_memory[:] = world.diplomatic_memory[-80:]
    tag = f"diplomacy:{category}:{world.current_month}:{major.faction_id}:{neutral.faction_id}"
    world.memory_tags.append(tag)
    major.memory_tags.append(tag)
    neutral.memory_tags.append(tag)


def _clone_world(world: WorldState) -> WorldState:
    return WorldState.from_dict(copy.deepcopy(world.to_dict()))


def _neutral_city(world: WorldState, neutral: Faction):
    return next((city for city in world.cities if city.owner_faction_id == neutral.faction_id), None)


def _actor_border_strength(world: WorldState, actor: Faction, neutral_city_id: str) -> int:
    adjacent = adjacent_city_ids(world, neutral_city_id)
    return sum(
        city.resources.troops + city.defense * 40
        for city in world.cities
        if city.city_id in adjacent and city.owner_faction_id == actor.faction_id
    )


def _active_agreement(world: WorldState, major_faction_id: str, neutral_faction_id: str, agreement_type: str) -> DiplomaticAgreement | None:
    return next((
        item for item in world.diplomatic_agreements
        if item.major_faction_id == major_faction_id
        and item.neutral_faction_id == neutral_faction_id
        and item.agreement_type == agreement_type
        and item.status == "active"
    ), None)


def _agreement_public(world: WorldState, agreement: DiplomaticAgreement) -> dict[str, Any]:
    labels = {"protection": "保护协议", "non_aggression": "互不侵犯"}
    reason_labels = {"fulfilled": "已履约", "treaty_breach": "主动违约", "protection_failed": "保护失败", "peaceful_integration": "和平整合"}
    remaining = max(0, int(agreement.expires_month or world.current_month) - world.current_month) if agreement.status == "active" else 0
    return {
        **agreement.to_dict(),
        "label": labels.get(agreement.agreement_type, agreement.agreement_type),
        "remaining_months": remaining,
        "end_reason_label": reason_labels.get(str(agreement.end_reason or ""), ""),
    }


def neutral_diplomatic_agreements_public(world: WorldState, neutral_faction_id: str) -> list[dict[str, Any]]:
    return [
        _agreement_public(world, item)
        for item in world.diplomatic_agreements
        if item.neutral_faction_id == neutral_faction_id
    ][-8:]


def diplomatic_memory_public(world: WorldState, neutral_faction_id: str) -> list[dict[str, Any]]:
    return [dict(item) for item in world.diplomatic_memory if item.get("neutral_faction_id") == neutral_faction_id][-8:]


def neutral_diplomacy_option(
    world: WorldState,
    *,
    actor_faction_id: str,
    neutral_faction_id: str,
    action_id: str,
) -> dict[str, Any]:
    action = NEUTRAL_DIPLOMACY_ACTIONS_BY_ID.get(str(action_id))
    if action is None:
        raise StrategyError("未知中立外交行动。")
    actor = faction_by_id(world, actor_faction_id)
    neutral = faction_by_id(world, neutral_faction_id)
    if actor.is_neutral_city_state:
        raise StrategyError("中立城邦不能代表主要势力提出外交行动。")
    if not neutral.is_neutral_city_state:
        raise StrategyError("普通中立外交只能以中立城邦为目标。")
    city = _neutral_city(world, neutral)
    relation = max(-100, min(100, int(neutral.relations.get(actor.faction_id, 0))))
    cooldown_until = diplomacy_cooldown_until(world, actor.faction_id, neutral.faction_id, action.action_id)
    blocked_reason = ""
    if city is None:
        blocked_reason = "该城邦已经失去城市，无法进行本地交涉。"
    elif world.current_month < cooldown_until:
        blocked_reason = f"该项交涉冷却至第 {cooldown_until} 月。"
    elif not any(
        other.owner_faction_id == actor.faction_id
        for other in world.cities
        if other.city_id in adjacent_city_ids(world, city.city_id)
    ):
        blocked_reason = "必须先与该中立城邦接壤。"
    elif actor.resources.money < action.money_cost:
        blocked_reason = f"势力金钱不足 {action.money_cost}。"
    elif actor.resources.food < action.food_cost:
        blocked_reason = f"势力粮食不足 {action.food_cost}。"
    elif actor.resources.troops < action.troop_cost:
        blocked_reason = f"势力兵力不足 {action.troop_cost}。"
    elif action.action_id == "trade" and city.resources.food < 80:
        blocked_reason = "该城邦没有足够粮食完成贸易。"
    elif action.action_id == "protection" and _active_agreement(world, actor.faction_id, neutral.faction_id, "protection"):
        blocked_reason = "双方已经存在保护协议。"
    elif action.action_id == "non_aggression" and _active_agreement(world, actor.faction_id, neutral.faction_id, "non_aggression"):
        blocked_reason = "双方已经存在互不侵犯协议。"
    elif action.action_id in {"demand_tribute", "intimidate"} and _active_agreement(world, actor.faction_id, neutral.faction_id, "protection"):
        blocked_reason = "不能威慑或索贡自己的保护对象。"
    elif action.action_id in {"demand_tribute", "intimidate"} and _active_agreement(world, actor.faction_id, neutral.faction_id, "non_aggression"):
        blocked_reason = "现有互不侵犯协议禁止威慑或索贡。"

    profile = neutral_city_state_profile(world, neutral.faction_id)
    border_strength = _actor_border_strength(world, actor, city.city_id) if city is not None else 0
    neutral_strength = city.resources.troops + city.defense * 40 if city is not None else 1
    pressure_ratio = round(border_strength / max(1, neutral_strength), 2)
    intimidated = neutral.diplomacy.get(actor.faction_id) == "intimidated"
    accepted = True
    response_reason = "城主接受这项互利提议。"
    direct_effect = ""
    if action.action_id == "aid":
        direct_effect = "势力粮 -80、钱 -60；城邦粮 +80、钱 +60；关系 +18。"
    elif action.action_id == "trade":
        accepted = relation >= -20
        response_reason = "关系尚可，城主愿意互市。" if accepted else "关系过低，城主拒绝开放互市。"
        direct_effect = "接受：势力钱 -40、粮 +80；城邦钱 +40、粮 -80；关系 +8。" if accepted else "拒绝：只消耗本月 1 军令。"
    elif action.action_id == "protection":
        accepted = relation >= 0 and (
            profile.get("current_need", {}).get("id") == "protection"
            or float(profile.get("factors", {}).get("threat_ratio", 0)) >= 0.75
            or relation >= 20
        )
        accepted = accepted and actor.diplomatic_reputation >= 30
        response_reason = (
            "提议方信誉过低，城主不再相信其保护承诺。"
            if actor.diplomatic_reputation < 30
            else ("城邦需要制衡或已信任提议方。" if accepted else "城主尚不认为有必要交出安全承诺。")
        )
        direct_effect = "接受：势力钱 -40、兵 -60；城邦兵 +60；关系 +15，并建立保护协议。" if accepted else "拒绝：只消耗本月 1 军令。"
    elif action.action_id == "non_aggression":
        accepted = relation >= -20 and neutral.incited_against_faction_id != actor.faction_id and actor.diplomatic_reputation >= 30
        response_reason = (
            "提议方信誉过低，城主不相信其停战承诺。"
            if actor.diplomatic_reputation < 30
            else ("当前关系足以维持最低和平。" if accepted else "敌意或现有攻击意图使城主拒绝签署。")
        )
        direct_effect = "接受：关系 +6，并建立互不侵犯协议。" if accepted else "拒绝：只消耗本月 1 军令。"
    elif action.action_id == "demand_tribute":
        accepted = pressure_ratio >= 1.15 or intimidated
        response_reason = "城主判断拒绝的军事风险过高。" if accepted else "边境压力不足以迫使城邦缴纳贡金。"
        tribute = min(70, city.resources.money) if city is not None else 0
        direct_effect = f"接受：城邦向势力支付 {tribute} 金钱；关系 -18。" if accepted else "拒绝：关系 -8。"
    elif action.action_id == "intimidate":
        accepted = pressure_ratio >= 0.9
        response_reason = "边境兵力足以形成可信威慑。" if accepted else "城主识破了缺乏实力支撑的威胁。"
        direct_effect = "成功：留下受威慑状态；关系 -12。" if accepted else "失败：关系 -5。"

    return {
        "id": action.action_id,
        "name": action.name,
        "description": action.description,
        "command_cost": 1,
        "resource_cost": {"money": action.money_cost, "food": action.food_cost, "troops": action.troop_cost},
        "can_propose": not blocked_reason,
        "blocked_reason": blocked_reason,
        "expected_accepted": accepted if not blocked_reason else False,
        "expected_response": "接受" if accepted and not blocked_reason else "拒绝",
        "response_reason": blocked_reason or response_reason,
        "direct_effect": direct_effect,
        "pressure_ratio": pressure_ratio,
        "cooldown_until_month": cooldown_until,
        "actor_reputation": actor.diplomatic_reputation,
    }


def neutral_diplomacy_options_public(world: WorldState, *, actor_faction_id: str, neutral_faction_id: str) -> list[dict[str, Any]]:
    return [
        neutral_diplomacy_option(
            world,
            actor_faction_id=actor_faction_id,
            neutral_faction_id=neutral_faction_id,
            action_id=action.action_id,
        )
        for action in NEUTRAL_DIPLOMACY_ACTIONS
    ]


def validate_neutral_diplomacy_action(world: WorldState, *, actor_faction_id: str, neutral_faction_id: str, action_id: str) -> None:
    option = neutral_diplomacy_option(
        world,
        actor_faction_id=actor_faction_id,
        neutral_faction_id=neutral_faction_id,
        action_id=action_id,
    )
    if not option["can_propose"]:
        raise StrategyError(str(option["blocked_reason"]))


def apply_neutral_diplomacy_action(
    world: WorldState,
    *,
    actor_faction_id: str,
    neutral_faction_id: str,
    action_id: str,
) -> WorldState:
    validate_neutral_diplomacy_action(
        world,
        actor_faction_id=actor_faction_id,
        neutral_faction_id=neutral_faction_id,
        action_id=action_id,
    )
    preview = neutral_diplomacy_option(
        world,
        actor_faction_id=actor_faction_id,
        neutral_faction_id=neutral_faction_id,
        action_id=action_id,
    )
    next_world = _clone_world(world)
    actor = faction_by_id(next_world, actor_faction_id)
    neutral = faction_by_id(next_world, neutral_faction_id)
    city = _neutral_city(next_world, neutral)
    assert city is not None
    accepted = bool(preview["expected_accepted"])
    relation_delta = 0
    if action_id == "aid":
        actor.resources.food -= 80
        actor.resources.money -= 60
        city.resources.food += 80
        city.resources.money += 60
        relation_delta = 18
    elif action_id == "trade" and accepted:
        actor.resources.money -= 40
        actor.resources.food += 80
        city.resources.money += 40
        city.resources.food -= 80
        relation_delta = 8
    elif action_id == "protection" and accepted:
        actor.resources.money -= 40
        actor.resources.troops -= 60
        city.resources.troops += 60
        relation_delta = 15
        next_world.diplomatic_agreements.append(DiplomaticAgreement(
            agreement_id=f"agreement:{next_world.current_month}:{actor.faction_id}:{neutral.faction_id}:protection",
            agreement_type="protection",
            major_faction_id=actor.faction_id,
            neutral_faction_id=neutral.faction_id,
            started_month=next_world.current_month,
            expires_month=next_world.current_month + AGREEMENT_DURATION_MONTHS,
            terms={"initial_troops": 60, "initial_money": 40},
        ))
    elif action_id == "non_aggression" and accepted:
        relation_delta = 6
        next_world.diplomatic_agreements.append(DiplomaticAgreement(
            agreement_id=f"agreement:{next_world.current_month}:{actor.faction_id}:{neutral.faction_id}:non_aggression",
            agreement_type="non_aggression",
            major_faction_id=actor.faction_id,
            neutral_faction_id=neutral.faction_id,
            started_month=next_world.current_month,
            expires_month=next_world.current_month + AGREEMENT_DURATION_MONTHS,
        ))
    elif action_id == "demand_tribute":
        relation_delta = -18 if accepted else -8
        if accepted:
            tribute = min(70, city.resources.money)
            city.resources.money -= tribute
            actor.resources.money += tribute
    elif action_id == "intimidate":
        relation_delta = -12 if accepted else -5
        if accepted:
            neutral.diplomacy[actor.faction_id] = "intimidated"

    neutral.relations[actor.faction_id] = max(-100, min(100, neutral.relations.get(actor.faction_id, 0) + relation_delta))
    from wujiang.strategy.peaceful_integration import adjust_neutral_influence

    influence_effects = {
        "aid": (18, 10),
        "trade": (10, 5) if accepted else (0, 0),
        "protection": (12, 6) if accepted else (0, 0),
        "non_aggression": (5, 3) if accepted else (0, 0),
        "demand_tribute": (-10, -5),
        "intimidate": (-10, -5),
    }
    influence_delta, support_delta = influence_effects[action_id]
    adjust_neutral_influence(
        next_world,
        major_faction_id=actor.faction_id,
        neutral_faction_id=neutral.faction_id,
        influence_delta=influence_delta,
        support_delta=support_delta,
    )
    set_diplomacy_cooldown(next_world, actor.faction_id, neutral.faction_id, action_id)
    reputation_delta = 2 if action_id == "aid" else (-2 if action_id in {"demand_tribute", "intimidate"} else 0)
    record_diplomatic_memory(
        next_world,
        category="negotiation_accepted" if accepted else "negotiation_refused",
        major=actor,
        neutral=neutral,
        summary=f"{actor.name}提出{NEUTRAL_DIPLOMACY_ACTIONS_BY_ID[action_id].name}，城主{'接受' if accepted else '拒绝'}。",
        action_id=action_id,
        reputation_delta=reputation_delta,
    )
    action = NEUTRAL_DIPLOMACY_ACTIONS_BY_ID[action_id]
    next_world.event_log.append(EventLogEntry(
        month=next_world.current_month,
        category="neutral_diplomacy_accepted" if accepted else "neutral_diplomacy_refused",
        message=f"{actor.name}向{neutral.name}提出{action.name}：城主{'接受' if accepted else '拒绝'}。{preview['direct_effect']}",
        related_ids=[actor.faction_id, neutral.faction_id, action_id],
    ))
    next_world.validate()
    return next_world


def advance_diplomacy_month(world: WorldState) -> WorldState:
    """Resolve active promises after the strategic month has advanced."""
    next_world = _clone_world(world)
    factions = {item.faction_id: item for item in next_world.factions}
    for agreement in next_world.diplomatic_agreements:
        if agreement.status != "active":
            continue
        major = factions[agreement.major_faction_id]
        neutral = factions[agreement.neutral_faction_id]
        breached = any(
            battle.attacker_faction_id == major.faction_id
            and battle.defender_faction_id == neutral.faction_id
            and battle.month >= agreement.started_month
            for battle in next_world.pending_battles
        )
        neutral_has_city = any(city.owner_faction_id == neutral.faction_id for city in next_world.cities)
        category = ""
        summary = ""
        reputation_delta = 0
        relation_delta = 0
        if breached:
            agreement.status = "broken"
            agreement.end_reason = "treaty_breach"
            category = "treaty_breach"
            reputation_delta = -25 if agreement.agreement_type == "protection" else -15
            relation_delta = -25
            summary = f"{major.name}主动攻击{neutral.name}，撕毁了{_agreement_public(next_world, agreement)['label']}。"
        elif agreement.agreement_type == "protection" and not neutral_has_city:
            agreement.status = "broken"
            agreement.end_reason = "protection_failed"
            category = "protection_failed"
            reputation_delta = -20
            relation_delta = -25
            summary = f"{major.name}未能保住{neutral.name}，保护承诺失败。"
        elif agreement.expires_month is not None and next_world.current_month >= agreement.expires_month:
            agreement.status = "ended"
            agreement.end_reason = "fulfilled"
            category = "agreement_fulfilled"
            reputation_delta = 8 if agreement.agreement_type == "protection" else 4
            relation_delta = 10 if agreement.agreement_type == "protection" else 5
            summary = f"{major.name}完整履行了对{neutral.name}的{_agreement_public(next_world, agreement)['label']}。"
        if not category:
            continue
        agreement.ended_month = next_world.current_month
        record_diplomatic_memory(
            next_world, category=category, major=major, neutral=neutral, summary=summary,
            agreement_id=agreement.agreement_id, reputation_delta=reputation_delta, relation_delta=relation_delta,
        )
        if category == "agreement_fulfilled":
            from wujiang.strategy.peaceful_integration import adjust_neutral_influence

            influence_delta, support_delta = (12, 6) if agreement.agreement_type == "protection" else (6, 3)
            adjust_neutral_influence(
                next_world,
                major_faction_id=major.faction_id,
                neutral_faction_id=neutral.faction_id,
                influence_delta=influence_delta,
                support_delta=support_delta,
            )
        next_world.event_log.append(EventLogEntry(
            month=next_world.current_month, category=category, message=summary,
            related_ids=[major.faction_id, neutral.faction_id, agreement.agreement_id],
        ))
    next_world.validate()
    return next_world
