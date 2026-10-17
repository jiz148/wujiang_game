"""Persisting match results without battle recordings."""
from __future__ import annotations

from typing import Any
import time

from wujiang.platform.match_history import MatchHistoryError
from wujiang.platform.http import runtime

def record_finished_room_history(room) -> bool:
    if getattr(room, "status", None) != "finished" or getattr(room, "battle", None) is None:
        return False
    room._finish_replay()
    replay = getattr(room, "replay", None)
    participants_by_user: dict[int, dict[str, Any]] = {}
    winner_team_id = int(getattr(room.battle, "winner", 0) or 0)
    for seat in sorted(room.seats.values(), key=lambda item: item.player_id):
        if not seat.is_human or seat.account_user_id is None:
            continue
        user_id = int(seat.account_user_id)
        participants_by_user.setdefault(
            user_id,
            {
                "user_id": user_id,
                "seat_id": seat.player_id,
                "team_id": seat.team_id,
                "result": "win" if seat.team_id == winner_team_id else "loss",
            },
        )
    if not participants_by_user:
        return False
    summary = room.serialize_summary()
    postgame = dict(summary.get("postgame") or {})
    postgame["key_turns"] = [{key: value for key, value in item.items() if key != "replay_step_index"}
                             for item in postgame.get("key_turns") or []]
    match_id = (replay.match_id if replay is not None else None) or getattr(room, "current_match_id", None)
    if not match_id:
        return False
    finished_at = replay.finished_at if replay is not None and replay.finished_at else time.time()
    created_at = replay.created_at if replay is not None else finished_at
    return runtime.MATCH_HISTORY_STORE.record_match(
        match={
            "match_id": match_id,
            "room_id": room.room_id,
            "mode": room.mode,
            "mode_name": summary.get("mode_name"),
            "experience_kind": room.experience_kind,
            "created_at": created_at,
            "finished_at": finished_at,
            "winner_team_id": winner_team_id,
            "reason_code": postgame.get("reason_code"),
            "reason_text": postgame.get("reason_text"),
            "duration_seconds": postgame.get("duration_seconds"),
            "mvp_name": (postgame.get("mvp") or {}).get("name"),
            "replay_path": "",
            "replay_step_count": 0,
            "postgame": postgame,
            "seats": summary.get("seats") or [],
        },
        participants=list(participants_by_user.values()),
    )


def historical_replay_payload(user_id: int, match_id: str, step_index: Any) -> dict[str, Any]:
    runtime.MATCH_HISTORY_STORE.get_for_user(user_id, match_id)
    raise MatchHistoryError("历史录像已取消；只能在当前房间回看本场战斗。")
