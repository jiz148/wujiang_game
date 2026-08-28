from __future__ import annotations

import os
import sqlite3
import threading
import time
from collections import Counter
from contextlib import closing
from pathlib import Path
from statistics import mean, median
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
    "strategy_campaign_create": {
        "campaign_id", "scenario_id", "variant_id", "content_version", "balance_version",
    },
    "strategy_campaign_lock": {"campaign_id"},
    "strategy_quick_opening_choice": {"campaign_id", "choice_id"},
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
    "strategy_quick_opening_choice",
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

    def healthcheck(self) -> None:
        self._ensure_schema()
        connection = self._connect()
        try:
            connection.execute("SELECT 1 FROM analytics_events LIMIT 1").fetchone()
        finally:
            connection.close()

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
                    CREATE TABLE IF NOT EXISTS strategy_campaign_snapshots (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      campaign_id INTEGER NOT NULL,
                      checkpoint TEXT NOT NULL,
                      sampled_at REAL NOT NULL,
                      campaign_created_at REAL NOT NULL,
                      month INTEGER NOT NULL,
                      campaign_status TEXT NOT NULL,
                      scenario_id TEXT NOT NULL,
                      variant_id TEXT NOT NULL,
                      content_version TEXT NOT NULL,
                      balance_version TEXT NOT NULL,
                      seed INTEGER NOT NULL,
                      human_count INTEGER NOT NULL,
                      crisis_id TEXT NOT NULL,
                      crisis_stage TEXT NOT NULL,
                      victory_route TEXT NOT NULL,
                      duration_seconds REAL,
                      human_city_count INTEGER NOT NULL,
                      ai_city_count INTEGER NOT NULL,
                      neutral_city_count INTEGER NOT NULL,
                      leading_city_gap INTEGER NOT NULL,
                      peaceful_integrations INTEGER NOT NULL,
                      resolved_battles INTEGER NOT NULL,
                      human_battle_wins INTEGER NOT NULL,
                      ai_battle_wins INTEGER NOT NULL,
                      resources_json TEXT NOT NULL,
                      UNIQUE(campaign_id, month, checkpoint)
                    );
                    CREATE INDEX IF NOT EXISTS idx_strategy_snapshots_dimensions
                      ON strategy_campaign_snapshots(
                        content_version, balance_version, variant_id, seed,
                        human_count, crisis_id, crisis_stage, month
                      );
                    CREATE INDEX IF NOT EXISTS idx_strategy_snapshots_campaign_time
                      ON strategy_campaign_snapshots(campaign_id, sampled_at, id);
                    """
                )
                connection.commit()
            self._schema_ready = True

    def record_strategy_snapshot(self, campaign: Any, *, checkpoint: str) -> None:
        import json

        normalized_checkpoint = _clean_text(checkpoint, field="checkpoint", max_length=24)
        if normalized_checkpoint not in {"created", "roster", "locked", "month", "sandbox", "archived"}:
            raise AnalyticsError("不支持的战略快照节点。")
        world = campaign.world
        contract = dict(world.campaign_contract or {})
        variant = contract.get("opening_variant") if isinstance(contract.get("opening_variant"), dict) else {}
        human_members = [
            member for member in campaign.members
            if int(getattr(member, "user_id", 0)) > 0 and str(getattr(member, "role", "")).lower() != "ai"
        ]
        human_faction_ids = {str(member.faction_id) for member in human_members}
        major_faction_ids = {faction.faction_id for faction in world.factions if faction.is_major}
        ai_faction_ids = major_faction_ids - human_faction_ids
        city_counts = Counter(city.owner_faction_id for city in world.cities)
        human_city_count = sum(city_counts[faction_id] for faction_id in human_faction_ids)
        ai_city_count = sum(city_counts[faction_id] for faction_id in ai_faction_ids)
        neutral_city_count = sum(
            city_counts[faction.faction_id]
            for faction in world.factions
            if faction.is_neutral_city_state
        )
        major_city_counts = [city_counts[faction_id] for faction_id in major_faction_ids]
        leading_city_gap = max(major_city_counts, default=0) - min(major_city_counts, default=0)
        resources = {
            "human": {"food": 0, "money": 0, "ether": 0, "troops": 0},
            "ai": {"food": 0, "money": 0, "ether": 0, "troops": 0},
        }
        for faction in world.factions:
            bucket = "human" if faction.faction_id in human_faction_ids else "ai" if faction.faction_id in ai_faction_ids else ""
            if not bucket:
                continue
            for key in resources[bucket]:
                resources[bucket][key] += int(getattr(faction.resources, key, 0))
        for city in world.cities:
            bucket = "human" if city.owner_faction_id in human_faction_ids else "ai" if city.owner_faction_id in ai_faction_ids else ""
            if not bucket:
                continue
            for key in resources[bucket]:
                resources[bucket][key] += int(getattr(city.resources, key, 0))
        crisis = next((item for item in world.world_crises if item.status != "resolved"), None)
        if crisis is None and world.world_crises:
            crisis = world.world_crises[0]
        conclusion = dict(world.campaign_conclusion or {})
        achieved = [str(item) for item in conclusion.get("achieved_condition_ids") or []]
        victory_route = achieved[0] if achieved else ("time_limit_assessment" if conclusion.get("reason") == "time_limit" else "")
        resolved_battles = [battle for battle in world.pending_battles if battle.status == "resolved"]
        human_battle_wins = sum(battle.winner_faction_id in human_faction_ids for battle in resolved_battles)
        ai_battle_wins = sum(battle.winner_faction_id in ai_faction_ids for battle in resolved_battles)
        sampled_at = time.time()
        with self._lock:
            self._ensure_schema()
            with closing(self._connect()) as connection:
                connection.execute(
                    """
                    INSERT INTO strategy_campaign_snapshots (
                      campaign_id, checkpoint, sampled_at, campaign_created_at, month,
                      campaign_status, scenario_id, variant_id, content_version, balance_version,
                      seed, human_count, crisis_id, crisis_stage, victory_route, duration_seconds,
                      human_city_count, ai_city_count, neutral_city_count, leading_city_gap,
                      peaceful_integrations, resolved_battles, human_battle_wins, ai_battle_wins,
                      resources_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(campaign_id, month, checkpoint) DO UPDATE SET
                      sampled_at=excluded.sampled_at, campaign_status=excluded.campaign_status,
                      human_count=excluded.human_count, crisis_id=excluded.crisis_id,
                      crisis_stage=excluded.crisis_stage, victory_route=excluded.victory_route,
                      duration_seconds=excluded.duration_seconds,
                      human_city_count=excluded.human_city_count, ai_city_count=excluded.ai_city_count,
                      neutral_city_count=excluded.neutral_city_count, leading_city_gap=excluded.leading_city_gap,
                      peaceful_integrations=excluded.peaceful_integrations,
                      resolved_battles=excluded.resolved_battles,
                      human_battle_wins=excluded.human_battle_wins, ai_battle_wins=excluded.ai_battle_wins,
                      resources_json=excluded.resources_json
                    """,
                    (
                        int(campaign.campaign_id), normalized_checkpoint, sampled_at,
                        float(campaign.created_at), int(world.current_month), str(campaign.status),
                        str(contract.get("id") or "legacy_sandbox"),
                        str(variant.get("id") or contract.get("experience_kind") or "legacy_default"),
                        str(contract.get("content_version") or "legacy"),
                        str(contract.get("balance_version") or "legacy"), int(world.seed),
                        len(human_members), str(getattr(crisis, "crisis_id", "none")),
                        str(getattr(crisis, "stage", "none")), victory_route,
                        max(0.0, sampled_at - float(campaign.created_at)) if conclusion else None,
                        human_city_count, ai_city_count, neutral_city_count, leading_city_gap,
                        sum(event.category == "neutral_city_state_peacefully_integrated" for event in world.event_log),
                        len(resolved_battles), human_battle_wins, ai_battle_wins,
                        json.dumps(resources, sort_keys=True),
                    ),
                )
                connection.commit()

    def strategy_dashboard(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        import json

        allowed_filters = {
            "content_version", "balance_version", "variant_id", "seed",
            "human_count", "victory_route", "crisis_id", "crisis_stage", "month",
        }
        normalized_filters = {
            key: _clean_text(value, field=key, max_length=80)
            for key, value in (filters or {}).items()
            if key in allowed_filters and str(value or "").strip()
        }
        clauses: list[str] = []
        values: list[Any] = []
        for key, value in normalized_filters.items():
            clauses.append(f"{key} = ?")
            values.append(int(value) if key in {"seed", "human_count", "month"} else value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        self._ensure_schema()
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"SELECT * FROM strategy_campaign_snapshots{where} ORDER BY sampled_at, id",
                values,
            ).fetchall()
            all_rows = connection.execute("SELECT * FROM strategy_campaign_snapshots").fetchall()
        latest_by_campaign: dict[int, sqlite3.Row] = {}
        monthly_by_campaign_month: dict[tuple[int, int], sqlite3.Row] = {}
        for row in rows:
            latest_by_campaign[int(row["campaign_id"])] = row
            monthly_by_campaign_month[(int(row["campaign_id"]), int(row["month"]))] = row
        latest = list(latest_by_campaign.values())
        completed = [row for row in latest if str(row["victory_route"])]
        durations = [float(row["duration_seconds"]) for row in completed if row["duration_seconds"] is not None]

        def distribution(key: str, source: list[sqlite3.Row]) -> list[dict[str, Any]]:
            counts = Counter(str(row[key] or "none") for row in source)
            return [{"key": key_value, "campaigns": count} for key_value, count in sorted(counts.items())]

        monthly_rows: list[dict[str, Any]] = []
        for month in sorted({int(row["month"]) for row in monthly_by_campaign_month.values()}):
            bucket = [row for row in monthly_by_campaign_month.values() if int(row["month"]) == month]
            resource_rows = [json.loads(str(row["resources_json"])) for row in bucket]
            monthly_rows.append({
                "month": month,
                "campaigns": len(bucket),
                "avg_leading_city_gap": round(mean(int(row["leading_city_gap"]) for row in bucket), 2),
                "avg_human_food": round(mean(item["human"]["food"] for item in resource_rows), 2),
                "avg_ai_food": round(mean(item["ai"]["food"] for item in resource_rows), 2),
                "avg_human_money": round(mean(item["human"]["money"] for item in resource_rows), 2),
                "avg_ai_money": round(mean(item["ai"]["money"] for item in resource_rows), 2),
                "avg_human_ether": round(mean(item["human"]["ether"] for item in resource_rows), 2),
                "avg_ai_ether": round(mean(item["ai"]["ether"] for item in resource_rows), 2),
            })
        total_major_cities = sum(int(row["human_city_count"]) + int(row["ai_city_count"]) for row in latest)
        total_battle_wins = sum(int(row["human_battle_wins"]) + int(row["ai_battle_wins"]) for row in latest)
        return {
            "generated_at": time.time(),
            "sample_quality": "unverified_local_or_live",
            "real_player_gate_status": "not_evaluated",
            "filters": normalized_filters,
            "filter_options": {
                key: sorted({str(row[key]) for row in all_rows if str(row[key] or "")})
                for key in allowed_filters
            },
            "summary": {
                "campaigns": len(latest),
                "completed_campaigns": len(completed),
                "completion_rate": round(len(completed) / len(latest), 4) if latest else None,
                "median_completion_seconds": median(durations) if durations else None,
                "peaceful_integrations": sum(int(row["peaceful_integrations"]) for row in latest),
                "resolved_battles": sum(int(row["resolved_battles"]) for row in latest),
                "ai_city_share": round(sum(int(row["ai_city_count"]) for row in latest) / total_major_cities, 4) if total_major_cities else None,
                "ai_battle_win_share": round(sum(int(row["ai_battle_wins"]) for row in latest) / total_battle_wins, 4) if total_battle_wins else None,
            },
            "incomplete_by_last_month": distribution("month", [row for row in latest if not str(row["victory_route"])]),
            "victory_routes": distribution("victory_route", completed),
            "variants": distribution("variant_id", latest),
            "crisis_stages": distribution("crisis_stage", latest),
            "human_counts": distribution("human_count", latest),
            "monthly": monthly_rows,
        }

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
