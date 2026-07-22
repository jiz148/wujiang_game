from __future__ import annotations

import copy
from typing import Any, Iterable

from wujiang.strategy.ai import _choose_city_policy, _city_policy_urgency
from wujiang.strategy.command import FACTION_MONTHLY_COMMAND_POINTS, monthly_briefings_public, strategy_action_command_cost
from wujiang.strategy.heroes import hero_ritual_capacity, perform_hero_ritual
from wujiang.strategy.models import EventLogEntry, OfficeOrder, WorldState
from wujiang.strategy.offices import OFFICE_TYPE_LABELS, ensure_office_system
from wujiang.strategy.story import choose_ai_story_choice, pending_story_event_for_faction, resolve_story_event
from wujiang.strategy.tactics import set_city_policy


FIRST_CAMPAIGN_ID = "city_states_twelve_months_v1"


def _enabled(world: WorldState) -> bool:
    return str(world.campaign_contract.get("id") or "") == FIRST_CAMPAIGN_ID


def _clone_world(world: WorldState) -> WorldState:
    return WorldState.from_dict(copy.deepcopy(world.to_dict()))


def _action_dict(action: Any) -> dict[str, Any]:
    if isinstance(action, dict):
        return action
    if hasattr(action, "to_dict"):
        return action.to_dict()
    return {}


def _protected_policy_city_ids(actions: Iterable[Any], faction_id: str) -> set[str]:
    return {
        str((action.get("payload") or {}).get("city_id") or "")
        for action in (_action_dict(item) for item in actions)
        if action.get("faction_id") == faction_id and action.get("action_type") == "set_city_policy"
    }


def _finish_order(
    world: WorldState,
    order_id: str,
    *,
    status: str,
    result: str,
    expected_month: int,
) -> None:
    order = next(item for item in world.office_orders if item.order_id == order_id)
    order.status = status
    order.details = {
        **order.details,
        "executor_office_id": order.receiver_office_id,
        "expected_completion_month": expected_month,
        "resolved_month": expected_month if status == "completed" else None,
        "result_summary": result,
    }


def _execute_ai_order(world: WorldState, order: OfficeOrder) -> tuple[WorldState, str, str]:
    receiver = next((office for office in world.offices if office.office_id == order.receiver_office_id), None)
    if receiver is None or receiver.controller_type != "ai" or receiver.status != "active":
        return world, "pending", "接收职位当前不由 AI 托管，等待对应玩家处理。"
    faction_id = receiver.faction_id
    objective = order.objective
    if order.order_type == "set_policy":
        city = next((item for item in world.cities if item.city_id == order.target_entity_id), None)
        policy = str(order.details.get("policy") or "")
        if city is not None and city.owner_faction_id == faction_id and city.city_id in receiver.managed_entity_ids:
            next_world = set_city_policy(world, faction_id=faction_id, city_id=city.city_id, policy=policy)
            return next_world, "completed", f"{city.name}已按主公命令设为{policy}。"
        return world, "rejected", "目标城市或方针已经不再合法。"
    if objective.startswith("[引导:set_policy]"):
        city = next((item for item in world.cities if item.city_id == order.target_entity_id), None)
        if city is not None and city.owner_faction_id == faction_id and city.city_id in receiver.managed_entity_ids:
            policy = _choose_city_policy(city, next(item for item in world.factions if item.faction_id == faction_id))
            next_world = set_city_policy(world, faction_id=faction_id, city_id=city.city_id, policy=policy)
            return next_world, "completed", f"{city.name}已由{OFFICE_TYPE_LABELS[receiver.office_type]}设为{policy}。"
        return world, "rejected", "目标城市不在接收职位的管辖范围内。"
    if objective.startswith("[引导:resolve_event]"):
        event = pending_story_event_for_faction(world, faction_id)
        if event is not None and (not order.target_entity_id or event.city_id == order.target_entity_id):
            choice = choose_ai_story_choice(world, event)
            if choice is not None and "resolve_city_event" in receiver.permissions:
                next_world = resolve_story_event(world, faction_id=faction_id, event_id=event.event_id, choice_id=choice.choice_id)
                return next_world, "completed", f"{OFFICE_TYPE_LABELS[receiver.office_type]}已选择“{choice.label}”处理事件。"
        return world, "rejected", "没有可由接收职位处理的待决事件。"
    if objective.startswith("[引导:ritual_or_appoint]"):
        city = next(
            (
                item
                for item in world.cities
                if item.owner_faction_id == faction_id
                and int(item.building_levels.get("ritual_site", 0)) > 0
                and item.resources.ether >= 30
                and (not order.target_entity_id or item.city_id == order.target_entity_id)
            ),
            None,
        )
        if city is not None and "perform_ritual" in receiver.permissions and hero_ritual_capacity(world, faction_id)["remaining"] > 0:
            next_world = perform_hero_ritual(
                world,
                faction_id=faction_id,
                city_id=city.city_id,
                issuer_office_id=receiver.office_id,
            )
            return next_world, "completed", f"{OFFICE_TYPE_LABELS[receiver.office_type]}已在{city.name}完成召唤祭祀。"
        return world, "accepted", "请求已接受，但当前职位权限、祭祀场、以太或职位容量尚不满足执行条件。"
    if order.order_type in {"attack_city", "defend_city"}:
        return world, "accepted", "攻防目标已纳入战区案牍；宣战与格子战仍等待玩家将军确认。"
    return world, "accepted", "命令已送达并纳入接收职位案牍；未匹配自动执行模板，不会擅自改变世界状态。"


def apply_player_office_automation(
    world: WorldState,
    *,
    controlled_faction_ids: Iterable[str],
    queued_actions: Iterable[Any],
    command_remaining_by_faction: dict[str, int],
) -> WorldState:
    """Run bounded routine automation for AI offices inside human factions."""
    if not _enabled(world):
        return world
    next_world = ensure_office_system(_clone_world(world))
    actions = list(queued_actions)
    for faction_id in sorted({str(item) for item in controlled_faction_ids}):
        faction = next((item for item in next_world.factions if item.faction_id == faction_id), None)
        if faction is None or faction.is_neutral_city_state:
            continue
        expected_month = next_world.current_month + 1
        results: list[str] = []
        pending_ids = [
            order.order_id
            for order in next_world.office_orders
            if order.status == "pending"
            and next((office for office in next_world.offices if office.office_id == order.receiver_office_id), None) is not None
        ]
        for order_id in pending_ids:
            order = next(item for item in next_world.office_orders if item.order_id == order_id)
            issuer = next((office for office in next_world.offices if office.office_id == order.issuer_office_id), None)
            if issuer is None or issuer.faction_id != faction_id:
                continue
            next_world, status, result = _execute_ai_order(next_world, order)
            _finish_order(next_world, order_id, status=status, result=result, expected_month=expected_month)
            if status != "pending":
                results.append(f"order:{order_id}:{status}")

        remaining = max(0, int(command_remaining_by_faction.get(faction_id, 0)))
        protected = _protected_policy_city_ids(actions, faction_id)
        faction = next(item for item in next_world.factions if item.faction_id == faction_id)
        routine_candidates = []
        for city in next_world.cities:
            if city.owner_faction_id != faction_id or city.city_id in protected:
                continue
            governor = next(
                (
                    office
                    for office in next_world.offices
                    if office.faction_id == faction_id
                    and office.office_type == "governor"
                    and office.controller_type == "ai"
                    and office.status == "active"
                    and city.city_id in office.managed_entity_ids
                ),
                None,
            )
            if governor is None:
                continue
            policy = _choose_city_policy(city, faction)
            urgency = _city_policy_urgency(city, faction)
            if city.policy != policy and urgency >= 800:
                routine_candidates.append((urgency, city.city_id, city, governor, policy))
        if remaining >= 1 and routine_candidates:
            _, _, city, governor, policy = max(routine_candidates, key=lambda item: (item[0], item[1]))
            next_world = set_city_policy(next_world, faction_id=faction_id, city_id=city.city_id, policy=policy)
            remaining -= 1
            results.append(f"routine_policy:{governor.office_id}:{city.city_id}:{policy}")
            next_world.event_log.append(
                EventLogEntry(
                    month=next_world.current_month,
                    category="office_automation",
                    message=f"{city.name}城主发现生存风险，自动将方针调整为{policy}，占用 1 点剩余军令。",
                    related_ids=[faction_id, governor.office_id, city.city_id],
                )
            )

        ai_office_ids = {
            office.office_id
            for office in next_world.offices
            if office.faction_id == faction_id and office.controller_type == "ai" and office.status == "active"
        }
        for duty in next_world.office_duties:
            if duty.office_id in ai_office_ids and duty.status == "pending" and duty.due_month == next_world.current_month:
                duty.status = "completed"
        if results:
            next_world.event_log.append(
                EventLogEntry(
                    month=next_world.current_month,
                    category="office_automation_summary",
                    message=f"{faction.name}的 AI 官职完成本月常规案牍：{len(results)} 项。",
                    related_ids=[faction_id, *results],
                )
            )
    next_world.validate()
    return next_world


def office_coordination_public(world: WorldState, queued_actions: Iterable[Any]) -> dict[str, dict[str, Any]]:
    if not _enabled(world):
        return {}
    actions = [_action_dict(item) for item in queued_actions]
    briefings = monthly_briefings_public(world)
    output: dict[str, dict[str, Any]] = {}
    offices = {office.office_id: office for office in world.offices}
    for faction in world.factions:
        if faction.is_neutral_city_state:
            continue
        faction_actions = [item for item in actions if item.get("faction_id") == faction.faction_id]
        decisions: list[dict[str, Any]] = []
        story = pending_story_event_for_faction(world, faction.faction_id)
        if story is not None:
            decisions.append({
                "kind": "story",
                "title": "决定待决事件",
                "detail": "月底未处理将自动采用放任结果。",
                "city_id": story.city_id,
                "planned": any(item.get("action_type") == "resolve_story_event" for item in faction_actions),
            })
        risky = []
        for city in world.cities:
            if city.owner_faction_id != faction.faction_id:
                continue
            support = int(city.support_by_faction.get(faction.faction_id, 50))
            food_need = max(1, city.resources.population // 80 + city.resources.troops // 120)
            score = (100 if city.resources.food < food_need else 0) + max(0, 50 - support)
            if score > 0:
                risky.append((score, city))
        if risky:
            _, city = max(risky, key=lambda item: (item[0], item[1].city_id))
            decisions.append({
                "kind": "city_crisis",
                "title": f"处置{city.name}生存风险",
                "detail": "决定是否投入资源安抚、赈济或镇压；AI 只会自动调整紧急方针。",
                "city_id": city.city_id,
                "planned": any((item.get("payload") or {}).get("city_id") == city.city_id for item in faction_actions),
            })
        threat = next((item for item in briefings.get(faction.faction_id, {}).get("entries", []) if item.get("kind") == "threat"), None)
        if threat is not None and len(decisions) < 3:
            decisions.append({
                "kind": "threat",
                "title": str(threat.get("title") or "判断边境威胁"),
                "detail": str(threat.get("detail") or "决定是否调整防务或准备冲突。"),
                "city_id": str(threat.get("city_id") or ""),
                "planned": False,
            })
        if not decisions:
            opportunity = next((item for item in briefings.get(faction.faction_id, {}).get("entries", []) if item.get("kind") == "opportunity"), None)
            decisions.append({
                "kind": "opportunity",
                "title": str((opportunity or {}).get("title") or "选择本月主动目标"),
                "detail": str((opportunity or {}).get("detail") or "可以推进月份，也可以投资发展或准备扩张。"),
                "city_id": str((opportunity or {}).get("city_id") or ""),
                "planned": False,
            })

        routine = []
        for city in world.cities:
            if city.owner_faction_id != faction.faction_id:
                continue
            governor = next(
                (
                    office
                    for office in world.offices
                    if office.faction_id == faction.faction_id
                    and office.office_type == "governor"
                    and city.city_id in office.managed_entity_ids
                ),
                None,
            )
            routine.append({
                "city_id": city.city_id,
                "city_name": city.name,
                "policy": city.policy,
                "executor_office_id": governor.office_id if governor else "",
                "mode": "ai_emergency" if governor and governor.controller_type == "ai" else "default_policy",
            })

        feedback = []
        for action in faction_actions:
            if action.get("action_type") not in {"issue_office_order", "send_office_request"}:
                continue
            payload = action.get("payload") or {}
            feedback.append({
                "status": "planned",
                "issuer_office_id": str(payload.get("issuer_office_id") or ""),
                "executor_office_id": str(payload.get("receiver_office_id") or ""),
                "objective": str(payload.get("objective") or ""),
                "command_cost": strategy_action_command_cost(str(action.get("action_type") or ""), payload),
                "expected_completion_month": world.current_month + 1,
                "result_summary": "推进月份时送达接收职位并生成执行回执。",
            })
        for order in world.office_orders:
            issuer = offices.get(order.issuer_office_id)
            if issuer is None or issuer.faction_id != faction.faction_id:
                continue
            feedback.append({
                "id": order.order_id,
                "status": order.status,
                "issuer_office_id": order.issuer_office_id,
                "executor_office_id": str(order.details.get("executor_office_id") or order.receiver_office_id),
                "objective": order.objective,
                "command_cost": 0 if order.order_type == "request" else 1,
                "expected_completion_month": int(order.details.get("expected_completion_month") or order.deadline_month or order.issued_month + 1),
                "result_summary": str(order.details.get("result_summary") or "等待接收职位处理。"),
            })
        output[faction.faction_id] = {
            "high_consequence_decisions": decisions[:3],
            "routine_maintenance": routine,
            "order_feedback": feedback[-8:],
            "automation_rule": "默认方针持续生效；AI 官职只在缺粮或叛乱风险下自动调整一座城，并且只能使用玩家计划后剩余的军令。",
        }
    return output
