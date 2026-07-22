from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from wujiang.strategy.models import EventLogEntry, StrategyError, WorldState


FIRST_CAMPAIGN_SCENARIO_ID = "city_states_twelve_months_v1"
FIRST_CAMPAIGN_CITY_COUNT = 8
FIRST_CAMPAIGN_MAJOR_FACTION_COUNT = 2
FIRST_CAMPAIGN_NEUTRAL_CITY_STATE_COUNT = 6
FIRST_CAMPAIGN_MONTH_LIMIT = 12


def first_campaign_contract() -> dict[str, Any]:
    return {
        "id": FIRST_CAMPAIGN_SCENARIO_ID,
        "name": "十二月城邦争衡",
        "city_count": FIRST_CAMPAIGN_CITY_COUNT,
        "major_faction_count": FIRST_CAMPAIGN_MAJOR_FACTION_COUNT,
        "neutral_city_state_count": FIRST_CAMPAIGN_NEUTRAL_CITY_STATE_COUNT,
        "month_limit": FIRST_CAMPAIGN_MONTH_LIMIT,
        "expected_duration_minutes": [60, 90],
        "available_victory_routes": ["unify_cities", "eliminate_enemy_factions", "peaceful_integration", "time_limit_assessment"],
        "locked_systems": ["formal_armies", "world_mainline", "relic_altar"],
    }


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


def campaign_assessment_rankings(world: WorldState) -> list[dict[str, Any]]:
    counts = city_counts_by_faction(world)
    rows: list[dict[str, Any]] = []
    for faction in world.factions:
        if faction.is_neutral_city_state:
            continue
        owned = [city for city in world.cities if city.owner_faction_id == faction.faction_id]
        support_score = (
            round(sum(int(city.support_by_faction.get(faction.faction_id, 50)) for city in owned) / len(owned))
            if owned
            else 0
        )
        battle_wins = sum(
            1
            for battle in world.pending_battles
            if battle.status == "resolved" and battle.winner_faction_id == faction.faction_id
        )
        city_score = counts.get(faction.faction_id, 0) * 100
        survival_score = 100 if owned else 25
        battle_score = min(100, battle_wins * 25)
        neutral_influence_value = 0
        for neutral in world.factions:
            if not neutral.is_neutral_city_state:
                continue
            neutral_city = next((city for city in world.cities if city.owner_faction_id == neutral.faction_id), None)
            if neutral_city is None:
                continue
            influence = int(neutral.influence_by_faction.get(faction.faction_id, 0))
            local_support = int(neutral_city.support_by_faction.get(faction.faction_id, 35))
            neutral_influence_value += max(0, influence - 40) + max(0, local_support - 40)
        peaceful_integrations = sum(
            1
            for neutral in world.factions
            if neutral.is_neutral_city_state and neutral.diplomacy.get(faction.faction_id) == "peacefully_integrated"
        )
        influence_score = min(100, neutral_influence_value // 4 + peaceful_integrations * 25)
        mainline_score = 0
        rows.append(
            {
                "faction_id": faction.faction_id,
                "faction_name": faction.name,
                "city_count": counts.get(faction.faction_id, 0),
                "city_score": city_score,
                "support_score": support_score,
                "survival_score": survival_score,
                "battle_wins": battle_wins,
                "battle_score": battle_score,
                "peaceful_integrations": peaceful_integrations,
                "influence_score": influence_score,
                "mainline_score": mainline_score,
                "total_score": city_score + support_score + survival_score + battle_score + influence_score + mainline_score,
            }
        )
    rows.sort(
        key=lambda row: (
            -int(row["total_score"]),
            -int(row["city_count"]),
            -int(row["support_score"]),
            -int(row["battle_wins"]),
            -int(row["influence_score"]),
            str(row["faction_id"]),
        )
    )
    previous_key: tuple[int, int, int, int, int] | None = None
    current_rank = 0
    for index, row in enumerate(rows, start=1):
        tie_key = (
            int(row["total_score"]),
            int(row["city_count"]),
            int(row["support_score"]),
            int(row["battle_wins"]),
            int(row["influence_score"]),
        )
        if tie_key != previous_key:
            current_rank = index
            previous_key = tie_key
        row["rank"] = current_rank
    return rows


def _campaign_conclusion_payload(
    world: WorldState,
    *,
    reason: str,
    achieved_conditions: list[dict[str, Any]],
) -> dict[str, Any]:
    rankings = campaign_assessment_rankings(world)
    early_winners = sorted(
        {
            str(condition["winner_faction_id"])
            for condition in achieved_conditions
            if condition.get("winner_faction_id")
        }
    )
    assessment_winners = [str(row["faction_id"]) for row in rankings if int(row.get("rank", 0)) == 1]
    conclusion = {
        "state": "settled",
        "reason": reason,
        "result_label": "提前胜利" if reason == "early_victory" else "十二月评议",
        "concluded_month": world.current_month,
        "winner_faction_ids": early_winners if early_winners else assessment_winners,
        "achieved_condition_ids": [str(condition["id"]) for condition in achieved_conditions],
        "rankings": rankings,
        "continued_at_month": None,
    }
    from wujiang.strategy.campaign_retrospective import build_campaign_retrospective

    conclusion["retrospective"] = build_campaign_retrospective(world, conclusion)
    return conclusion


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

    major_faction_ids = {faction.faction_id for faction in world.factions if not faction.is_neutral_city_state}
    active_major_faction_ids = [faction_id for faction_id in active_faction_ids if faction_id in major_faction_ids]
    elimination_winner = (
        active_major_faction_ids[0]
        if total_cities > 0 and len(active_major_faction_ids) == 1
        else None
    )
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
    contract = dict(world.campaign_contract)
    month_limit = int(contract.get("month_limit", 0)) if contract else 0
    months_remaining = max(0, month_limit - world.current_month) if month_limit else None
    deadline_reached = bool(month_limit and world.current_month >= month_limit)
    conclusion = dict(world.campaign_conclusion)
    if not conclusion and contract and (achieved_conditions or deadline_reached):
        conclusion = _campaign_conclusion_payload(
            world,
            reason="early_victory" if achieved_conditions else "time_limit",
            achieved_conditions=achieved_conditions,
        )
    campaign_state = str(conclusion.get("state") or ("active" if contract else "legacy_sandbox"))
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
        "campaign_contract": contract,
        "month_limit": month_limit or None,
        "months_remaining": months_remaining,
        "deadline_reached": deadline_reached,
        "campaign_state": campaign_state,
        "awaiting_conclusion_choice": campaign_state == "settled",
        "can_advance_month": campaign_state not in {"settled", "archived"},
        "conclusion": conclusion,
        "campaign_complete": bool(achieved_conditions or conclusion),
        "winner_faction_ids": list(conclusion.get("winner_faction_ids") or sorted(
            {
                str(condition["winner_faction_id"])
                for condition in achieved_conditions
                if condition.get("winner_faction_id")
            }
        )),
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

    if status["conclusion"] and not next_world.campaign_conclusion:
        next_world.campaign_conclusion = dict(status["conclusion"])
        conclusion = next_world.campaign_conclusion
        winner_names = [names.get(faction_id, faction_id) for faction_id in conclusion["winner_faction_ids"]]
        next_world.memory_tags.append(
            f"campaign_concluded:{conclusion['reason']}:{conclusion['concluded_month']}"
        )
        next_world.event_log.append(
            EventLogEntry(
                month=next_world.current_month,
                category="campaign_concluded",
                message=f"战役进入{conclusion['result_label']}：{'、'.join(winner_names) or '并列'}位列第一。",
                related_ids=[str(item) for item in conclusion["winner_faction_ids"]],
            )
        )

    next_world.validate()
    return next_world


def require_campaign_orders_open(world: WorldState) -> None:
    status = evaluate_strategic_status(world)
    if status["campaign_state"] == "archived":
        raise StrategyError("战役已经归档，结局与复盘已冻结，不能继续下令。")
    if status["awaiting_conclusion_choice"]:
        raise StrategyError("战役已经进入结算，请先由房主选择结束战役或继续沙盒。")


def continue_campaign_as_sandbox(world: WorldState) -> WorldState:
    next_world = record_strategic_status_events(world)
    if not next_world.campaign_conclusion:
        raise StrategyError("战役尚未进入结算，不能转入结算后沙盒。")
    if str(next_world.campaign_conclusion.get("state")) == "archived":
        raise StrategyError("战役已经归档，不能再转入自由沙盒。")
    if str(next_world.campaign_conclusion.get("state")) == "sandbox":
        return next_world
    next_world.campaign_conclusion["state"] = "sandbox"
    next_world.campaign_conclusion["continued_at_month"] = next_world.current_month
    tag = f"campaign_continued_as_sandbox:{next_world.current_month}"
    if tag not in next_world.memory_tags:
        next_world.memory_tags.append(tag)
        next_world.event_log.append(
            EventLogEntry(
                month=next_world.current_month,
                category="campaign_continued_as_sandbox",
                message="房主选择保留本次结算结果，并继续自由沙盒。",
            )
        )
    next_world.validate()
    return next_world
