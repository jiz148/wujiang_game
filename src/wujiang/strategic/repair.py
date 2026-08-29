from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from wujiang.strategic.audit import append_operation_audit, verify_operation_audit_chain
from wujiang.strategic.backup import StrategyBackupManager
from wujiang.strategic.diagnostics import run_strategy_diagnostics
from wujiang.strategic.errors import StrategyError
from wujiang.strategic.migrations import CURRENT_STRATEGY_SAVE_VERSION, migrate_world_payload
from wujiang.strategic.models import WorldState
from wujiang.strategic.store import CURRENT_STRATEGY_SCHEMA_VERSION


REPAIR_PLAN_VERSION = 1
REPAIR_TOKEN_ENV = "WUJIANG_STRATEGY_REPAIR_TOKEN"
_SAFE_OPERATOR = re.compile(r"^[a-zA-Z0-9_.@-]{2,64}$")
_SAFE_REASON = re.compile(r"^[a-z0-9_-]{2,48}$")


def database_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def maintenance_marker_path(db_path: str | Path) -> Path:
    path = Path(db_path).expanduser().resolve()
    return path.with_name(f"{path.name}.maintenance.json")


@dataclass(frozen=True, slots=True)
class StrategyRepairPlan:
    plan_id: str
    plan_version: int
    plan_type: str
    db_path: str
    db_sha256: str
    created_at: float
    actions: tuple[dict[str, Any], ...]
    blocked_findings: tuple[dict[str, Any], ...]
    backup_path: str = ""
    backup_sha256: str = ""

    def _unsigned_dict(self) -> dict[str, Any]:
        return {
            "plan_version": self.plan_version, "plan_type": self.plan_type,
            "db_path": self.db_path, "db_sha256": self.db_sha256,
            "created_at": self.created_at, "actions": list(self.actions),
            "blocked_findings": list(self.blocked_findings),
            "backup_path": self.backup_path, "backup_sha256": self.backup_sha256,
        }

    def to_dict(self) -> dict[str, Any]:
        return {"plan_id": self.plan_id, **self._unsigned_dict()}

    @classmethod
    def build(cls, **payload: Any) -> StrategyRepairPlan:
        unsigned = {
            "plan_version": REPAIR_PLAN_VERSION,
            "plan_type": str(payload["plan_type"]),
            "db_path": str(payload["db_path"]),
            "db_sha256": str(payload["db_sha256"]),
            "created_at": float(payload.get("created_at", time.time())),
            "actions": list(payload.get("actions", [])),
            "blocked_findings": list(payload.get("blocked_findings", [])),
            "backup_path": str(payload.get("backup_path", "")),
            "backup_sha256": str(payload.get("backup_sha256", "")),
        }
        plan_id = hashlib.sha256(
            json.dumps(unsigned, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).hexdigest()
        return cls(plan_id=plan_id, actions=tuple(unsigned.pop("actions")),
                   blocked_findings=tuple(unsigned.pop("blocked_findings")), **unsigned)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> StrategyRepairPlan:
        if not isinstance(payload, dict):
            raise StrategyError("Repair plan must be a JSON object.")
        if int(payload.get("plan_version", 0)) != REPAIR_PLAN_VERSION:
            raise StrategyError("Repair plan version is not supported.")
        rebuilt = cls.build(
            plan_type=payload.get("plan_type"), db_path=payload.get("db_path"),
            db_sha256=payload.get("db_sha256"), created_at=payload.get("created_at"),
            actions=payload.get("actions"), blocked_findings=payload.get("blocked_findings"),
            backup_path=payload.get("backup_path", ""),
            backup_sha256=payload.get("backup_sha256", ""),
        )
        if not hmac.compare_digest(str(payload.get("plan_id") or ""), rebuilt.plan_id):
            raise StrategyError("Repair plan digest does not match its contents.")
        return rebuilt


def _read_world_rows(path: Path) -> dict[int, tuple[sqlite3.Row, WorldState]]:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT id, status, current_month, world_json FROM strategy_campaigns ORDER BY id"
        ).fetchall()
        loaded: dict[int, tuple[sqlite3.Row, WorldState]] = {}
        for row in rows:
            try:
                world = WorldState.from_dict(migrate_world_payload(json.loads(str(row["world_json"]))))
            except Exception:
                continue
            loaded[int(row["id"])] = (row, world)
        return loaded
    finally:
        connection.close()


def create_repair_plan(
    db_path: str | Path, *, backup_dir: str | Path | None = None
) -> StrategyRepairPlan:
    path = Path(db_path).expanduser().resolve()
    report = run_strategy_diagnostics(path, backup_dir=backup_dir)
    worlds = _read_world_rows(path) if path.is_file() else {}
    supported_codes: set[tuple[str, int]] = set()
    actions: list[dict[str, Any]] = []
    for campaign_id, (row, world) in worlds.items():
        set_values: dict[str, Any] = {}
        if int(row["current_month"]) != int(world.current_month):
            set_values["current_month"] = int(world.current_month)
            supported_codes.add(("campaign_month_mismatch", campaign_id))
        world_archived = str(world.campaign_conclusion.get("state") or "") == "archived"
        if world_archived and str(row["status"]) != "archived":
            set_values["status"] = "archived"
            supported_codes.add(("campaign_archive_mismatch", campaign_id))
        if set_values:
            actions.append({
                "kind": "sync_campaign_index_from_world", "campaign_id": campaign_id,
                "expected_status": str(row["status"]),
                "expected_current_month": int(row["current_month"]),
                "expected_world_hash": hashlib.sha256(str(row["world_json"]).encode("utf-8")).hexdigest(),
                "set": set_values,
            })
    blocked = [
        finding.to_dict() for finding in report.findings
        if finding.severity == "critical"
        and (finding.code, int(finding.campaign_id or 0)) not in supported_codes
    ]
    return StrategyRepairPlan.build(
        plan_type="bounded_repair", db_path=str(path), db_sha256=database_sha256(path),
        actions=actions, blocked_findings=blocked,
    )


def create_restore_plan(
    db_path: str | Path, backup_path: str | Path, *, backup_dir: str | Path | None = None
) -> StrategyRepairPlan:
    path = Path(db_path).expanduser().resolve()
    manager = StrategyBackupManager(path, backup_dir=backup_dir)
    record = manager.verify_backup(backup_path)
    manager.drill_restore(record.path)
    if record.strategy_schema_version != CURRENT_STRATEGY_SCHEMA_VERSION:
        raise StrategyError("Restore backup schema version does not match the running service.")
    if record.strategy_save_version != CURRENT_STRATEGY_SAVE_VERSION:
        raise StrategyError("Restore backup save version does not match the running service.")
    backup_report = run_strategy_diagnostics(record.path, backup_dir=manager.backup_dir / "unused")
    critical = [item.to_dict() for item in backup_report.findings if item.severity == "critical"]
    if critical:
        raise StrategyError("Restore backup failed strategy diagnostics.")
    return StrategyRepairPlan.build(
        plan_type="restore_backup", db_path=str(path), db_sha256=database_sha256(path),
        actions=[{"kind": "restore_verified_backup"}], blocked_findings=[],
        backup_path=str(record.path), backup_sha256=record.sha256,
    )


def _require_apply_authorization(authorization: str, operator: str, reason_code: str) -> None:
    expected = str(os.environ.get(REPAIR_TOKEN_ENV) or "")
    if len(expected) < 16:
        raise StrategyError(f"{REPAIR_TOKEN_ENV} must be configured with at least 16 characters.")
    if not hmac.compare_digest(str(authorization or ""), expected):
        raise StrategyError("Restricted strategy repair authorization failed.")
    if not _SAFE_OPERATOR.fullmatch(str(operator or "")):
        raise StrategyError("Repair operator must be a 2-64 character account identifier.")
    if not _SAFE_REASON.fullmatch(str(reason_code or "")):
        raise StrategyError("Repair reason code must use 2-48 lowercase letters, digits, '_' or '-'.")


@contextmanager
def _maintenance_guard(path: Path, *, plan_id: str, operator: str) -> Iterator[Path]:
    marker = maintenance_marker_path(path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"plan_id": plan_id, "operator": operator, "created_at": time.time()}, sort_keys=True)
    try:
        with marker.open("x", encoding="utf-8") as target:
            target.write(payload)
    except FileExistsError as exc:
        raise StrategyError(f"Strategy maintenance marker already exists: {marker}") from exc
    try:
        yield marker
    finally:
        try:
            current = json.loads(marker.read_text(encoding="utf-8"))
            if str(current.get("plan_id")) == plan_id:
                marker.unlink(missing_ok=True)
        except (OSError, ValueError, json.JSONDecodeError):
            pass


def _load_plan(plan: StrategyRepairPlan | dict[str, Any] | str | Path) -> StrategyRepairPlan:
    if isinstance(plan, StrategyRepairPlan):
        return StrategyRepairPlan.from_dict(plan.to_dict())
    if isinstance(plan, dict):
        return StrategyRepairPlan.from_dict(plan)
    return StrategyRepairPlan.from_dict(json.loads(Path(plan).read_text(encoding="utf-8")))


def apply_repair_plan(
    plan: StrategyRepairPlan | dict[str, Any] | str | Path, *, confirm: str,
    operator: str, reason_code: str, authorization: str,
    backup_dir: str | Path | None = None,
) -> dict[str, Any]:
    loaded = _load_plan(plan)
    _require_apply_authorization(authorization, operator, reason_code)
    if not hmac.compare_digest(str(confirm or ""), loaded.plan_id):
        raise StrategyError("Repair confirmation must exactly match the plan id.")
    if loaded.blocked_findings:
        raise StrategyError("Repair plan contains unsupported critical findings; use a verified backup or the runbook.")
    if not loaded.actions:
        raise StrategyError("Repair plan contains no allowlisted actions.")
    path = Path(loaded.db_path).resolve()
    if database_sha256(path) != loaded.db_sha256:
        raise StrategyError("Live database changed after planning; generate a new repair plan.")
    with _maintenance_guard(path, plan_id=loaded.plan_id, operator=operator):
        if loaded.plan_type == "restore_backup":
            return _apply_restore(loaded, operator=operator, reason_code=reason_code, backup_dir=backup_dir)
        if loaded.plan_type != "bounded_repair":
            raise StrategyError("Unknown repair plan type.")
        return _apply_bounded(loaded, operator=operator, reason_code=reason_code, backup_dir=backup_dir)


def _apply_bounded(
    plan: StrategyRepairPlan, *, operator: str, reason_code: str,
    backup_dir: str | Path | None,
) -> dict[str, Any]:
    path = Path(plan.db_path)
    manager = StrategyBackupManager(path, backup_dir=backup_dir)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    backup = None
    try:
        backup = manager.create_backup(
            reason=f"pre_repair-{plan.plan_id[:12]}", automatic=True,
            source_connection=connection, strategy_schema_version=CURRENT_STRATEGY_SCHEMA_VERSION,
        )
        connection.execute("BEGIN IMMEDIATE")
        for action in plan.actions:
            if action.get("kind") != "sync_campaign_index_from_world":
                raise StrategyError("Repair plan contains a non-allowlisted action.")
            campaign_id = int(action["campaign_id"])
            row = connection.execute(
                "SELECT status, current_month, world_json FROM strategy_campaigns WHERE id = ?", (campaign_id,)
            ).fetchone()
            if row is None:
                raise StrategyError("Repair target campaign no longer exists.")
            current_world_hash = hashlib.sha256(str(row["world_json"]).encode("utf-8")).hexdigest()
            if (str(row["status"]) != action["expected_status"]
                    or int(row["current_month"]) != int(action["expected_current_month"])
                    or current_world_hash != action["expected_world_hash"]):
                raise StrategyError("Repair target changed after planning.")
            values = dict(action.get("set") or {})
            if set(values) - {"current_month", "status"}:
                raise StrategyError("Repair action attempts to change a non-allowlisted field.")
            status = str(values.get("status", row["status"]))
            month = int(values.get("current_month", row["current_month"]))
            connection.execute(
                "UPDATE strategy_campaigns SET status = ?, current_month = ?, updated_at = ? WHERE id = ?",
                (status, month, time.time(), campaign_id),
            )
            append_operation_audit(
                connection, campaign_id=campaign_id, actor_user_id=None, actor_username=operator,
                operation="admin.campaign_index_repaired", target_type="campaign", target_id=campaign_id,
                before_hash=plan.db_sha256, after_hash=current_world_hash,
                details={"plan_id": plan.plan_id, "reason_code": reason_code,
                         "status": status, "month": month},
            )
        audit_rows = connection.execute("SELECT * FROM strategy_operation_audit ORDER BY id").fetchall()
        valid, message, _ = verify_operation_audit_chain(audit_rows)
        if not valid:
            raise StrategyError(f"Repair audit verification failed before commit: {message}")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    report = run_strategy_diagnostics(path, backup_dir=backup_dir)
    if report.status == "critical":
        raise StrategyError("Post-repair diagnostics remain critical; preserve the pre-repair backup and follow the runbook.")
    return {"status": "passed", "plan_id": plan.plan_id, "plan_type": plan.plan_type,
            "actions_applied": len(plan.actions), "pre_repair_backup": backup.to_dict() if backup else None,
            "post_diagnostics": report.to_dict()}


def _apply_restore(
    plan: StrategyRepairPlan, *, operator: str, reason_code: str,
    backup_dir: str | Path | None,
) -> dict[str, Any]:
    path = Path(plan.db_path)
    manager = StrategyBackupManager(path, backup_dir=backup_dir)
    record = manager.verify_backup(plan.backup_path)
    if record.sha256 != plan.backup_sha256:
        raise StrategyError("Restore backup changed after planning.")
    manager.drill_restore(record.path)
    live = sqlite3.connect(path, timeout=1)
    try:
        live.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        pre_backup = manager.create_backup(
            reason=f"pre_restore-{plan.plan_id[:12]}", automatic=True,
            source_connection=live, strategy_schema_version=CURRENT_STRATEGY_SCHEMA_VERSION,
        )
        live.execute("BEGIN EXCLUSIVE")
        live.commit()
    finally:
        live.close()
    staged = path.with_name(f"{path.name}.{plan.plan_id[:12]}.restore-partial")
    staged.unlink(missing_ok=True)
    source = sqlite3.connect(f"file:{record.path.as_posix()}?mode=ro", uri=True)
    destination = sqlite3.connect(staged)
    destination.row_factory = sqlite3.Row
    try:
        source.backup(destination)
        append_operation_audit(
            destination, campaign_id=None, actor_user_id=None, actor_username=operator,
            operation="admin.backup_restored", target_type="database", target_id=path.name,
            before_hash=plan.db_sha256, after_hash=record.sha256,
            details={"plan_id": plan.plan_id, "reason_code": reason_code,
                     "backup_file": record.path.name},
        )
        destination.commit()
    finally:
        source.close()
        destination.close()
    staged_report = run_strategy_diagnostics(staged, backup_dir=manager.backup_dir / "unused")
    if staged_report.status == "critical":
        staged.unlink(missing_ok=True)
        raise StrategyError("Staged restore failed post-restore diagnostics.")
    os.replace(staged, path)
    for suffix in ("-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)
    final_report = run_strategy_diagnostics(path, backup_dir=backup_dir)
    if final_report.status == "critical":
        raise StrategyError("Restored database failed final diagnostics; use the preserved pre-restore backup.")
    return {"status": "passed", "plan_id": plan.plan_id, "plan_type": plan.plan_type,
            "restored_backup": record.to_dict(), "pre_restore_backup": pre_backup.to_dict(),
            "post_diagnostics": final_report.to_dict()}
