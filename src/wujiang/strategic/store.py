from __future__ import annotations

import json
import hashlib
import os
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Any, Iterable

from wujiang.strategic.audit import append_operation_audit, sha256_text
from wujiang.strategic.backup import StrategyBackupManager, StrategyBackupRecord
from wujiang.strategic.battles import resolve_battle_room_result
from wujiang.strategic.command import faction_command_points, strategy_action_command_cost
from wujiang.strategic.generation import generate_random_world
from wujiang.strategic.migrations import (
    CURRENT_STRATEGY_SAVE_VERSION,
    migrate_world_payload,
    strategy_save_version,
)
from wujiang.strategic.models import CampaignMember, EventLogEntry, StrategyError, WorldState
from wujiang.platform.auth import AuthUser, DEFAULT_AUTH_DB_PATH


DEFAULT_STRATEGY_DB_PATH = DEFAULT_AUTH_DB_PATH
PRESENCE_TTL_SECONDS = 60 * 5
JOIN_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
JOIN_CODE_LENGTH = 6
AI_MEMBER_ROLE = "ai"
MAX_HUMAN_PLAYERS = 4
MAX_HUMANS_PER_FACTION = 2
CURRENT_STRATEGY_SCHEMA_VERSION = 3


def strategy_database_path(raw_path: str | None = None) -> Path:
    configured = str(raw_path or os.environ.get("WUJIANG_STRATEGY_DB") or "").strip()
    return Path(configured).expanduser() if configured else DEFAULT_STRATEGY_DB_PATH


def _faction_index(faction_id: str) -> int:
    try:
        parsed = int(str(faction_id).rsplit("_", 1)[-1])
    except (TypeError, ValueError):
        parsed = 0
    return max(1, parsed)


def _human_faction_counts(
    faction_ids: Iterable[str],
    members: Iterable[CampaignMember],
) -> dict[str, int]:
    counts = {str(faction_id): 0 for faction_id in faction_ids}
    for member in members:
        if (
            int(member.user_id) > 0
            and str(member.role).lower() != AI_MEMBER_ROLE
            and member.faction_id in counts
        ):
            counts[member.faction_id] += 1
    return counts


def _next_human_faction_id(
    faction_ids: Iterable[str],
    members: Iterable[CampaignMember],
    *,
    preferred_faction_id: str = "",
) -> str:
    ordered_ids = [str(faction_id) for faction_id in faction_ids]
    counts = _human_faction_counts(ordered_ids, members)
    preferred = str(preferred_faction_id or "")
    if preferred:
        if preferred not in counts:
            raise StrategyError("选择的主要势力不存在。")
        if counts[preferred] >= MAX_HUMANS_PER_FACTION:
            raise StrategyError("该势力的真人官职席位已满。", status=HTTPStatus.CONFLICT)
        return preferred
    available = [
        faction_id
        for faction_id in ordered_ids
        if counts[faction_id] < MAX_HUMANS_PER_FACTION
    ]
    if not available:
        raise StrategyError("战役真人席位已满。", status=HTTPStatus.CONFLICT)
    return min(available, key=lambda faction_id: (counts[faction_id], ordered_ids.index(faction_id)))


@dataclass(frozen=True, slots=True)
class ResumeStatus:
    can_resume: bool
    online_initial_user_ids: tuple[int, ...]
    missing_initial_user_ids: tuple[int, ...]
    initial_user_ids: tuple[int, ...]
    campaign_status: str
    submission_month: int
    ready_user_ids: tuple[int, ...]
    drafting_user_ids: tuple[int, ...]
    proxy_ai_user_ids: tuple[int, ...]
    can_advance_month: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "can_resume": self.can_resume,
            "can_view": True,
            "read_only": self.campaign_status == "archived",
            "access_mode": "read_only" if self.campaign_status == "archived" else "interactive",
            "online_initial_user_ids": list(self.online_initial_user_ids),
            "missing_initial_user_ids": list(self.missing_initial_user_ids),
            "initial_user_ids": list(self.initial_user_ids),
            "campaign_status": self.campaign_status,
            "submission_month": self.submission_month,
            "ready_user_ids": list(self.ready_user_ids),
            "drafting_user_ids": list(self.drafting_user_ids),
            "proxy_ai_user_ids": list(self.proxy_ai_user_ids),
            "can_advance_month": self.can_advance_month,
        }


@dataclass(frozen=True, slots=True)
class QueuedStrategyAction:
    action_id: int
    campaign_id: int
    user_id: int
    username: str
    faction_id: str
    month: int
    action_type: str
    action_key: str
    payload: dict[str, Any]
    status: str
    submitted_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.action_id,
            "campaign_id": self.campaign_id,
            "user_id": self.user_id,
            "username": self.username,
            "faction_id": self.faction_id,
            "month": self.month,
            "action_type": self.action_type,
            "action_key": self.action_key,
            "payload": dict(self.payload),
            "status": self.status,
            "submitted_at": self.submitted_at,
            "command_cost": strategy_action_command_cost(self.action_type, self.payload),
            "issuer_office_id": str(self.payload.get("issuer_office_id") or ""),
        }


@dataclass(frozen=True, slots=True)
class OfficeChangeRequestRecord:
    request_id: int
    campaign_id: int
    month: int
    request_type: str
    faction_id: str
    office_id: str
    initiator_user_id: int
    target_user_id: int
    status: str
    created_at: float
    resolved_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.request_id,
            "campaign_id": self.campaign_id,
            "month": self.month,
            "request_type": self.request_type,
            "faction_id": self.faction_id,
            "office_id": self.office_id,
            "initiator_user_id": self.initiator_user_id,
            "target_user_id": self.target_user_id,
            "status": self.status,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
        }


@dataclass(frozen=True, slots=True)
class OfficeTakeoverRecord:
    takeover_id: int
    campaign_id: int
    month: int
    faction_id: str
    office_id: str
    grantor_user_id: int
    delegate_user_id: int
    status: str
    created_at: float
    ended_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.takeover_id,
            "campaign_id": self.campaign_id,
            "month": self.month,
            "faction_id": self.faction_id,
            "office_id": self.office_id,
            "grantor_user_id": self.grantor_user_id,
            "delegate_user_id": self.delegate_user_id,
            "status": self.status,
            "created_at": self.created_at,
            "ended_at": self.ended_at,
        }


@dataclass(frozen=True, slots=True)
class StrategicBattleCheckpoint:
    room_id: str
    campaign_id: int
    battle_id: str
    participant_user_ids: tuple[int, ...]
    room_blob: bytes
    room_version: int
    format_version: int
    status: str
    checkpoint_hash: str
    created_at: float
    updated_at: float
    restart_count: int


@dataclass(frozen=True, slots=True)
class BattleRecoveryRecord:
    room_id: str
    battle_id: str
    participant_user_ids: tuple[int, ...]
    checkpoint_version: int
    format_version: int
    checkpoint_status: str
    integrity_status: str
    updated_at: float
    restart_count: int

    def to_public_dict(
        self,
        *,
        battle_status: str,
        archived: bool,
        participant_names: dict[int, str],
    ) -> dict[str, Any]:
        if self.integrity_status != "valid":
            status = "restart_required"
        elif battle_status == "resolved" or self.checkpoint_status == "completed":
            status = "archived_replay" if archived else "completed"
        else:
            status = "resume_available"
        return {
            "room_id": self.room_id,
            "battle_id": self.battle_id,
            "status": status,
            "battle_status": battle_status,
            "checkpoint_version": self.checkpoint_version,
            "format_version": self.format_version,
            "integrity_status": self.integrity_status,
            "updated_at": self.updated_at,
            "restart_count": self.restart_count,
            "participant_user_ids": list(self.participant_user_ids),
            "participant_names": [
                participant_names.get(user_id, f"账号 {user_id}")
                for user_id in self.participant_user_ids
            ],
            "read_only": archived,
        }


@dataclass(frozen=True, slots=True)
class CampaignRecord:
    campaign_id: int
    join_code: str
    join_code_enabled: bool
    name: str
    owner_user_id: int
    status: str
    current_month: int
    created_at: float
    updated_at: float
    world: WorldState
    members: tuple[CampaignMember, ...]
    queued_actions: tuple[QueuedStrategyAction, ...] = ()
    office_change_requests: tuple[OfficeChangeRequestRecord, ...] = ()
    office_takeovers: tuple[OfficeTakeoverRecord, ...] = ()
    battle_recoveries: tuple[BattleRecoveryRecord, ...] = ()

    def to_public_dict(
        self,
        *,
        resume_status: ResumeStatus | None = None,
        viewer_user_id: int | None = None,
        viewer_faction_id: str | None = None,
    ) -> dict[str, Any]:
        from wujiang.strategic.campaign_tutorial import campaign_tutorial_public
        from wujiang.strategic.campaign_retrospective import campaign_retrospective_public
        from wujiang.strategic.ai_goals import ai_strategic_goals_public
        from wujiang.strategic.monthly_cycle import monthly_cycle_public
        from wujiang.strategic.office_automation import office_coordination_public

        command_points_by_faction = {
            faction.faction_id: faction_command_points(faction.faction_id, self.queued_actions)
            for faction in self.world.factions
        }
        invite_status = "locked" if self.status != "lobby" else ("open" if self.join_code_enabled else "revoked")
        public_join_code = self.join_code if invite_status == "open" else ""
        payload = {
            "id": self.campaign_id,
            "detail": True,
            "join_code": public_join_code,
            "invite": {
                "status": invite_status,
                "join_code": public_join_code,
                "can_join": invite_status == "open",
            },
            "name": self.name,
            "owner_user_id": self.owner_user_id,
            "status": self.status,
            "current_month": self.current_month,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "world": self.world.to_public_dict(),
            "members": [member.to_dict() for member in self.members],
            "queued_actions": [action.to_dict() for action in self.queued_actions],
            "office_change_requests": [
                request.to_dict()
                for request in self.office_change_requests
            ],
            "office_takeovers": [takeover.to_dict() for takeover in self.office_takeovers],
            "command_points_by_faction": command_points_by_faction,
        }
        archived = str(self.world.campaign_conclusion.get("state") or "") == "archived"
        participants = {
            int(member.user_id): member.username
            for member in self.members
            if int(member.user_id) > 0
        }
        battle_status_by_id = {
            battle.battle_id: battle.status
            for battle in self.world.pending_battles
        }
        recovery_rows = [
            recovery.to_public_dict(
                battle_status=battle_status_by_id.get(recovery.battle_id, "missing"),
                archived=archived,
                participant_names=participants,
            )
            for recovery in self.battle_recoveries
        ]
        payload["recovery"] = {
            "access_mode": "read_only" if archived else "interactive",
            "read_only": archived,
            "battle_count": len(recovery_rows),
            "resume_available_count": sum(row["status"] == "resume_available" for row in recovery_rows),
            "restart_required_count": sum(row["status"] == "restart_required" for row in recovery_rows),
            "completed_count": sum(row["status"] in {"completed", "archived_replay"} for row in recovery_rows),
            "battles": recovery_rows,
        }
        payload["world"]["monthly_cycle"] = monthly_cycle_public(self.world, self.queued_actions)
        payload["world"]["campaign_tutorial"] = campaign_tutorial_public(self.world, self.queued_actions)
        payload["world"]["campaign_retrospective"] = campaign_retrospective_public(self.world)
        payload["world"]["ai_strategic_goals"] = ai_strategic_goals_public(self.world)
        payload["world"]["office_coordination"] = office_coordination_public(self.world, self.queued_actions)
        if resume_status is not None:
            payload["resume"] = resume_status.to_dict()
        if viewer_faction_id is None and viewer_user_id is not None:
            from wujiang.strategic.service import campaign_member_faction_id
            from wujiang.strategic.errors import StrategyError as CampaignAccessError

            try:
                viewer_faction_id = campaign_member_faction_id(self, int(viewer_user_id))
            except CampaignAccessError:
                viewer_faction_id = None
        if viewer_faction_id and self.status not in {"lobby"}:
            from wujiang.strategic.vision import mask_campaign_public_for_faction

            payload = mask_campaign_public_for_faction(payload, self.world, viewer_faction_id)
        payload["detail"] = True
        return payload

    def to_list_dict(self, *, resume_status: ResumeStatus | None = None) -> dict[str, Any]:
        invite_status = "locked" if self.status != "lobby" else ("open" if self.join_code_enabled else "revoked")
        public_join_code = self.join_code if invite_status == "open" else ""
        conclusion = dict(self.world.campaign_conclusion or {})
        payload = {
            "id": self.campaign_id,
            "detail": False,
            "join_code": public_join_code,
            "invite": {
                "status": invite_status,
                "join_code": public_join_code,
                "can_join": invite_status == "open",
            },
            "name": self.name,
            "owner_user_id": self.owner_user_id,
            "status": self.status,
            "current_month": self.current_month,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "world": {
                "current_month": self.world.current_month,
                "cities": [{"id": city.city_id} for city in self.world.cities],
                "factions": [{"id": faction.faction_id} for faction in self.world.factions],
                "strategic_status": {
                    "awaiting_conclusion_choice": bool(conclusion.get("state") == "awaiting_choice"),
                    "conclusion": {
                        "result_label": conclusion.get("result_label") or "",
                        "state": conclusion.get("state") or "",
                    },
                    "can_advance_month": bool(resume_status.can_advance_month) if resume_status is not None else False,
                },
            },
            "members": [member.to_dict() for member in self.members],
        }
        if resume_status is not None:
            payload["resume"] = resume_status.to_dict()
        return payload


class StrategyStore:
    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        backup_dir: str | Path | None = None,
        automatic_backup_retention: int = 12,
    ) -> None:
        self.db_path = strategy_database_path(str(db_path) if db_path is not None else None)
        self._lock = threading.RLock()
        self._schema_ready = False
        self._automatic_backup_keys: set[str] = set()
        self.backups = StrategyBackupManager(
            self.db_path,
            backup_dir=backup_dir,
            automatic_retention=automatic_backup_retention,
        )

    def _connect(self) -> sqlite3.Connection:
        maintenance_marker = self.db_path.with_name(f"{self.db_path.name}.maintenance.json")
        if maintenance_marker.exists():
            raise StrategyError("战略服务正在执行受限维护，请稍后重试。", status=HTTPStatus.SERVICE_UNAVAILABLE)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def healthcheck(self) -> None:
        self._ensure_schema()
        connection = self._connect()
        try:
            connection.execute("SELECT 1 FROM strategy_campaigns LIMIT 1").fetchone()
        finally:
            connection.close()

    @contextmanager
    def _connection(self) -> sqlite3.Connection:
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._lock:
            if self._schema_ready:
                return
            with self._connection() as connection:
                migration_required, source_schema_version = self._schema_migration_state(connection)
                if migration_required:
                    self._create_automatic_backup(
                        connection,
                        key="pre_schema_migration",
                        reason="pre_schema_migration",
                        schema_version=source_schema_version,
                    )
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS strategy_campaigns (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      join_code TEXT,
                      join_code_enabled INTEGER NOT NULL DEFAULT 1,
                      name TEXT NOT NULL,
                      owner_user_id INTEGER NOT NULL,
                      status TEXT NOT NULL,
                      current_month INTEGER NOT NULL,
                      world_json TEXT NOT NULL,
                      created_at REAL NOT NULL,
                      updated_at REAL NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS strategy_members (
                      campaign_id INTEGER NOT NULL REFERENCES strategy_campaigns(id) ON DELETE CASCADE,
                      user_id INTEGER NOT NULL,
                      username TEXT NOT NULL,
                      role TEXT NOT NULL,
                      faction_id TEXT NOT NULL,
                      is_initial_player INTEGER NOT NULL,
                      joined_at REAL NOT NULL,
                      PRIMARY KEY (campaign_id, user_id)
                    );

                    CREATE TABLE IF NOT EXISTS strategy_presence (
                      campaign_id INTEGER NOT NULL REFERENCES strategy_campaigns(id) ON DELETE CASCADE,
                      user_id INTEGER NOT NULL,
                      last_seen_at REAL NOT NULL,
                      is_online INTEGER NOT NULL,
                      PRIMARY KEY (campaign_id, user_id)
                    );

                    CREATE TABLE IF NOT EXISTS strategy_actions (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      campaign_id INTEGER NOT NULL REFERENCES strategy_campaigns(id) ON DELETE CASCADE,
                      user_id INTEGER NOT NULL,
                      username TEXT NOT NULL,
                      faction_id TEXT NOT NULL,
                      month INTEGER NOT NULL,
                      action_type TEXT NOT NULL,
                      action_key TEXT NOT NULL,
                      payload_json TEXT NOT NULL,
                      status TEXT NOT NULL,
                      submitted_at REAL NOT NULL,
                      UNIQUE (campaign_id, user_id, month, action_type, action_key)
                    );

                    CREATE TABLE IF NOT EXISTS strategy_month_submissions (
                      campaign_id INTEGER NOT NULL REFERENCES strategy_campaigns(id) ON DELETE CASCADE,
                      user_id INTEGER NOT NULL,
                      month INTEGER NOT NULL,
                      status TEXT NOT NULL,
                      updated_at REAL NOT NULL,
                      PRIMARY KEY (campaign_id, user_id, month)
                    );

                    CREATE TABLE IF NOT EXISTS strategy_office_change_requests (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      campaign_id INTEGER NOT NULL REFERENCES strategy_campaigns(id) ON DELETE CASCADE,
                      month INTEGER NOT NULL,
                      request_type TEXT NOT NULL,
                      faction_id TEXT NOT NULL,
                      office_id TEXT NOT NULL,
                      initiator_user_id INTEGER NOT NULL,
                      target_user_id INTEGER NOT NULL,
                      status TEXT NOT NULL,
                      created_at REAL NOT NULL,
                      resolved_at REAL
                    );

                    CREATE TABLE IF NOT EXISTS strategy_office_takeovers (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      campaign_id INTEGER NOT NULL REFERENCES strategy_campaigns(id) ON DELETE CASCADE,
                      month INTEGER NOT NULL,
                      faction_id TEXT NOT NULL,
                      office_id TEXT NOT NULL,
                      grantor_user_id INTEGER NOT NULL,
                      delegate_user_id INTEGER NOT NULL,
                      status TEXT NOT NULL,
                      created_at REAL NOT NULL,
                      ended_at REAL
                    );

                    CREATE TABLE IF NOT EXISTS strategy_battle_checkpoints (
                      room_id TEXT PRIMARY KEY,
                      campaign_id INTEGER NOT NULL REFERENCES strategy_campaigns(id) ON DELETE CASCADE,
                      battle_id TEXT NOT NULL,
                      participant_user_ids_json TEXT NOT NULL,
                      room_blob BLOB NOT NULL,
                      room_version INTEGER NOT NULL,
                      format_version INTEGER NOT NULL DEFAULT 1,
                      status TEXT NOT NULL,
                      checkpoint_hash TEXT NOT NULL,
                      created_at REAL NOT NULL,
                      updated_at REAL NOT NULL,
                      restart_count INTEGER NOT NULL DEFAULT 0
                    );

                    CREATE TABLE IF NOT EXISTS strategy_schema_migrations (
                      version INTEGER PRIMARY KEY,
                      name TEXT NOT NULL,
                      applied_at REAL NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS strategy_save_migrations (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      campaign_id INTEGER NOT NULL REFERENCES strategy_campaigns(id) ON DELETE CASCADE,
                      from_version INTEGER NOT NULL,
                      to_version INTEGER NOT NULL,
                      before_payload TEXT NOT NULL,
                      before_hash TEXT NOT NULL,
                      after_hash TEXT NOT NULL,
                      migrated_at REAL NOT NULL,
                      UNIQUE (campaign_id, from_version, to_version)
                    );

                    CREATE TABLE IF NOT EXISTS strategy_operation_audit (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      campaign_id INTEGER,
                      actor_user_id INTEGER,
                      actor_username TEXT NOT NULL DEFAULT '',
                      operation TEXT NOT NULL,
                      target_type TEXT NOT NULL,
                      target_id TEXT NOT NULL,
                      result TEXT NOT NULL,
                      before_hash TEXT NOT NULL DEFAULT '',
                      after_hash TEXT NOT NULL DEFAULT '',
                      details_json TEXT NOT NULL DEFAULT '{}',
                      created_at REAL NOT NULL,
                      previous_hash TEXT NOT NULL,
                      entry_hash TEXT NOT NULL UNIQUE
                    );

                    CREATE INDEX IF NOT EXISTS idx_strategy_members_user_id ON strategy_members(user_id);
                    CREATE INDEX IF NOT EXISTS idx_strategy_presence_campaign ON strategy_presence(campaign_id);
                    CREATE INDEX IF NOT EXISTS idx_strategy_actions_campaign_month
                      ON strategy_actions(campaign_id, month, status, submitted_at);
                    CREATE INDEX IF NOT EXISTS idx_strategy_month_submissions_campaign
                      ON strategy_month_submissions(campaign_id, month, status);
                    CREATE INDEX IF NOT EXISTS idx_strategy_office_changes_campaign
                      ON strategy_office_change_requests(campaign_id, status, created_at);
                    CREATE INDEX IF NOT EXISTS idx_strategy_office_takeovers_campaign
                      ON strategy_office_takeovers(campaign_id, status, created_at);
                    CREATE INDEX IF NOT EXISTS idx_strategy_battle_checkpoints_campaign
                      ON strategy_battle_checkpoints(campaign_id, status, updated_at);
                    CREATE INDEX IF NOT EXISTS idx_strategy_operation_audit_campaign
                      ON strategy_operation_audit(campaign_id, created_at);
                    CREATE INDEX IF NOT EXISTS idx_strategy_operation_audit_operation
                      ON strategy_operation_audit(operation, created_at);
                    """
                )
                self._migrate_schema(connection)
            self._schema_ready = True

    def _schema_migration_state(self, connection: sqlite3.Connection) -> tuple[bool, int]:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        if "strategy_campaigns" not in tables:
            return False, 0
        if "strategy_schema_migrations" not in tables:
            return True, 0
        applied = {
            int(row[0])
            for row in connection.execute("SELECT version FROM strategy_schema_migrations").fetchall()
        }
        future_versions = sorted(
            version for version in applied
            if version > CURRENT_STRATEGY_SCHEMA_VERSION
        )
        if future_versions:
            raise StrategyError(
                f"战略数据库版本 {future_versions[-1]} 高于当前支持版本 {CURRENT_STRATEGY_SCHEMA_VERSION}，请升级服务。",
                status=HTTPStatus.CONFLICT,
            )
        source_version = max(applied, default=0)
        required = any(
            version not in applied
            for version in range(1, CURRENT_STRATEGY_SCHEMA_VERSION + 1)
        )
        return required, source_version

    def _create_automatic_backup(
        self,
        connection: sqlite3.Connection,
        *,
        key: str,
        reason: str,
        schema_version: int = CURRENT_STRATEGY_SCHEMA_VERSION,
    ) -> StrategyBackupRecord | None:
        if key in self._automatic_backup_keys:
            return None
        record = self.backups.create_backup(
            reason=reason,
            automatic=True,
            source_connection=connection,
            strategy_schema_version=schema_version,
        )
        self._automatic_backup_keys.add(key)
        return record

    def create_backup(self, *, reason: str = "manual") -> StrategyBackupRecord:
        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                return self.backups.create_backup(
                    reason=reason,
                    automatic=False,
                    source_connection=connection,
                    strategy_schema_version=CURRENT_STRATEGY_SCHEMA_VERSION,
                )

    def list_backups(self) -> list[StrategyBackupRecord]:
        return self.backups.list_backups()

    def drill_backup_restore(self, backup_path: str | Path) -> dict[str, Any]:
        with self._lock:
            return self.backups.drill_restore(backup_path)

    def _migrate_schema(self, connection: sqlite3.Connection) -> None:
        applied_versions = {
            int(row["version"])
            for row in connection.execute(
                "SELECT version FROM strategy_schema_migrations ORDER BY version"
            ).fetchall()
        }
        future_versions = sorted(
            version for version in applied_versions
            if version > CURRENT_STRATEGY_SCHEMA_VERSION
        )
        if future_versions:
            raise StrategyError(
                f"战略数据库版本 {future_versions[-1]} 高于当前支持版本 {CURRENT_STRATEGY_SCHEMA_VERSION}，请升级服务。",
                status=HTTPStatus.CONFLICT,
            )
        migrations = {
            1: ("campaign_invitation_baseline", self._migrate_schema_v1),
            2: ("save_migration_rollback_payload", self._migrate_schema_v2),
            3: ("tamper_evident_operation_audit", self._migrate_schema_v3),
        }
        for version in range(1, CURRENT_STRATEGY_SCHEMA_VERSION + 1):
            if version in applied_versions:
                continue
            name, migration = migrations[version]
            migration(connection)
            connection.execute(
                "INSERT INTO strategy_schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                (version, name, time.time()),
            )

    def _migrate_schema_v1(self, connection: sqlite3.Connection) -> None:
        campaign_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(strategy_campaigns)").fetchall()
        }
        if "join_code" not in campaign_columns:
            connection.execute("ALTER TABLE strategy_campaigns ADD COLUMN join_code TEXT")
        if "join_code_enabled" not in campaign_columns:
            connection.execute(
                "ALTER TABLE strategy_campaigns ADD COLUMN join_code_enabled INTEGER NOT NULL DEFAULT 1"
            )
        rows = connection.execute(
            """
            SELECT id
            FROM strategy_campaigns
            WHERE join_code IS NULL OR TRIM(join_code) = ''
            """
        ).fetchall()
        for row in rows:
            connection.execute(
                "UPDATE strategy_campaigns SET join_code = ? WHERE id = ?",
                (self._unique_join_code(connection), int(row["id"])),
            )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_strategy_campaigns_join_code
              ON strategy_campaigns(join_code)
            """
        )

    def _migrate_schema_v2(self, connection: sqlite3.Connection) -> None:
        migration_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(strategy_save_migrations)").fetchall()
        }
        if "before_payload" not in migration_columns:
            connection.execute(
                "ALTER TABLE strategy_save_migrations ADD COLUMN before_payload TEXT NOT NULL DEFAULT ''"
            )

    def _migrate_schema_v3(self, connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS strategy_operation_audit (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              campaign_id INTEGER,
              actor_user_id INTEGER,
              actor_username TEXT NOT NULL DEFAULT '',
              operation TEXT NOT NULL,
              target_type TEXT NOT NULL,
              target_id TEXT NOT NULL,
              result TEXT NOT NULL,
              before_hash TEXT NOT NULL DEFAULT '',
              after_hash TEXT NOT NULL DEFAULT '',
              details_json TEXT NOT NULL DEFAULT '{}',
              created_at REAL NOT NULL,
              previous_hash TEXT NOT NULL,
              entry_hash TEXT NOT NULL UNIQUE
            );
            CREATE INDEX IF NOT EXISTS idx_strategy_operation_audit_campaign
              ON strategy_operation_audit(campaign_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_strategy_operation_audit_operation
              ON strategy_operation_audit(operation, created_at);
            """
        )

    def _audit_operation(
        self,
        connection: sqlite3.Connection,
        *,
        campaign_id: int,
        actor_user_id: int | None,
        operation: str,
        target_type: str,
        target_id: str | int,
        before_hash: str = "",
        after_hash: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        actor_username = ""
        if actor_user_id is not None:
            row = connection.execute(
                "SELECT username FROM strategy_members WHERE campaign_id = ? AND user_id = ?",
                (int(campaign_id), int(actor_user_id)),
            ).fetchone()
            actor_username = str(row["username"]) if row is not None else ""
        append_operation_audit(
            connection,
            campaign_id=int(campaign_id),
            actor_user_id=int(actor_user_id) if actor_user_id is not None else None,
            actor_username=actor_username,
            operation=operation,
            target_type=target_type,
            target_id=target_id,
            before_hash=before_hash,
            after_hash=after_hash,
            details=details,
        )

    def _load_campaign_world(
        self,
        connection: sqlite3.Connection,
        campaign_id: int,
        serialized_world: str,
    ) -> WorldState:
        try:
            raw = json.loads(str(serialized_world))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise StrategyError(
                f"战役 {campaign_id} 的存档不是有效 JSON，已拒绝读取。",
                status=HTTPStatus.CONFLICT,
            ) from exc
        if not isinstance(raw, dict):
            raise StrategyError(
                f"战役 {campaign_id} 的存档根节点必须是对象。",
                status=HTTPStatus.CONFLICT,
            )
        from_version = strategy_save_version(raw)
        migrated = migrate_world_payload(raw)
        world = WorldState.from_dict(migrated)
        if from_version < CURRENT_STRATEGY_SAVE_VERSION:
            self._create_automatic_backup(
                connection,
                key="pre_save_migration",
                reason="pre_save_migration",
            )
            canonical = json.dumps(world.to_dict(), ensure_ascii=False, sort_keys=True)
            before_hash = hashlib.sha256(str(serialized_world).encode("utf-8")).hexdigest()
            after_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            connection.execute(
                "UPDATE strategy_campaigns SET world_json = ? WHERE id = ?",
                (canonical, int(campaign_id)),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO strategy_save_migrations
                  (campaign_id, from_version, to_version, before_payload, before_hash, after_hash, migrated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(campaign_id),
                    from_version,
                    CURRENT_STRATEGY_SAVE_VERSION,
                    str(serialized_world),
                    before_hash,
                    after_hash,
                    time.time(),
                ),
            )
        return world

    def _unique_join_code(self, connection: sqlite3.Connection) -> str:
        while True:
            code = "".join(secrets.choice(JOIN_CODE_ALPHABET) for _ in range(JOIN_CODE_LENGTH))
            existing = connection.execute(
                "SELECT 1 FROM strategy_campaigns WHERE join_code = ?",
                (code,),
            ).fetchone()
            if existing is None:
                return code

    def save_battle_checkpoint(
        self,
        *,
        campaign_id: int,
        battle_id: str,
        room_id: str,
        participant_user_ids: Iterable[int],
        room_blob: bytes,
        room_version: int,
        status: str,
        restarted: bool = False,
    ) -> StrategicBattleCheckpoint:
        normalized_room_id = str(room_id or "").strip().upper()
        normalized_battle_id = str(battle_id or "").strip()
        participants = tuple(sorted({int(user_id) for user_id in participant_user_ids if int(user_id) > 0}))
        blob = bytes(room_blob)
        if not normalized_room_id or not normalized_battle_id or not participants or not blob:
            raise StrategyError("战略战斗检查点缺少房间、战斗、参与者或状态数据。")
        normalized_status = "completed" if str(status).lower() in {"finished", "completed"} else "active"
        digest = hashlib.sha256(blob).hexdigest()
        now = time.time()
        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                campaign_row = connection.execute(
                    "SELECT status, world_json FROM strategy_campaigns WHERE id = ?",
                    (int(campaign_id),),
                ).fetchone()
                if campaign_row is None:
                    raise StrategyError("战略战役不存在。", status=HTTPStatus.NOT_FOUND)
                world = self._load_campaign_world(
                    connection,
                    int(campaign_id),
                    str(campaign_row["world_json"]),
                )
                bound = next(
                    (
                        item for item in world.pending_battles
                        if item.battle_id == normalized_battle_id
                        and str(item.battle_room_id or "").strip().upper() == normalized_room_id
                    ),
                    None,
                )
                if bound is None:
                    raise StrategyError("检查点房间未绑定到指定战略战斗。", status=HTTPStatus.CONFLICT)
                existing = connection.execute(
                    "SELECT * FROM strategy_battle_checkpoints WHERE room_id = ?",
                    (normalized_room_id,),
                ).fetchone()
                if str(campaign_row["status"]) == "archived":
                    if existing is None:
                        raise StrategyError("归档战役不能再创建战斗检查点。", status=HTTPStatus.CONFLICT)
                    return self._battle_checkpoint_from_row(existing)
                created_at = float(existing["created_at"]) if existing is not None else now
                restart_count = int(existing["restart_count"]) if existing is not None else 0
                if restarted:
                    restart_count += 1
                connection.execute(
                    """
                    INSERT INTO strategy_battle_checkpoints
                      (room_id, campaign_id, battle_id, participant_user_ids_json, room_blob,
                       room_version, format_version, status, checkpoint_hash, created_at, updated_at, restart_count)
                    VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
                    ON CONFLICT(room_id) DO UPDATE SET
                      campaign_id = excluded.campaign_id,
                      battle_id = excluded.battle_id,
                      participant_user_ids_json = excluded.participant_user_ids_json,
                      room_blob = excluded.room_blob,
                      room_version = excluded.room_version,
                      format_version = excluded.format_version,
                      status = excluded.status,
                      checkpoint_hash = excluded.checkpoint_hash,
                      updated_at = excluded.updated_at,
                      restart_count = excluded.restart_count
                    """,
                    (
                        normalized_room_id,
                        int(campaign_id),
                        normalized_battle_id,
                        json.dumps(list(participants)),
                        sqlite3.Binary(blob),
                        int(room_version),
                        normalized_status,
                        digest,
                        created_at,
                        now,
                        restart_count,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM strategy_battle_checkpoints WHERE room_id = ?",
                    (normalized_room_id,),
                ).fetchone()
                assert row is not None
                previous_digest = str(existing["checkpoint_hash"]) if existing is not None else ""
                if existing is None or restarted or normalized_status == "completed":
                    operation = (
                        "battle.checkpoint_created" if existing is None
                        else "battle.checkpoint_restarted" if restarted
                        else "battle.checkpoint_completed"
                    )
                    self._audit_operation(
                        connection, campaign_id=int(campaign_id), actor_user_id=None,
                        operation=operation, target_type="battle_checkpoint",
                        target_id=normalized_room_id, before_hash=previous_digest,
                        after_hash=digest, details={"battle_id": normalized_battle_id,
                                                    "room_version": int(room_version),
                                                    "status": normalized_status,
                                                    "restart_count": restart_count},
                    )
                return self._battle_checkpoint_from_row(row)

    def battle_checkpoint_for_user(self, room_id: str, user_id: int) -> StrategicBattleCheckpoint:
        normalized_room_id = str(room_id or "").strip().upper()
        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                row = connection.execute(
                    """
                    SELECT checkpoint.*
                    FROM strategy_battle_checkpoints AS checkpoint
                    JOIN strategy_members AS member
                      ON member.campaign_id = checkpoint.campaign_id
                    WHERE checkpoint.room_id = ? AND member.user_id = ?
                    """,
                    (normalized_room_id, int(user_id)),
                ).fetchone()
                if row is None:
                    raise StrategyError("该账号没有这场战略战斗的恢复权限。", status=HTTPStatus.NOT_FOUND)
                checkpoint = self._battle_checkpoint_from_row(row)
                if int(user_id) not in checkpoint.participant_user_ids:
                    raise StrategyError("只有原战略战斗参与者可以恢复该检查点。", status=HTTPStatus.FORBIDDEN)
                if hashlib.sha256(checkpoint.room_blob).hexdigest() != checkpoint.checkpoint_hash:
                    raise StrategyError(
                        "战略战斗检查点校验失败；可从战前不可变快照安全重开，战役结果尚未写入。",
                        status=HTTPStatus.CONFLICT,
                    )
                return checkpoint

    def battle_checkpoint(self, room_id: str) -> StrategicBattleCheckpoint | None:
        normalized_room_id = str(room_id or "").strip().upper()
        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                row = connection.execute(
                    "SELECT * FROM strategy_battle_checkpoints WHERE room_id = ?",
                    (normalized_room_id,),
                ).fetchone()
                return self._battle_checkpoint_from_row(row) if row is not None else None

    def campaign_access_mode(self, campaign_id: int) -> str:
        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                row = connection.execute(
                    "SELECT status, world_json FROM strategy_campaigns WHERE id = ?",
                    (int(campaign_id),),
                ).fetchone()
                if row is None:
                    raise StrategyError("战役不存在。", status=HTTPStatus.NOT_FOUND)
                world = self._load_campaign_world(
                    connection,
                    int(campaign_id),
                    str(row["world_json"]),
                )
                archived = str(row["status"]) == "archived" or (
                    str(world.campaign_conclusion.get("state") or "") == "archived"
                )
                return "read_only" if archived else "interactive"

    @staticmethod
    def _battle_checkpoint_from_row(row: sqlite3.Row) -> StrategicBattleCheckpoint:
        return StrategicBattleCheckpoint(
            room_id=str(row["room_id"]),
            campaign_id=int(row["campaign_id"]),
            battle_id=str(row["battle_id"]),
            participant_user_ids=tuple(int(value) for value in json.loads(str(row["participant_user_ids_json"]))),
            room_blob=bytes(row["room_blob"]),
            room_version=int(row["room_version"]),
            format_version=int(row["format_version"]),
            status=str(row["status"]),
            checkpoint_hash=str(row["checkpoint_hash"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            restart_count=int(row["restart_count"]),
        )

    def create_campaign(
        self,
        *,
        owner: AuthUser,
        name: str,
        initial_players: Iterable[AuthUser] | None = None,
        seed: int = 1,
        city_count: int = 8,
        faction_count: int = 2,
        neutral_city_states: bool = False,
        campaign_contract: dict[str, Any] | None = None,
    ) -> CampaignRecord:
        normalized_name = " ".join(str(name or "").strip().split())
        if len(normalized_name) < 2:
            raise StrategyError("战役名称至少需要 2 个字符。")
        if len(normalized_name) > 40:
            raise StrategyError("战役名称最多 40 个字符。")

        players_by_id: dict[int, AuthUser] = {owner.user_id: owner}
        for player in initial_players or ():
            players_by_id[int(player.user_id)] = player
        contract = dict(campaign_contract or {})
        if contract:
            city_count = int(contract.get("city_count", city_count))
            faction_count = int(contract.get("major_faction_count", faction_count))
            neutral_city_states = int(contract.get("neutral_city_state_count", 0)) > 0
        if len(players_by_id) > MAX_HUMAN_PLAYERS:
            raise StrategyError(f"一个战略战役最多支持 {MAX_HUMAN_PLAYERS} 名真人。")
        if len(players_by_id) > faction_count * MAX_HUMANS_PER_FACTION:
            if contract:
                raise StrategyError(f"每个主要势力最多 {MAX_HUMANS_PER_FACTION} 名真人。")
            faction_count = (len(players_by_id) + MAX_HUMANS_PER_FACTION - 1) // MAX_HUMANS_PER_FACTION
        world = generate_random_world(
            seed=seed,
            city_count=city_count,
            faction_count=faction_count,
            neutral_city_states=neutral_city_states,
            campaign_contract=contract,
        )
        now = time.time()

        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO strategy_campaigns
                      (join_code, join_code_enabled, name, owner_user_id, status, current_month, world_json, created_at, updated_at)
                    VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self._unique_join_code(connection),
                        normalized_name,
                        owner.user_id,
                        "lobby",
                        world.current_month,
                        json.dumps(world.to_dict(), ensure_ascii=False, sort_keys=True),
                        now,
                        now,
                    ),
                )
                campaign_id = int(cursor.lastrowid)
                assigned_members: list[CampaignMember] = []
                major_faction_ids = [
                    faction.faction_id
                    for faction in world.factions
                    if faction.is_major
                ]
                for player in players_by_id.values():
                    faction_id = _next_human_faction_id(major_faction_ids, assigned_members)
                    role = "host" if int(player.user_id) == int(owner.user_id) else "member"
                    connection.execute(
                        """
                        INSERT INTO strategy_members
                          (campaign_id, user_id, username, role, faction_id, is_initial_player, joined_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            campaign_id,
                            int(player.user_id),
                            player.username,
                            role,
                            faction_id,
                            1,
                            now,
                        ),
                    )
                    assigned_members.append(
                        CampaignMember(
                            user_id=int(player.user_id),
                            username=player.username,
                            role=role,
                            faction_id=faction_id,
                        )
                    )
                world_json = json.dumps(world.to_dict(), ensure_ascii=False, sort_keys=True)
                self._audit_operation(
                    connection, campaign_id=campaign_id, actor_user_id=owner.user_id,
                    operation="campaign.created", target_type="campaign", target_id=campaign_id,
                    after_hash=sha256_text(world_json),
                    details={"status": "lobby", "month": world.current_month,
                             "human_players": len(players_by_id)},
                )
                return self._campaign_from_connection(connection, campaign_id)

    def join_campaign_by_code(
        self,
        join_code: str,
        user: AuthUser,
        *,
        join_host_faction: bool = False,
    ) -> CampaignRecord:
        normalized_code = "".join(str(join_code or "").strip().upper().split())
        if len(normalized_code) != JOIN_CODE_LENGTH:
            raise StrategyError("战役加入码必须是 6 位。")
        now = time.time()
        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                campaign = self._campaign_from_join_code(connection, normalized_code)
                if not campaign.join_code_enabled:
                    raise StrategyError("战役加入码已被房主撤销。", status=HTTPStatus.NOT_FOUND)
                existing = connection.execute(
                    """
                    SELECT 1
                    FROM strategy_members
                    WHERE campaign_id = ? AND user_id = ?
                    """,
                    (campaign.campaign_id, int(user.user_id)),
                ).fetchone()
                if existing is not None:
                    return campaign
                if campaign.status != "lobby":
                    raise StrategyError("战役已经锁定，只有初始玩家可以恢复。", status=HTTPStatus.CONFLICT)
                human_members = [
                    member
                    for member in campaign.members
                    if int(member.user_id) > 0 and str(member.role).lower() != AI_MEMBER_ROLE
                ]
                if len(human_members) >= MAX_HUMAN_PLAYERS:
                    raise StrategyError("战役真人席位已满。", status=HTTPStatus.CONFLICT)
                major_faction_ids = [
                    faction.faction_id
                    for faction in campaign.world.factions
                    if faction.is_major
                ]
                owner_member = next(
                    member
                    for member in human_members
                    if int(member.user_id) == int(campaign.owner_user_id)
                )
                faction_id = _next_human_faction_id(
                    major_faction_ids,
                    human_members,
                    preferred_faction_id=owner_member.faction_id if join_host_faction else "",
                )
                connection.execute(
                    """
                    INSERT INTO strategy_members
                      (campaign_id, user_id, username, role, faction_id, is_initial_player, joined_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        campaign.campaign_id,
                        int(user.user_id),
                        user.username,
                        "member",
                        faction_id,
                        1,
                        now,
                    ),
                )
                self._audit_operation(
                    connection, campaign_id=campaign.campaign_id, actor_user_id=user.user_id,
                    operation="campaign.member_joined", target_type="member", target_id=user.user_id,
                    details={"faction_id": faction_id, "role": "member"},
                )
                return self._campaign_from_connection(connection, campaign.campaign_id)

    def lock_initial_players(self, campaign_id: int, user_id: int) -> CampaignRecord:
        now = time.time()
        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                campaign = self._campaign_from_connection(connection, int(campaign_id))
                if int(campaign.owner_user_id) != int(user_id):
                    raise StrategyError("只有战役房主可以锁定初始玩家。", status=HTTPStatus.FORBIDDEN)
                if campaign.status == "active":
                    return campaign
                if campaign.status != "lobby":
                    raise StrategyError("当前战役状态不能锁定初始玩家。", status=HTTPStatus.CONFLICT)
                if not campaign.members:
                    raise StrategyError("战役缺少初始玩家。")
                used_faction_ids = {member.faction_id for member in campaign.members}
                for faction in campaign.world.factions:
                    if faction.faction_id in used_faction_ids:
                        continue
                    faction_index = _faction_index(faction.faction_id)
                    connection.execute(
                        """
                        INSERT INTO strategy_members
                          (campaign_id, user_id, username, role, faction_id, is_initial_player, joined_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            int(campaign_id),
                            -faction_index,
                            f"{faction.name} AI",
                            AI_MEMBER_ROLE,
                            faction.faction_id,
                            1,
                            now + faction_index / 1000,
                        ),
                    )
                connection.execute(
                    """
                    UPDATE strategy_campaigns
                    SET status = 'active', updated_at = ?
                    WHERE id = ?
                    """,
                    (now, int(campaign_id)),
                )
                self._audit_operation(
                    connection, campaign_id=int(campaign_id), actor_user_id=int(user_id),
                    operation="campaign.locked", target_type="campaign", target_id=campaign_id,
                    details={"status": "active", "member_count": len(campaign.members)},
                )
                return self._campaign_from_connection(connection, int(campaign_id))

    def rotate_join_code(self, campaign_id: int, user_id: int) -> CampaignRecord:
        now = time.time()
        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                campaign = self._campaign_from_connection(connection, int(campaign_id))
                if int(campaign.owner_user_id) != int(user_id):
                    raise StrategyError("只有战役房主可以重新生成加入码。", status=HTTPStatus.FORBIDDEN)
                if campaign.status != "lobby":
                    raise StrategyError("战役锁定后不能重新开放加入邀请。", status=HTTPStatus.CONFLICT)
                connection.execute(
                    """
                    UPDATE strategy_campaigns
                    SET join_code = ?, join_code_enabled = 1, updated_at = ?
                    WHERE id = ?
                    """,
                    (self._unique_join_code(connection), now, int(campaign_id)),
                )
                self._audit_operation(
                    connection, campaign_id=int(campaign_id), actor_user_id=int(user_id),
                    operation="campaign.invitation_rotated", target_type="campaign", target_id=campaign_id,
                    details={"enabled": True},
                )
                return self._campaign_from_connection(connection, int(campaign_id))

    def revoke_join_code(self, campaign_id: int, user_id: int) -> CampaignRecord:
        now = time.time()
        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                campaign = self._campaign_from_connection(connection, int(campaign_id))
                if int(campaign.owner_user_id) != int(user_id):
                    raise StrategyError("只有战役房主可以撤销加入码。", status=HTTPStatus.FORBIDDEN)
                if campaign.status != "lobby":
                    raise StrategyError("战役锁定后邀请已经关闭。", status=HTTPStatus.CONFLICT)
                if not campaign.join_code_enabled:
                    return campaign
                connection.execute(
                    "UPDATE strategy_campaigns SET join_code_enabled = 0, updated_at = ? WHERE id = ?",
                    (now, int(campaign_id)),
                )
                self._audit_operation(
                    connection, campaign_id=int(campaign_id), actor_user_id=int(user_id),
                    operation="campaign.invitation_revoked", target_type="campaign", target_id=campaign_id,
                    details={"enabled": False},
                )
                return self._campaign_from_connection(connection, int(campaign_id))

    def delete_campaign(self, campaign_id: int, user_id: int) -> None:
        """房主永久删除一个战役。

        此前战役只能归档，而归档的战役仍然留在列表里——试玩几局之后列表就再也
        清不干净了。归档和删除是两件事：归档是"这局打完了，留着复盘"，删除是
        "这局不该存在"。

        子表全部带 ON DELETE CASCADE 且连接开了 PRAGMA foreign_keys，所以删主
        表一行就够。审计日志是例外：它按 hash 链追加，且 campaign_id 上没有外
        键——删掉历史条目会打断链，所以留着，再补一条删除记录。
        """
        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                campaign = self._campaign_from_connection(connection, int(campaign_id))
                if int(campaign.owner_user_id) != int(user_id):
                    raise StrategyError("只有战役房主可以删除战役。", status=HTTPStatus.FORBIDDEN)
                # 审计要在删除之前写：_audit_operation 会去 strategy_members 里查
                # 操作者的用户名，那张表跟着主表一起被级联删掉。
                self._audit_operation(
                    connection, campaign_id=int(campaign_id), actor_user_id=int(user_id),
                    operation="campaign.deleted", target_type="campaign", target_id=campaign_id,
                    details={"status": campaign.status, "month": campaign.world.current_month,
                             "name": campaign.name},
                )
                connection.execute("DELETE FROM strategy_campaigns WHERE id = ?", (int(campaign_id),))

    def list_campaigns_for_user(self, user_id: int) -> list[CampaignRecord]:
        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                rows = connection.execute(
                    """
                    SELECT campaign_id
                    FROM strategy_members
                    WHERE user_id = ?
                    ORDER BY joined_at DESC
                    """,
                    (int(user_id),),
                ).fetchall()
                return [self._campaign_from_connection(connection, int(row["campaign_id"])) for row in rows]

    def get_campaign_for_user(self, campaign_id: int, user_id: int) -> CampaignRecord:
        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                self._require_member(connection, int(campaign_id), int(user_id))
                return self._campaign_from_connection(connection, int(campaign_id))

    def mark_online(self, campaign_id: int, user: AuthUser) -> ResumeStatus:
        now = time.time()
        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                self._require_member(connection, int(campaign_id), int(user.user_id))
                connection.execute(
                    """
                    INSERT INTO strategy_presence (campaign_id, user_id, last_seen_at, is_online)
                    VALUES (?, ?, ?, 1)
                    ON CONFLICT(campaign_id, user_id)
                    DO UPDATE SET last_seen_at = excluded.last_seen_at, is_online = 1
                    """,
                    (int(campaign_id), int(user.user_id), now),
                )
                return self.resume_status_from_connection(connection, int(campaign_id), now=now)

    def mark_offline(self, campaign_id: int, user_id: int) -> ResumeStatus:
        now = time.time()
        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                self._require_member(connection, int(campaign_id), int(user_id))
                connection.execute(
                    """
                    INSERT INTO strategy_presence (campaign_id, user_id, last_seen_at, is_online)
                    VALUES (?, ?, ?, 0)
                    ON CONFLICT(campaign_id, user_id)
                    DO UPDATE SET last_seen_at = excluded.last_seen_at, is_online = 0
                    """,
                    (int(campaign_id), int(user_id), now),
                )
                return self.resume_status_from_connection(connection, int(campaign_id), now=now)

    def resume_status(self, campaign_id: int) -> ResumeStatus:
        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                return self.resume_status_from_connection(connection, int(campaign_id), now=time.time())

    def require_can_resume(self, campaign_id: int, user_id: int) -> ResumeStatus:
        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                self._require_member(connection, int(campaign_id), int(user_id))
                status = self.resume_status_from_connection(connection, int(campaign_id), now=time.time())
                if status.campaign_status != "active":
                    raise StrategyError("战役需要房主锁定初始玩家后才能继续。", status=HTTPStatus.CONFLICT)
                return status

    def set_month_ready(self, campaign_id: int, user_id: int, *, ready: bool) -> ResumeStatus:
        now = time.time()
        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                self._require_member(connection, int(campaign_id), int(user_id))
                campaign = self._campaign_from_connection(connection, int(campaign_id))
                if campaign.status != "active":
                    raise StrategyError("战役锁定后才能提交月度计划。", status=HTTPStatus.CONFLICT)
                member = next(
                    (
                        item for item in campaign.members
                        if int(item.user_id) == int(user_id)
                        and item.is_initial_player
                        and str(item.role).lower() != AI_MEMBER_ROLE
                    ),
                    None,
                )
                if member is None:
                    raise StrategyError("只有真人初始成员可以提交月度计划。", status=HTTPStatus.FORBIDDEN)
                connection.execute(
                    """
                    INSERT INTO strategy_month_submissions
                      (campaign_id, user_id, month, status, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(campaign_id, user_id, month)
                    DO UPDATE SET status = excluded.status, updated_at = excluded.updated_at
                    """,
                    (
                        int(campaign_id),
                        int(user_id),
                        int(campaign.world.current_month),
                        "ready" if ready else "drafting",
                        now,
                    ),
                )
                self._audit_operation(
                    connection, campaign_id=int(campaign_id), actor_user_id=int(user_id),
                    operation="month.submission_changed", target_type="month_submission",
                    target_id=f"{campaign.world.current_month}:{user_id}",
                    details={"month": campaign.world.current_month,
                             "status": "ready" if ready else "drafting"},
                )
                return self.resume_status_from_connection(connection, int(campaign_id), now=now)

    def close_month_deadline(self, campaign_id: int, user_id: int) -> ResumeStatus:
        now = time.time()
        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                self._require_member(connection, int(campaign_id), int(user_id))
                campaign = self._campaign_from_connection(connection, int(campaign_id))
                if int(campaign.owner_user_id) != int(user_id):
                    raise StrategyError("只有战役房主可以关闭本月截止。", status=HTTPStatus.FORBIDDEN)
                if campaign.status != "active":
                    raise StrategyError("战役锁定后才能关闭月度截止。", status=HTTPStatus.CONFLICT)
                status = self.resume_status_from_connection(connection, int(campaign_id), now=now)
                if int(user_id) not in status.ready_user_ids:
                    raise StrategyError("房主必须先提交自己的本月计划。", status=HTTPStatus.CONFLICT)
                online_unready = sorted(
                    set(status.drafting_user_ids).intersection(status.online_initial_user_ids)
                )
                if online_unready:
                    raise StrategyError(
                        "仍有在线玩家尚未提交，不能强制启用 AI 托管。",
                        status=HTTPStatus.CONFLICT,
                    )
                for absent_user_id in status.drafting_user_ids:
                    connection.execute(
                        """
                        INSERT INTO strategy_month_submissions
                          (campaign_id, user_id, month, status, updated_at)
                        VALUES (?, ?, ?, 'proxy_ai', ?)
                        ON CONFLICT(campaign_id, user_id, month)
                        DO UPDATE SET status = 'proxy_ai', updated_at = excluded.updated_at
                        """,
                        (
                            int(campaign_id),
                            int(absent_user_id),
                            int(campaign.world.current_month),
                            now,
                        ),
                    )
                self._audit_operation(
                    connection, campaign_id=int(campaign_id), actor_user_id=int(user_id),
                    operation="month.deadline_closed", target_type="campaign_month",
                    target_id=f"{campaign_id}:{campaign.world.current_month}",
                    details={"month": campaign.world.current_month,
                             "proxy_ai_user_ids": list(status.drafting_user_ids)},
                )
                return self.resume_status_from_connection(connection, int(campaign_id), now=now)

    def require_can_advance_month(self, campaign_id: int, user_id: int) -> ResumeStatus:
        self.set_month_ready(campaign_id, user_id, ready=True)
        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                status = self.resume_status_from_connection(connection, int(campaign_id), now=time.time())
                if not status.can_advance_month:
                    raise StrategyError(
                        "仍有真人成员处于拟定中；请等待其提交，或在其离线后由房主关闭本月截止。",
                        status=HTTPStatus.CONFLICT,
                    )
                return status

    def temporary_ai_faction_ids(self, campaign_id: int, month: int) -> set[str]:
        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                rows = connection.execute(
                    """
                    SELECT m.faction_id
                    FROM strategy_month_submissions s
                    JOIN strategy_members m
                      ON m.campaign_id = s.campaign_id AND m.user_id = s.user_id
                    WHERE s.campaign_id = ? AND s.month = ? AND s.status = 'proxy_ai'
                    """,
                    (int(campaign_id), int(month)),
                ).fetchall()
                return {str(row["faction_id"]) for row in rows if str(row["faction_id"])}

    def request_office_change(
        self,
        campaign_id: int,
        user_id: int,
        *,
        request_type: str,
        office_id: str,
        target_user_id: int = 0,
    ) -> CampaignRecord:
        normalized_type = str(request_type or "").strip()
        if normalized_type not in {"handover", "vacate"}:
            raise StrategyError("官职协作请求类型无效。")
        now = time.time()
        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                self._require_member(connection, int(campaign_id), int(user_id))
                campaign = self._campaign_from_connection(connection, int(campaign_id))
                if campaign.status != "active":
                    raise StrategyError("战役锁定后才能发起官职交接。", status=HTTPStatus.CONFLICT)
                member = next(item for item in campaign.members if int(item.user_id) == int(user_id))
                office = next(
                    (
                        item
                        for item in campaign.world.offices
                        if item.office_id == str(office_id)
                        and item.faction_id == member.faction_id
                    ),
                    None,
                )
                if office is None:
                    raise StrategyError("只能处理本势力的官职。", status=HTTPStatus.FORBIDDEN)
                if normalized_type == "handover":
                    if (
                        office.controller_type != "player"
                        or int(office.controller_user_id or 0) != int(user_id)
                        or office.status != "active"
                    ):
                        raise StrategyError("只能交接自己当前控制的有效职位。", status=HTTPStatus.FORBIDDEN)
                    target = next(
                        (
                            item
                            for item in campaign.members
                            if int(item.user_id) == int(target_user_id)
                            and item.faction_id == member.faction_id
                            and int(item.user_id) > 0
                            and str(item.role).lower() != AI_MEMBER_ROLE
                        ),
                        None,
                    )
                    if target is None or int(target.user_id) == int(user_id):
                        raise StrategyError("只能向同势力的另一名真人成员交接。")
                    resolved_target_user_id = int(target.user_id)
                else:
                    lord = next(
                        (
                            item
                            for item in campaign.world.offices
                            if item.faction_id == member.faction_id
                            and item.office_type == "lord"
                            and item.status == "active"
                            and item.controller_type == "player"
                            and int(item.controller_user_id or 0) == int(user_id)
                        ),
                        None,
                    )
                    if lord is None:
                        raise StrategyError("只有本势力真人主公可以提出撤换。", status=HTTPStatus.FORBIDDEN)
                    if office.office_type == "lord":
                        raise StrategyError("主公职位请使用双方交接，不能直接撤为空缺。")
                    if office.controller_type != "player" or int(office.controller_user_id or 0) <= 0:
                        raise StrategyError("只能请求撤换由真人控制的有效职位。")
                    resolved_target_user_id = int(office.controller_user_id or 0)
                    if resolved_target_user_id == int(user_id):
                        raise StrategyError("主公不能向自己发起撤换请求。")
                duplicate = connection.execute(
                    """
                    SELECT 1
                    FROM strategy_office_change_requests
                    WHERE campaign_id = ? AND status = 'pending'
                      AND (office_id = ? OR target_user_id = ?)
                    """,
                    (int(campaign_id), str(office_id), resolved_target_user_id),
                ).fetchone()
                if duplicate is not None:
                    raise StrategyError("该职位或目标成员已有待确认的官职请求。", status=HTTPStatus.CONFLICT)
                connection.execute(
                    """
                    INSERT INTO strategy_office_change_requests
                      (campaign_id, month, request_type, faction_id, office_id,
                       initiator_user_id, target_user_id, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                    """,
                    (
                        int(campaign_id),
                        int(campaign.world.current_month),
                        normalized_type,
                        member.faction_id,
                        str(office_id),
                        int(user_id),
                        resolved_target_user_id,
                        now,
                    ),
                )
                return self._campaign_from_connection(connection, int(campaign_id))

    def respond_office_change(
        self,
        campaign_id: int,
        user_id: int,
        *,
        request_id: int,
        accept: bool,
    ) -> CampaignRecord:
        now = time.time()
        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                self._require_member(connection, int(campaign_id), int(user_id))
                row = connection.execute(
                    """
                    SELECT *
                    FROM strategy_office_change_requests
                    WHERE id = ? AND campaign_id = ?
                    """,
                    (int(request_id), int(campaign_id)),
                ).fetchone()
                if row is None:
                    raise StrategyError("官职请求不存在。", status=HTTPStatus.NOT_FOUND)
                if str(row["status"]) != "pending":
                    raise StrategyError("官职请求已经处理。", status=HTTPStatus.CONFLICT)
                if int(row["target_user_id"]) != int(user_id):
                    raise StrategyError("只有被请求的成员可以确认。", status=HTTPStatus.FORBIDDEN)
                campaign = self._campaign_from_connection(connection, int(campaign_id))
                if int(row["month"]) != int(campaign.world.current_month):
                    return campaign
                if not accept:
                    connection.execute(
                        """
                        UPDATE strategy_office_change_requests
                        SET status = 'rejected', resolved_at = ?
                        WHERE id = ?
                        """,
                        (now, int(request_id)),
                    )
                    return self._campaign_from_connection(connection, int(campaign_id))
                involved_user_ids = (
                    {
                        int(row["initiator_user_id"]),
                        int(row["target_user_id"]),
                    }
                    if str(row["request_type"]) == "handover"
                    else {int(row["target_user_id"])}
                )
                placeholders = ",".join("?" for _ in involved_user_ids)
                pending_action = connection.execute(
                    f"""
                    SELECT 1
                    FROM strategy_actions
                    WHERE campaign_id = ? AND month = ? AND status = 'pending'
                      AND user_id IN ({placeholders})
                    LIMIT 1
                    """,
                    (
                        int(campaign_id),
                        int(campaign.world.current_month),
                        *sorted(involved_user_ids),
                    ),
                ).fetchone()
                if pending_action is not None:
                    raise StrategyError("交接双方仍有本月军令；请先在无待结算军令时确认。", status=HTTPStatus.CONFLICT)
                submission_rows = connection.execute(
                    f"""
                    SELECT user_id, status
                    FROM strategy_month_submissions
                    WHERE campaign_id = ? AND month = ?
                      AND user_id IN ({placeholders})
                    """,
                    (
                        int(campaign_id),
                        int(campaign.world.current_month),
                        *sorted(involved_user_ids),
                    ),
                ).fetchall()
                if any(str(item["status"]) != "drafting" for item in submission_rows):
                    raise StrategyError("已提交或被托管的成员不能确认官职变化。", status=HTTPStatus.CONFLICT)
                if str(row["request_type"]) == "handover":
                    from wujiang.strategic.heroes import handover_player_office

                    next_world = handover_player_office(
                        campaign.world,
                        faction_id=str(row["faction_id"]),
                        office_id=str(row["office_id"]),
                        from_user_id=int(row["initiator_user_id"]),
                        to_user_id=int(row["target_user_id"]),
                    )
                else:
                    from wujiang.strategic.heroes import vacate_player_office

                    next_world = vacate_player_office(
                        campaign.world,
                        faction_id=str(row["faction_id"]),
                        office_id=str(row["office_id"]),
                        user_id=int(row["target_user_id"]),
                    )
                connection.execute(
                    """
                    UPDATE strategy_campaigns
                    SET current_month = ?, world_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        int(next_world.current_month),
                        json.dumps(next_world.to_dict(), ensure_ascii=False, sort_keys=True),
                        now,
                        int(campaign_id),
                    ),
                )
                connection.execute(
                    """
                    UPDATE strategy_office_change_requests
                    SET status = 'accepted', resolved_at = ?
                    WHERE id = ?
                    """,
                    (now, int(request_id)),
                )
                return self._campaign_from_connection(connection, int(campaign_id))

    def grant_office_takeover(
        self,
        campaign_id: int,
        user_id: int,
        *,
        office_id: str,
        delegate_user_id: int,
    ) -> CampaignRecord:
        now = time.time()
        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                self._require_member(connection, int(campaign_id), int(user_id))
                campaign = self._campaign_from_connection(connection, int(campaign_id))
                if campaign.status != "active":
                    raise StrategyError("战役锁定后才能授权临时代管。", status=HTTPStatus.CONFLICT)
                from wujiang.strategic.objectives import require_campaign_orders_open

                require_campaign_orders_open(campaign.world)
                grantor = next(item for item in campaign.members if int(item.user_id) == int(user_id))
                lord = next(
                    (
                        office for office in campaign.world.offices
                        if office.faction_id == grantor.faction_id
                        and office.office_type == "lord"
                        and office.status == "active"
                        and office.controller_type == "player"
                        and int(office.controller_user_id or 0) == int(user_id)
                    ),
                    None,
                )
                if lord is None:
                    raise StrategyError("只有本势力真人主公可以授权临时代管。", status=HTTPStatus.FORBIDDEN)
                delegate = next(
                    (
                        member for member in campaign.members
                        if int(member.user_id) == int(delegate_user_id)
                        and member.faction_id == grantor.faction_id
                        and int(member.user_id) > 0
                        and str(member.role).lower() != AI_MEMBER_ROLE
                    ),
                    None,
                )
                if delegate is None:
                    raise StrategyError("只能授权同势力真人成员代管。")
                if int(delegate.user_id) == int(user_id):
                    raise StrategyError("主公只能授权同势力的另一名真人成员代管。")
                office = next(
                    (
                        item for item in campaign.world.offices
                        if item.office_id == str(office_id)
                        and item.faction_id == grantor.faction_id
                    ),
                    None,
                )
                if office is None or office.office_type == "lord":
                    raise StrategyError("只能临时代管本势力的非主公空缺职位。")
                if office.status != "vacant" or office.holder_id is not None:
                    raise StrategyError("只有空缺职位可以授权临时代管。", status=HTTPStatus.CONFLICT)
                submission = connection.execute(
                    """
                    SELECT status FROM strategy_month_submissions
                    WHERE campaign_id = ? AND user_id = ? AND month = ?
                    """,
                    (int(campaign_id), int(delegate_user_id), int(campaign.world.current_month)),
                ).fetchone()
                if submission is not None and str(submission["status"]) != "drafting":
                    raise StrategyError("已提交或被托管的成员不能接受临时代管。", status=HTTPStatus.CONFLICT)
                active = connection.execute(
                    """
                    SELECT 1 FROM strategy_office_takeovers
                    WHERE campaign_id = ? AND status = 'active'
                      AND (office_id = ? OR delegate_user_id = ?)
                    """,
                    (int(campaign_id), office.office_id, int(delegate_user_id)),
                ).fetchone()
                if active is not None:
                    raise StrategyError("该职位或成员已有本月临时代管。", status=HTTPStatus.CONFLICT)
                office.holder_id = f"temporary:player:{int(delegate_user_id)}"
                office.holder_type = "temporary_player"
                office.controller_type = "player"
                office.controller_user_id = int(delegate_user_id)
                office.status = "active"
                campaign.world.event_log.append(
                    EventLogEntry(
                        month=campaign.world.current_month,
                        category="player_office_takeover_granted",
                        message=f"账号 {user_id} 授权账号 {delegate_user_id} 当月代管 {office.office_id}。",
                        related_ids=[grantor.faction_id, office.office_id, str(user_id), str(delegate_user_id)],
                    )
                )
                campaign.world.validate()
                connection.execute(
                    """
                    UPDATE strategy_campaigns
                    SET world_json = ?, updated_at = ? WHERE id = ?
                    """,
                    (json.dumps(campaign.world.to_dict(), ensure_ascii=False, sort_keys=True), now, int(campaign_id)),
                )
                connection.execute(
                    """
                    INSERT INTO strategy_office_takeovers
                      (campaign_id, month, faction_id, office_id, grantor_user_id,
                       delegate_user_id, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'active', ?)
                    """,
                    (
                        int(campaign_id), int(campaign.world.current_month), grantor.faction_id,
                        office.office_id, int(user_id), int(delegate_user_id), now,
                    ),
                )
                return self._campaign_from_connection(connection, int(campaign_id))

    def revoke_office_takeover(self, campaign_id: int, user_id: int, *, takeover_id: int) -> CampaignRecord:
        now = time.time()
        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                self._require_member(connection, int(campaign_id), int(user_id))
                campaign = self._campaign_from_connection(connection, int(campaign_id))
                row = connection.execute(
                    "SELECT * FROM strategy_office_takeovers WHERE id = ? AND campaign_id = ?",
                    (int(takeover_id), int(campaign_id)),
                ).fetchone()
                if row is None:
                    raise StrategyError("临时代管记录不存在。", status=HTTPStatus.NOT_FOUND)
                if str(row["status"]) != "active":
                    raise StrategyError("临时代管已经结束。", status=HTTPStatus.CONFLICT)
                lord_user_id = next(
                    (
                        int(office.controller_user_id or 0) for office in campaign.world.offices
                        if office.faction_id == str(row["faction_id"])
                        and office.office_type == "lord"
                        and office.controller_type == "player"
                    ),
                    0,
                )
                allowed = {int(row["grantor_user_id"]), int(row["delegate_user_id"]), lord_user_id}
                if int(user_id) not in allowed:
                    raise StrategyError("只有授权主公、现任主公或代管者可以结束代管。", status=HTTPStatus.FORBIDDEN)
                if any(
                    action.user_id == int(row["delegate_user_id"])
                    and str(action.payload.get("issuer_office_id") or "") == str(row["office_id"])
                    for action in campaign.queued_actions
                ):
                    raise StrategyError("该代管职位仍有待结算军令，不能提前结束。", status=HTTPStatus.CONFLICT)
                self._end_takeover_in_world(
                    campaign.world,
                    office_id=str(row["office_id"]),
                    delegate_user_id=int(row["delegate_user_id"]),
                    category="player_office_takeover_revoked",
                )
                connection.execute(
                    "UPDATE strategy_campaigns SET world_json = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(campaign.world.to_dict(), ensure_ascii=False, sort_keys=True), now, int(campaign_id)),
                )
                connection.execute(
                    "UPDATE strategy_office_takeovers SET status = 'revoked', ended_at = ? WHERE id = ?",
                    (now, int(takeover_id)),
                )
                return self._campaign_from_connection(connection, int(campaign_id))

    def expire_office_takeovers(self, campaign_id: int, user_id: int) -> CampaignRecord:
        now = time.time()
        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                self._require_member(connection, int(campaign_id), int(user_id))
                campaign = self._campaign_from_connection(connection, int(campaign_id))
                rows = connection.execute(
                    """
                    SELECT * FROM strategy_office_takeovers
                    WHERE campaign_id = ? AND status = 'active' AND month < ?
                    """,
                    (int(campaign_id), int(campaign.world.current_month)),
                ).fetchall()
                for row in rows:
                    self._end_takeover_in_world(
                        campaign.world,
                        office_id=str(row["office_id"]),
                        delegate_user_id=int(row["delegate_user_id"]),
                        category="player_office_takeover_expired",
                    )
                    connection.execute(
                        "UPDATE strategy_office_takeovers SET status = 'expired', ended_at = ? WHERE id = ?",
                        (now, int(row["id"])),
                    )
                if rows:
                    campaign.world.validate()
                    connection.execute(
                        "UPDATE strategy_campaigns SET world_json = ?, updated_at = ? WHERE id = ?",
                        (json.dumps(campaign.world.to_dict(), ensure_ascii=False, sort_keys=True), now, int(campaign_id)),
                    )
                return self._campaign_from_connection(connection, int(campaign_id))

    @staticmethod
    def _end_takeover_in_world(
        world: WorldState,
        *,
        office_id: str,
        delegate_user_id: int,
        category: str,
    ) -> None:
        office = next((item for item in world.offices if item.office_id == str(office_id)), None)
        if (
            office is not None
            and office.holder_type == "temporary_player"
            and int(office.controller_user_id or 0) == int(delegate_user_id)
        ):
            office.holder_id = None
            office.holder_type = None
            office.controller_type = "ai"
            office.controller_user_id = None
            office.status = "vacant"
        world.event_log.append(
            EventLogEntry(
                month=world.current_month,
                category=category,
                message=f"账号 {delegate_user_id} 对 {office_id} 的临时代管结束，职位恢复空缺。",
                related_ids=[office_id, str(delegate_user_id)],
            )
        )

    def update_world(self, campaign_id: int, user_id: int, world: WorldState) -> CampaignRecord:
        now = time.time()
        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                self._require_member(connection, int(campaign_id), int(user_id))
                existing = connection.execute(
                    "SELECT status, world_json FROM strategy_campaigns WHERE id = ?",
                    (int(campaign_id),),
                ).fetchone()
                if existing is None:
                    raise StrategyError("战役不存在。", status=HTTPStatus.NOT_FOUND)
                next_archived = str(world.campaign_conclusion.get("state") or "") == "archived"
                if str(existing["status"]) == "archived":
                    existing_world = self._load_campaign_world(
                        connection,
                        int(campaign_id),
                        str(existing["world_json"]),
                    )
                    if world.to_dict() != existing_world.to_dict():
                        raise StrategyError("战役已经归档，只能只读查看，不能再修改。", status=HTTPStatus.CONFLICT)
                    return self._campaign_from_connection(connection, int(campaign_id))
                before_json = str(existing["world_json"])
                after_json = json.dumps(world.to_dict(), ensure_ascii=False, sort_keys=True)
                connection.execute(
                    """
                    UPDATE strategy_campaigns
                    SET status = ?, current_month = ?, world_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        "archived" if next_archived else str(existing["status"]),
                        int(world.current_month),
                        after_json,
                        now,
                        int(campaign_id),
                    ),
                )
                for hero in world.strategic_heroes:
                    if hero.controller_type != "player" or hero.controller_user_id is None:
                        continue
                    faction_id = hero.faction_id if hero.status == "serving" and hero.faction_id else ""
                    connection.execute(
                        """
                        UPDATE strategy_members
                        SET faction_id = ?
                        WHERE campaign_id = ? AND user_id = ? AND role != ?
                        """,
                        (faction_id, int(campaign_id), int(hero.controller_user_id), AI_MEMBER_ROLE),
                    )
                self._audit_operation(
                    connection, campaign_id=int(campaign_id), actor_user_id=int(user_id),
                    operation="world.updated", target_type="campaign_world", target_id=campaign_id,
                    before_hash=sha256_text(before_json), after_hash=sha256_text(after_json),
                    details={"month": world.current_month,
                             "status": "archived" if next_archived else str(existing["status"])},
                )
                return self._campaign_from_connection(connection, int(campaign_id))

    def queue_action(
        self,
        *,
        campaign_id: int,
        user: AuthUser,
        action_type: str,
        action_key: str,
        payload: dict[str, Any],
    ) -> CampaignRecord:
        normalized_type = str(action_type or "").strip()
        normalized_key = str(action_key or "").strip()
        if not normalized_type or not normalized_key:
            raise StrategyError("Strategy action type and key are required.")
        if not isinstance(payload, dict):
            raise StrategyError("Strategy action payload must be an object.")
        now = time.time()
        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                self._require_member(connection, int(campaign_id), int(user.user_id))
                campaign = self._campaign_from_connection(connection, int(campaign_id))
                member = next(
                    (item for item in campaign.members if int(item.user_id) == int(user.user_id)),
                    None,
                )
                if member is None:
                    raise StrategyError("Campaign member not found.", status=HTTPStatus.FORBIDDEN)
                submission = connection.execute(
                    """
                    SELECT status
                    FROM strategy_month_submissions
                    WHERE campaign_id = ? AND user_id = ? AND month = ?
                    """,
                    (int(campaign_id), int(user.user_id), int(campaign.world.current_month)),
                ).fetchone()
                submission_status = str(submission["status"]) if submission is not None else "drafting"
                if submission_status == "ready":
                    raise StrategyError(
                        "你已提交本月计划；请先撤回提交再修改军令。",
                        status=HTTPStatus.CONFLICT,
                    )
                if submission_status == "proxy_ai":
                    raise StrategyError(
                        "本月已进入 AI 临时托管；请先重新提交以取回控制。",
                        status=HTTPStatus.CONFLICT,
                    )
                if normalized_type == "cancel_tactic_research":
                    connection.execute(
                        """
                        UPDATE strategy_actions
                        SET status = 'cancelled'
                        WHERE campaign_id = ? AND user_id = ? AND month = ?
                          AND action_type = 'unlock_tactic_tech' AND status = 'pending'
                        """,
                        (int(campaign_id), int(user.user_id), int(campaign.world.current_month)),
                    )
                    from wujiang.strategic.tactics import cancel_tactic_research

                    next_world = cancel_tactic_research(campaign.world, faction_id=member.faction_id)
                    after_json = json.dumps(next_world.to_dict(), ensure_ascii=False, sort_keys=True)
                    connection.execute(
                        """
                        UPDATE strategy_campaigns
                        SET world_json = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (after_json, now, int(campaign_id)),
                    )
                    return self._campaign_from_connection(connection, int(campaign_id))
                connection.execute(
                    """
                    INSERT INTO strategy_actions
                      (campaign_id, user_id, username, faction_id, month, action_type,
                       action_key, payload_json, status, submitted_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                    ON CONFLICT(campaign_id, user_id, month, action_type, action_key)
                    DO UPDATE SET
                      username = excluded.username,
                      faction_id = excluded.faction_id,
                      payload_json = excluded.payload_json,
                      status = 'pending',
                      submitted_at = excluded.submitted_at
                    """,
                    (
                        int(campaign_id),
                        int(user.user_id),
                        user.username,
                        member.faction_id,
                        int(campaign.world.current_month),
                        normalized_type,
                        normalized_key,
                        json.dumps(payload, ensure_ascii=False, sort_keys=True),
                        now,
                    ),
                )
                action_row = connection.execute(
                    """
                    SELECT id FROM strategy_actions
                    WHERE campaign_id = ? AND user_id = ? AND month = ?
                      AND action_type = ? AND action_key = ?
                    """,
                    (int(campaign_id), int(user.user_id), int(campaign.world.current_month),
                     normalized_type, normalized_key),
                ).fetchone()
                assert action_row is not None
                self._audit_operation(
                    connection, campaign_id=int(campaign_id), actor_user_id=int(user.user_id),
                    operation="action.queued", target_type="strategy_action",
                    target_id=int(action_row["id"]), after_hash=sha256_text(
                        json.dumps(payload, ensure_ascii=False, sort_keys=True)
                    ), details={"month": campaign.world.current_month, "action_type": normalized_type},
                )
                return self._campaign_from_connection(connection, int(campaign_id))

    def cancel_queued_action(
        self,
        *,
        campaign_id: int,
        user: AuthUser,
        action_id: int,
    ) -> CampaignRecord:
        now = time.time()
        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                self._require_member(connection, int(campaign_id), int(user.user_id))
                campaign = self._campaign_from_connection(connection, int(campaign_id))
                submission = connection.execute(
                    """
                    SELECT status
                    FROM strategy_month_submissions
                    WHERE campaign_id = ? AND user_id = ? AND month = ?
                    """,
                    (int(campaign_id), int(user.user_id), int(campaign.world.current_month)),
                ).fetchone()
                submission_status = str(submission["status"]) if submission is not None else "drafting"
                if submission_status == "ready":
                    raise StrategyError(
                        "你已提交本月计划；请先撤回提交再修改军令。",
                        status=HTTPStatus.CONFLICT,
                    )
                if submission_status == "proxy_ai":
                    raise StrategyError(
                        "本月已进入 AI 临时托管；请先重新提交以取回控制。",
                        status=HTTPStatus.CONFLICT,
                    )
                row = connection.execute(
                    """
                    SELECT id, user_id, action_type
                    FROM strategy_actions
                    WHERE id = ? AND campaign_id = ? AND month = ? AND status = 'pending'
                    """,
                    (int(action_id), int(campaign_id), int(campaign.world.current_month)),
                ).fetchone()
                if row is None:
                    raise StrategyError("这条军令不存在或已经执行。", status=HTTPStatus.NOT_FOUND)
                if int(row["user_id"]) != int(user.user_id):
                    raise StrategyError("只能删除自己提交的军令。", status=HTTPStatus.FORBIDDEN)
                connection.execute(
                    """
                    UPDATE strategy_actions
                    SET status = 'cancelled', submitted_at = ?
                    WHERE id = ?
                    """,
                    (now, int(action_id)),
                )
                self._audit_operation(
                    connection, campaign_id=int(campaign_id), actor_user_id=int(user.user_id),
                    operation="action.cancelled", target_type="strategy_action",
                    target_id=int(action_id), details={
                        "month": campaign.world.current_month,
                        "action_type": str(row["action_type"]),
                    },
                )
                return self._campaign_from_connection(connection, int(campaign_id))

    def mark_queued_actions_resolved(self, campaign_id: int, user_id: int, month: int) -> None:
        now = time.time()
        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                self._require_member(connection, int(campaign_id), int(user_id))
                connection.execute(
                    """
                    UPDATE strategy_actions
                    SET status = 'resolved', submitted_at = ?
                    WHERE campaign_id = ? AND month = ? AND status = 'pending'
                    """,
                    (now, int(campaign_id), int(month)),
                )
                self._audit_operation(
                    connection, campaign_id=int(campaign_id), actor_user_id=int(user_id),
                    operation="action.month_resolved", target_type="campaign_month",
                    target_id=f"{campaign_id}:{month}", details={"month": int(month)},
                )

    def resolve_battle_room_result(
        self,
        *,
        battle_room_id: str,
        winner_team_id: int,
        battle_summary: str = "",
        surviving_grid_units_by_team: dict[int, int] | None = None,
        surviving_hero_codes_by_team: dict[int, set[str] | list[str] | tuple[str, ...]] | None = None,
    ) -> CampaignRecord | None:
        room_id = str(battle_room_id or "").strip().upper()
        if not room_id:
            return None
        now = time.time()
        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                rows = connection.execute(
                    """
                    SELECT id, world_json
                    FROM strategy_campaigns
                    WHERE world_json LIKE ?
                    ORDER BY updated_at DESC, id DESC
                    """,
                    (f"%{room_id}%",),
                ).fetchall()
                for row in rows:
                    world = self._load_campaign_world(
                        connection,
                        int(row["id"]),
                        str(row["world_json"]),
                    )
                    battle = next(
                        (
                            item
                            for item in world.pending_battles
                            if str(item.battle_room_id or "").strip().upper() == room_id
                        ),
                        None,
                    )
                    if battle is None:
                        continue
                    if battle.status != "pending":
                        return self._campaign_from_connection(connection, int(row["id"]))
                    before_json = str(row["world_json"])
                    next_world = resolve_battle_room_result(
                        world,
                        battle_room_id=room_id,
                        winner_team_id=winner_team_id,
                        battle_summary=battle_summary,
                        surviving_grid_units_by_team=surviving_grid_units_by_team,
                        surviving_hero_codes_by_team=surviving_hero_codes_by_team,
                    )
                    after_json = json.dumps(next_world.to_dict(), ensure_ascii=False, sort_keys=True)
                    connection.execute(
                        """
                        UPDATE strategy_campaigns
                        SET current_month = ?, world_json = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            int(next_world.current_month),
                            after_json,
                            now,
                            int(row["id"]),
                        ),
                    )
                    self._audit_operation(
                        connection, campaign_id=int(row["id"]), actor_user_id=None,
                        operation="battle.result_resolved", target_type="battle_room",
                        target_id=room_id, before_hash=sha256_text(before_json),
                        after_hash=sha256_text(after_json),
                        details={"battle_id": battle.battle_id, "winner_team_id": int(winner_team_id)},
                    )
                    return self._campaign_from_connection(connection, int(row["id"]))
        return None

    def resume_status_from_connection(
        self,
        connection: sqlite3.Connection,
        campaign_id: int,
        *,
        now: float,
    ) -> ResumeStatus:
        member_rows = connection.execute(
            """
            SELECT user_id
            FROM strategy_members
            WHERE campaign_id = ? AND is_initial_player = 1 AND role != ?
            ORDER BY user_id
            """,
            (int(campaign_id), AI_MEMBER_ROLE),
        ).fetchall()
        initial_user_ids = tuple(int(row["user_id"]) for row in member_rows)
        if not initial_user_ids:
            raise StrategyError("战役缺少初始玩家。")
        campaign_row = connection.execute(
            "SELECT status, current_month FROM strategy_campaigns WHERE id = ?",
            (int(campaign_id),),
        ).fetchone()
        if campaign_row is None:
            raise StrategyError("战役不存在。", status=HTTPStatus.NOT_FOUND)
        campaign_status = str(campaign_row["status"])
        current_month = int(campaign_row["current_month"])
        presence_rows = connection.execute(
            """
            SELECT user_id, last_seen_at, is_online
            FROM strategy_presence
            WHERE campaign_id = ?
            """,
            (int(campaign_id),),
        ).fetchall()
        online_ids = {
            int(row["user_id"])
            for row in presence_rows
            if int(row["is_online"]) == 1 and now - float(row["last_seen_at"]) <= PRESENCE_TTL_SECONDS
        }
        online_initial_user_ids = tuple(user_id for user_id in initial_user_ids if user_id in online_ids)
        missing_initial_user_ids = tuple(user_id for user_id in initial_user_ids if user_id not in online_ids)
        submission_rows = connection.execute(
            """
            SELECT user_id, status
            FROM strategy_month_submissions
            WHERE campaign_id = ? AND month = ?
            """,
            (int(campaign_id), current_month),
        ).fetchall()
        submission_by_user_id = {
            int(row["user_id"]): str(row["status"])
            for row in submission_rows
        }
        ready_user_ids = tuple(
            user_id for user_id in initial_user_ids
            if submission_by_user_id.get(user_id) == "ready"
        )
        proxy_ai_user_ids = tuple(
            user_id for user_id in initial_user_ids
            if submission_by_user_id.get(user_id) == "proxy_ai"
        )
        drafting_user_ids = tuple(
            user_id for user_id in initial_user_ids
            if submission_by_user_id.get(user_id, "drafting") == "drafting"
        )
        return ResumeStatus(
            can_resume=campaign_status == "active",
            online_initial_user_ids=online_initial_user_ids,
            missing_initial_user_ids=missing_initial_user_ids,
            initial_user_ids=initial_user_ids,
            campaign_status=campaign_status,
            submission_month=current_month,
            ready_user_ids=ready_user_ids,
            drafting_user_ids=drafting_user_ids,
            proxy_ai_user_ids=proxy_ai_user_ids,
            can_advance_month=(
                campaign_status == "active"
                and not drafting_user_ids
                and bool(initial_user_ids)
            ),
        )

    def _require_member(self, connection: sqlite3.Connection, campaign_id: int, user_id: int) -> None:
        row = connection.execute(
            """
            SELECT 1
            FROM strategy_members
            WHERE campaign_id = ? AND user_id = ?
            """,
            (int(campaign_id), int(user_id)),
        ).fetchone()
        if row is None:
            raise StrategyError("你不是这个战役的成员，不能恢复或操作该战役。", status=HTTPStatus.FORBIDDEN)

    def _campaign_from_connection(self, connection: sqlite3.Connection, campaign_id: int) -> CampaignRecord:
        row = connection.execute(
            "SELECT * FROM strategy_campaigns WHERE id = ?",
            (int(campaign_id),),
        ).fetchone()
        if row is None:
            raise StrategyError("战役不存在。", status=HTTPStatus.NOT_FOUND)
        member_rows = connection.execute(
            """
            SELECT *
            FROM strategy_members
            WHERE campaign_id = ?
            ORDER BY joined_at, user_id
            """,
            (int(campaign_id),),
        ).fetchall()
        world = self._load_campaign_world(
            connection,
            int(campaign_id),
            str(row["world_json"]),
        )
        members = tuple(
            CampaignMember(
                user_id=int(member["user_id"]),
                username=str(member["username"]),
                role=str(member["role"]),
                faction_id=str(member["faction_id"]),
                is_initial_player=bool(member["is_initial_player"]),
            )
            for member in member_rows
        )
        from wujiang.strategic.heroes import ensure_strategic_hero_system
        from wujiang.strategic.offices import ensure_office_system

        world = ensure_office_system(world, members)
        world = ensure_strategic_hero_system(world, members)
        action_rows = connection.execute(
            """
            SELECT *
            FROM strategy_actions
            WHERE campaign_id = ? AND month = ? AND status = 'pending'
            ORDER BY submitted_at, id
            """,
            (int(campaign_id), int(world.current_month)),
        ).fetchall()
        queued_actions = tuple(
            QueuedStrategyAction(
                action_id=int(action["id"]),
                campaign_id=int(action["campaign_id"]),
                user_id=int(action["user_id"]),
                username=str(action["username"]),
                faction_id=str(action["faction_id"]),
                month=int(action["month"]),
                action_type=str(action["action_type"]),
                action_key=str(action["action_key"]),
                payload=json.loads(str(action["payload_json"])),
                status=str(action["status"]),
                submitted_at=float(action["submitted_at"]),
            )
            for action in action_rows
        )
        connection.execute(
            """
            UPDATE strategy_office_change_requests
            SET status = 'expired', resolved_at = COALESCE(resolved_at, ?)
            WHERE campaign_id = ? AND status = 'pending' AND month < ?
            """,
            (time.time(), int(campaign_id), int(world.current_month)),
        )
        office_request_rows = connection.execute(
            """
            SELECT *
            FROM strategy_office_change_requests
            WHERE campaign_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 40
            """,
            (int(campaign_id),),
        ).fetchall()
        office_change_requests = tuple(
            OfficeChangeRequestRecord(
                request_id=int(item["id"]),
                campaign_id=int(item["campaign_id"]),
                month=int(item["month"]),
                request_type=str(item["request_type"]),
                faction_id=str(item["faction_id"]),
                office_id=str(item["office_id"]),
                initiator_user_id=int(item["initiator_user_id"]),
                target_user_id=int(item["target_user_id"]),
                status=str(item["status"]),
                created_at=float(item["created_at"]),
                resolved_at=float(item["resolved_at"]) if item["resolved_at"] is not None else None,
            )
            for item in office_request_rows
        )
        takeover_rows = connection.execute(
            """
            SELECT * FROM strategy_office_takeovers
            WHERE campaign_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 40
            """,
            (int(campaign_id),),
        ).fetchall()
        office_takeovers = tuple(
            OfficeTakeoverRecord(
                takeover_id=int(item["id"]),
                campaign_id=int(item["campaign_id"]),
                month=int(item["month"]),
                faction_id=str(item["faction_id"]),
                office_id=str(item["office_id"]),
                grantor_user_id=int(item["grantor_user_id"]),
                delegate_user_id=int(item["delegate_user_id"]),
                status=str(item["status"]),
                created_at=float(item["created_at"]),
                ended_at=float(item["ended_at"]) if item["ended_at"] is not None else None,
            )
            for item in takeover_rows
        )
        checkpoint_rows = connection.execute(
            """
            SELECT * FROM strategy_battle_checkpoints
            WHERE campaign_id = ?
            ORDER BY updated_at DESC, room_id
            """,
            (int(campaign_id),),
        ).fetchall()
        battle_recoveries = tuple(
            BattleRecoveryRecord(
                room_id=str(item["room_id"]),
                battle_id=str(item["battle_id"]),
                participant_user_ids=tuple(
                    int(value)
                    for value in json.loads(str(item["participant_user_ids_json"]))
                ),
                checkpoint_version=int(item["room_version"]),
                format_version=int(item["format_version"]),
                checkpoint_status=str(item["status"]),
                integrity_status=(
                    "valid"
                    if hashlib.sha256(bytes(item["room_blob"])).hexdigest() == str(item["checkpoint_hash"])
                    else "corrupt"
                ),
                updated_at=float(item["updated_at"]),
                restart_count=int(item["restart_count"]),
            )
            for item in checkpoint_rows
        )
        return CampaignRecord(
            campaign_id=int(row["id"]),
            join_code=str(row["join_code"] or ""),
            join_code_enabled=bool(row["join_code_enabled"]),
            name=str(row["name"]),
            owner_user_id=int(row["owner_user_id"]),
            status=str(row["status"]),
            current_month=int(row["current_month"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            world=world,
            members=members,
            queued_actions=queued_actions,
            office_change_requests=office_change_requests,
            office_takeovers=office_takeovers,
            battle_recoveries=battle_recoveries,
        )

    def _campaign_from_join_code(self, connection: sqlite3.Connection, join_code: str) -> CampaignRecord:
        row = connection.execute(
            "SELECT id FROM strategy_campaigns WHERE join_code = ?",
            (str(join_code),),
        ).fetchone()
        if row is None:
            raise StrategyError("战役加入码不存在。", status=HTTPStatus.NOT_FOUND)
        return self._campaign_from_connection(connection, int(row["id"]))
