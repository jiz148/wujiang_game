"""Persisting finished battle rooms into account match history."""
from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import time

from wujiang.platform.http.runtime import PROJECT_ROOT
from wujiang.platform.match_history import MatchHistoryError
from wujiang.tactical.heroes.registry import list_heroes
from wujiang.platform.http import runtime

def record_finished_room_history(room) -> bool:
    if getattr(room, "status", None) != "finished" or getattr(room, "battle", None) is None:
        return False
    # full 保持旧行为：没有可播放的录像就不入库。off / actions 仍然记胜负和席位，
    # 只是战绩上的回放入口关着——逐步回放依赖全量快照，动作档放不出来。
    mode = room._replay_mode() if hasattr(room, "_replay_mode") else "full"
    if mode != "off":
        room._ensure_replay_saved()
    replay = getattr(room, "replay", None)
    playable = bool(
        mode == "full"
        and replay is not None
        and replay.saved_path
        and replay.finished_at
    )
    if mode == "full" and not playable:
        return False
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
    postgame = summary.get("postgame") or {}
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
            "replay_path": replay.saved_path if playable else "",
            "replay_step_count": replay.step_count if playable else 0,
            "postgame": postgame,
            "seats": summary.get("seats") or [],
        },
        participants=list(participants_by_user.values()),
    )


def historical_replay_payload(user_id: int, match_id: str, step_index: Any) -> dict[str, Any]:
    history = runtime.MATCH_HISTORY_STORE.get_for_user(user_id, match_id)
    replay_path = Path(str(history.pop("replay_path") or ""))
    resolved_path = replay_path.resolve() if replay_path.is_absolute() else (PROJECT_ROOT / replay_path).resolve()
    replay_root = (PROJECT_ROOT / "replays").resolve()
    if replay_root not in resolved_path.parents or not resolved_path.is_file():
        raise MatchHistoryError("这场对局的回放文件不可用。")
    try:
        saved = json.loads(resolved_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MatchHistoryError("这场对局的回放文件无法读取。") from exc
    steps = saved.get("steps") if isinstance(saved.get("steps"), list) else []
    if not steps:
        raise MatchHistoryError("这场对局没有可播放的回放步骤。")
    try:
        requested_index = int(step_index)
    except (TypeError, ValueError):
        requested_index = len(steps) - 1
    if requested_index < 0:
        requested_index = len(steps) - 1
    resolved_index = max(0, min(requested_index, len(steps) - 1))
    battle = steps[resolved_index].get("omniscient_battle") or {}
    room = {
        "room_id": history["room_id"],
        "match_id": history["match_id"],
        "status": "finished",
        "mode": history["mode"],
        "mode_name": history["mode_name"],
        "experience_kind": history["experience_kind"],
        "historical": True,
        "viewer_player_id": None,
        "viewer_team_id": history["viewer_team_id"],
        "viewer_is_host": False,
        "can_rematch": False,
        "seats": history["seats"],
        "postgame": history["postgame"],
        "replay": {
            "available": True,
            "step_count": len(steps),
            "last_step_index": len(steps) - 1,
            "finished": True,
            "can_use_omniscient": True,
        },
    }
    return {
        "heroes": list_heroes(),
        "room": room,
        "battle": battle,
        "match": history,
        "replay": {
            **room["replay"],
            "step_index": resolved_index,
            "omniscient": True,
        },
    }
