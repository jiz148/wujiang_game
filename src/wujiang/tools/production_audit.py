from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from wujiang.deployment import run_production_audit
from wujiang.strategy.backup import strategy_backup_directory
from wujiang.strategy.store import strategy_database_path
from wujiang.web.analytics import analytics_database_path
from wujiang.web.auth import auth_database_path
from wujiang.web.match_history import match_history_database_path
from wujiang.web.observability import ObservabilityConfig
from wujiang.web.security import SecurityConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the read-only production configuration, privacy, integrity, and restore-drill audit."
    )
    parser.add_argument(
        "--public-base-url", default=os.environ.get("WUJIANG_PUBLIC_BASE_URL", ""),
        help="Exact public HTTPS site root; defaults to WUJIANG_PUBLIC_BASE_URL.",
    )
    parser.add_argument("--auth-db", type=Path, default=auth_database_path())
    parser.add_argument("--analytics-db", type=Path, default=analytics_database_path())
    parser.add_argument("--match-history-db", type=Path, default=match_history_database_path())
    parser.add_argument("--strategy-db", type=Path, default=strategy_database_path())
    parser.add_argument("--backup-dir", type=Path, default=None)
    parser.add_argument("--replay-dir", type=Path, default=Path("replays"))
    parser.add_argument(
        "--allow-missing-backup", action="store_true",
        help="Development-only escape hatch; formal release audits require a verified backup.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        security = SecurityConfig.from_environment()
        observability = ObservabilityConfig.from_environment(security.environment)
        backup_dir = args.backup_dir or strategy_backup_directory(args.strategy_db)
        report = run_production_audit(
            public_base_url=args.public_base_url,
            security=security,
            observability=observability,
            auth_db=args.auth_db,
            analytics_db=args.analytics_db,
            match_history_db=args.match_history_db,
            strategy_db=args.strategy_db,
            backup_dir=backup_dir,
            replay_dir=args.replay_dir,
            require_backup=not args.allow_missing_backup,
        )
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if report.status == "passed" else 1
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
