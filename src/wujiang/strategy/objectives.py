from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from wujiang.strategy.models import EventLogEntry, WorldState


@dataclass(frozen=True, slots=True)
class VictoryCondition:
    condition_id: str
    name: str
    description: str
    implemented: bool

    def to_status(self, *, achieved: bool = False, winner_faction_id: str | None = None) -> dict[str, Any]:
        return {
            "id": self.condition_id,
            "name": self.name,
            "description": self.description,
            "implemented": self.implemented,
            "achieved": bool(achieved),
            "winner_faction_id": winner_faction_id,
        }


VICTORY_CONDITIONS: tuple[VictoryCondition, ...] = (
    VictoryCondition(
        condition_id="unify_cities",
        name="统一城邦",
        description="同一势力控制地图上的全部城市。",
        implemented=True,
    ),
    VictoryCondition(
        condition_id="eliminate_enemy_factions",
        name="消灭敌对势力",
        description="只剩一个势力仍控制城市；无城势力进入流亡路线。",
        implemented=True,
    ),
    VictoryCondition(
        condition_id="world_mainline",
        name="世界主线",
        description="完成雪鬼、电甲子等世界主线目标。v0.1 仅保留目标槽位。",
        implemented=False,
    ),
    VictoryCondition(
        condition_id="relic_altar",
        name="圣物祭坛",
        description="围绕圣物、祭坛和英灵召唤达成特殊胜利。v0.1 仅保留目标槽位。",
        implemented=False,
    ),
)


def _faction_name_by_id(world: WorldState) -> dict[str, str]:
    return {faction.faction_id: faction.name for faction in world.factions}


def city_counts_by_faction(world: WorldState) -> dict[str, int]:
    counts = {faction.faction_id: 0 for faction in world.factions}
    for city in world.cities:
        counts[city.owner_faction_id] = counts.get(city.owner_faction_id, 0) + 1
    return counts


def evaluate_strategic_status(world: WorldState) -> dict[str, Any]:
    counts = city_counts_by_faction(world)
    names = _faction_name_by_id(world)
    total_cities = len(world.cities)
    active_faction_ids = [faction_id for faction_id, count in counts.items() if count > 0]
    exiled_faction_ids = [faction_id for faction_id, count in counts.items() if count <= 0]

    unified_winner = None
    if total_cities > 0:
        for faction_id, count in counts.items():
            if count == total_cities:
                unified_winner = faction_id
                break

    elimination_winner = active_faction_ids[0] if total_cities > 0 and len(active_faction_ids) == 1 else None
    condition_statuses: list[dict[str, Any]] = []
    for condition in VICTORY_CONDITIONS:
        achieved = False
        winner_faction_id = None
        if condition.condition_id == "unify_cities" and unified_winner:
            achieved = True
            winner_faction_id = unified_winner
        elif condition.condition_id == "eliminate_enemy_factions" and elimination_winner:
            achieved = True
            winner_faction_id = elimination_winner
        condition_statuses.append(condition.to_status(achieved=achieved, winner_faction_id=winner_faction_id))

    achieved_conditions = [
        condition
        for condition in condition_statuses
        if condition["implemented"] and condition["achieved"]
    ]
    return {
        "city_counts_by_faction": counts,
        "active_faction_ids": active_faction_ids,
        "exiled_faction_ids": exiled_faction_ids,
        "active_factions": [
            {"id": faction_id, "name": names.get(faction_id, faction_id), "city_count": counts.get(faction_id, 0)}
            for faction_id in active_faction_ids
        ],
        "exiled_factions": [
            {"id": faction_id, "name": names.get(faction_id, faction_id), "city_count": counts.get(faction_id, 0)}
            for faction_id in exiled_faction_ids
        ],
        "victory_conditions": condition_statuses,
        "achieved_conditions": achieved_conditions,
        "campaign_complete": bool(achieved_conditions),
        "winner_faction_ids": sorted(
            {
                str(condition["winner_faction_id"])
                for condition in achieved_conditions
                if condition.get("winner_faction_id")
            }
        ),
    }


def record_strategic_status_events(world: WorldState) -> WorldState:
    next_world = WorldState.from_dict(copy.deepcopy(world.to_dict()))
    status = evaluate_strategic_status(next_world)
    names = _faction_name_by_id(next_world)

    for faction_id in status["exiled_faction_ids"]:
        tag = f"exile:{faction_id}"
        if tag in next_world.memory_tags:
            continue
        next_world.memory_tags.append(tag)
        next_world.event_log.append(
            EventLogEntry(
                month=next_world.current_month,
                category="faction_exiled",
                message=f"{names.get(faction_id, faction_id)}进入流亡状态。",
                related_ids=[faction_id],
            )
        )

    for condition in status["achieved_conditions"]:
        winner_faction_id = condition.get("winner_faction_id")
        if not winner_faction_id:
            continue
        tag = f"victory:{condition['id']}:{winner_faction_id}"
        if tag in next_world.memory_tags:
            continue
        next_world.memory_tags.append(tag)
        next_world.event_log.append(
            EventLogEntry(
                month=next_world.current_month,
                category="victory_achieved",
                message=f"{names.get(str(winner_faction_id), str(winner_faction_id))}达成胜利目标：{condition['name']}。",
                related_ids=[str(winner_faction_id), str(condition["id"])],
            )
        )

    next_world.validate()
    return next_world
