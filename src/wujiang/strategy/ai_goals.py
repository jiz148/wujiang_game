from __future__ import annotations

import copy
from typing import Any

from wujiang.strategy.battles import city_attack_commitment
from wujiang.strategy.models import City, EventLogEntry, Faction, WorldState
from wujiang.strategy.objectives import FIRST_CAMPAIGN_SCENARIO_ID
from wujiang.strategy.simulation import rebellion_risk


GOAL_DURATION_MONTHS = {"stabilize_food": 2, "stabilize_unrest": 2, "secure_border": 2, "ritual_reinforcement": 3, "raise_army": 2, "capture_city": 3}


def _enabled(world: WorldState) -> bool:
    return str(world.campaign_contract.get("id") or "") == FIRST_CAMPAIGN_SCENARIO_ID


def _cities(world: WorldState, faction_id: str) -> list[City]:
    return [city for city in world.cities if city.owner_faction_id == faction_id]


def _adjacent_city_ids(world: WorldState) -> dict[str, set[str]]:
    nodes = {node.node_id: node for node in world.nodes}
    city_nodes = {city.city_id: city.node_id for city in world.cities}
    return {
        city.city_id: {
            other_id
            for other_id, node_id in city_nodes.items()
            if node_id in set(getattr(nodes.get(city.node_id), "connected_node_ids", []))
        }
        for city in world.cities
    }


def _food_need(city: City) -> int:
    return max(1, city.resources.population // 80 + city.resources.troops // 120)


def _active_hero_count(world: WorldState, faction_id: str) -> int:
    return sum(1 for hero in world.strategic_heroes if hero.faction_id == faction_id and hero.status == "serving")


def _ritual_candidate(world: WorldState, faction: Faction) -> City | None:
    candidates = [
        city
        for city in _cities(world, faction.faction_id)
        if int(city.building_levels.get("ritual_site", 0)) > 0 and city.resources.ether >= 30
    ]
    return max(candidates, key=lambda city: (city.resources.ether, city.city_id), default=None)


def _border_pairs(world: WorldState, faction_id: str) -> list[tuple[City, City]]:
    adjacent = _adjacent_city_ids(world)
    cities = {city.city_id: city for city in world.cities}
    return [
        (source, cities[target_id])
        for source in _cities(world, faction_id)
        for target_id in sorted(adjacent.get(source.city_id, set()))
        if cities[target_id].owner_faction_id != faction_id
    ]


def _goal_id(faction_id: str, goal_type: str, month: int) -> str:
    return f"ai_goal:{faction_id}:{goal_type}:{month}"


def _new_goal(
    world: WorldState,
    faction: Faction,
    *,
    goal_type: str,
    title: str,
    rationale: str,
    target_city: City | None = None,
    source_city: City | None = None,
    baseline: int = 0,
    policy_fragment: str | None = None,
    change_reason: str = "根据当前资源、地理与威胁选择。",
) -> dict[str, Any]:
    duration = GOAL_DURATION_MONTHS[goal_type]
    return {
        "id": _goal_id(faction.faction_id, goal_type, world.current_month),
        "faction_id": faction.faction_id,
        "goal_type": goal_type,
        "title": title,
        "rationale": rationale,
        "start_month": world.current_month,
        "end_month": world.current_month + duration - 1,
        "duration_months": duration,
        "status": "active",
        "target_city_id": target_city.city_id if target_city else None,
        "source_city_id": source_city.city_id if source_city else None,
        "baseline": baseline,
        "policy_fragment": policy_fragment,
        "progress": 0,
        "progress_label": "目标刚刚确立。",
        "last_action_summary": "尚未执行本月行动。",
        "change_reason": change_reason,
    }


def _select_goal(world: WorldState, faction: Faction, *, change_reason: str) -> dict[str, Any]:
    owned = _cities(world, faction.faction_id)
    food_city = min(
        (city for city in owned if city.resources.food < max(_food_need(city), city.resources.population // 10)),
        key=lambda city: (city.resources.food - _food_need(city), city.city_id),
        default=None,
    )
    if food_city is not None:
        return _new_goal(
            world,
            faction,
            goal_type="stabilize_food",
            title=f"稳住{food_city.name}粮情",
            rationale="粮食低于安全储备，继续扩张会放大民心与叛乱风险。",
            target_city=food_city,
            baseline=food_city.resources.food,
            policy_fragment="粮食",
            change_reason=change_reason,
        )

    unrest_city = max(
        owned,
        key=lambda city: (rebellion_risk(city, food_shortage=city.resources.food < _food_need(city)), city.city_id),
        default=None,
    )
    if unrest_city is not None and rebellion_risk(unrest_city, food_shortage=False) >= 45:
        return _new_goal(
            world,
            faction,
            goal_type="stabilize_unrest",
            title=f"稳住{unrest_city.name}",
            rationale="当地民心与秩序已成为势力当前最大风险。",
            target_city=unrest_city,
            baseline=rebellion_risk(unrest_city, food_shortage=False),
            policy_fragment=(
                "镇压"
                if unrest_city.resources.troops >= max(600, unrest_city.resources.population // 25)
                else "自治"
            ),
            change_reason=change_reason,
        )

    ritual_city = _ritual_candidate(world, faction)
    if ritual_city is not None and _active_hero_count(world, faction.faction_id) < 2:
        return _new_goal(
            world,
            faction,
            goal_type="ritual_reinforcement",
            title=f"在{ritual_city.name}祭祀扩军",
            rationale="当前任职英灵不足，且已有祭祀场与可用以太。",
            target_city=ritual_city,
            baseline=_active_hero_count(world, faction.faction_id),
            change_reason=change_reason,
        )

    borders = _border_pairs(world, faction.faction_id)
    threatened = [
        (source, target)
        for source, target in borders
        if target.resources.troops >= source.resources.troops * 4 // 5 or source.defense < source.level + 3
    ]
    if threatened:
        source, target = max(
            threatened,
            key=lambda pair: (pair[1].resources.troops - pair[0].resources.troops, pair[0].city_id),
        )
        return _new_goal(
            world,
            faction,
            goal_type="secure_border",
            title=f"守住{source.name}边境",
            rationale=f"邻接的{target.name}形成兵力或城防压力。",
            target_city=source,
            source_city=source,
            baseline=source.defense,
            policy_fragment="城防",
            change_reason=change_reason,
        )

    if borders:
        source, target = min(
            borders,
            key=lambda pair: (pair[1].resources.troops + pair[1].defense * 80, pair[1].city_id),
        )
        if source.resources.troops >= target.resources.troops:
            return _new_goal(
                world,
                faction,
                goal_type="capture_city",
                title=f"夺取{target.name}",
                rationale=f"{source.name}与目标相邻，当前是成本最低的扩张方向。",
                target_city=target,
                source_city=source,
                baseline=target.resources.troops + target.defense * 80,
                change_reason=change_reason,
            )

    weakest = min(owned, key=lambda city: (city.resources.troops, city.city_id))
    return _new_goal(
        world,
        faction,
        goal_type="raise_army",
        title=f"在{weakest.name}蓄兵",
        rationale="当前没有安全的进攻窗口，先补足最薄弱城市的守军。",
        target_city=weakest,
        baseline=weakest.resources.troops,
        policy_fragment="征兵",
        change_reason=change_reason,
    )


def ensure_ai_strategic_goal(world: WorldState, faction_id: str) -> dict[str, Any] | None:
    if not _enabled(world):
        return None
    faction = next((item for item in world.factions if item.faction_id == faction_id), None)
    if faction is None or faction.is_neutral_city_state:
        return None
    state = copy.deepcopy(world.ai_strategic_goals.get(faction_id) or {"current": None, "history": []})
    current = state.get("current")
    if isinstance(current, dict) and current.get("status") == "active" and world.current_month <= int(current.get("end_month", 0)):
        world.ai_strategic_goals[faction_id] = state
        return current

    history = list(state.get("history") or [])
    change_reason = "根据当前资源、地理与威胁选择。"
    if isinstance(current, dict):
        previous = copy.deepcopy(current)
        if previous.get("status") == "active":
            previous["status"] = "expired"
            previous["progress_label"] = "目标期限结束，AI 已重新评估局势。"
        history.append(previous)
        change_reason = f"前一目标“{previous.get('title') or '未命名目标'}”已{ '完成' if previous.get('status') == 'completed' else '到期' }，重新评估局势。"
    goal = _select_goal(world, faction, change_reason=change_reason)
    state = {"current": goal, "history": history[-4:]}
    world.ai_strategic_goals[faction_id] = state
    world.event_log.append(
        EventLogEntry(
            month=world.current_month,
            category="strategy_ai_goal_selected",
            message=f"{faction.name}公开短期战略目标：{goal['title']}（至第 {goal['end_month']} 月）。",
            related_ids=[faction_id, str(goal.get("target_city_id") or "")],
        )
    )
    return goal


def preferred_policy_for_goal(goal: dict[str, Any] | None) -> tuple[str, str] | None:
    if not goal or goal.get("status") != "active" or not goal.get("target_city_id"):
        return None
    policy_fragment = str(goal.get("policy_fragment") or "")
    return (str(goal["target_city_id"]), policy_fragment) if policy_fragment else None


def preferred_attack_for_goal(world: WorldState, faction_id: str, goal: dict[str, Any] | None) -> tuple[str, str] | None:
    if not goal or goal.get("goal_type") != "capture_city" or goal.get("status") != "active":
        return None
    source_id = str(goal.get("source_city_id") or "")
    target_id = str(goal.get("target_city_id") or "")
    cities = {city.city_id: city for city in world.cities}
    source = cities.get(source_id)
    target = cities.get(target_id)
    if source is None or target is None or source.owner_faction_id != faction_id or target.owner_faction_id == faction_id:
        return None
    if target_id not in _adjacent_city_ids(world).get(source_id, set()):
        return None
    if city_attack_commitment(source.resources.troops) < (
        target.resources.troops + target.defense * 80 + int(target.support_by_faction.get(target.owner_faction_id, 50)) * 3
    ):
        return None
    return source_id, target_id


def update_ai_strategic_goal(world: WorldState, faction_id: str, actions: list[str]) -> None:
    state = world.ai_strategic_goals.get(faction_id)
    goal = state.get("current") if isinstance(state, dict) else None
    if not isinstance(goal, dict) or goal.get("status") != "active":
        return
    city = next((item for item in world.cities if item.city_id == goal.get("target_city_id")), None)
    faction = next((item for item in world.factions if item.faction_id == faction_id), None)
    progress = int(goal.get("progress", 0))
    completed = False
    goal_type = goal.get("goal_type")
    if goal_type == "stabilize_food" and city is not None:
        safe = max(_food_need(city), city.resources.population // 10)
        progress = min(100, max(0, round(city.resources.food / max(1, safe) * 100)))
        completed = city.resources.food >= safe
        label = f"{city.name}现有粮食 {city.resources.food}，安全线 {safe}。"
    elif goal_type == "secure_border" and city is not None:
        target = city.level + 3
        progress = min(100, max(0, round(city.defense / max(1, target) * 100)))
        completed = city.defense >= target
        label = f"{city.name}城防 {city.defense}，目标 {target}。"
    elif goal_type == "stabilize_unrest" and city is not None:
        risk = rebellion_risk(city, food_shortage=city.resources.food < _food_need(city))
        progress = min(100, max(0, round((int(goal.get("baseline", 45)) - risk) / max(1, int(goal.get("baseline", 45)) - 44) * 100)))
        completed = risk < 45
        label = f"{city.name}当前叛乱风险 {risk}，目标降至 45 以下。"
    elif goal_type == "ritual_reinforcement":
        current = _active_hero_count(world, faction_id)
        progress = 100 if current > int(goal.get("baseline", 0)) else 35 if any(action.startswith("ritual:") for action in actions) else 10
        completed = current > int(goal.get("baseline", 0))
        label = f"当前任职英灵 {current} 名。"
    elif goal_type == "capture_city" and city is not None:
        completed = city.owner_faction_id == faction_id
        progress = 100 if completed else 60 if any(action.startswith("attack:") for action in actions) else 25
        label = f"{city.name}当前由{faction.name if completed and faction else '其他势力'}控制。"
    elif goal_type == "raise_army" and city is not None:
        baseline = int(goal.get("baseline", 0))
        target = baseline + max(120, baseline // 4)
        progress = min(100, max(0, round((city.resources.troops - baseline) / max(1, target - baseline) * 100)))
        completed = city.resources.troops >= target
        label = f"{city.name}兵力从 {baseline} 增至 {city.resources.troops}，目标 {target}。"
    else:
        label = "目标环境已经改变，等待下月重新评估。"
    goal["progress"] = progress
    goal["progress_label"] = label
    goal["last_action_summary"] = _action_summary(actions, goal)
    if completed:
        goal["status"] = "completed"
        world.event_log.append(
            EventLogEntry(
                month=world.current_month,
                category="strategy_ai_goal_completed",
                message=f"{faction.name if faction else faction_id}完成短期战略目标：{goal.get('title')}。",
                related_ids=[faction_id, str(goal.get("target_city_id") or "")],
            )
        )


def _action_summary(actions: list[str], goal: dict[str, Any]) -> str:
    target = str(goal.get("target_city_id") or "")
    matching = [action for action in actions if target and target in action]
    if not matching:
        matching = [action for action in actions if action.startswith(("ritual:", "attack:", "policy:"))]
    if not matching:
        return "本月受资源、军令或合法目标限制，暂未执行直接推进动作。"
    labels = []
    for action in matching[:2]:
        if action.startswith("policy:"):
            labels.append("调整城市方针")
        elif action.startswith("ritual:"):
            labels.append("举行祭祀并补充英灵")
        elif action.startswith("attack:"):
            labels.append("向目标城市发动进攻")
        elif action.startswith("defender:"):
            labels.append("配置防守英灵")
        else:
            labels.append("执行配套行动")
    return "；".join(labels) + "。"


def ai_strategic_goals_public(world: WorldState) -> list[dict[str, Any]]:
    if not _enabled(world):
        return []
    factions = {faction.faction_id: faction for faction in world.factions}
    cities = {city.city_id: city for city in world.cities}
    rows = []
    for faction_id, state in sorted(world.ai_strategic_goals.items()):
        faction = factions.get(faction_id)
        current = state.get("current") if isinstance(state, dict) else None
        if faction is None or faction.is_neutral_city_state or not isinstance(current, dict):
            continue
        target = cities.get(str(current.get("target_city_id") or ""))
        rows.append(
            {
                **copy.deepcopy(current),
                "faction_name": faction.name,
                "target_city_name": target.name if target else None,
                "months_remaining": max(0, int(current.get("end_month", world.current_month)) - world.current_month + 1),
                "previous_goal": copy.deepcopy((state.get("history") or [])[-1]) if state.get("history") else None,
            }
        )
    return rows
