from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from wujiang.strategic.models import EventLogEntry, StrategyError, WorldState
from wujiang.strategic.quick_campaign import QUICK_CAMPAIGN_SCENARIO_ID, quick_campaign_opening_status


FIRST_CAMPAIGN_SCENARIO_ID = "city_states_twelve_months_v1"
FIRST_CAMPAIGN_CITY_COUNT = 20
FIRST_CAMPAIGN_MAJOR_FACTION_COUNT = 2
FIRST_CAMPAIGN_NEUTRAL_CITY_STATE_COUNT = 18
MIN_MAJOR_FACTION_COUNT = 2
MAX_MAJOR_FACTION_COUNT = 10
FIRST_CAMPAIGN_MONTH_LIMIT = 12
CAMPAIGN_CONTENT_VERSION = "p8.1-content-1"
CAMPAIGN_BALANCE_VERSION = "p8.1-balance-1"
DEFAULT_CAMPAIGN_VARIANT_ID = "classic_frontier"

CAMPAIGN_OPENING_VARIANTS: dict[str, dict[str, Any]] = {
    "classic_frontier": {
        "id": "classic_frontier",
        "name": "经典边境",
        "core_question": "在雪鬼危机到来前，如何平衡城邦外交、战争准备与圣物经营？",
        "modifiers": ["使用标准钱粮、城防、兵员与以太开局。"],
    },
    "hungry_frontier": {
        "id": "hungry_frontier",
        "name": "粮荒前线",
        "core_question": "当全境粮食储备骤减时，是优先保城、贸易求援，还是冒险扩张？",
        "modifiers": ["所有城市开局粮食降至 70%。", "主要势力开局粮食降至 75%。"],
    },
    "fortified_leagues": {
        "id": "fortified_leagues",
        "name": "坚城联盟",
        "core_question": "中立城邦更难武力吞并时，能否用外交、影响力和长期围城打开局面？",
        "modifiers": ["中立城邦城防 +2。", "中立城邦守军 +120。", "当地自治支持 +15。"],
    },
    "ether_tide": {
        "id": "ether_tide",
        "name": "以太潮汐",
        "core_question": "以太充裕但主要势力资金紧张时，是否围绕英灵与圣物路线竞速？",
        "modifiers": ["所有城市开局以太 +60。", "主要势力开局以太 +30、金钱 -80。"],
    },
}


def campaign_variant_catalog_public() -> list[dict[str, Any]]:
    return [dict(CAMPAIGN_OPENING_VARIANTS[variant_id]) for variant_id in CAMPAIGN_OPENING_VARIANTS]


def campaign_map_scale(major_faction_count: int | None = None) -> tuple[int, int, int]:
    majors = max(
        MIN_MAJOR_FACTION_COUNT,
        min(MAX_MAJOR_FACTION_COUNT, int(major_faction_count or FIRST_CAMPAIGN_MAJOR_FACTION_COUNT)),
    )
    from wujiang.strategic.catalog import MAX_WORLD_CITIES

    city_count = min(MAX_WORLD_CITIES, max(FIRST_CAMPAIGN_CITY_COUNT, majors * 2))
    return majors, city_count, city_count - majors


def first_campaign_contract(
    variant_id: str = DEFAULT_CAMPAIGN_VARIANT_ID,
    *,
    major_faction_count: int | None = None,
) -> dict[str, Any]:
    normalized_variant_id = str(variant_id or DEFAULT_CAMPAIGN_VARIANT_ID).strip().lower()
    variant = CAMPAIGN_OPENING_VARIANTS.get(normalized_variant_id)
    if variant is None:
        raise StrategyError("未知的战役开局变体。")
    majors, city_count, neutrals = campaign_map_scale(major_faction_count)
    return {
        "id": FIRST_CAMPAIGN_SCENARIO_ID,
        "name": "十二月城邦争衡",
        "content_version": CAMPAIGN_CONTENT_VERSION,
        "balance_version": CAMPAIGN_BALANCE_VERSION,
        "opening_variant": dict(variant),
        "city_count": city_count,
        "major_faction_count": majors,
        "neutral_city_state_count": neutrals,
        "month_limit": FIRST_CAMPAIGN_MONTH_LIMIT,
        "expected_duration_minutes": [60, 90],
        "available_victory_routes": [
            "unify_cities",
            "eliminate_enemy_factions",
            "peaceful_integration",
            "world_mainline_victory",
            "time_limit_assessment",
        ],
        "locked_systems": [],
    }


def apply_campaign_opening_variant(world: WorldState) -> WorldState:
    contract = world.campaign_contract
    raw_variant = contract.get("opening_variant") if isinstance(contract, dict) else None
    if not isinstance(raw_variant, dict):
        return world
    variant_id = str(raw_variant.get("id") or DEFAULT_CAMPAIGN_VARIANT_ID)
    if variant_id not in CAMPAIGN_OPENING_VARIANTS:
        raise StrategyError("战役存档包含未知的开局变体。")
    tag = f"campaign_opening_variant:{variant_id}"
    if tag in world.memory_tags:
        return world
    if variant_id == "hungry_frontier":
        for city in world.cities:
            city.resources.food = max(0, city.resources.food * 70 // 100)
        for faction in world.factions:
            if faction.is_major:
                faction.resources.food = max(0, faction.resources.food * 75 // 100)
    elif variant_id == "fortified_leagues":
        neutral_ids = {faction.faction_id for faction in world.factions if faction.is_neutral_city_state}
        for city in world.cities:
            if city.owner_faction_id not in neutral_ids:
                continue
            city.defense += 2
            city.resources.troops += 120
            city.support_by_faction["local_autonomy"] = min(
                100, int(city.support_by_faction.get("local_autonomy", 0)) + 15
            )
        for faction in world.factions:
            if faction.is_neutral_city_state:
                faction.resources.troops += 120
    elif variant_id == "ether_tide":
        for city in world.cities:
            city.resources.ether += 60
        for faction in world.factions:
            if faction.is_major:
                faction.resources.ether += 30
                faction.resources.money = max(0, faction.resources.money - 80)
    world.memory_tags.append(tag)
    if variant_id != DEFAULT_CAMPAIGN_VARIANT_ID:
        world.event_log.append(
            EventLogEntry(
                month=1,
                category="campaign_opening_variant",
                message=(
                    f"本次战役采用“{raw_variant.get('name', variant_id)}”："
                    f"{raw_variant.get('core_question', '')}"
                ),
                visibility="player_visible",
            )
        )
    return world


@dataclass(frozen=True, slots=True)
class VictoryCondition:
    condition_id: str
    name: str
    description: str
    implemented: bool

    def to_status(
        self,
        *,
        achieved: bool = False,
        winner_faction_id: str | None = None,
        winner_faction_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        winner_ids = list(winner_faction_ids or ([winner_faction_id] if winner_faction_id else []))
        return {
            "id": self.condition_id,
            "name": self.name,
            "description": self.description,
            "implemented": self.implemented,
            "achieved": bool(achieved),
            "winner_faction_id": winner_faction_id or (winner_ids[0] if winner_ids else None),
            "winner_faction_ids": winner_ids,
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
        description="赢得第 11 月北境雪鬼决战；联军反攻分支可共享主线胜利。",
        implemented=True,
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
        if not faction.is_major:
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
        mainline_score = min(
            100,
            max(
                [
                    int(crisis.contributions_by_faction.get(faction.faction_id, 0))
                    for crisis in world.world_crises
                ]
                or [0]
            ),
        )
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
            str(winner_faction_id)
            for condition in achieved_conditions
            for winner_faction_id in (
                condition.get("winner_faction_ids")
                or ([condition.get("winner_faction_id")] if condition.get("winner_faction_id") else [])
            )
            if winner_faction_id
        }
    )
    assessment_winners = [str(row["faction_id"]) for row in rankings if int(row.get("rank", 0)) == 1]
    conclusion = {
        "state": "settled",
        "reason": reason,
        "result_label": (
            "提前胜利"
            if reason == "early_victory"
            else str(world.campaign_contract.get("assessment_label") or "十二月评议")
        ),
        "concluded_month": world.current_month,
        "winner_faction_ids": early_winners if early_winners else assessment_winners,
        "achieved_condition_ids": [str(condition["id"]) for condition in achieved_conditions],
        "rankings": rankings,
        "continued_at_month": None,
    }
    from wujiang.strategic.campaign_retrospective import build_campaign_retrospective

    conclusion["retrospective"] = build_campaign_retrospective(world, conclusion)
    return conclusion


def evaluate_strategic_status(world: WorldState) -> dict[str, Any]:
    counts = city_counts_by_faction(world)
    names = _faction_name_by_id(world)
    total_cities = len(world.cities)
    regular_faction_ids = {
        faction.faction_id for faction in world.factions if not faction.is_world_crisis
    }
    active_faction_ids = [
        faction_id for faction_id, count in counts.items()
        if count > 0 and faction_id in regular_faction_ids
    ]
    exiled_faction_ids = [
        faction_id for faction_id, count in counts.items()
        if count <= 0 and faction_id in regular_faction_ids
    ]

    unified_winner = None
    if total_cities > 0:
        for faction_id, count in counts.items():
            if count == total_cities and faction_id in regular_faction_ids:
                unified_winner = faction_id
                break

    major_faction_ids = {faction.faction_id for faction in world.factions if faction.is_major}
    active_major_faction_ids = [faction_id for faction_id in active_faction_ids if faction_id in major_faction_ids]
    elimination_winner = (
        active_major_faction_ids[0]
        if total_cities > 0 and len(active_major_faction_ids) == 1
        else None
    )
    mainline_winners = sorted(
        {
            faction_id
            for crisis in world.world_crises
            if crisis.stage == "resolved" and crisis.showdown_outcome == "victory"
            for faction_id in crisis.mainline_winner_faction_ids
        }
    )
    from wujiang.strategic.occupation import has_pending_occupation, pending_occupation_city_ids

    pending_occupation = has_pending_occupation(world)
    pending_occupation_ids = pending_occupation_city_ids(world)
    condition_statuses: list[dict[str, Any]] = []
    for condition in VICTORY_CONDITIONS:
        achieved = False
        winner_faction_id = None
        if condition.condition_id == "unify_cities" and unified_winner and not pending_occupation:
            achieved = True
            winner_faction_id = unified_winner
        elif condition.condition_id == "eliminate_enemy_factions" and elimination_winner and not pending_occupation:
            achieved = True
            winner_faction_id = elimination_winner
        elif condition.condition_id == "world_mainline" and mainline_winners:
            achieved = True
            winner_faction_id = mainline_winners[0]
        status = condition.to_status(
            achieved=achieved,
            winner_faction_id=winner_faction_id,
            winner_faction_ids=mainline_winners if condition.condition_id == "world_mainline" else None,
        )
        if (
            str(world.campaign_contract.get("id") or "") == QUICK_CAMPAIGN_SCENARIO_ID
            and condition.condition_id == "world_mainline"
        ):
            status["implemented"] = False
        condition_statuses.append(status)

    achieved_conditions = [
        condition
        for condition in condition_statuses
        if condition["implemented"] and condition["achieved"]
    ]
    contract = dict(world.campaign_contract)
    month_limit = int(contract.get("month_limit", 0)) if contract else 0
    months_remaining = max(0, month_limit - world.current_month) if month_limit else None
    deadline_reached = bool(month_limit and world.current_month >= month_limit)
    current_year = (max(1, world.current_month) - 1) // 12 + 1
    current_month_in_year = ((max(1, world.current_month) - 1) % 12) + 1
    conclusion = dict(world.campaign_conclusion)
    defer_early_victory = pending_occupation and not deadline_reached
    if not conclusion and contract and (achieved_conditions or deadline_reached) and not defer_early_victory:
        conclusion = _campaign_conclusion_payload(
            world,
            reason="early_victory" if achieved_conditions else "time_limit",
            achieved_conditions=achieved_conditions,
        )
    campaign_state = str(conclusion.get("state") or ("active" if contract else "legacy_sandbox"))
    quick_opening_by_faction = {
        faction.faction_id: quick_campaign_opening_status(world, faction.faction_id)
        for faction in world.factions
        if faction.is_major and str(contract.get("id") or "") == QUICK_CAMPAIGN_SCENARIO_ID
    }
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
        "year_limit": int(contract.get("year_limit", 0) or 0) or None,
        "months_remaining": months_remaining,
        "deadline_reached": deadline_reached,
        "current_year": current_year,
        "current_month_in_year": current_month_in_year,
        "calendar_label": f"第{current_year}年{current_month_in_year}月",
        "campaign_state": campaign_state,
        "awaiting_conclusion_choice": campaign_state == "settled",
        "can_advance_month": campaign_state not in {"settled", "archived"},
        "conclusion": conclusion,
        "awaiting_occupation_policy": pending_occupation,
        "pending_occupation_city_ids": pending_occupation_ids,
        "campaign_complete": bool((achieved_conditions and not pending_occupation) or conclusion),
        "winner_faction_ids": list(conclusion.get("winner_faction_ids") or sorted(
            {
                str(winner_faction_id)
                for condition in achieved_conditions
                for winner_faction_id in (
                    condition.get("winner_faction_ids")
                    or ([condition.get("winner_faction_id")] if condition.get("winner_faction_id") else [])
                )
                if winner_faction_id
            }
        )),
        "quick_opening_by_faction": quick_opening_by_faction,
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
        winner_ids = condition.get("winner_faction_ids") or (
            [condition["winner_faction_id"]] if condition.get("winner_faction_id") else []
        )
        for winner_faction_id in winner_ids:
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
