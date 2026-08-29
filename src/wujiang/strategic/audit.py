from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Iterable

from wujiang.strategic.errors import StrategyError


AUDIT_GENESIS_HASH = "0" * 64
_SAFE_NAME = re.compile(r"^[a-z0-9_.:-]{1,64}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_DETAIL_KEYS = {
    "join_code", "password", "payload", "payload_json", "room_blob", "session",
    "session_token", "token", "world", "world_json",
}


@dataclass(frozen=True, slots=True)
class StrategyAuditRecord:
    audit_id: int
    campaign_id: int | None
    actor_user_id: int | None
    actor_username: str
    operation: str
    target_type: str
    target_id: str
    result: str
    before_hash: str
    after_hash: str
    details: dict[str, Any]
    created_at: float
    previous_hash: str
    entry_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.audit_id,
            "campaign_id": self.campaign_id,
            "actor_user_id": self.actor_user_id,
            "actor_username": self.actor_username,
            "operation": self.operation,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "result": self.result,
            "before_hash": self.before_hash,
            "after_hash": self.after_hash,
            "details": self.details,
            "created_at": self.created_at,
            "previous_hash": self.previous_hash,
            "entry_hash": self.entry_hash,
        }


def sha256_text(value: str | bytes) -> str:
    raw = value if isinstance(value, bytes) else str(value).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _safe_name(value: str, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SAFE_NAME.fullmatch(normalized):
        raise StrategyError(f"Invalid strategy audit {field}.")
    return normalized


def _safe_details(value: Any, *, key: str = "") -> Any:
    if key.lower() in _FORBIDDEN_DETAIL_KEYS:
        raise StrategyError(f"Sensitive field cannot be written to strategy audit: {key}.")
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) > 160:
            raise StrategyError("Strategy audit detail strings are limited to 160 characters.")
        return value
    if isinstance(value, (list, tuple)):
        if len(value) > 32:
            raise StrategyError("Strategy audit detail lists are limited to 32 items.")
        return [_safe_details(item) for item in value]
    if isinstance(value, dict):
        if len(value) > 32:
            raise StrategyError("Strategy audit details are limited to 32 fields.")
        return {str(item_key): _safe_details(item, key=str(item_key)) for item_key, item in value.items()}
    raise StrategyError("Strategy audit details must contain JSON-safe primitives only.")


def append_operation_audit(
    connection: sqlite3.Connection,
    *,
    campaign_id: int | None,
    actor_user_id: int | None,
    actor_username: str = "",
    operation: str,
    target_type: str,
    target_id: str | int,
    before_hash: str = "",
    after_hash: str = "",
    details: dict[str, Any] | None = None,
    created_at: float | None = None,
) -> StrategyAuditRecord:
    operation = _safe_name(operation, "operation")
    target_type = _safe_name(target_type, "target type")
    target_id = str(target_id or "").strip()[:96]
    if not target_id:
        raise StrategyError("Strategy audit target id is required.")
    before_hash = str(before_hash or "")
    after_hash = str(after_hash or "")
    if before_hash and not _HASH.fullmatch(before_hash):
        raise StrategyError("Invalid strategy audit before hash.")
    if after_hash and not _HASH.fullmatch(after_hash):
        raise StrategyError("Invalid strategy audit after hash.")
    safe_details = _safe_details(dict(details or {}))
    details_json = json.dumps(safe_details, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    timestamp = float(created_at if created_at is not None else time.time())
    previous = connection.execute(
        "SELECT entry_hash FROM strategy_operation_audit ORDER BY id DESC LIMIT 1"
    ).fetchone()
    previous_hash = str(previous[0]) if previous is not None else AUDIT_GENESIS_HASH
    canonical = json.dumps(
        {
            "campaign_id": int(campaign_id) if campaign_id is not None else None,
            "actor_user_id": int(actor_user_id) if actor_user_id is not None else None,
            "actor_username": str(actor_username or "")[:80],
            "operation": operation,
            "target_type": target_type,
            "target_id": target_id,
            "result": "committed",
            "before_hash": before_hash,
            "after_hash": after_hash,
            "details": safe_details,
            "created_at": timestamp,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    entry_hash = sha256_text(f"{previous_hash}\n{canonical}")
    cursor = connection.execute(
        """
        INSERT INTO strategy_operation_audit
          (campaign_id, actor_user_id, actor_username, operation, target_type, target_id,
           result, before_hash, after_hash, details_json, created_at, previous_hash, entry_hash)
        VALUES (?, ?, ?, ?, ?, ?, 'committed', ?, ?, ?, ?, ?, ?)
        """,
        (campaign_id, actor_user_id, str(actor_username or "")[:80], operation, target_type,
         target_id, before_hash, after_hash, details_json, timestamp, previous_hash, entry_hash),
    )
    return StrategyAuditRecord(
        int(cursor.lastrowid), campaign_id, actor_user_id, str(actor_username or "")[:80],
        operation, target_type, target_id, "committed", before_hash, after_hash,
        safe_details, timestamp, previous_hash, entry_hash,
    )


def audit_record_from_row(row: sqlite3.Row) -> StrategyAuditRecord:
    return StrategyAuditRecord(
        audit_id=int(row["id"]),
        campaign_id=int(row["campaign_id"]) if row["campaign_id"] is not None else None,
        actor_user_id=int(row["actor_user_id"]) if row["actor_user_id"] is not None else None,
        actor_username=str(row["actor_username"]), operation=str(row["operation"]),
        target_type=str(row["target_type"]), target_id=str(row["target_id"]),
        result=str(row["result"]), before_hash=str(row["before_hash"]),
        after_hash=str(row["after_hash"]), details=json.loads(str(row["details_json"])),
        created_at=float(row["created_at"]), previous_hash=str(row["previous_hash"]),
        entry_hash=str(row["entry_hash"]),
    )


def read_operation_audit(
    connection: sqlite3.Connection, *, campaign_id: int | None = None, limit: int = 100
) -> list[StrategyAuditRecord]:
    query = "SELECT * FROM strategy_operation_audit"
    params: list[Any] = []
    if campaign_id is not None:
        query += " WHERE campaign_id = ?"
        params.append(int(campaign_id))
    query += " ORDER BY id DESC LIMIT ?"
    params.append(max(1, min(1000, int(limit))))
    return [audit_record_from_row(row) for row in connection.execute(query, params).fetchall()]


def verify_operation_audit_chain(rows: Iterable[sqlite3.Row]) -> tuple[bool, str, int]:
    previous_hash = AUDIT_GENESIS_HASH
    count = 0
    for row in rows:
        count += 1
        try:
            details = json.loads(str(row["details_json"]))
            canonical = json.dumps(
                {
                    "campaign_id": int(row["campaign_id"]) if row["campaign_id"] is not None else None,
                    "actor_user_id": int(row["actor_user_id"]) if row["actor_user_id"] is not None else None,
                    "actor_username": str(row["actor_username"]), "operation": str(row["operation"]),
                    "target_type": str(row["target_type"]), "target_id": str(row["target_id"]),
                    "result": str(row["result"]), "before_hash": str(row["before_hash"]),
                    "after_hash": str(row["after_hash"]), "details": details,
                    "created_at": float(row["created_at"]),
                }, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            return False, f"audit row {row['id']} is malformed: {exc}", count
        expected = sha256_text(f"{previous_hash}\n{canonical}")
        if str(row["previous_hash"]) != previous_hash or str(row["entry_hash"]) != expected:
            return False, f"audit chain mismatch at row {row['id']}", count
        previous_hash = expected
    return True, "ok", count
