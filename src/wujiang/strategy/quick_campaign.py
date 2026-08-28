from __future__ import annotations

import copy
from typing import Any

from wujiang.strategy.models import EventLogEntry, StrategyError, WorldState


QUICK_CAMPAIGN_SCENARIO_ID = "city_states_six_months_quick_v1"
QUICK_CAMPAIGN_CITY_COUNT = 5
QUICK_CAMPAIGN_MAJOR_FACTION_COUNT = 2
QUICK_CAMPAIGN_NEUTRAL_CITY_STATE_COUNT = 3
QUICK_CAMPAIGN_MONTH_LIMIT = 6
QUICK_CAMPAIGN_CONTENT_VERSION = "r1-content-2"
QUICK_CAMPAIGN_BALANCE_VERSION = "r1-balance-2"
QUICK_OPENING_CHOICE_TAG_PREFIX = "quick_campaign_opening_choice:"

QUICK_OPENING_CHOICES: tuple[dict[str, Any], ...] = (
    {
        "id": "stabilize",
        "name": "先稳住民心",
        "pitch": "把第一月变成可靠的内政起点，降低后续缺粮与动乱压力。",
        "effect_summary": "主城粮食 +140，己方民心 +15。",
        "result_summary": "主城粮仓得到补充，民心明显回升。",
        "next_step": "接下来查看主城月度预测，再决定屯田或征兵。",
    },
    {
        "id": "diplomacy",
        "name": "抢先结交城邦",
        "pitch": "提前建立一条和平扩张路线，让六个月内的外交投入更快见效。",
        "effect_summary": "最近中立城邦关系 +25、影响力 +20、当地支持 +10。",
        "result_summary": "使者已经抵达最近的中立城邦，和平整合路线获得先手。",
        "next_step": "接下来向该城邦提供援助或贸易，继续累积关系与影响力。",
    },
    {
        "id": "mobilize",
        "name": "立即整军备战",
        "pitch": "把第一场冲突提前到战役前段，快速形成可执行的进攻窗口。",
        "effect_summary": "主城兵力 +180，势力粮食 +100。",
        "result_summary": "新军已经在主城集结，前线粮草也已备妥。",
        "next_step": "接下来查看相邻城市兵力，选择进攻、增兵或组建军队。",
    },
)


def quick_campaign_contract() -> dict[str, Any]:
    return {
        "id": QUICK_CAMPAIGN_SCENARIO_ID,
        "name": "六个月边境决断",
        "experience_kind": "quick_campaign",
        "content_version": QUICK_CAMPAIGN_CONTENT_VERSION,
        "balance_version": QUICK_CAMPAIGN_BALANCE_VERSION,
        "city_count": QUICK_CAMPAIGN_CITY_COUNT,
        "major_faction_count": QUICK_CAMPAIGN_MAJOR_FACTION_COUNT,
        "neutral_city_state_count": QUICK_CAMPAIGN_NEUTRAL_CITY_STATE_COUNT,
        "month_limit": QUICK_CAMPAIGN_MONTH_LIMIT,
        "expected_duration_minutes": [25, 35],
        "core_question": "六个月内，你要靠民心、外交还是战争取得边境优势？",
        "assessment_label": "六月评议",
        "initial_role": "lord",
        "recommended_decision_count": 3,
        "available_victory_routes": [
            "unify_cities",
            "eliminate_enemy_factions",
            "peaceful_integration",
            "time_limit_assessment",
        ],
        "locked_systems": ["world_mainline", "relic_altar"],
    }


def is_quick_campaign(world: WorldState) -> bool:
    return str(world.campaign_contract.get("id") or "") == QUICK_CAMPAIGN_SCENARIO_ID


def apply_quick_campaign_setup(world: WorldState) -> WorldState:
    """Shape the compact map once without introducing a second combat ruleset."""
    if not is_quick_campaign(world) or "quick_campaign_setup" in world.memory_tags:
        return world
    next_world = WorldState.from_dict(copy.deepcopy(world.to_dict()))
    for faction in next_world.factions:
        if faction.is_neutral_city_state:
            faction.resources.troops = min(faction.resources.troops, 140)
    for city in next_world.cities:
        owner = next((item for item in next_world.factions if item.faction_id == city.owner_faction_id), None)
        if owner is not None and owner.is_neutral_city_state:
            city.resources.troops = min(city.resources.troops, 320)
            city.defense = min(city.defense, 5)
        elif owner is not None and owner.is_major and city.city_id == owner.capital_city_id:
            city.resources.troops = max(city.resources.troops, 420)
    nodes = {node.node_id: node for node in next_world.nodes}
    neutral_cities = sorted(
        (
            city
            for city in next_world.cities
            if next((faction for faction in next_world.factions if faction.faction_id == city.owner_faction_id), None)
            and next(faction for faction in next_world.factions if faction.faction_id == city.owner_faction_id).is_neutral_city_state
        ),
        key=lambda city: city.city_id,
    )
    major_factions = sorted((faction for faction in next_world.factions if faction.is_major), key=lambda faction: faction.faction_id)
    for index, faction in enumerate(major_factions):
        capital = next((city for city in next_world.cities if city.city_id == faction.capital_city_id), None)
        neutral_city = neutral_cities[index % len(neutral_cities)] if neutral_cities else None
        capital_node = nodes.get(capital.node_id) if capital is not None else None
        neutral_node = nodes.get(neutral_city.node_id) if neutral_city is not None else None
        if capital_node is None or neutral_node is None:
            continue
        if neutral_node.node_id not in capital_node.connected_node_ids:
            capital_node.connected_node_ids.append(neutral_node.node_id)
        if capital_node.node_id not in neutral_node.connected_node_ids:
            neutral_node.connected_node_ids.append(capital_node.node_id)
        capital_node.connected_node_ids.sort()
        neutral_node.connected_node_ids.sort()
    next_world.memory_tags.append("quick_campaign_setup")
    next_world.event_log.append(
        EventLogEntry(
            month=1,
            category="quick_campaign_setup",
            message="六个月边境决断开始：圣物与世界危机暂不启用；主城已连接最近城邦，边境防务按紧凑战役标准调整。",
            visibility="player_visible",
        )
    )
    next_world.validate()
    return next_world


def _choice_tag(faction_id: str, choice_id: str) -> str:
    return f"{QUICK_OPENING_CHOICE_TAG_PREFIX}{faction_id}:{choice_id}"


def _selected_choice_id(world: WorldState, faction_id: str) -> str:
    prefix = f"{QUICK_OPENING_CHOICE_TAG_PREFIX}{faction_id}:"
    tag = next((item for item in world.memory_tags if item.startswith(prefix)), "")
    return tag[len(prefix):] if tag else ""


def _faction(world: WorldState, faction_id: str):
    faction = next((item for item in world.factions if item.faction_id == str(faction_id)), None)
    if faction is None or not faction.is_major:
        raise StrategyError("快速战役的开局抉择只属于主要势力。")
    return faction


def _capital_city(world: WorldState, faction_id: str):
    faction = _faction(world, faction_id)
    city = next((item for item in world.cities if item.city_id == faction.capital_city_id), None)
    if city is None:
        raise StrategyError("当前势力没有可执行开局抉择的主城。")
    return city


def _nearest_neutral_city(world: WorldState, faction_id: str):
    capital = _capital_city(world, faction_id)
    node_by_id = {node.node_id: node for node in world.nodes}
    distance_by_node = {capital.node_id: 0}
    frontier = [capital.node_id]
    while frontier:
        node_id = frontier.pop(0)
        for neighbor_id in node_by_id.get(node_id).connected_node_ids if node_by_id.get(node_id) else ():
            if neighbor_id in distance_by_node:
                continue
            distance_by_node[neighbor_id] = distance_by_node[node_id] + 1
            frontier.append(neighbor_id)
    neutral_ids = {faction.faction_id for faction in world.factions if faction.is_neutral_city_state}
    candidates = [city for city in world.cities if city.owner_faction_id in neutral_ids]
    if not candidates:
        raise StrategyError("当前战役没有可结交的中立城邦。")
    return min(candidates, key=lambda city: (distance_by_node.get(city.node_id, 999), city.city_id))


def quick_campaign_opening_status(world: WorldState, faction_id: str) -> dict[str, Any] | None:
    if not is_quick_campaign(world):
        return None
    faction = _faction(world, faction_id)
    capital = _capital_city(world, faction_id)
    neutral_city = _nearest_neutral_city(world, faction_id)
    neutral_faction = next(item for item in world.factions if item.faction_id == neutral_city.owner_faction_id)
    selected_choice_id = _selected_choice_id(world, faction_id)
    choices = []
    for raw in QUICK_OPENING_CHOICES:
        choice = dict(raw)
        choice["target_city_id"] = neutral_city.city_id if choice["id"] == "diplomacy" else capital.city_id
        choice["target_city_name"] = neutral_city.name if choice["id"] == "diplomacy" else capital.name
        choice["selected"] = choice["id"] == selected_choice_id
        choices.append(choice)
    selected = next((item for item in choices if item["selected"]), None)
    return {
        "available": not selected_choice_id and world.current_month == 1,
        "faction_id": faction.faction_id,
        "faction_name": faction.name,
        "capital_city_id": capital.city_id,
        "capital_city_name": capital.name,
        "neutral_city_id": neutral_city.city_id,
        "neutral_city_name": neutral_city.name,
        "neutral_faction_id": neutral_faction.faction_id,
        "selected_choice_id": selected_choice_id or None,
        "selected_choice": selected,
        "choices": choices,
    }


def _action_dict(action: Any) -> dict[str, Any]:
    if isinstance(action, dict):
        return action
    if hasattr(action, "to_dict"):
        return action.to_dict()
    return {}


def _action_is_planned(
    actions: list[dict[str, Any]],
    action_type: str,
    payload: dict[str, Any],
    keys: tuple[str, ...],
) -> bool:
    return any(
        action.get("action_type") == action_type
        and all(str((action.get("payload") or {}).get(key) or "") == str(payload.get(key) or "") for key in keys)
        for action in actions
    )


def _quick_governance_recommendation(
    world: WorldState,
    faction_id: str,
    opening_choice_id: str,
    actions: list[dict[str, Any]],
) -> dict[str, Any]:
    capital = _capital_city(world, faction_id)
    policy_by_opening = {
        "stabilize": "稳定优先",
        "diplomacy": "金钱优先",
        "mobilize": "征兵优先",
    }
    policy = policy_by_opening.get(opening_choice_id, "稳定优先")
    governor = next(
        (
            office
            for office in world.offices
            if office.faction_id == faction_id
            and office.office_type == "governor"
            and office.status == "active"
            and capital.city_id in office.managed_entity_ids
        ),
        None,
    )
    support = int(capital.support_by_faction.get(faction_id, 50))
    if capital.policy == policy:
        return {
            "kind": "governance",
            "title": f"{capital.name}已执行{policy}",
            "detail": f"当前粮食 {capital.resources.food}、民心 {support}；无需重复花费军令。",
            "city_id": capital.city_id,
            "target_city_id": capital.city_id,
            "planned": True,
            "resolved": True,
            "available": True,
            "button_label": "当前方针已就绪",
            "recommended_action": None,
        }
    payload = {
        "receiver_office_id": governor.office_id if governor is not None else "",
        "office_order_type": "set_policy",
        "objective": f"将{capital.name}设为{policy}",
        "target_entity_id": capital.city_id,
        "priority": 2,
        "deadline_month": world.current_month + 1,
        "city_policy": policy,
    }
    planned = bool(governor) and _action_is_planned(
        actions,
        "issue_office_order",
        payload,
        ("receiver_office_id", "office_order_type", "target_entity_id", "city_policy"),
    )
    return {
        "kind": "governance",
        "title": f"让{capital.name}转为{policy}",
        "detail": f"委托直属城主在月结执行；当前粮食 {capital.resources.food}、民心 {support}。",
        "city_id": capital.city_id,
        "target_city_id": capital.city_id,
        "planned": planned,
        "resolved": False,
        "available": governor is not None,
        "blocked_reason": "主城当前没有可接令的直属城主。" if governor is None else "",
        "button_label": "委托城主 · 1 军令",
        "recommended_action": {
            "action_type": "issue_office_order",
            "command_cost": 1,
            "payload": payload,
        } if governor is not None else None,
    }


def _quick_diplomacy_recommendation(
    world: WorldState,
    faction_id: str,
    actions: list[dict[str, Any]],
) -> dict[str, Any]:
    from wujiang.strategy.diplomacy import neutral_diplomacy_options_public

    try:
        city = _nearest_neutral_city(world, faction_id)
    except StrategyError:
        return {
            "kind": "diplomacy",
            "title": "中立城邦已全部退出角逐",
            "detail": "外交扩张窗口已经关闭，把军令投入治理或军事行动。",
            "city_id": "",
            "target_city_id": "",
            "planned": False,
            "resolved": True,
            "available": False,
            "blocked_reason": "当前没有可交涉的中立城邦。",
            "button_label": "暂无外交目标",
            "recommended_action": None,
        }
    neutral = next(item for item in world.factions if item.faction_id == city.owner_faction_id)
    options = neutral_diplomacy_options_public(
        world,
        actor_faction_id=faction_id,
        neutral_faction_id=neutral.faction_id,
    )
    preference = {"aid": 0, "trade": 1, "protection": 2, "non_aggression": 3, "intimidate": 4, "demand_tribute": 5}
    available = sorted(
        (item for item in options if item.get("can_propose") and item.get("expected_accepted")),
        key=lambda item: (preference.get(str(item.get("id") or ""), 99), str(item.get("id") or "")),
    )
    if not available:
        available = sorted(
            (item for item in options if item.get("can_propose")),
            key=lambda item: (preference.get(str(item.get("id") or ""), 99), str(item.get("id") or "")),
        )
    option = available[0] if available else None
    relation = int(neutral.relations.get(faction_id, 0))
    influence = int(neutral.influence_by_faction.get(faction_id, 0))
    if option is None:
        blocked_reason = next((str(item.get("blocked_reason") or "") for item in options if item.get("blocked_reason")), "本月没有合法交涉。")
        return {
            "kind": "diplomacy",
            "title": f"观察{city.name}外交窗口",
            "detail": f"当前关系 {relation}、影响力 {influence}；{blocked_reason}",
            "city_id": city.city_id,
            "target_city_id": city.city_id,
            "planned": False,
            "resolved": False,
            "available": False,
            "blocked_reason": blocked_reason,
            "button_label": "本月外交不可用",
            "recommended_action": None,
        }
    payload = {
        "neutral_faction_id": neutral.faction_id,
        "diplomacy_action_id": str(option["id"]),
    }
    planned = _action_is_planned(
        actions,
        "neutral_diplomacy",
        payload,
        ("neutral_faction_id", "diplomacy_action_id"),
    )
    return {
        "kind": "diplomacy",
        "title": f"向{city.name}{option['name']}",
        "detail": f"关系 {relation}、影响力 {influence}；{option['direct_effect']}",
        "city_id": city.city_id,
        "target_city_id": city.city_id,
        "planned": planned,
        "resolved": False,
        "available": True,
        "button_label": f"安排{option['name']} · 1 军令",
        "recommended_action": {
            "action_type": "neutral_diplomacy",
            "command_cost": 1,
            "payload": payload,
        },
    }


def _quick_military_recommendation(
    world: WorldState,
    faction_id: str,
    actions: list[dict[str, Any]],
) -> dict[str, Any]:
    from wujiang.strategy.battles import MIN_ATTACK_TROOPS, city_attack_commitment

    nodes = {node.node_id: node for node in world.nodes}
    cities_by_node = {city.node_id: city for city in world.cities}
    neutral_ids = {faction.faction_id for faction in world.factions if faction.is_neutral_city_state}
    candidates = []
    for source in world.cities:
        if source.owner_faction_id != faction_id or source.resources.troops < MIN_ATTACK_TROOPS:
            continue
        node = nodes.get(source.node_id)
        for target_node_id in node.connected_node_ids if node is not None else ():
            target = cities_by_node.get(target_node_id)
            if target is None or target.owner_faction_id == faction_id:
                continue
            defender_power = int(target.resources.troops) + int(target.defense) * 40
            commitment = city_attack_commitment(source.resources.troops)
            candidates.append((
                0 if target.owner_faction_id in neutral_ids else 1,
                defender_power - commitment,
                target.city_id,
                source.city_id,
                source,
                target,
                commitment,
                defender_power,
            ))
    if not candidates:
        return {
            "kind": "military",
            "title": "前线尚无合法亲征路线",
            "detail": "需要至少 50 兵力的己方城市与外部城市相邻。",
            "city_id": _capital_city(world, faction_id).city_id,
            "target_city_id": "",
            "planned": False,
            "resolved": False,
            "available": False,
            "blocked_reason": "当前没有满足兵力与相邻条件的进攻目标。",
            "button_label": "暂无亲征目标",
            "recommended_action": None,
        }
    _, _, _, _, source, target, commitment, defender_power = min(candidates)
    payload = {
        "source_city_id": source.city_id,
        "target_city_id": target.city_id,
        "resolution_mode": "quick",
        "attacker_hero_codes": [],
    }
    planned = _action_is_planned(
        actions,
        "declare_attack",
        payload,
        ("source_city_id", "target_city_id", "resolution_mode"),
    )
    return {
        "kind": "military",
        "title": f"从{source.name}亲征{target.name}",
        "detail": f"预计投入 {commitment} 兵；目标守军 {target.resources.troops}、城防 {target.defense}（防守强度 {defender_power}）。",
        "city_id": source.city_id,
        "target_city_id": target.city_id,
        "planned": planned,
        "resolved": False,
        "available": True,
        "button_label": "计划快速亲征 · 2 军令",
        "recommended_action": {
            "action_type": "declare_attack",
            "command_cost": 2,
            "payload": payload,
        },
    }


def quick_campaign_recommendations(
    world: WorldState,
    faction_id: str,
    queued_actions: Any = (),
) -> dict[str, Any] | None:
    """Return at most three real, same-rules actions for the compact campaign."""
    if not is_quick_campaign(world):
        return None
    _faction(world, faction_id)
    opening_choice_id = _selected_choice_id(world, faction_id)
    if not opening_choice_id:
        return {
            "opening_required": True,
            "recommendation_limit": 3,
            "recommendations": [],
            "conflict_window": {
                "available": False,
                "expected_month": 2,
                "summary": "先完成第一月开局国策，再生成三项本月行动。",
            },
        }
    actions = [_action_dict(item) for item in queued_actions]
    by_kind = {
        "governance": _quick_governance_recommendation(world, faction_id, opening_choice_id, actions),
        "diplomacy": _quick_diplomacy_recommendation(world, faction_id, actions),
        "military": _quick_military_recommendation(world, faction_id, actions),
    }
    order_by_opening = {
        "stabilize": ("governance", "diplomacy", "military"),
        "diplomacy": ("diplomacy", "governance", "military"),
        "mobilize": ("military", "diplomacy", "governance"),
    }
    recommendations = [by_kind[kind] for kind in order_by_opening.get(opening_choice_id, ("governance", "diplomacy", "military"))][:3]
    expansion = next(
        (item for item in recommendations if item["kind"] in {"military", "diplomacy"} and item["available"]),
        None,
    )
    recent_battle = next(
        (
            battle
            for battle in reversed(world.pending_battles)
            if battle.status == "resolved"
            and battle.month >= max(1, world.current_month - 1)
            and faction_id in {battle.attacker_faction_id, battle.defender_faction_id}
        ),
        None,
    )
    recent_outcome = None
    if recent_battle is not None:
        source = next((city for city in world.cities if city.city_id == recent_battle.source_city_id), None)
        target = next((city for city in world.cities if city.city_id == recent_battle.target_city_id), None)
        won = recent_battle.winner_faction_id == faction_id
        actor = "亲征" if recent_battle.attacker_faction_id == faction_id else "守城"
        recent_outcome = {
            "battle_id": recent_battle.battle_id,
            "month": recent_battle.month,
            "won": won,
            "source_city_id": recent_battle.source_city_id,
            "target_city_id": recent_battle.target_city_id,
            "summary": (
                f"第 {recent_battle.month} 月{actor}结果：{source.name if source else recent_battle.source_city_id} → "
                f"{target.name if target else recent_battle.target_city_id}，{'我方获胜' if won else '我方失利'}。"
            ),
        }
    expected_month = 2 if world.current_month <= 2 else world.current_month
    return {
        "opening_required": False,
        "opening_choice_id": opening_choice_id,
        "recommendation_limit": 3,
        "recommendations": recommendations,
        "recent_outcome": recent_outcome,
        "conflict_window": {
            "available": expansion is not None,
            "kind": expansion["kind"] if expansion is not None else None,
            "expected_month": expected_month,
            "source_city_id": expansion.get("city_id") if expansion is not None else "",
            "target_city_id": expansion.get("target_city_id") if expansion is not None else "",
            "summary": (
                (
                    f"第 {expected_month} 月前可执行：{expansion['title']}。"
                    if world.current_month <= 2
                    else f"本月可执行：{expansion['title']}。"
                )
                if expansion is not None
                else "当前没有合法的外交或军事扩张窗口。"
            ),
        },
    }


def apply_quick_campaign_opening_choice(
    world: WorldState,
    *,
    faction_id: str,
    choice_id: str,
) -> WorldState:
    if not is_quick_campaign(world):
        raise StrategyError("只有六个月快速战役可以执行开局抉择。")
    if world.current_month != 1:
        raise StrategyError("开局抉择必须在第一个月完成。")
    faction = _faction(world, faction_id)
    if _selected_choice_id(world, faction_id):
        raise StrategyError("这个势力已经完成开局抉择，不能重复领取效果。")
    choice = next((item for item in QUICK_OPENING_CHOICES if item["id"] == str(choice_id)), None)
    if choice is None:
        raise StrategyError("未知的快速战役开局抉择。")

    next_world = WorldState.from_dict(copy.deepcopy(world.to_dict()))
    faction = _faction(next_world, faction_id)
    capital = _capital_city(next_world, faction_id)
    related_ids = [faction_id, str(choice_id), capital.city_id]
    if choice_id == "stabilize":
        capital.resources.food += 140
        capital.support_by_faction[faction_id] = min(
            100, int(capital.support_by_faction.get(faction_id, 50)) + 15
        )
    elif choice_id == "diplomacy":
        neutral_city = _nearest_neutral_city(next_world, faction_id)
        neutral = next(item for item in next_world.factions if item.faction_id == neutral_city.owner_faction_id)
        neutral.relations[faction_id] = min(100, int(neutral.relations.get(faction_id, 0)) + 25)
        neutral.influence_by_faction[faction_id] = min(
            100, int(neutral.influence_by_faction.get(faction_id, 0)) + 20
        )
        neutral_city.support_by_faction[faction_id] = min(
            100, int(neutral_city.support_by_faction.get(faction_id, 35)) + 10
        )
        related_ids = [faction_id, str(choice_id), neutral.faction_id, neutral_city.city_id]
    elif choice_id == "mobilize":
        capital.resources.troops += 180
        faction.resources.food += 100

    next_world.memory_tags.append(_choice_tag(faction_id, str(choice_id)))
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="quick_campaign_opening_choice",
            message=f"{faction.name}选择“{choice['name']}”：{choice['result_summary']}",
            related_ids=related_ids,
            visibility="player_visible",
        )
    )
    next_world.validate()
    return next_world
