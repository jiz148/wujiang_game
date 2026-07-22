from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MATCH_HISTORY_DB_PATH = PROJECT_ROOT / "var" / "match-history.sqlite3"
RECENT_MATCH_LIMIT = 10
MASTERY_LEVELS: tuple[tuple[int, str], ...] = (
    (1, "初识"),
    (3, "熟练"),
    (8, "精通"),
    (15, "专家"),
    (30, "大师"),
)


class MatchHistoryError(ValueError):
    pass


def match_history_database_path(raw_path: str | None = None) -> Path:
    configured = str(raw_path or os.environ.get("WUJIANG_MATCH_HISTORY_DB") or "").strip()
    return Path(configured).expanduser() if configured else DEFAULT_MATCH_HISTORY_DB_PATH


class MatchHistoryStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = match_history_database_path(str(db_path) if db_path is not None else None)
        self._lock = threading.RLock()
        self._schema_ready = False

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._lock:
            if self._schema_ready:
                return
            with closing(self._connect()) as connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS match_history (
                      match_id TEXT PRIMARY KEY,
                      room_id TEXT NOT NULL,
                      mode TEXT NOT NULL,
                      mode_name TEXT NOT NULL,
                      experience_kind TEXT NOT NULL,
                      created_at REAL NOT NULL,
                      finished_at REAL NOT NULL,
                      winner_team_id INTEGER NOT NULL,
                      reason_code TEXT NOT NULL,
                      reason_text TEXT NOT NULL,
                      duration_seconds INTEGER NOT NULL,
                      mvp_name TEXT,
                      replay_path TEXT NOT NULL,
                      replay_step_count INTEGER NOT NULL,
                      postgame_json TEXT NOT NULL,
                      seats_json TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS match_participants (
                      match_id TEXT NOT NULL REFERENCES match_history(match_id) ON DELETE CASCADE,
                      user_id INTEGER NOT NULL,
                      seat_id INTEGER NOT NULL,
                      team_id INTEGER NOT NULL,
                      result TEXT NOT NULL,
                      PRIMARY KEY (match_id, user_id)
                    );
                    CREATE INDEX IF NOT EXISTS idx_match_participants_user
                      ON match_participants(user_id, match_id);
                    CREATE INDEX IF NOT EXISTS idx_match_history_finished
                      ON match_history(finished_at DESC);
                    """
                )
                connection.commit()
            self._schema_ready = True

    def record_match(
        self,
        *,
        match: dict[str, Any],
        participants: list[dict[str, Any]],
    ) -> bool:
        self._ensure_schema()
        match_id = str(match.get("match_id") or "").strip()
        if not match_id or not participants:
            return False
        with self._lock, closing(self._connect()) as connection:
            existing = connection.execute(
                "SELECT 1 FROM match_history WHERE match_id = ?",
                (match_id,),
            ).fetchone()
            if existing is not None:
                return False
            connection.execute(
                """
                INSERT INTO match_history (
                  match_id, room_id, mode, mode_name, experience_kind,
                  created_at, finished_at, winner_team_id, reason_code, reason_text,
                  duration_seconds, mvp_name, replay_path, replay_step_count,
                  postgame_json, seats_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    match_id,
                    str(match.get("room_id") or ""),
                    str(match.get("mode") or "classic"),
                    str(match.get("mode_name") or "标准选将"),
                    str(match.get("experience_kind") or "custom"),
                    float(match.get("created_at") or 0),
                    float(match.get("finished_at") or 0),
                    int(match.get("winner_team_id") or 0),
                    str(match.get("reason_code") or "other"),
                    str(match.get("reason_text") or "对局已结束。"),
                    max(0, int(match.get("duration_seconds") or 0)),
                    str(match.get("mvp_name") or "") or None,
                    str(match.get("replay_path") or ""),
                    max(0, int(match.get("replay_step_count") or 0)),
                    json.dumps(match.get("postgame") or {}, ensure_ascii=False),
                    json.dumps(match.get("seats") or [], ensure_ascii=False),
                ),
            )
            for participant in participants:
                connection.execute(
                    """
                    INSERT INTO match_participants (match_id, user_id, seat_id, team_id, result)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        match_id,
                        int(participant["user_id"]),
                        int(participant["seat_id"]),
                        int(participant["team_id"]),
                        str(participant["result"]),
                    ),
                )
            connection.commit()
        return True

    def _public_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "match_id": row["match_id"],
            "room_id": row["room_id"],
            "mode": row["mode"],
            "mode_name": row["mode_name"],
            "experience_kind": row["experience_kind"],
            "created_at": row["created_at"],
            "finished_at": row["finished_at"],
            "winner_team_id": row["winner_team_id"],
            "reason_code": row["reason_code"],
            "reason_text": row["reason_text"],
            "duration_seconds": row["duration_seconds"],
            "mvp_name": row["mvp_name"],
            "replay_step_count": row["replay_step_count"],
            "postgame": json.loads(row["postgame_json"]),
            "seats": json.loads(row["seats_json"]),
            "viewer_seat_id": row["seat_id"],
            "viewer_team_id": row["team_id"],
            "result": row["result"],
            "replay_available": bool(row["replay_path"] and row["replay_step_count"]),
        }

    def list_recent(self, user_id: int, *, limit: int = RECENT_MATCH_LIMIT) -> list[dict[str, Any]]:
        self._ensure_schema()
        normalized_limit = max(1, min(int(limit), RECENT_MATCH_LIMIT))
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT history.*, participant.seat_id, participant.team_id, participant.result
                FROM match_history AS history
                JOIN match_participants AS participant ON participant.match_id = history.match_id
                WHERE participant.user_id = ?
                ORDER BY history.finished_at DESC, history.match_id DESC
                LIMIT ?
                """,
                (int(user_id), normalized_limit),
            ).fetchall()
        return [self._public_row(row) for row in rows]

    def get_for_user(self, user_id: int, match_id: str) -> dict[str, Any]:
        self._ensure_schema()
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT history.*, participant.seat_id, participant.team_id, participant.result
                FROM match_history AS history
                JOIN match_participants AS participant ON participant.match_id = history.match_id
                WHERE participant.user_id = ? AND history.match_id = ?
                """,
                (int(user_id), str(match_id or "").strip()),
            ).fetchone()
        if row is None:
            raise MatchHistoryError("战绩不存在，或当前账号没有查看权限。")
        payload = self._public_row(row)
        payload["replay_path"] = row["replay_path"]
        return payload

    @staticmethod
    def _mastery_level(points: int) -> dict[str, Any]:
        normalized = max(0, int(points))
        current_threshold, current_name = MASTERY_LEVELS[0]
        for threshold, name in MASTERY_LEVELS:
            if normalized < threshold:
                break
            current_threshold, current_name = threshold, name
        next_level = next(((threshold, name) for threshold, name in MASTERY_LEVELS if threshold > normalized), None)
        return {
            "name": current_name,
            "threshold": current_threshold,
            "next_name": next_level[1] if next_level else None,
            "next_threshold": next_level[0] if next_level else None,
            "remaining": max(0, next_level[0] - normalized) if next_level else 0,
        }

    @staticmethod
    def _seat_roster(seats: list[dict[str, Any]], seat_id: int) -> list[dict[str, str]]:
        seat = next((item for item in seats if int(item.get("player_id") or 0) == int(seat_id)), None)
        if seat is None:
            return []
        roster: list[dict[str, str]] = []
        for hero in seat.get("hero_roster") or []:
            code = str(hero.get("code") or hero.get("hero_code") or "").strip()
            if code:
                roster.append({"code": code, "name": str(hero.get("name") or code)})
        if roster:
            return roster
        names_by_code = {
            str(hero.get("code") or ""): str(hero.get("name") or hero.get("code") or "")
            for hero in seat.get("heroes") or []
            if str(hero.get("code") or "")
        }
        return [
            {"code": str(code), "name": names_by_code.get(str(code), str(code))}
            for code, count in (seat.get("hero_counts") or {}).items()
            if int(count or 0) > 0
        ]

    def progression_overview(self, user_id: int) -> dict[str, Any]:
        self._ensure_schema()
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT history.match_id, history.finished_at, history.seats_json,
                       participant.seat_id, participant.result
                FROM match_history AS history
                JOIN match_participants AS participant ON participant.match_id = history.match_id
                WHERE participant.user_id = ?
                ORDER BY history.finished_at DESC, history.match_id DESC
                """,
                (int(user_id),),
            ).fetchall()

        heroes: dict[str, dict[str, Any]] = {}
        total_wins = 0
        for row in rows:
            won = str(row["result"]) == "win"
            if won:
                total_wins += 1
            try:
                seats = json.loads(row["seats_json"])
            except (TypeError, json.JSONDecodeError):
                seats = []
            distinct_roster = {
                hero["code"]: hero
                for hero in self._seat_roster(seats if isinstance(seats, list) else [], int(row["seat_id"]))
            }
            for code, hero in distinct_roster.items():
                progress = heroes.setdefault(
                    code,
                    {
                        "hero_code": code,
                        "hero_name": hero["name"],
                        "matches": 0,
                        "wins": 0,
                        "losses": 0,
                        "mastery_points": 0,
                        "last_played_at": 0.0,
                    },
                )
                progress["hero_name"] = hero["name"] or progress["hero_name"]
                progress["matches"] += 1
                progress["wins"] += 1 if won else 0
                progress["losses"] += 0 if won else 1
                progress["mastery_points"] += 2 if won else 1
                progress["last_played_at"] = max(float(progress["last_played_at"]), float(row["finished_at"] or 0))

        hero_progress: list[dict[str, Any]] = []
        for progress in heroes.values():
            level = self._mastery_level(int(progress["mastery_points"]))
            progress["mastery_level"] = level["name"]
            progress["mastery_threshold"] = level["threshold"]
            progress["next_mastery_level"] = level["next_name"]
            progress["next_mastery_threshold"] = level["next_threshold"]
            progress["points_to_next_level"] = level["remaining"]
            progress["win_rate"] = round(progress["wins"] / progress["matches"], 4) if progress["matches"] else 0.0
            hero_progress.append(progress)
        hero_progress.sort(
            key=lambda item: (
                -int(item["mastery_points"]),
                -int(item["matches"]),
                -int(item["wins"]),
                -float(item["last_played_at"]),
                str(item["hero_code"]),
            )
        )

        candidates = [item for item in hero_progress if item["next_mastery_level"]]
        candidates.sort(
            key=lambda item: (
                int(item["points_to_next_level"]),
                -int(item["mastery_points"]),
                -float(item["last_played_at"]),
                str(item["hero_code"]),
            )
        )
        if candidates:
            target = candidates[0]
            next_goal = {
                "kind": "mastery_level",
                "hero_code": target["hero_code"],
                "hero_name": target["hero_name"],
                "target_level": target["next_mastery_level"],
                "points_remaining": target["points_to_next_level"],
                "message": (
                    f"再获得 {target['points_to_next_level']} 点熟练度，"
                    f"{target['hero_name']}即可达到{target['next_mastery_level']}。"
                ),
            }
        elif hero_progress:
            next_goal = {
                "kind": "mastery_complete",
                "hero_code": hero_progress[0]["hero_code"],
                "hero_name": hero_progress[0]["hero_name"],
                "target_level": None,
                "points_remaining": 0,
                "message": f"{hero_progress[0]['hero_name']}已达到大师；尝试培养另一名武将。",
            }
        else:
            next_goal = {
                "kind": "first_match",
                "hero_code": None,
                "hero_name": None,
                "target_level": "初识",
                "points_remaining": 1,
                "message": "完成第一场正式对局，开始积累武将熟练度。",
            }

        total_matches = len(rows)
        return {
            "total_matches": total_matches,
            "total_wins": total_wins,
            "total_losses": total_matches - total_wins,
            "win_rate": round(total_wins / total_matches, 4) if total_matches else 0.0,
            "hero_progress": hero_progress,
            "next_goal": next_goal,
            "mastery_levels": [
                {"name": name, "threshold": threshold}
                for threshold, name in MASTERY_LEVELS
            ],
            "grants_gameplay_power": False,
        }
