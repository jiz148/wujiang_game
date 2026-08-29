from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wujiang.strategic.errors import StrategyError
from wujiang.strategic.migrations import CURRENT_STRATEGY_SAVE_VERSION, migrate_world_payload
from wujiang.strategic.models import WorldState


BACKUP_MANIFEST_VERSION = 1
DEFAULT_AUTOMATIC_BACKUP_RETENTION = 12


def strategy_backup_directory(db_path: Path, raw_path: str | Path | None = None) -> Path:
    configured = str(raw_path or os.environ.get("WUJIANG_STRATEGY_BACKUP_DIR") or "").strip()
    return Path(configured).expanduser() if configured else db_path.parent / "backups"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_reason(reason: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(reason or "backup").strip()).strip("-")
    return normalized[:48] or "backup"


@dataclass(frozen=True, slots=True)
class StrategyBackupRecord:
    path: Path
    manifest_path: Path
    reason: str
    automatic: bool
    created_at: float
    size_bytes: int
    sha256: str
    strategy_schema_version: int
    strategy_save_version: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "manifest_path": str(self.manifest_path),
            "reason": self.reason,
            "automatic": self.automatic,
            "created_at": self.created_at,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "strategy_schema_version": self.strategy_schema_version,
            "strategy_save_version": self.strategy_save_version,
        }


class StrategyBackupManager:
    def __init__(
        self,
        db_path: str | Path,
        *,
        backup_dir: str | Path | None = None,
        automatic_retention: int = DEFAULT_AUTOMATIC_BACKUP_RETENTION,
    ) -> None:
        self.db_path = Path(db_path).expanduser()
        self.backup_dir = strategy_backup_directory(self.db_path, backup_dir)
        self.automatic_retention = max(1, int(automatic_retention))

    @staticmethod
    def _manifest_path(backup_path: Path) -> Path:
        return backup_path.with_name(f"{backup_path.name}.json")

    def _require_managed_path(self, backup_path: str | Path) -> Path:
        resolved_dir = self.backup_dir.resolve()
        resolved = Path(backup_path).expanduser().resolve()
        try:
            resolved.relative_to(resolved_dir)
        except ValueError as exc:
            raise StrategyError("备份文件必须位于配置的战略备份目录中。") from exc
        return resolved

    def create_backup(
        self,
        *,
        reason: str,
        automatic: bool,
        source_connection: sqlite3.Connection | None = None,
        strategy_schema_version: int = 0,
    ) -> StrategyBackupRecord:
        if not self.db_path.exists() and source_connection is None:
            raise StrategyError("战略数据库尚不存在，不能创建备份。")
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        created_at = time.time()
        timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(created_at))
        suffix = secrets.token_hex(4)
        backup_path = self.backup_dir / f"wujiang-{timestamp}-{_safe_reason(reason)}-{suffix}.sqlite3"
        partial_path = backup_path.with_name(f"{backup_path.name}.partial")
        manifest_path = self._manifest_path(backup_path)
        manifest_partial = manifest_path.with_name(f"{manifest_path.name}.partial")
        owns_source = source_connection is None
        source = source_connection or sqlite3.connect(self.db_path)
        try:
            destination = sqlite3.connect(partial_path)
            try:
                source.backup(destination)
                quick_check = str(destination.execute("PRAGMA quick_check").fetchone()[0])
                if quick_check.lower() != "ok":
                    raise StrategyError(f"备份 SQLite 快速校验失败：{quick_check}")
            finally:
                destination.close()
            partial_path.replace(backup_path)
            digest = _sha256_file(backup_path)
            manifest = {
                "manifest_version": BACKUP_MANIFEST_VERSION,
                "database_filename": backup_path.name,
                "reason": str(reason or "backup"),
                "automatic": bool(automatic),
                "created_at": created_at,
                "size_bytes": backup_path.stat().st_size,
                "sha256": digest,
                "sqlite_quick_check": "ok",
                "strategy_schema_version": int(strategy_schema_version),
                "strategy_save_version": CURRENT_STRATEGY_SAVE_VERSION,
            }
            manifest_partial.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            manifest_partial.replace(manifest_path)
        except Exception:
            partial_path.unlink(missing_ok=True)
            manifest_partial.unlink(missing_ok=True)
            backup_path.unlink(missing_ok=True)
            manifest_path.unlink(missing_ok=True)
            raise
        finally:
            if owns_source:
                source.close()
        record = self.verify_backup(backup_path)
        if automatic:
            self.prune_automatic_backups()
        return record

    def verify_backup(self, backup_path: str | Path) -> StrategyBackupRecord:
        path = self._require_managed_path(backup_path)
        manifest_path = self._manifest_path(path)
        if not path.is_file() or not manifest_path.is_file():
            raise StrategyError("备份文件或清单不存在。")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise StrategyError("备份清单无法读取。") from exc
        if int(manifest.get("manifest_version", 0)) != BACKUP_MANIFEST_VERSION:
            raise StrategyError("备份清单版本不受支持。")
        if str(manifest.get("database_filename") or "") != path.name:
            raise StrategyError("备份清单与数据库文件名不匹配。")
        actual_size = path.stat().st_size
        actual_hash = _sha256_file(path)
        if int(manifest.get("size_bytes", -1)) != actual_size:
            raise StrategyError("备份文件大小与清单不匹配。")
        if str(manifest.get("sha256") or "") != actual_hash:
            raise StrategyError("备份文件摘要校验失败。")
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            result = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        finally:
            connection.close()
        if result.lower() != "ok":
            raise StrategyError(f"备份 SQLite 快速校验失败：{result}")
        return StrategyBackupRecord(
            path=path,
            manifest_path=manifest_path,
            reason=str(manifest.get("reason") or "backup"),
            automatic=bool(manifest.get("automatic")),
            created_at=float(manifest.get("created_at", 0)),
            size_bytes=actual_size,
            sha256=actual_hash,
            strategy_schema_version=int(manifest.get("strategy_schema_version", 0)),
            strategy_save_version=int(manifest.get("strategy_save_version", 0)),
        )

    def list_backups(self) -> list[StrategyBackupRecord]:
        if not self.backup_dir.exists():
            return []
        records: list[StrategyBackupRecord] = []
        for path in self.backup_dir.glob("wujiang-*.sqlite3"):
            try:
                records.append(self.verify_backup(path))
            except StrategyError:
                continue
        return sorted(records, key=lambda item: (item.created_at, item.path.name), reverse=True)

    def prune_automatic_backups(self) -> list[Path]:
        automatic = [record for record in self.list_backups() if record.automatic]
        removed: list[Path] = []
        for record in automatic[self.automatic_retention :]:
            record.path.unlink(missing_ok=True)
            record.manifest_path.unlink(missing_ok=True)
            removed.append(record.path)
        return removed

    def drill_restore(self, backup_path: str | Path) -> dict[str, Any]:
        record = self.verify_backup(backup_path)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="restore-drill-", dir=self.backup_dir) as temp_dir:
            restored_path = Path(temp_dir) / "restored.sqlite3"
            source = sqlite3.connect(f"file:{record.path.as_posix()}?mode=ro", uri=True)
            destination = sqlite3.connect(restored_path)
            try:
                source.backup(destination)
            finally:
                source.close()
                destination.close()
            connection = sqlite3.connect(restored_path)
            connection.row_factory = sqlite3.Row
            try:
                integrity_rows = [str(row[0]) for row in connection.execute("PRAGMA integrity_check").fetchall()]
                foreign_key_rows = connection.execute("PRAGMA foreign_key_check").fetchall()
                table_names = {
                    str(row[0])
                    for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
                }
                campaign_count = 0
                checkpoint_count = 0
                if "strategy_campaigns" in table_names:
                    campaign_rows = connection.execute("SELECT id, world_json FROM strategy_campaigns ORDER BY id").fetchall()
                    campaign_count = len(campaign_rows)
                    for row in campaign_rows:
                        raw = json.loads(str(row["world_json"]))
                        WorldState.from_dict(migrate_world_payload(raw))
                if "strategy_battle_checkpoints" in table_names:
                    checkpoint_rows = connection.execute(
                        "SELECT room_blob, checkpoint_hash FROM strategy_battle_checkpoints"
                    ).fetchall()
                    checkpoint_count = len(checkpoint_rows)
                    for row in checkpoint_rows:
                        actual = hashlib.sha256(bytes(row["room_blob"])).hexdigest()
                        if actual != str(row["checkpoint_hash"]):
                            raise StrategyError("恢复演练发现战略战斗检查点摘要不匹配。")
            finally:
                connection.close()
        if integrity_rows != ["ok"]:
            raise StrategyError(f"恢复演练完整性检查失败：{'；'.join(integrity_rows)}")
        if foreign_key_rows:
            raise StrategyError(f"恢复演练发现 {len(foreign_key_rows)} 条外键错误。")
        return {
            "status": "passed",
            "backup": record.to_dict(),
            "sqlite_integrity": "ok",
            "foreign_key_errors": 0,
            "campaigns_validated": campaign_count,
            "battle_checkpoints_validated": checkpoint_count,
            "live_database_untouched": True,
        }
