from __future__ import annotations

import copy
from collections import defaultdict
from typing import Any

from wujiang.strategy.models import EventLogEntry, StrategyError, WorldState


IMPORTANT_EVENT_CATEGORIES = {
    "battle_resolved",
    "campaign_concluded",
    "city_crisis",
    "faction_exiled",
    "hero_founded_faction",
    "hero_ritual_summoned",
    "rebellion_suppressed",
    "rebellion_uprising",
    "story_consequence",
    "story_event_choice",
    "strategic_hero_appointed",
    "victory_achieved",
}

OFFICE_LABELS = {
    "lord": "主公",
    "grand_general": "大将军",
    "general": "将军",
    "governor": "城主",
}


def _faction_names(world: WorldState) -> dict[str, str]:
    return {faction.faction_id: faction.name for faction in world.factions}


def _city_names(world: WorldState) -> dict[str, str]:
    return {city.city_id: city.name for city in world.cities}


def _faction_outcomes(world: WorldState, conclusion: dict[str, Any]) -> list[dict[str, Any]]:
    winners = {str(item) for item in conclusion.get("winner_faction_ids", [])}
    rankings = {str(row.get("faction_id")): row for row in conclusion.get("rankings", [])}
    rows = []
    for faction in world.factions:
        if faction.is_neutral_city_state:
            continue
        ranking = rankings.get(faction.faction_id, {})
        city_count = int(ranking.get("city_count", 0))
        if faction.faction_id in winners:
            outcome = "victory"
            outcome_label = "胜利"
            summary = "在本次战役评议中位列第一。"
        elif city_count > 0:
            outcome = "survived"
            outcome_label = "存续"
            summary = f"战役结束时仍控制 {city_count} 座城市，势力得以存续。"
        else:
            outcome = "defeat_exile"
            outcome_label = "败北·流亡"
            summary = "战役结束时失去全部城市，但仍保留流亡与重建的故事状态。"
        rows.append(
            {
                "faction_id": faction.faction_id,
                "faction_name": faction.name,
                "outcome": outcome,
                "outcome_label": outcome_label,
                "summary": summary,
                "rank": int(ranking.get("rank", 0)),
                "total_score": int(ranking.get("total_score", 0)),
                "city_count": city_count,
            }
        )
    rows.sort(key=lambda row: (row["rank"] or 999, row["faction_id"]))
    return rows


def _key_months(world: WorldState, conclusion: dict[str, Any]) -> list[dict[str, Any]]:
    by_month: dict[int, list[str]] = defaultdict(list)
    for event in world.event_log:
        if event.category in IMPORTANT_EVENT_CATEGORIES and event.message not in by_month[event.month]:
            by_month[event.month].append(event.message)
    for report in world.monthly_reports:
        month = int(report.get("month", 0))
        for change in report.get("city_changes", []):
            if not change.get("owner_changed"):
                continue
            message = f"{change.get('city_name') or change.get('city_id')}易主。"
            if message not in by_month[month]:
                by_month[month].append(message)
    concluded_month = int(conclusion.get("concluded_month", world.current_month))
    conclusion_line = f"战役以{conclusion.get('result_label') or '正式结算'}结束。"
    if conclusion_line not in by_month[concluded_month]:
        by_month[concluded_month].append(conclusion_line)
    selected_months = sorted(by_month)
    if len(selected_months) > 8:
        selected_months = selected_months[:3] + selected_months[-5:]
    return [
        {"month": month, "headline": messages[0], "events": messages[:4]}
        for month in selected_months
        for messages in [by_month[month]]
    ]


def _city_changes(world: WorldState) -> list[dict[str, Any]]:
    faction_names = _faction_names(world)
    rows = []
    for report in world.monthly_reports:
        month = int(report.get("month", 0))
        for change in report.get("city_changes", []):
            if not change.get("owner_changed"):
                continue
            before = str(change.get("owner_before") or "")
            after = str(change.get("owner_after") or "")
            rows.append(
                {
                    "month": month,
                    "city_id": str(change.get("city_id") or ""),
                    "city_name": str(change.get("city_name") or change.get("city_id") or "未知城市"),
                    "owner_before": before,
                    "owner_before_name": faction_names.get(before, before),
                    "owner_after": after,
                    "owner_after_name": faction_names.get(after, after),
                }
            )
    recorded = {(row["month"], row["city_id"]) for row in rows}
    for battle in world.pending_battles:
        if battle.status != "resolved" or battle.winner_faction_id != battle.attacker_faction_id:
            continue
        key = (battle.month, battle.target_city_id)
        if key in recorded:
            continue
        rows.append(
            {
                "month": battle.month,
                "city_id": battle.target_city_id,
                "city_name": _city_names(world).get(battle.target_city_id, battle.target_city_id),
                "owner_before": battle.defender_faction_id,
                "owner_before_name": faction_names.get(battle.defender_faction_id, battle.defender_faction_id),
                "owner_after": battle.attacker_faction_id,
                "owner_after_name": faction_names.get(battle.attacker_faction_id, battle.attacker_faction_id),
            }
        )
    rows.sort(key=lambda row: (row["month"], row["city_id"]))
    return rows


def _battle_history(world: WorldState) -> list[dict[str, Any]]:
    faction_names = _faction_names(world)
    city_names = _city_names(world)
    rows = []
    for battle in world.pending_battles:
        if battle.status != "resolved":
            continue
        rows.append(
            {
                "battle_id": battle.battle_id,
                "month": battle.month,
                "source_city_id": battle.source_city_id,
                "source_city_name": city_names.get(battle.source_city_id, battle.source_city_id),
                "target_city_id": battle.target_city_id,
                "target_city_name": city_names.get(battle.target_city_id, battle.target_city_id),
                "attacker_faction_id": battle.attacker_faction_id,
                "attacker_faction_name": faction_names.get(battle.attacker_faction_id, battle.attacker_faction_id),
                "defender_faction_id": battle.defender_faction_id,
                "defender_faction_name": faction_names.get(battle.defender_faction_id, battle.defender_faction_id),
                "winner_faction_id": battle.winner_faction_id,
                "winner_faction_name": faction_names.get(str(battle.winner_faction_id), str(battle.winner_faction_id or "未决")),
                "resolution_mode": battle.resolution_mode,
                "grid_battle": battle.resolution_mode in {"manual", "ai_auto", "watch_ai"},
                "attacker_hero_codes": list(battle.attacker_hero_codes or []),
                "defender_hero_codes": list(battle.defender_hero_codes or []),
                "result_summary": str(battle.battle_result.get("summary") or (battle.report[-1] if battle.report else "战斗已结算。")),
            }
        )
    rows.sort(key=lambda row: (row["month"], row["battle_id"]))
    return rows


def _hero_experiences(world: WorldState, battles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    faction_names = _faction_names(world)
    offices = {office.office_id: office for office in world.offices}
    event_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    hero_codes = {hero.hero_code for hero in world.strategic_heroes}
    for event in world.event_log:
        for related_id in event.related_ids:
            if related_id in hero_codes and event.category in IMPORTANT_EVENT_CATEGORIES:
                event_rows[related_id].append({"month": event.month, "summary": event.message})
    rows = []
    for hero in world.strategic_heroes:
        appearances = [
            battle
            for battle in battles
            if hero.hero_code in battle["attacker_hero_codes"] or hero.hero_code in battle["defender_hero_codes"]
        ]
        wins = 0
        for battle in appearances:
            fought_for = (
                battle["attacker_faction_id"]
                if hero.hero_code in battle["attacker_hero_codes"]
                else battle["defender_faction_id"]
            )
            wins += int(battle["winner_faction_id"] == fought_for)
        office = offices.get(hero.office_id or "")
        rows.append(
            {
                "hero_code": hero.hero_code,
                "faction_id": hero.faction_id,
                "faction_name": faction_names.get(str(hero.faction_id), str(hero.faction_id or "无所属")),
                "final_status": hero.status,
                "office_type": office.office_type if office else None,
                "office_label": OFFICE_LABELS.get(office.office_type, office.office_type) if office else "未任职",
                "battle_appearances": len(appearances),
                "battle_wins": wins,
                "experiences": event_rows.get(hero.hero_code, [])[-4:],
            }
        )
    rows.sort(key=lambda row: (-row["battle_appearances"], row["hero_code"]))
    return rows


def build_campaign_retrospective(world: WorldState, conclusion: dict[str, Any]) -> dict[str, Any]:
    battles = _battle_history(world)
    return {
        "version": 1,
        "concluded_month": int(conclusion.get("concluded_month", world.current_month)),
        "result_label": str(conclusion.get("result_label") or "战役结算"),
        "faction_outcomes": _faction_outcomes(world, conclusion),
        "key_months": _key_months(world, conclusion),
        "city_changes": _city_changes(world),
        "battles": battles,
        "hero_experiences": _hero_experiences(world, battles),
        "summary": {
            "resolved_battles": len(battles),
            "grid_battles": sum(1 for battle in battles if battle["grid_battle"]),
            "cities_changed_hands": len(_city_changes(world)),
            "story_choices": sum(1 for event in world.event_log if event.category == "story_event_choice"),
        },
    }


def campaign_retrospective_public(world: WorldState) -> dict[str, Any]:
    retrospective = world.campaign_conclusion.get("retrospective")
    return copy.deepcopy(retrospective) if isinstance(retrospective, dict) else {}


def archive_campaign(world: WorldState) -> WorldState:
    from wujiang.strategy.objectives import record_strategic_status_events

    next_world = record_strategic_status_events(world)
    if not next_world.campaign_conclusion:
        raise StrategyError("战役尚未结算，不能归档。")
    if str(next_world.campaign_conclusion.get("state")) == "archived":
        return next_world
    next_world.campaign_conclusion["state"] = "archived"
    next_world.campaign_conclusion["archived_at_month"] = next_world.current_month
    tag = f"campaign_archived:{next_world.current_month}"
    if tag not in next_world.memory_tags:
        next_world.memory_tags.append(tag)
        next_world.event_log.append(
            EventLogEntry(
                month=next_world.current_month,
                category="campaign_archived",
                message="房主结束并归档本次战役；结局与复盘已冻结保存。",
            )
        )
    next_world.validate()
    return next_world
