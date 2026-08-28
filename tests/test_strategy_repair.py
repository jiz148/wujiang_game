from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wujiang.strategy.diagnostics import run_strategy_diagnostics  # noqa: E402
from wujiang.strategy.errors import StrategyError  # noqa: E402
from wujiang.strategy.repair import (  # noqa: E402
    REPAIR_TOKEN_ENV,
    StrategyRepairPlan,
    apply_repair_plan,
    create_repair_plan,
    create_restore_plan,
    maintenance_marker_path,
)
from wujiang.strategy.store import StrategyStore  # noqa: E402
from wujiang.tools.strategy_repair import main as repair_main  # noqa: E402
from wujiang.web.auth import AuthUser  # noqa: E402


class StrategyRepairTests(unittest.TestCase):
    TOKEN = "repair-approval-secret-2026"

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.db_path = self.root / "wujiang.sqlite3"
        self.backup_dir = self.root / "backups"
        self.store = StrategyStore(self.db_path, backup_dir=self.backup_dir)
        self.alice = AuthUser(user_id=1, username="Alice", created_at=1.0)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def _campaign_with_index_corruption(self):
        campaign = self.store.create_campaign(owner=self.alice, name="Repair Walkthrough", seed=601, city_count=6)
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute(
                "UPDATE strategy_campaigns SET current_month = 99 WHERE id = ?", (campaign.campaign_id,)
            )
            connection.commit()
        return campaign

    def _apply(self, plan: StrategyRepairPlan, **overrides):
        params = {
            "confirm": plan.plan_id, "operator": "ops.alice",
            "reason_code": "incident-601", "authorization": self.TOKEN,
            "backup_dir": self.backup_dir,
        }
        params.update(overrides)
        with patch.dict(os.environ, {REPAIR_TOKEN_ENV: self.TOKEN}):
            return apply_repair_plan(plan, **params)

    def test_bounded_plan_repairs_only_campaign_index_with_backup_and_audit(self) -> None:
        campaign = self._campaign_with_index_corruption()
        plan = create_repair_plan(self.db_path, backup_dir=self.backup_dir)

        result = self._apply(plan)

        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["actions_applied"], 1)
        self.assertTrue(Path(result["pre_repair_backup"]["path"]).is_file())
        self.assertEqual(run_strategy_diagnostics(self.db_path, backup_dir=self.backup_dir).status, "healthy")
        restored = self.store.get_campaign_for_user(campaign.campaign_id, self.alice.user_id)
        self.assertEqual(restored.current_month, restored.world.current_month)
        with closing(sqlite3.connect(self.db_path)) as connection:
            row = connection.execute(
                "SELECT operation, actor_username, details_json FROM strategy_operation_audit ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertEqual(row[0], "admin.campaign_index_repaired")
        self.assertEqual(row[1], "ops.alice")
        self.assertEqual(json.loads(row[2])["reason_code"], "incident-601")

    def test_wrong_authorization_confirmation_and_changed_database_fail_closed(self) -> None:
        self._campaign_with_index_corruption()
        plan = create_repair_plan(self.db_path, backup_dir=self.backup_dir)
        with self.assertRaises(StrategyError):
            self._apply(plan, authorization="wrong-secret")
        with self.assertRaises(StrategyError):
            self._apply(plan, confirm="0" * 64)
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute("UPDATE strategy_campaigns SET updated_at = updated_at + 1")
            connection.commit()
        with self.assertRaises(StrategyError) as raised:
            self._apply(plan)
        self.assertIn("changed after planning", str(raised.exception))

    def test_unsupported_audit_damage_blocks_bounded_repair(self) -> None:
        self._campaign_with_index_corruption()
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute("UPDATE strategy_operation_audit SET details_json = '{}' WHERE id = 1")
            connection.commit()
        plan = create_repair_plan(self.db_path, backup_dir=self.backup_dir)

        self.assertIn("audit_chain_invalid", {item["code"] for item in plan.blocked_findings})
        with self.assertRaises(StrategyError):
            self._apply(plan)

    def test_invalid_world_still_produces_a_blocked_plan_instead_of_guessing(self) -> None:
        campaign = self.store.create_campaign(owner=self.alice, name="Broken World", seed=604, city_count=6)
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute(
                "UPDATE strategy_campaigns SET world_json = '{broken' WHERE id = ?", (campaign.campaign_id,)
            )
            connection.commit()

        plan = create_repair_plan(self.db_path, backup_dir=self.backup_dir)

        self.assertEqual(plan.actions, ())
        self.assertIn("campaign_world_invalid", {item["code"] for item in plan.blocked_findings})
        with self.assertRaises(StrategyError):
            self._apply(plan)

    def test_verified_backup_restore_preserves_pre_restore_snapshot_and_records_operation(self) -> None:
        campaign = self.store.create_campaign(owner=self.alice, name="Restore Walkthrough", seed=602, city_count=6)
        backup = self.store.create_backup(reason="known-good")
        live = self.store.get_campaign_for_user(campaign.campaign_id, self.alice.user_id)
        live.world.current_month = 2
        self.store.update_world(campaign.campaign_id, self.alice.user_id, live.world)
        plan = create_restore_plan(
            self.db_path, backup.path, backup_dir=self.backup_dir,
        )

        result = self._apply(plan, reason_code="restore-known-good")

        self.assertEqual(result["status"], "passed")
        self.assertTrue(Path(result["pre_restore_backup"]["path"]).is_file())
        reopened = StrategyStore(self.db_path, backup_dir=self.backup_dir)
        self.assertEqual(reopened.get_campaign_for_user(campaign.campaign_id, self.alice.user_id).world.current_month, 1)
        with closing(sqlite3.connect(self.db_path)) as connection:
            row = connection.execute(
                "SELECT operation, actor_username FROM strategy_operation_audit ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertEqual(row, ("admin.backup_restored", "ops.alice"))
        self.assertFalse(maintenance_marker_path(self.db_path).exists())

    def test_maintenance_marker_blocks_store_and_plan_digest_rejects_tampering(self) -> None:
        self.store.create_campaign(owner=self.alice, name="Maintenance Gate", seed=603, city_count=6)
        marker = maintenance_marker_path(self.db_path)
        marker.write_text("{}", encoding="utf-8")
        try:
            blocked_store = StrategyStore(self.db_path, backup_dir=self.backup_dir)
            with self.assertRaises(StrategyError) as raised:
                blocked_store.list_campaigns_for_user(self.alice.user_id)
            self.assertEqual(raised.exception.status, 503)
        finally:
            marker.unlink()
        plan = create_repair_plan(self.db_path, backup_dir=self.backup_dir)
        tampered = plan.to_dict()
        tampered["db_sha256"] = "0" * 64
        with self.assertRaises(StrategyError):
            StrategyRepairPlan.from_dict(tampered)

    def test_cli_plan_and_apply_complete_the_repair_walkthrough(self) -> None:
        self._campaign_with_index_corruption()
        plan_path = self.root / "repair-plan.json"
        planned_output = io.StringIO()
        with redirect_stdout(planned_output):
            planned_status = repair_main([
                "--db", str(self.db_path), "--backup-dir", str(self.backup_dir),
                "plan", "--out", str(plan_path),
            ])
        planned = json.loads(planned_output.getvalue())
        apply_output = io.StringIO()
        env = {REPAIR_TOKEN_ENV: self.TOKEN, "WUJIANG_STRATEGY_REPAIR_APPROVAL": self.TOKEN}
        with patch.dict(os.environ, env), redirect_stdout(apply_output):
            apply_status = repair_main([
                "--db", str(self.db_path), "--backup-dir", str(self.backup_dir),
                "apply", str(plan_path), "--confirm", planned["plan_id"],
                "--operator", "ops.cli", "--reason-code", "cli-walkthrough",
            ])
        applied = json.loads(apply_output.getvalue())

        self.assertEqual(planned_status, 0)
        self.assertEqual(apply_status, 0)
        self.assertEqual(applied["status"], "passed")
        self.assertEqual(applied["post_diagnostics"]["status"], "healthy")


if __name__ == "__main__":
    unittest.main()
