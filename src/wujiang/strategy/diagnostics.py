from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from wujiang.strategy.audit import verify_operation_audit_chain
from wujiang.strategy.backup import StrategyBackupManager
from wujiang.strategy.migrations import (
    CURRENT_STRATEGY_SAVE_VERSION,
    migrate_world_payload,
    strategy_save_version,
)
from wujiang.strategy.models import WorldState
from wujiang.strategy.store import CURRENT_STRATEGY_SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class DiagnosticFinding:
    severity: str
    code: str
    message: str
    campaign_id: int | None = None
    room_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "severity": self.severity, "code": self.code, "message": self.message,
        }
        if self.campaign_id is not None:
            payload["campaign_id"] = self.campaign_id
        if self.room_id:
            payload["room_id"] = self.room_id
        return payload


@dataclass(slots=True)
class StrategyDiagnosticReport:
    db_path: str
    checked_at: float = field(default_factory=time.time)
    findings: list[DiagnosticFinding] = field(default_factory=list)
    checks: dict[str, int] = field(default_factory=dict)

    @property
    def status(self) -> str:
        severities = {item.severity for item in self.findings}
        return "critical" if "critical" in severities else "warning" if "warning" in severities else "healthy"

    def add(self, severity: str, code: str, message: str, **scope: Any) -> None:
        self.findings.append(DiagnosticFinding(severity, code, message, **scope))

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status, "db_path": self.db_path, "checked_at": self.checked_at,
            "checks": dict(self.checks),
            "finding_counts": {
                severity: sum(item.severity == severity for item in self.findings)
                for severity in ("critical", "warning", "info")
            },
            "findings": [item.to_dict() for item in self.findings],
        }


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }


def run_strategy_diagnostics(
    db_path: str | Path, *, backup_dir: str | Path | None = None
) -> StrategyDiagnosticReport:
    path = Path(db_path).expanduser().resolve()
    report = StrategyDiagnosticReport(str(path))
    if not path.is_file():
        report.add("critical", "database_missing", "Strategy database file does not exist.")
        return report
    worlds_by_campaign: dict[int, WorldState] = {}
    try:
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        report.add("critical", "database_open_failed", f"Cannot open strategy database read-only: {exc}")
        return report
    try:
        try:
            quick_rows = connection.execute("PRAGMA quick_check").fetchall()
            quick_messages = [str(row[0]) for row in quick_rows]
            report.checks["sqlite_quick_check_rows"] = len(quick_messages)
            if quick_messages != ["ok"]:
                report.add("critical", "sqlite_quick_check_failed", "; ".join(quick_messages[:5]))
        except sqlite3.Error as exc:
            report.add("critical", "sqlite_quick_check_failed", str(exc))

        try:
            foreign_rows = connection.execute("PRAGMA foreign_key_check").fetchall()
            report.checks["foreign_key_violations"] = len(foreign_rows)
            if foreign_rows:
                report.add("critical", "foreign_key_violation", f"Found {len(foreign_rows)} foreign-key violations.")
        except sqlite3.Error as exc:
            report.add("critical", "foreign_key_check_failed", str(exc))

        tables = _tables(connection)
        required = {
            "strategy_campaigns", "strategy_members", "strategy_actions",
            "strategy_battle_checkpoints", "strategy_schema_migrations",
            "strategy_save_migrations", "strategy_operation_audit",
        }
        missing = sorted(required - tables)
        if missing:
            report.add("critical", "schema_tables_missing", f"Missing strategy tables: {', '.join(missing)}")

        if "strategy_schema_migrations" in tables:
            versions = [
                int(row[0]) for row in connection.execute(
                    "SELECT version FROM strategy_schema_migrations ORDER BY version"
                ).fetchall()
            ]
            report.checks["schema_migrations"] = len(versions)
            expected = list(range(1, CURRENT_STRATEGY_SCHEMA_VERSION + 1))
            if versions != expected:
                report.add("critical", "schema_version_invalid", f"Schema versions {versions}; expected {expected}.")

        if "strategy_campaigns" in tables:
            campaigns = connection.execute(
                "SELECT id, status, current_month, world_json FROM strategy_campaigns ORDER BY id"
            ).fetchall()
            report.checks["campaigns"] = len(campaigns)
            for row in campaigns:
                campaign_id = int(row["id"])
                try:
                    payload = json.loads(str(row["world_json"]))
                    if not isinstance(payload, dict):
                        raise ValueError("world payload is not an object")
                    version = strategy_save_version(payload)
                    if version > CURRENT_STRATEGY_SAVE_VERSION:
                        raise ValueError(f"future save version {version}")
                    if version < CURRENT_STRATEGY_SAVE_VERSION:
                        report.add("warning", "campaign_save_migration_pending", f"Save version {version} requires migration to {CURRENT_STRATEGY_SAVE_VERSION}.", campaign_id=campaign_id)
                    world = WorldState.from_dict(migrate_world_payload(payload))
                    worlds_by_campaign[campaign_id] = world
                    if int(row["current_month"]) != int(world.current_month):
                        report.add("critical", "campaign_month_mismatch", "Database month differs from world month.", campaign_id=campaign_id)
                    world_archived = str(world.campaign_conclusion.get("state") or "") == "archived"
                    if (str(row["status"]) == "archived") != world_archived:
                        report.add("critical", "campaign_archive_mismatch", "Campaign and world archive states differ.", campaign_id=campaign_id)
                except Exception as exc:
                    report.add("critical", "campaign_world_invalid", f"World payload cannot be loaded: {exc}", campaign_id=campaign_id)

        if "strategy_actions" in tables:
            actions = connection.execute(
                "SELECT id, campaign_id, month, status, payload_json FROM strategy_actions"
            ).fetchall()
            report.checks["strategy_actions"] = len(actions)
            for row in actions:
                campaign_id = int(row["campaign_id"])
                try:
                    payload = json.loads(str(row["payload_json"]))
                    if not isinstance(payload, dict):
                        raise ValueError("action payload is not an object")
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    report.add("critical", "strategy_action_payload_invalid", f"Action {row['id']}: {exc}", campaign_id=campaign_id)
                world = worlds_by_campaign.get(campaign_id)
                if world is not None and str(row["status"]) == "pending" and int(row["month"]) < int(world.current_month):
                    report.add("warning", "stale_pending_action", f"Pending action {row['id']} belongs to past month {row['month']}.", campaign_id=campaign_id)
                if world is not None and int(row["month"]) > int(world.current_month):
                    report.add("critical", "future_strategy_action", f"Action {row['id']} belongs to future month {row['month']}.", campaign_id=campaign_id)

        if "strategy_battle_checkpoints" in tables:
            checkpoints = connection.execute(
                "SELECT room_id, campaign_id, battle_id, participant_user_ids_json, room_blob, checkpoint_hash FROM strategy_battle_checkpoints"
            ).fetchall()
            report.checks["battle_checkpoints"] = len(checkpoints)
            for row in checkpoints:
                room_id = str(row["room_id"])
                campaign_id = int(row["campaign_id"])
                digest = hashlib.sha256(bytes(row["room_blob"])).hexdigest()
                if digest != str(row["checkpoint_hash"]):
                    report.add("critical", "checkpoint_hash_mismatch", "Battle checkpoint digest does not match its bytes.", campaign_id=campaign_id, room_id=room_id)
                world = worlds_by_campaign.get(campaign_id)
                bound = world is not None and any(
                    item.battle_id == str(row["battle_id"])
                    and str(item.battle_room_id or "").strip().upper() == room_id.upper()
                    for item in world.pending_battles
                )
                if not bound:
                    report.add("critical", "checkpoint_binding_invalid", "Battle checkpoint is not bound to the campaign battle and room.", campaign_id=campaign_id, room_id=room_id)
                try:
                    participants = {int(value) for value in json.loads(str(row["participant_user_ids_json"]))}
                    members = {
                        int(item[0]) for item in connection.execute(
                            "SELECT user_id FROM strategy_members WHERE campaign_id = ?", (campaign_id,)
                        ).fetchall()
                    }
                    unknown = sorted(participants - members)
                    if unknown:
                        report.add("critical", "checkpoint_participant_invalid", f"Checkpoint has non-member participants: {unknown}.", campaign_id=campaign_id, room_id=room_id)
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    report.add("critical", "checkpoint_participants_malformed", str(exc), campaign_id=campaign_id, room_id=room_id)

        if "strategy_operation_audit" in tables:
            audit_rows = connection.execute("SELECT * FROM strategy_operation_audit ORDER BY id").fetchall()
            valid, message, count = verify_operation_audit_chain(audit_rows)
            report.checks["audit_entries"] = count
            if not valid:
                report.add("critical", "audit_chain_invalid", message)
    except sqlite3.Error as exc:
        report.add("critical", "database_query_failed", str(exc))
    finally:
        connection.close()

    manager = StrategyBackupManager(path, backup_dir=backup_dir)
    if manager.backup_dir.exists():
        backup_paths = sorted(manager.backup_dir.glob("*.sqlite3"))
        report.checks["backup_files"] = len(backup_paths)
        for backup_path in backup_paths:
            try:
                manager.verify_backup(backup_path)
            except Exception as exc:
                report.add("warning", "backup_invalid", f"{backup_path.name}: {exc}")
    else:
        report.checks["backup_files"] = 0
        report.add("info", "backup_directory_missing", "No managed backup directory exists yet.")
    return report
