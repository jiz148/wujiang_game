from __future__ import annotations

import copy
from typing import Any, Iterable

from wujiang.strategy.models import City, WorldState
from wujiang.strategy.rebellion import rebellion_force_troops
from wujiang.strategy.simulation import (
    POLICIES,
    _apply_policy,
    _consume_city_upkeep,
    _update_rebellion_state,
    owner_support,
    rebellion_risk,
)


RESOURCE_KEYS = ("food", "money", "population", "ether", "troops")


def _action_dict(action: Any) -> dict[str, Any]:
    if isinstance(action, dict):
        return action
    if hasattr(action, "to_dict"):
        return action.to_dict()
    return {}


def _queued_policy(city: City, queued_actions: Iterable[Any]) -> str:
    policy = city.policy
    for raw_action in queued_actions:
        action = _action_dict(raw_action)
        payload = action.get("payload") or {}
        if action.get("action_type") == "set_city_policy" and payload.get("city_id") == city.city_id:
            policy = str(payload.get("policy") or policy)
    return policy


def forecast_city_month(city: City, *, queued_actions: Iterable[Any] = ()) -> dict[str, Any]:
    """Forecast the deterministic city economy using the same settlement helpers as advance_month."""
    preview = City.from_dict(copy.deepcopy(city.to_dict()))
    preview.policy = _queued_policy(preview, queued_actions)
    policy_warning = ""
    if preview.policy not in POLICIES:
        policy_warning = f"无法识别方针“{preview.policy}”，预测按稳定优先计算。"
        preview.policy = "稳定优先"
    before_resources = preview.resources.to_dict()
    before_support = owner_support(preview)
    before_defense = preview.defense
    food_need = max(1, preview.resources.population // 80 + preview.resources.troops // 120)
    events = []
    _apply_policy(preview, events, 0)
    shortage = _consume_city_upkeep(preview, events, 0)
    risk = rebellion_risk(preview, food_shortage=shortage)
    _update_rebellion_state(preview, risk, events, 0)
    after_resources = preview.resources.to_dict()
    stage = "安全" if risk <= 0 else "隐患" if risk < 45 else "危机事件" if risk < 75 else "正式叛乱"
    return {
        "city_id": city.city_id,
        "city_name": city.name,
        "policy": preview.policy,
        "food_upkeep": food_need,
        "food_shortage": shortage,
        "resources_before": before_resources,
        "resources_after": after_resources,
        "resource_delta": {key: after_resources[key] - before_resources[key] for key in RESOURCE_KEYS},
        "support_before": before_support,
        "support_after": owner_support(preview),
        "support_delta": owner_support(preview) - before_support,
        "defense_delta": preview.defense - before_defense,
        "rebellion_risk": risk,
        "rebellion_stage": stage,
        "rebellion_force_after": rebellion_force_troops(preview),
        "warning": policy_warning,
    }


def record_monthly_report(
    before: WorldState,
    after: WorldState,
    *,
    resolved_actions: Iterable[Any] = (),
) -> WorldState:
    next_world = WorldState.from_dict(copy.deepcopy(after.to_dict()))
    before_cities = {city.city_id: city for city in before.cities}
    city_changes = []
    for city in next_world.cities:
        previous = before_cities.get(city.city_id)
        if previous is None:
            continue
        previous_resources = previous.resources.to_dict()
        resources = city.resources.to_dict()
        resource_delta = {key: resources[key] - previous_resources[key] for key in RESOURCE_KEYS}
        support_before = owner_support(previous)
        support_after = owner_support(city)
        owner_changed = previous.owner_faction_id != city.owner_faction_id
        if not owner_changed and not any(resource_delta.values()) and support_before == support_after and previous.defense == city.defense:
            continue
        city_changes.append(
            {
                "city_id": city.city_id,
                "city_name": city.name,
                "owner_before": previous.owner_faction_id,
                "owner_after": city.owner_faction_id,
                "owner_changed": owner_changed,
                "resource_delta": resource_delta,
                "support_before": support_before,
                "support_after": support_after,
                "support_delta": support_after - support_before,
                "defense_delta": city.defense - previous.defense,
                "rebellion_force_before": rebellion_force_troops(previous),
                "rebellion_force_after": rebellion_force_troops(city),
            }
        )
    new_events = next_world.event_log[len(before.event_log):]
    action_rows = []
    for raw_action in resolved_actions:
        action = _action_dict(raw_action)
        action_rows.append(
            {
                "action_type": str(action.get("action_type") or ""),
                "action_key": str(action.get("action_key") or ""),
                "faction_id": str(action.get("faction_id") or ""),
                "payload": dict(action.get("payload") or {}),
            }
        )
    report = {
        "from_month": before.current_month,
        "month": next_world.current_month,
        "city_changes": city_changes,
        "resolved_actions": action_rows,
        "important_events": [
            {"category": event.category, "message": event.message, "related_ids": list(event.related_ids)}
            for event in new_events[-12:]
            if event.category != "city_income"
        ],
    }
    next_world.monthly_reports = [*next_world.monthly_reports, report][-24:]
    next_world.validate()
    return next_world


def _report_for_faction(world: WorldState, faction_id: str) -> dict[str, Any] | None:
    if not world.monthly_reports:
        return None
    report = world.monthly_reports[-1]
    visible_cities = [
        change
        for change in report.get("city_changes", [])
        if faction_id in {change.get("owner_before"), change.get("owner_after")}
    ]
    visible_actions = [
        action for action in report.get("resolved_actions", []) if action.get("faction_id") == faction_id
    ]
    faction_city_ids = {city.city_id for city in world.cities if city.owner_faction_id == faction_id}
    visible_events = [
        event
        for event in report.get("important_events", [])
        if not event.get("related_ids") or faction_city_ids.intersection(event.get("related_ids", [])) or faction_id in event.get("related_ids", [])
    ]
    return {
        "from_month": report.get("from_month"),
        "month": report.get("month"),
        "city_changes": visible_cities,
        "resolved_actions": visible_actions,
        "important_events": visible_events,
    }


def monthly_cycle_public(world: WorldState, queued_actions: Iterable[Any]) -> dict[str, dict[str, Any]]:
    from wujiang.strategy.story import story_events_public

    actions = [_action_dict(action) for action in queued_actions]
    output: dict[str, dict[str, Any]] = {}
    briefings = __import__("wujiang.strategy.command", fromlist=["monthly_briefings_public"]).monthly_briefings_public(world)
    public_story_events = {event["id"]: event for event in story_events_public(world)}
    for faction in world.factions:
        faction_actions = [action for action in actions if action.get("faction_id") == faction.faction_id]
        city_forecasts = [
            forecast_city_month(city, queued_actions=faction_actions)
            for city in world.cities
            if city.owner_faction_id == faction.faction_id
        ]
        must_handle = []
        for forecast in city_forecasts:
            if forecast["food_shortage"]:
                must_handle.append(f"{forecast['city_name']}预计缺粮，民心将下降。")
            if forecast["rebellion_risk"] >= 45:
                must_handle.append(f"{forecast['city_name']}叛乱风险 {forecast['rebellion_risk']}（{forecast['rebellion_stage']}）。")
        pending_story = next(
            (event for event in world.story_events if event.faction_id == faction.faction_id and event.status == "pending"),
            None,
        )
        if pending_story is not None:
            title = public_story_events.get(pending_story.event_id, {}).get("title") or pending_story.template_id
            must_handle.append(f"待决事件：{title}；月末未处理将自动放任。")
        briefing_entries = briefings.get(faction.faction_id, {}).get("entries", [])
        if not must_handle and briefing_entries:
            must_handle.append(str(briefing_entries[0].get("text") or briefing_entries[0].get("summary") or "关注当前最大威胁。"))
        planned = []
        for action in faction_actions:
            planned.append(
                {
                    **action,
                    "affected_months": [world.current_month, world.current_month + 1],
                    "settlement_note": "推进月份时先执行计划，再进行城市月结。",
                }
            )
        output[faction.faction_id] = {
            "previous_month": _report_for_faction(world, faction.faction_id),
            "must_handle": must_handle,
            "advance_forecast": {
                "target_month": world.current_month + 1,
                "cities": city_forecasts,
                "disclaimer": "经济、维护与叛乱按当前已知状态确定性预测；战争、事件结果和 AI 决策不在预测内。",
            },
            "planned_actions": planned,
        }
    return output
