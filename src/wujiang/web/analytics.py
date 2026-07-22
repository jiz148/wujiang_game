from __future__ import annotations

import os
import sqlite3
import threading
import time
from collections import Counter
from contextlib import closing
from pathlib import Path
from statistics import median
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ANALYTICS_DB_PATH = PROJECT_ROOT / "var" / "analytics.sqlite3"

EVENT_FIELDS: dict[str, set[str]] = {
    "home_view": {"entry_state"},
    "quick_start_click": {"entry_state"},
    "quick_ai_start": {"match_id", "roster_code", "opponent_code"},
    "rematch_start": {"match_id", "mode", "duration_ms"},
    "tutorial_start": {"tutorial_id"},
    "tutorial_step": {"tutorial_id", "step_id", "status", "duration_ms"},
    "tutorial_complete": {"tutorial_id", "duration_ms"},
    "tutorial_exit": {"tutorial_id", "step_id", "reason", "duration_ms"},
    "first_effective_action": {"tutorial_id", "action_type", "duration_ms"},
    "match_start": {"match_id", "mode"},
    "match_end": {"match_id", "mode", "result", "duration_ms"},
    "invalid_action": {"match_id", "mode", "action_type", "reason"},
    "action_succeeded": {"match_id", "mode", "action_type"},
    "progression_view": {"source", "empty_state"},
    "strategy_campaign_create": {"campaign_id", "scenario_id"},
    "strategy_campaign_lock": {"campaign_id"},
    "strategy_campaign_enter": {"campaign_id"},
    "strategy_campaign_milestone": {"campaign_id", "month"},
    "strategy_battle_trigger": {"campaign_id", "month", "resolution_mode"},
    "strategy_campaign_complete": {"campaign_id", "month", "reason"},
    "strategy_campaign_archive": {"campaign_id", "month"},
    "strategy_campaign_continue_sandbox": {"campaign_id", "month"},
}
FUNNEL_EVENTS = (
    "home_view",
    "quick_start_click",
    "tutorial_start",
    "first_effective_action",
    "tutorial_complete",
    "quick_ai_start",
    "match_start",
    "match_end",
    "rematch_start",
    "strategy_campaign_create",
    "strategy_campaign_lock",
    "strategy_campaign_enter",
    "strategy_campaign_milestone",
    "strategy_battle_trigger",
    "strategy_campaign_complete",
    "strategy_campaign_archive",
    "strategy_campaign_continue_sandbox",
)
MAX_SESSION_ID_LENGTH = 64
MAX_VALUE_LENGTH = 160


class AnalyticsError(ValueError):
    pass


def analytics_database_path(raw_path: str | None = None) -> Path:
    configured = str(raw_path or os.environ.get("WUJIANG_ANALYTICS_DB") or "").strip()
    return Path(configured).expanduser() if configured else DEFAULT_ANALYTICS_DB_PATH


def _clean_text(value: Any, *, field: str, max_length: int = MAX_VALUE_LENGTH) -> str:
    cleaned = " ".join(str(value or "").strip().split())
    if len(cleaned) > max_length:
        raise AnalyticsError(f"{field} 过长。")
    return cleaned


class AnalyticsStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = analytics_database_path(str(db_path) if db_path is not None else None)
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
                    CREATE TABLE IF NOT EXISTS analytics_events (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      event_name TEXT NOT NULL,
                      anonymous_session_id TEXT NOT NULL,
                      occurred_at REAL NOT NULL,
                      properties_json TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_analytics_events_name_time
                      ON analytics_events(event_name, occurred_at);
                    CREATE INDEX IF NOT EXISTS idx_analytics_events_session
                      ON analytics_events(anonymous_session_id);
                    """
                )
                connection.commit()
            self._schema_ready = True

    def record(self, event_name: str, anonymous_session_id: str, properties: dict[str, Any] | None = None) -> int:
        import json

        normalized_name = _clean_text(event_name, field="event_name", max_length=48)
        if normalized_name not in EVENT_FIELDS:
            raise AnalyticsError("不支持的埋点事件。")
        session_id = _clean_text(
            anonymous_session_id,
            field="anonymous_session_id",
            max_length=MAX_SESSION_ID_LENGTH,
        )
        if not session_id:
            raise AnalyticsError("缺少匿名会话标识。")
        raw_properties = properties if isinstance(properties, dict) else {}
        allowed = EVENT_FIELDS[normalized_name]
        cleaned_properties: dict[str, Any] = {}
        for key, value in raw_properties.items():
            if key not in allowed or value is None:
                continue
            if key == "duration_ms":
                cleaned_properties[key] = max(0, min(int(value), 86_400_000))
            else:
                cleaned_properties[key] = _clean_text(value, field=key)
        with self._lock:
            self._ensure_schema()
            with closing(self._connect()) as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO analytics_events
                      (event_name, anonymous_session_id, occurred_at, properties_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    (normalized_name, session_id, time.time(), json.dumps(cleaned_properties, ensure_ascii=False)),
                )
                connection.commit()
                return int(cursor.lastrowid)

    def funnel(self) -> dict[str, Any]:
        import json

        self._ensure_schema()
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT event_name, anonymous_session_id, properties_json FROM analytics_events ORDER BY id"
            ).fetchall()
        event_counts = Counter(str(row["event_name"]) for row in rows)
        sessions_by_event: dict[str, set[str]] = {name: set() for name in EVENT_FIELDS}
        for row in rows:
            sessions_by_event[str(row["event_name"])].add(str(row["anonymous_session_id"]))
        home_sessions = len(sessions_by_event["home_view"])
        steps = []
        for name in FUNNEL_EVENTS:
            unique_sessions = len(sessions_by_event[name])
            steps.append(
                {
                    "event": name,
                    "events": event_counts[name],
                    "unique_sessions": unique_sessions,
                    "from_home_rate": round(unique_sessions / home_sessions, 4) if home_sessions else None,
                }
            )
        first_action_durations = []
        tutorial_durations = []
        match_durations = []
        started_match_ids: set[str] = set()
        ended_match_ids: set[str] = set()
        rematched_match_ids: set[str] = set()
        for row in rows:
            try:
                properties = json.loads(str(row["properties_json"] or "{}"))
            except json.JSONDecodeError:
                properties = {}
            match_id = str(properties.get("match_id") or "").strip()
            if row["event_name"] == "match_start" and match_id:
                started_match_ids.add(match_id)
            duration = properties.get("duration_ms")
            if not isinstance(duration, (int, float)):
                continue
            if row["event_name"] == "first_effective_action":
                first_action_durations.append(float(duration))
            elif row["event_name"] == "tutorial_complete":
                tutorial_durations.append(float(duration))
            elif row["event_name"] == "match_end":
                match_durations.append(float(duration))
                if match_id:
                    ended_match_ids.add(match_id)
            elif row["event_name"] == "rematch_start" and float(duration) <= 600_000:
                if match_id:
                    rematched_match_ids.add(match_id)
        tutorial_starts = len(sessions_by_event["tutorial_start"])
        tutorial_completes = len(sessions_by_event["tutorial_complete"])
        match_starts = len(started_match_ids) or event_counts["match_start"]
        match_ends = len(ended_match_ids) or event_counts["match_end"]
        successful_actions = event_counts["action_succeeded"]
        invalid_actions = event_counts["invalid_action"]
        attempted_actions = successful_actions + invalid_actions
        return {
            "generated_at": time.time(),
            "total_events": len(rows),
            "unique_sessions": len({str(row["anonymous_session_id"]) for row in rows}),
            "steps": steps,
            "metrics": {
                "first_effective_action_median_ms": median(first_action_durations) if first_action_durations else None,
                "tutorial_completion_rate": round(tutorial_completes / tutorial_starts, 4) if tutorial_starts else None,
                "match_completion_rate": round(match_ends / match_starts, 4) if match_starts else None,
                "invalid_action_rate": round(invalid_actions / attempted_actions, 4) if attempted_actions else None,
                "tutorial_duration_median_ms": median(tutorial_durations) if tutorial_durations else None,
                "match_duration_median_ms": median(match_durations) if match_durations else None,
                "action_attempts": attempted_actions,
                "rematch_within_10m_rate": (
                    round(len(rematched_match_ids & ended_match_ids) / len(ended_match_ids), 4)
                    if ended_match_ids
                    else None
                ),
            },
        }
