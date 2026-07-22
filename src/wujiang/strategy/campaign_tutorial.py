from __future__ import annotations

import copy
from typing import Any, Iterable

from wujiang.strategy.models import EventLogEntry, StrategyError, WorldState


TUTORIAL_ID = "first_three_months_v1"
FIRST_CAMPAIGN_ID = "city_states_twelve_months_v1"
SKIP_EXPLANATION = "跳过只会隐藏前三个月的情境目标；不会获得或失去资源，不会替你下令，也不会跳过战略月份。"

TUTORIAL_STEPS = (
    {
        "id": "survey_border",
        "month": 1,
        "chapter": "第一月 · 读局与治理",
        "title": "查看边境",
        "detail": "在地图上确认己方城市、相邻中立城邦和可通行路线。",
        "action_kind": "map",
    },
    {
        "id": "set_policy",
        "month": 1,
        "chapter": "第一月 · 读局与治理",
        "title": "设置城市方针",
        "detail": "根据粮食、民心和兵力，为一座辖区城市提交方针；无城主权限时通过命令链委托。",
        "action_kind": "city_command",
    },
    {
        "id": "resolve_event",
        "month": 1,
        "chapter": "第一月 · 读局与治理",
        "title": "处理待决事件",
        "detail": "主动选择事件结果；无本地事件权限时通过命令链委托城主处理。",
        "action_kind": "story",
    },
    {
        "id": "ritual_or_appoint",
        "month": 2,
        "chapter": "第二月 · 建立执行力量",
        "title": "祭祀或任命",
        "detail": "举行一次召唤祭祀，或由主公任命一名已效忠武将；其他官职可向上级提出请求。",
        "action_kind": "organization",
    },
    {
        "id": "prepare_conflict",
        "month": 3,
        "chapter": "第三月 · 准备冲突",
        "title": "准备一次边境冲突",
        "detail": "按当前官职请兵、调兵、征募、设置防守、发布攻防命令或计划进攻。",
        "action_kind": "conflict",
    },
)

STEP_ACTION_TYPES = {
    "set_policy": {"set_city_policy", "tutorial_goal:set_policy"},
    "resolve_event": {"resolve_story_event", "tutorial_goal:resolve_event"},
    "ritual_or_appoint": {"perform_hero_ritual", "appoint_strategic_hero", "tutorial_goal:ritual_or_appoint"},
    "prepare_conflict": {
        "declare_attack",
        "increase_city_troops",
        "register_city_soldiers",
        "transfer_registered_units",
        "request_registered_units",
        "approve_registered_unit_request",
        "prepare_conflict_office_order",
    },
}

STEP_EVENT_CATEGORIES = {
    "set_policy": {"city_policy"},
    "resolve_event": {"story_event_choice"},
    "ritual_or_appoint": {"hero_ritual_summoned", "strategic_hero_appointed"},
    "prepare_conflict": {
        "battle_declared",
        "battle_defender_hero_set",
        "field_troops_levied",
        "city_garrison_levied",
        "city_soldiers_registered",
        "registered_units_transferred",
        "registered_units_requested",
        "registered_unit_request_approved",
    },
}


def _enabled(world: WorldState) -> bool:
    return str(world.campaign_contract.get("id") or "") == FIRST_CAMPAIGN_ID


def _progress(world: WorldState, faction_id: str) -> dict[str, Any]:
    progress_by_faction = world.campaign_tutorial.get("progress_by_faction") or {}
    raw = progress_by_faction.get(faction_id) or {}
    return dict(raw) if isinstance(raw, dict) else {}


def _action_dict(action: Any) -> dict[str, Any]:
    if isinstance(action, dict):
        return action
    if hasattr(action, "to_dict"):
        return action.to_dict()
    return {}


def _faction_action_types(world: WorldState, faction_id: str, queued_actions: Iterable[Any]) -> set[str]:
    faction_actions = [
        action
        for action in (_action_dict(item) for item in queued_actions)
        if action.get("faction_id") == faction_id
    ]
    action_types = {str(action.get("action_type") or "") for action in faction_actions}
    if any(
        action.get("action_type") == "issue_office_order"
        and str((action.get("payload") or {}).get("office_order_type") or "") in {"attack_city", "defend_city"}
        for action in faction_actions
    ):
        action_types.add("prepare_conflict_office_order")
    for action in faction_actions:
        objective = str((action.get("payload") or {}).get("objective") or "")
        for step_id in ("set_policy", "resolve_event", "ritual_or_appoint"):
            if objective.startswith(f"[引导:{step_id}]"):
                action_types.add(f"tutorial_goal:{step_id}")
    offices_by_id = {office.office_id: office for office in world.offices}
    if any(
        order.order_type in {"attack_city", "defend_city"}
        and offices_by_id.get(order.issuer_office_id) is not None
        and offices_by_id[order.issuer_office_id].faction_id == faction_id
        for order in world.office_orders
    ):
        action_types.add("prepare_conflict_office_order")
    for order in world.office_orders:
        issuer = offices_by_id.get(order.issuer_office_id)
        if issuer is None or issuer.faction_id != faction_id:
            continue
        for step_id in ("set_policy", "resolve_event", "ritual_or_appoint"):
            if order.objective.startswith(f"[引导:{step_id}]"):
                action_types.add(f"tutorial_goal:{step_id}")
    return action_types


def _faction_event_categories(world: WorldState, faction_id: str) -> set[str]:
    owned_city_ids = {city.city_id for city in world.cities if city.owner_faction_id == faction_id}
    return {
        event.category
        for event in world.event_log
        if faction_id in event.related_ids or owned_city_ids.intersection(event.related_ids)
    }


def campaign_tutorial_public(world: WorldState, queued_actions: Iterable[Any]) -> dict[str, dict[str, Any]]:
    if not _enabled(world):
        return {}
    output: dict[str, dict[str, Any]] = {}
    for faction in world.factions:
        if faction.faction_type != "major":
            continue
        progress = _progress(world, faction.faction_id)
        acknowledged = set(progress.get("acknowledged_steps") or [])
        action_types = _faction_action_types(world, faction.faction_id, queued_actions)
        event_categories = _faction_event_categories(world, faction.faction_id)
        steps = []
        for definition in TUTORIAL_STEPS:
            step_id = str(definition["id"])
            completed = step_id in acknowledged
            if step_id in STEP_ACTION_TYPES:
                completed = completed or bool(action_types.intersection(STEP_ACTION_TYPES[step_id]))
                completed = completed or bool(event_categories.intersection(STEP_EVENT_CATEGORIES[step_id]))
            timing = "completed" if completed else "upcoming" if world.current_month < int(definition["month"]) else "active" if world.current_month == int(definition["month"]) else "overdue"
            steps.append({**definition, "completed": completed, "timing": timing})
        completed_count = sum(1 for step in steps if step["completed"])
        skipped = bool(progress.get("skipped"))
        output[faction.faction_id] = {
            "id": TUTORIAL_ID,
            "enabled": True,
            "skipped": skipped,
            "skipped_month": progress.get("skipped_month"),
            "skip_explanation": SKIP_EXPLANATION,
            "completed": completed_count == len(steps),
            "completed_count": completed_count,
            "total_count": len(steps),
            "current_month": world.current_month,
            "guide_period_ended": world.current_month > 3,
            "steps": steps,
        }
    return output


def update_campaign_tutorial(world: WorldState, *, faction_id: str, action: str) -> WorldState:
    if not _enabled(world):
        raise StrategyError("这个战役场景没有前三个月引导。")
    faction = next((item for item in world.factions if item.faction_id == faction_id), None)
    if faction is None or faction.faction_type != "major":
        raise StrategyError("只有主要势力成员可以更新战役引导。")
    normalized = str(action or "").strip()
    if normalized not in {"survey_border", "skip"}:
        raise StrategyError("未知的战役引导操作。")
    next_world = WorldState.from_dict(copy.deepcopy(world.to_dict()))
    state = dict(next_world.campaign_tutorial)
    state.setdefault("id", TUTORIAL_ID)
    progress_by_faction = dict(state.get("progress_by_faction") or {})
    progress = dict(progress_by_faction.get(faction_id) or {})
    if normalized == "survey_border":
        acknowledged = list(progress.get("acknowledged_steps") or [])
        if "survey_border" not in acknowledged:
            acknowledged.append("survey_border")
            next_world.event_log.append(
                EventLogEntry(
                    month=next_world.current_month,
                    category="campaign_tutorial_step",
                    message=f"{faction.name}完成引导目标：查看边境。",
                    related_ids=[faction_id, "survey_border"],
                )
            )
        progress["acknowledged_steps"] = acknowledged
    else:
        if not progress.get("skipped"):
            next_world.event_log.append(
                EventLogEntry(
                    month=next_world.current_month,
                    category="campaign_tutorial_skipped",
                    message=f"{faction.name}跳过前三个月情境引导；普通战役规则与资源不变。",
                    related_ids=[faction_id],
                )
            )
        progress["skipped"] = True
        progress["skipped_month"] = next_world.current_month
    progress_by_faction[faction_id] = progress
    state["progress_by_faction"] = progress_by_faction
    next_world.campaign_tutorial = state
    next_world.validate()
    return next_world
