from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from wujiang.strategy.backup import DEFAULT_AUTOMATIC_BACKUP_RETENTION, StrategyBackupManager
from wujiang.strategy.errors import StrategyError
from wujiang.strategy.store import StrategyStore, strategy_database_path


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create, verify, list, or drill-test wujiang SQLite backups without modifying the live database."
    )
    parser.add_argument("--db", type=Path, default=strategy_database_path(), help="SQLite database path.")
    parser.add_argument("--backup-dir", type=Path, default=None, help="Managed backup directory.")
    parser.add_argument(
        "--retention",
        type=int,
        default=DEFAULT_AUTOMATIC_BACKUP_RETENTION,
        help="Maximum automatic backups to retain; manual backups are never pruned.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create", help="Create a manual online SQLite snapshot.")
    create.add_argument("--reason", default="manual", help="Short operator reason stored in the manifest.")
    subparsers.add_parser("list", help="List verified managed backups.")
    verify = subparsers.add_parser("verify", help="Verify manifest, digest, size, and SQLite quick check.")
    verify.add_argument("backup", type=Path)
    drill = subparsers.add_parser("drill", help="Restore into an isolated temporary DB and validate durable state.")
    drill.add_argument("backup", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    manager = StrategyBackupManager(
        args.db,
        backup_dir=args.backup_dir,
        automatic_retention=args.retention,
    )
    try:
        if args.command == "create":
            store = StrategyStore(
                args.db,
                backup_dir=args.backup_dir,
                automatic_backup_retention=args.retention,
            )
            _print_json(store.create_backup(reason=args.reason).to_dict())
        elif args.command == "list":
            _print_json({"backups": [record.to_dict() for record in manager.list_backups()]})
        elif args.command == "verify":
            _print_json({"status": "passed", "backup": manager.verify_backup(args.backup).to_dict()})
        elif args.command == "drill":
            _print_json(manager.drill_restore(args.backup))
        else:  # pragma: no cover - argparse owns this branch
            raise StrategyError("未知备份命令。")
    except StrategyError as exc:
        _print_json({"status": "failed", "error": str(exc)})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
