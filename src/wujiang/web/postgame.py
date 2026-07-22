from __future__ import annotations

import time
from typing import Any, Optional

from wujiang.engine.core import Battle
from wujiang.web.replay import ReplayRecorder


TEAM_NAMES = {1: "红队", 2: "蓝队"}


def _rounded(value: Any) -> float:
    return round(float(value or 0.0), 3)


def _mvp_score(entry: dict[str, Any]) -> float:
    return round(
        _rounded(entry.get("damage_dealt")) * 4
        + _rounded(entry.get("healing_done")) * 3
        + _rounded(entry.get("damage_taken")) * 1.5
        + int(entry.get("kills") or 0) * 2
        + int(entry.get("shields_broken") or 0) * 0.5
        + int(entry.get("chain_reactions") or 0) * 0.5,
        3,
    )


def _replay_step_for_event(replay: Optional[ReplayRecorder], event_id: int) -> Optional[int]:
    if replay is None:
        return None
    for step in replay.steps:
        if int(step.omniscient_battle.get("summary_event_count") or 0) >= event_id:
            return step.index
    return replay.last_index if replay.steps else None


def _key_event_payload(event: dict[str, Any], replay: Optional[ReplayRecorder]) -> dict[str, Any]:
    kind = str(event.get("kind") or "")
    actor = str(event.get("actor_name") or "系统")
    target = str(event.get("target_name") or "")
    action = str(event.get("action_name") or "")
    amount = _rounded(event.get("amount"))
    if kind == "defeat":
        title = f"{target} 被击破"
        detail = f"{actor} 通过【{action}】完成击破。"
    elif kind == "damage":
        title = f"单次高伤害：{amount:g}"
        detail = f"{actor} 使用【{action}】令 {target} 实际损失 {amount:g} 生命。"
    elif kind == "healing":
        title = f"关键治疗：{amount:g}"
        detail = f"{actor} 使用【{action}】令 {target} 实际恢复 {amount:g} 生命。"
    elif action == "投降":
        title = f"{target} 投降"
        detail = "对局因玩家投降结束。"
    elif action == "回合上限随机判定":
        title = "达到武将回合上限"
        detail = "系统按规则随机判定胜方。"
    else:
        title = "胜负已决定"
        detail = f"{target or '败方'}的{action or '结束条件'}已触发。"
    event_id = int(event.get("event_id") or 0)
    return {
        "event_id": event_id,
        "kind": kind,
        "turn_index": int(event.get("turn_index") or 1),
        "title": title,
        "detail": detail,
        "replay_step_index": _replay_step_for_event(replay, event_id),
    }


def _select_key_events(events: list[dict[str, Any]], replay: Optional[ReplayRecorder]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[int] = set()

    def add(event: Optional[dict[str, Any]]) -> None:
        if event is None or len(selected) >= 4:
            return
        event_id = int(event.get("event_id") or 0)
        if event_id in seen:
            return
        seen.add(event_id)
        selected.append(_key_event_payload(event, replay))

    defeats = [event for event in events if event.get("kind") == "defeat"]
    for event in sorted(defeats, key=lambda item: int(item.get("event_id") or 0))[-2:]:
        add(event)
    damage_events = [event for event in events if event.get("kind") == "damage"]
    add(max(damage_events, key=lambda item: float(item.get("amount") or 0.0), default=None))
    healing_events = [event for event in events if event.get("kind") == "healing"]
    add(max(healing_events, key=lambda item: float(item.get("amount") or 0.0), default=None))
    match_end_events = [event for event in events if event.get("kind") == "match_end"]
    add(match_end_events[-1] if match_end_events else None)
    return sorted(selected, key=lambda item: (item["turn_index"], item["event_id"]))


def build_postgame_summary(
    battle: Optional[Battle],
    replay: Optional[ReplayRecorder],
    *,
    seat_names: Optional[dict[int, str]] = None,
) -> dict[str, Any]:
    if battle is None or battle.winner is None:
        return {"available": False}
    entries = battle.combat_summary_entries()
    normalized_entries: list[dict[str, Any]] = []
    for entry in entries:
        item = dict(entry)
        for key in ("damage_dealt", "healing_done", "damage_taken", "healing_received"):
            item[key] = _rounded(item.get(key))
        item["contribution_score"] = _mvp_score(item)
        owner_seat_id = item.get("owner_seat_id")
        item["owner_name"] = (seat_names or {}).get(int(owner_seat_id)) if owner_seat_id is not None else None
        normalized_entries.append(item)
    normalized_entries.sort(
        key=lambda item: (int(item.get("player_id") or 0), -float(item["contribution_score"]), str(item.get("unit_id") or ""))
    )

    team_stats: list[dict[str, Any]] = []
    for team_id in (1, 2):
        team_entries = [entry for entry in normalized_entries if int(entry.get("player_id") or 0) == team_id]
        team_stats.append(
            {
                "team_id": team_id,
                "team_name": TEAM_NAMES[team_id],
                "damage_dealt": _rounded(sum(float(entry["damage_dealt"]) for entry in team_entries)),
                "healing_done": _rounded(sum(float(entry["healing_done"]) for entry in team_entries)),
                "damage_taken": _rounded(sum(float(entry["damage_taken"]) for entry in team_entries)),
                "kills": sum(int(entry.get("kills") or 0) for entry in team_entries),
                "shields_broken": sum(int(entry.get("shields_broken") or 0) for entry in team_entries),
                "chain_reactions": sum(int(entry.get("chain_reactions") or 0) for entry in team_entries),
            }
        )

    winner_entries = [entry for entry in normalized_entries if int(entry.get("player_id") or 0) == battle.winner]
    winner_entries.sort(
        key=lambda item: (
            -float(item["contribution_score"]),
            -int(item.get("kills") or 0),
            -float(item.get("damage_dealt") or 0.0),
            -float(item.get("healing_done") or 0.0),
            str(item.get("unit_id") or ""),
        )
    )
    mvp = dict(winner_entries[0]) if winner_entries else None
    if mvp is not None:
        mvp["explanation"] = (
            f"伤害 {mvp['damage_dealt']:g}、治疗 {mvp['healing_done']:g}、承伤 {mvp['damage_taken']:g}、"
            f"击破 {int(mvp.get('kills') or 0)}、破盾 {int(mvp.get('shields_broken') or 0)}、"
            f"连锁 {int(mvp.get('chain_reactions') or 0)}。"
        )

    finished_at = replay.finished_at if replay and replay.finished_at is not None else time.time()
    started_at = replay.created_at if replay is not None else finished_at
    reason_text = battle.win_reason_text or f"{TEAM_NAMES.get(battle.winner, f'玩家 {battle.winner}')}满足胜利条件。"
    return {
        "available": True,
        "winner_team_id": battle.winner,
        "winner_team_name": TEAM_NAMES.get(battle.winner, f"玩家 {battle.winner}"),
        "reason_code": battle.win_reason_code or "other",
        "reason_text": reason_text,
        "completed_turns": int(battle.completed_turns),
        "duration_seconds": max(0, int(finished_at - started_at)),
        "team_stats": team_stats,
        "hero_stats": normalized_entries,
        "mvp": mvp,
        "mvp_formula": "伤害×4 + 治疗×3 + 承伤×1.5 + 击破×2 + 破盾×0.5 + 连锁×0.5",
        "key_turns": _select_key_events(list(battle.summary_events), replay),
    }
