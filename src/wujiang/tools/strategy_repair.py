from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
from typing import Any

from wujiang.strategic.errors import StrategyError
from wujiang.strategic.repair import (
    REPAIR_TOKEN_ENV,
    StrategyRepairPlan,
    apply_repair_plan,
    create_repair_plan,
    create_restore_plan,
)
from wujiang.strategic.store import strategy_database_path


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _write_plan(plan: StrategyRepairPlan, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan and execute allowlisted wujiang strategy repairs.")
    parser.add_argument("--db", type=Path, default=strategy_database_path(), help="SQLite database path.")
    parser.add_argument("--backup-dir", type=Path, default=None, help="Managed backup directory.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan = subparsers.add_parser("plan", help="Create a read-only bounded repair plan.")
    plan.add_argument("--out", type=Path, required=True)
    restore = subparsers.add_parser("restore-plan", help="Create a plan for a verified whole-database restore.")
    restore.add_argument("backup", type=Path)
    restore.add_argument("--out", type=Path, required=True)
    apply = subparsers.add_parser("apply", help="Apply a previously generated plan under maintenance guard.")
    apply.add_argument("plan", type=Path)
    apply.add_argument("--confirm", required=True, help="Exact 64-character plan id.")
    apply.add_argument("--operator", required=True, help="Traceable operator account identifier.")
    apply.add_argument("--reason-code", required=True, help="Allowlisted-style incident/change reason code.")
    apply.add_argument(
        "--authorization-env", default="WUJIANG_STRATEGY_REPAIR_APPROVAL",
        help="Environment variable containing the approval secret; prompts when absent.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "plan":
            plan = create_repair_plan(args.db, backup_dir=args.backup_dir)
            _write_plan(plan, args.out)
            _print_json({"status": "planned", "plan_path": str(args.out.resolve()), **plan.to_dict()})
            return 0 if plan.actions and not plan.blocked_findings else 2
        if args.command == "restore-plan":
            plan = create_restore_plan(args.db, args.backup, backup_dir=args.backup_dir)
            _write_plan(plan, args.out)
            _print_json({"status": "planned", "plan_path": str(args.out.resolve()), **plan.to_dict()})
            return 0
        authorization = str(os.environ.get(args.authorization_env) or "")
        if not authorization:
            authorization = getpass.getpass(f"Approval secret matching {REPAIR_TOKEN_ENV}: ")
        result = apply_repair_plan(
            args.plan, confirm=args.confirm, operator=args.operator,
            reason_code=args.reason_code, authorization=authorization,
            backup_dir=args.backup_dir,
        )
        _print_json(result)
        return 0
    except (OSError, ValueError, json.JSONDecodeError, StrategyError) as exc:
        _print_json({"status": "failed", "error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
