from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from wujiang.strategy.backup import StrategyBackupManager
from wujiang.strategy.diagnostics import run_strategy_diagnostics
from wujiang.web.observability import ObservabilityConfig
from wujiang.web.security import SecurityConfig, normalize_origin


FORBIDDEN_DURABLE_KEYS = {
    "password", "password_hash", "session_token", "auth_token",
    "player_token", "room_token", "join_code",
}


def validate_production_configuration(
    *, public_base_url: str | None, security: SecurityConfig, observability: ObservabilityConfig,
) -> None:
    if security.environment != "production" or observability.environment != "production":
        return
    public_origin = normalize_origin(public_base_url)
    if not public_origin or not public_origin.startswith("https://"):
        raise ValueError("Production requires an explicit HTTPS public base URL.")
    if not security.require_https or security.allow_query_auth_tokens:
        raise ValueError("Production must require HTTPS and disable query-string auth tokens.")
    if public_origin not in {normalize_origin(value) for value in security.allowed_origins}:
        raise ValueError("Production allowed origins must include the exact public base URL origin.")
    if not security.trusted_proxy_networks:
        raise ValueError("Production requires at least one explicit trusted proxy network.")
    if len(observability.ops_token) < 16:
        raise ValueError("Production requires WUJIANG_OPS_TOKEN with at least 16 characters.")
    lowered_token = observability.ops_token.casefold()
    if any(marker in lowered_token for marker in ("replace-with", "change-me", "changeme", "example-token")):
        raise ValueError("Production WUJIANG_OPS_TOKEN still contains a placeholder value.")
    if not observability.request_logs_enabled:
        raise ValueError("Production structured request logging must be enabled.")


@dataclass(frozen=True, slots=True)
class DeploymentAuditFinding:
    severity: str
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"severity": self.severity, "code": self.code, "message": self.message}


@dataclass(slots=True)
class DeploymentAuditReport:
    findings: list[DeploymentAuditFinding] = field(default_factory=list)
    checks: dict[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> str:
        return "failed" if any(item.severity == "critical" for item in self.findings) else "passed"

    def add(self, severity: str, code: str, message: str) -> None:
        self.findings.append(DeploymentAuditFinding(severity, code, message))

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "checks": dict(self.checks),
            "finding_counts": {
                severity: sum(item.severity == severity for item in self.findings)
                for severity in ("critical", "warning", "info")
            },
            "findings": [item.to_dict() for item in self.findings],
        }


def _open_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _contains_forbidden_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = re.sub(r"[^a-z0-9]+", "_", str(key).casefold()).strip("_")
            if normalized in FORBIDDEN_DURABLE_KEYS or _contains_forbidden_key(nested):
                return True
    elif isinstance(value, list):
        return any(_contains_forbidden_key(item) for item in value)
    return False


def run_production_audit(
    *,
    public_base_url: str | None,
    security: SecurityConfig,
    observability: ObservabilityConfig,
    auth_db: str | Path,
    analytics_db: str | Path,
    match_history_db: str | Path,
    strategy_db: str | Path,
    backup_dir: str | Path | None = None,
    replay_dir: str | Path | None = None,
    require_backup: bool = True,
) -> DeploymentAuditReport:
    report = DeploymentAuditReport()
    try:
        validate_production_configuration(
            public_base_url=public_base_url, security=security, observability=observability,
        )
        report.checks["production_configuration"] = "passed"
    except ValueError as exc:
        report.checks["production_configuration"] = "failed"
        report.add("critical", "production_configuration_invalid", str(exc))

    paths = {
        "auth": Path(auth_db).expanduser(), "analytics": Path(analytics_db).expanduser(),
        "match_history": Path(match_history_db).expanduser(), "strategy": Path(strategy_db).expanduser(),
    }
    report.checks["database_files"] = 0
    for name, path in paths.items():
        if not path.is_file():
            report.add("critical", f"{name}_database_missing", f"Required {name} database is missing.")
        else:
            report.checks["database_files"] += 1

    if paths["auth"].is_file():
        try:
            connection = _open_read_only(paths["auth"])
            try:
                tables = _table_names(connection)
                if not {"users", "sessions"}.issubset(tables):
                    raise ValueError("required auth tables are missing")
                columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(users)")}
                if "password" in columns:
                    report.add("critical", "plaintext_password_column", "Auth schema contains a plaintext password column.")
                session_rows = connection.execute("SELECT token, token_hash FROM sessions").fetchall()
                invalid_sessions = sum(
                    not str(row["token"]).startswith(("stored:", "migrated:"))
                    or not re.fullmatch(r"[0-9a-f]{64}", str(row["token_hash"] or ""))
                    for row in session_rows
                )
                report.checks["sessions_checked"] = len(session_rows)
                if invalid_sessions:
                    report.add("critical", "session_secret_at_rest", "One or more sessions are not digest-only at rest.")
            finally:
                connection.close()
        except (sqlite3.Error, ValueError) as exc:
            report.add("critical", "auth_privacy_check_failed", f"Auth privacy check failed: {type(exc).__name__}.")

    if paths["analytics"].is_file():
        try:
            connection = _open_read_only(paths["analytics"])
            try:
                tables = _table_names(connection)
                if "analytics_events" not in tables:
                    raise ValueError("analytics table is missing")
                rows = connection.execute("SELECT properties_json FROM analytics_events").fetchall()
                forbidden = 0
                for row in rows:
                    payload = json.loads(str(row[0] or "{}"))
                    forbidden += int(_contains_forbidden_key(payload))
                report.checks["analytics_events_checked"] = len(rows)
                if forbidden:
                    report.add("critical", "analytics_contains_credentials", "Analytics properties contain forbidden credential keys.")
            finally:
                connection.close()
        except (sqlite3.Error, ValueError, json.JSONDecodeError) as exc:
            report.add("critical", "analytics_privacy_check_failed", f"Analytics privacy check failed: {type(exc).__name__}.")

    if paths["match_history"].is_file():
        try:
            connection = _open_read_only(paths["match_history"])
            try:
                tables = _table_names(connection)
                if "match_history" not in tables:
                    raise ValueError("match-history table is missing")
                columns = {
                    re.sub(r"[^a-z0-9]+", "_", str(row[1]).casefold()).strip("_")
                    for row in connection.execute("PRAGMA table_info(match_history)")
                }
                forbidden_columns = sorted(columns & FORBIDDEN_DURABLE_KEYS)
                if forbidden_columns:
                    report.add("critical", "match_history_credential_columns", "Match history contains credential columns.")
                rows = connection.execute("SELECT postgame_json, seats_json FROM match_history").fetchall()
                forbidden_payloads = 0
                for row in rows:
                    forbidden_payloads += int(_contains_forbidden_key(json.loads(str(row[0] or "{}"))))
                    forbidden_payloads += int(_contains_forbidden_key(json.loads(str(row[1] or "[]"))))
                report.checks["match_history_records_checked"] = len(rows)
                if forbidden_payloads:
                    report.add("critical", "match_history_contains_credentials", "Match-history JSON contains forbidden credential keys.")
                report.checks["match_history_schema"] = "checked"
            finally:
                connection.close()
        except (sqlite3.Error, ValueError) as exc:
            report.add("critical", "match_history_privacy_check_failed", f"Match-history privacy check failed: {type(exc).__name__}.")

    if paths["strategy"].is_file():
        diagnostic = run_strategy_diagnostics(paths["strategy"], backup_dir=backup_dir)
        report.checks["strategy_diagnostics"] = diagnostic.status
        if diagnostic.status != "healthy":
            report.add("critical", "strategy_diagnostics_not_healthy", "Strategy integrity diagnostics reported non-healthy findings.")
        try:
            connection = _open_read_only(paths["strategy"])
            try:
                tables = _table_names(connection)
                if "strategy_operation_audit" not in tables:
                    raise ValueError("strategy audit table is missing")
                rows = connection.execute("SELECT details_json FROM strategy_operation_audit").fetchall()
                forbidden = sum(_contains_forbidden_key(json.loads(str(row[0] or "{}"))) for row in rows)
                report.checks["strategy_audit_entries_checked"] = len(rows)
                if forbidden:
                    report.add("critical", "strategy_audit_contains_credentials", "Strategy audit metadata contains forbidden credential keys.")
            finally:
                connection.close()
        except (sqlite3.Error, ValueError, json.JSONDecodeError) as exc:
            report.add("critical", "strategy_privacy_check_failed", f"Strategy privacy check failed: {type(exc).__name__}.")

        manager = StrategyBackupManager(paths["strategy"], backup_dir=backup_dir)
        backups = manager.list_backups()
        report.checks["verified_backups"] = len(backups)
        if require_backup and not backups:
            report.add("critical", "verified_backup_missing", "No verified managed backup is available.")
        elif backups:
            try:
                drill = manager.drill_restore(backups[0].path)
                report.checks["latest_backup_restore_drill"] = drill["status"]
            except Exception:
                report.add("critical", "backup_restore_drill_failed", "Latest managed backup failed the isolated restore drill.")

    replay_root = Path(replay_dir).expanduser() if replay_dir is not None else None
    if replay_root is not None:
        replay_files = sorted(replay_root.rglob("*.json")) if replay_root.exists() else []
        report.checks["replay_files_checked"] = len(replay_files)
        for replay_path in replay_files:
            try:
                payload = json.loads(replay_path.read_text(encoding="utf-8"))
                if _contains_forbidden_key(payload):
                    report.add("critical", "replay_contains_credentials", "A replay file contains forbidden credential keys.")
                    break
            except (OSError, ValueError, json.JSONDecodeError):
                report.add("critical", "replay_privacy_check_failed", "A replay file could not be parsed during privacy audit.")
                break
    return report
