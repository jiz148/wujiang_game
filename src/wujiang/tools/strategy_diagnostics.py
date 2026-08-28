from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from wujiang.strategy.audit import read_operation_audit, verify_operation_audit_chain
from wujiang.strategy.diagnostics import run_strategy_diagnostics
from wujiang.strategy.store import strategy_database_path


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only integrity and operation-audit tools for wujiang strategy data.")
    parser.add_argument("--db", type=Path, default=strategy_database_path(), help="SQLite database path.")
    parser.add_argument("--backup-dir", type=Path, default=None, help="Managed backup directory.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check", help="Run all read-only integrity diagnostics.")
    audit = subparsers.add_parser("audit", help="Verify and display sanitized committed operations.")
    audit.add_argument("--campaign-id", type=int, default=None)
    audit.add_argument("--limit", type=int, default=100)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "check":
        report = run_strategy_diagnostics(args.db, backup_dir=args.backup_dir)
        _print_json(report.to_dict())
        return 1 if report.status == "critical" else 0
    try:
        path = Path(args.db).expanduser().resolve()
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            chain_rows = connection.execute("SELECT * FROM strategy_operation_audit ORDER BY id").fetchall()
            valid, message, count = verify_operation_audit_chain(chain_rows)
            records = read_operation_audit(
                connection, campaign_id=args.campaign_id, limit=args.limit
            )
        finally:
            connection.close()
        _print_json({"status": "passed" if valid else "failed", "chain_message": message,
                     "chain_entries": count, "operations": [item.to_dict() for item in records]})
        return 0 if valid else 1
    except (OSError, sqlite3.Error, ValueError) as exc:
        _print_json({"status": "failed", "error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
