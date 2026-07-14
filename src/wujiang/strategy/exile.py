from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from wujiang.strategy.models import City, EventLogEntry, Faction, StrategyError, WorldState
from wujiang.strategy.objectives import evaluate_strategic_status
from wujiang.strategy.simulation import clamp


@dataclass(frozen=True, slots=True)
class ExileAction:
    action_id: str
    name: str
    description: str
    requires_target_city: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.action_id,
            "name": self.name,
            "description": self.description,
            "requires_target_city": self.requires_target_city,
        }


EXILE_ACTIONS: tuple[ExileAction, ...] = (
    ExileAction(
        action_id="seek_aid",
        name="求援",
        description="向盟友、商会和地方豪族求援，获得少量粮食、金钱和以太。",
    ),
    ExileAction(
        action_id="rally_followers",
        name="募兵",
        description="召集旧部和流民，积攒可用于重建据点的流亡军。",
    ),
    ExileAction(
        action_id="build_network",
        name="潜伏联络",
        description="在目标城市建立地下网络，提高该城对流亡势力的支持度。",
        requires_target_city=True,
    ),
    ExileAction(
        action_id="rebuild_base",
        name="重建据点",
        description="消耗流亡军和金钱，在低守军或高支持度城市重新取得一座据点。",
        requires_target_city=True,
    ),
)

EXILE_ACTIONS_BY_ID = {action.action_id: action for action in EXILE_ACTIONS}
REBUILD_TROOP_COST = 300
REBUILD_MONEY_COST = 120
REBUILD_MIN_SUPPORT = 45
REBUILD_MAX_TARGET_TROOPS = 260


def exile_action_choices_public() -> list[dict[str, Any]]:
    return [action.to_dict() for action in EXILE_ACTIONS]


def _clone_world(world: WorldState) -> WorldState:
    return WorldState.from_dict(copy.deepcopy(world.to_dict()))


def _faction(world: WorldState, faction_id: str) -> Faction:
    for faction in world.factions:
        if faction.faction_id == faction_id:
            return faction
    raise StrategyError("势力不存在。")


def _city(world: WorldState, city_id: str) -> City:
    for city in world.cities:
        if city.city_id == city_id:
            return city
    raise StrategyError("城市不存在。")


def faction_is_exiled(world: WorldState, faction_id: str) -> bool:
    status = evaluate_strategic_status(world)
    return faction_id in set(status["exiled_faction_ids"])


def validate_exile_action(
    world: WorldState,
    *,
    faction_id: str,
    action_id: str,
    target_city_id: str = "",
) -> None:
    action = EXILE_ACTIONS_BY_ID.get(str(action_id or "").strip())
    if action is None:
        raise StrategyError("流亡行动不存在。")
    _faction(world, faction_id)
    if not faction_is_exiled(world, faction_id):
        raise StrategyError("只有无城的流亡势力可以执行流亡行动。")
    if action.requires_target_city and not str(target_city_id or "").strip():
        raise StrategyError("该流亡行动需要选择目标城市。")
    if not action.requires_target_city and str(target_city_id or "").strip():
        raise StrategyError("该流亡行动不需要目标城市。")
    if action.requires_target_city:
        target = _city(world, target_city_id)
        if target.owner_faction_id == faction_id:
            raise StrategyError("流亡势力不能把自己的城市作为目标。")
    if action.action_id == "rebuild_base":
        faction = _faction(world, faction_id)
        target = _city(world, target_city_id)
        support = target.support_by_faction.get(faction_id, 0)
        if faction.resources.troops < REBUILD_TROOP_COST or faction.resources.money < REBUILD_MONEY_COST:
            raise StrategyError("重建据点需要 300 流亡军和 120 金钱。")
        if target.resources.troops > REBUILD_MAX_TARGET_TROOPS and support < REBUILD_MIN_SUPPORT:
            raise StrategyError("目标城市守军过强，或当地支持度不足。")


def apply_exile_action(
    world: WorldState,
    *,
    faction_id: str,
    action_id: str,
    target_city_id: str = "",
) -> WorldState:
    validate_exile_action(world, faction_id=faction_id, action_id=action_id, target_city_id=target_city_id)
    next_world = _clone_world(world)
    faction = _faction(next_world, faction_id)
    action = EXILE_ACTIONS_BY_ID[action_id]
    target = _city(next_world, target_city_id) if action.requires_target_city else None

    if action.action_id == "seek_aid":
        faction.resources.food += 140
        faction.resources.money += 100
        faction.resources.ether += 10
        message = f"{faction.name}在流亡中获得外部援助：粮食 +140，金钱 +100，以太 +10。"
        related_ids = [faction_id]
    elif action.action_id == "rally_followers":
        faction.resources.troops += 180
        faction.resources.food += 60
        message = f"{faction.name}召集旧部与流民：流亡军 +180，粮食 +60。"
        related_ids = [faction_id]
    elif action.action_id == "build_network" and target is not None:
        target.support_by_faction[faction_id] = clamp(target.support_by_faction.get(faction_id, 20) + 12, 0, 100)
        faction.resources.money += 30
        message = f"{faction.name}在{target.name}建立地下网络，当地支持度提升至 {target.support_by_faction[faction_id]}。"
        related_ids = [faction_id, target.city_id]
    elif action.action_id == "rebuild_base" and target is not None:
        previous_owner_id = target.owner_faction_id
        faction.resources.troops -= REBUILD_TROOP_COST
        faction.resources.money -= REBUILD_MONEY_COST
        target.owner_faction_id = faction_id
        target.resources.troops = REBUILD_TROOP_COST
        target.support_by_faction[faction_id] = clamp(target.support_by_faction.get(faction_id, 40) + 20, 0, 100)
        target.support_by_faction[previous_owner_id] = clamp(target.support_by_faction.get(previous_owner_id, 50) - 20, 0, 100)
        message = f"{faction.name}在{target.name}重建据点，重新取得城市控制权。"
        related_ids = [faction_id, target.city_id, previous_owner_id]
    else:
        raise StrategyError("流亡行动无法结算。")

    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="exile_action",
            message=message,
            related_ids=related_ids,
        )
    )
    next_world.validate()
    return next_world
