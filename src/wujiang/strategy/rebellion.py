from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from wujiang.strategy.models import City, EventLogEntry, Faction, StrategyError, WorldState


REBELLION_FORCE_PREFIX = "rebellion_force:"
MIN_REBELLION_BATTLE_TROOPS = 50


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(value)))


def rebellion_force_troops(city: City) -> int:
    for state in city.event_states:
        if not state.startswith(REBELLION_FORCE_PREFIX):
            continue
        parts = state.split(":")
        if len(parts) >= 2:
            try:
                return max(0, int(parts[1]))
            except ValueError:
                return 0
    return 0


def set_rebellion_force_troops(city: City, troops: int, *, month: int) -> None:
    city.event_states = [state for state in city.event_states if not state.startswith(REBELLION_FORCE_PREFIX)]
    if int(troops) > 0:
        city.event_states.append(f"{REBELLION_FORCE_PREFIX}{int(troops)}:month:{int(month)}")


@dataclass(frozen=True, slots=True)
class RebellionAction:
    action_id: str
    name: str
    description: str
    money_cost: int = 0
    city_food_cost: int = 0
    city_troop_cost: int = 0
    owner_support_delta: int = 0
    local_autonomy_delta: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.action_id,
            "name": self.name,
            "description": self.description,
            "money_cost": self.money_cost,
            "city_food_cost": self.city_food_cost,
            "city_troop_cost": self.city_troop_cost,
            "owner_support_delta": self.owner_support_delta,
            "local_autonomy_delta": self.local_autonomy_delta,
            "requires_target_city": True,
        }


REBELLION_ACTIONS: tuple[RebellionAction, ...] = (
    RebellionAction(
        action_id="appease",
        name="安抚民心",
        description="消耗势力金钱，提升目标城市统治支持度。",
        money_cost=80,
        owner_support_delta=8,
        local_autonomy_delta=1,
    ),
    RebellionAction(
        action_id="relief_grain",
        name="开仓赈济",
        description="消耗目标城市粮食，缓和饥荒或危机带来的不满。",
        city_food_cost=120,
        owner_support_delta=6,
    ),
    RebellionAction(
        action_id="suppress",
        name="派兵镇压",
        description="消耗目标城市兵力，压低自治派影响并恢复短期秩序。",
        city_troop_cost=120,
        owner_support_delta=4,
        local_autonomy_delta=-10,
    ),
)

REBELLION_ACTIONS_BY_ID = {action.action_id: action for action in REBELLION_ACTIONS}


def rebellion_action_choices_public() -> list[dict[str, Any]]:
    return [action.to_dict() for action in REBELLION_ACTIONS]


def _clone_world(world: WorldState) -> WorldState:
    return WorldState.from_dict(copy.deepcopy(world.to_dict()))


def _city(world: WorldState, city_id: str) -> City:
    for city in world.cities:
        if city.city_id == city_id:
            return city
    raise StrategyError("城市不存在。")


def _faction(world: WorldState, faction_id: str) -> Faction:
    for faction in world.factions:
        if faction.faction_id == faction_id:
            return faction
    raise StrategyError("势力不存在。")


def validate_rebellion_action(
    world: WorldState,
    *,
    faction_id: str,
    action_id: str,
    city_id: str,
) -> RebellionAction:
    action = REBELLION_ACTIONS_BY_ID.get(str(action_id or "").strip())
    if action is None:
        raise StrategyError("叛乱处理行动不存在。")
    city = _city(world, str(city_id or "").strip())
    if city.owner_faction_id != faction_id:
        raise StrategyError("只能处理本势力控制城市的叛乱风险。")
    faction = _faction(world, faction_id)
    if faction.resources.money < action.money_cost:
        raise StrategyError("势力金钱不足，无法执行叛乱处理行动。")
    if city.resources.food < action.city_food_cost:
        raise StrategyError("城市粮食不足，无法执行叛乱处理行动。")
    if city.resources.troops < action.city_troop_cost:
        raise StrategyError("城市兵力不足，无法执行叛乱处理行动。")
    return action


def validate_rebellion_battle(
    world: WorldState,
    *,
    faction_id: str,
    city_id: str,
    troops: int | None = None,
) -> int:
    city = _city(world, str(city_id or "").strip())
    if city.owner_faction_id != faction_id:
        raise StrategyError("只能清剿本势力控制城市的叛军。")
    rebel_force = rebellion_force_troops(city)
    if rebel_force <= 0:
        raise StrategyError("目标城市没有可清剿的叛军。")
    if city.resources.troops < MIN_REBELLION_BATTLE_TROOPS:
        raise StrategyError("城市兵力不足，无法清剿叛军。")
    if troops is None:
        committed = min(city.resources.troops, max(MIN_REBELLION_BATTLE_TROOPS, rebel_force))
    else:
        committed = int(troops)
    if committed < MIN_REBELLION_BATTLE_TROOPS:
        raise StrategyError("清剿叛军至少需要 50 兵力。")
    if committed > city.resources.troops:
        raise StrategyError("投入兵力不能超过城市现有兵力。")
    return committed


def apply_rebellion_battle(
    world: WorldState,
    *,
    faction_id: str,
    city_id: str,
    troops: int | None = None,
) -> WorldState:
    committed = validate_rebellion_battle(world, faction_id=faction_id, city_id=city_id, troops=troops)
    next_world = _clone_world(world)
    city = _city(next_world, city_id)
    rebel_force = rebellion_force_troops(city)
    local_autonomy = _clamp(city.support_by_faction.get("local_autonomy", 45), 0, 100)
    defender_score = rebel_force + local_autonomy * 2
    attacker_score = committed + city.defense * 20
    attacker_wins = attacker_score >= defender_score

    if attacker_wins:
        troop_loss = min(committed, max(10, rebel_force // 2))
        remaining_force = 0
        city.support_by_faction[faction_id] = _clamp(city.support_by_faction.get(faction_id, 50) + 3, 0, 100)
        city.support_by_faction["local_autonomy"] = _clamp(local_autonomy - 8, 0, 100)
    else:
        troop_loss = min(committed, max(10, committed * 2 // 3))
        remaining_force = max(0, rebel_force - max(20, committed // 2))
        city.support_by_faction[faction_id] = _clamp(city.support_by_faction.get(faction_id, 50) - 3, 0, 100)
        city.support_by_faction["local_autonomy"] = _clamp(local_autonomy + 4, 0, 100)

    city.resources.troops -= troop_loss
    set_rebellion_force_troops(city, remaining_force, month=next_world.current_month)
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="rebellion_battle",
            message=(
                f"{city.name}清剿叛军{'成功' if attacker_wins else '失败'}："
                f"投入 {committed}，损失 {troop_loss}，叛军 {rebel_force}->{remaining_force}。"
            ),
            related_ids=[faction_id, city.city_id, str(committed), str(rebel_force), str(remaining_force)],
        )
    )
    if attacker_wins:
        next_world.event_log.append(
            EventLogEntry(
                month=next_world.current_month,
                category="rebellion_suppressed",
                message=f"{city.name}的叛军被清剿。",
                related_ids=[faction_id, city.city_id, "rebellion_battle"],
            )
        )
    next_world.validate()
    return next_world


def apply_rebellion_action(
    world: WorldState,
    *,
    faction_id: str,
    action_id: str,
    city_id: str,
) -> WorldState:
    from wujiang.strategy.simulation import rebellion_risk

    action = validate_rebellion_action(world, faction_id=faction_id, action_id=action_id, city_id=city_id)
    next_world = _clone_world(world)
    city = _city(next_world, city_id)
    faction = _faction(next_world, faction_id)
    previous_risk = rebellion_risk(city, food_shortage=False)
    previous_force = rebellion_force_troops(city)

    faction.resources.money -= action.money_cost
    city.resources.food -= action.city_food_cost
    city.resources.troops -= action.city_troop_cost
    city.support_by_faction[faction_id] = _clamp(
        city.support_by_faction.get(faction_id, 50) + action.owner_support_delta,
        0,
        100,
    )
    if action.local_autonomy_delta:
        city.support_by_faction["local_autonomy"] = _clamp(
            city.support_by_faction.get("local_autonomy", 45) + action.local_autonomy_delta,
            0,
            100,
        )
    if action.action_id == "suppress" and previous_force > 0:
        set_rebellion_force_troops(
            city,
            max(0, previous_force - action.city_troop_cost * 2),
            month=next_world.current_month,
        )

    city.event_states = [
        state
        for state in city.event_states
        if not state.startswith("rebellion_action:")
    ]
    city.event_states.append(f"rebellion_action:{action.action_id}:month:{next_world.current_month}")
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="rebellion_action",
            message=(
                f"{faction.name}在{city.name}执行{action.name}，"
                f"叛乱风险 {previous_risk}->{rebellion_risk(city, food_shortage=False)}。"
            ),
            related_ids=[faction_id, city.city_id, action.action_id],
        )
    )
    if action.action_id == "suppress" and previous_force > 0 and rebellion_force_troops(city) <= 0:
        next_world.event_log.append(
            EventLogEntry(
                month=next_world.current_month,
                category="rebellion_suppressed",
                message=f"{city.name}的叛军被镇压。",
                related_ids=[faction_id, city.city_id, action.action_id],
            )
        )
    next_world.validate()
    return next_world
