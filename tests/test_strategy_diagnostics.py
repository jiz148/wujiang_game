from __future__ import annotations

import io
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wujiang.strategy.audit import read_operation_audit, verify_operation_audit_chain  # noqa: E402
from wujiang.strategy.diagnostics import run_strategy_diagnostics  # noqa: E402
from wujiang.strategy.store import StrategyStore  # noqa: E402
from wujiang.tools.strategy_diagnostics import main as diagnostics_main  # noqa: E402
from wujiang.web.auth import AuthUser  # noqa: E402


class StrategyDiagnosticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.db_path = self.root / "wujiang.sqlite3"
        self.backup_dir = self.root / "backups"
        self.store = StrategyStore(self.db_path, backup_dir=self.backup_dir)
        self.alice = AuthUser(user_id=1, username="Alice", created_at=1.0)
        self.bob = AuthUser(user_id=2, username="Bob", created_at=2.0)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_committed_operations_form_chain_without_copying_secrets_or_payloads(self) -> None:
        campaign = self.store.create_campaign(owner=self.alice, name="Audit Campaign", seed=501, city_count=6)
        join_code = campaign.join_code
        self.store.join_campaign_by_code(join_code, self.bob)
        self.store.rotate_join_code(campaign.campaign_id, self.alice.user_id)
        self.store.lock_initial_players(campaign.campaign_id, self.alice.user_id)
        self.store.queue_action(
            campaign_id=campaign.campaign_id, user=self.alice,
            action_type="develop", action_key="private-action-key",
            payload={"city_id": "city_1", "session_token": "secret-session-value"},
        )

        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute("SELECT * FROM strategy_operation_audit ORDER BY id").fetchall()
            valid, message, count = verify_operation_audit_chain(rows)
            records = read_operation_audit(connection, campaign_id=campaign.campaign_id)

        self.assertTrue(valid, message)
        self.assertEqual(count, len(records))
        self.assertTrue({"campaign.created", "campaign.member_joined", "campaign.locked", "action.queued"}.issubset(
            {record.operation for record in records}
        ))
        serialized = json.dumps([record.to_dict() for record in records], ensure_ascii=False)
        self.assertNotIn(join_code, serialized)
        self.assertNotIn("secret-session-value", serialized)
        self.assertNotIn("private-action-key", serialized)
        self.assertNotIn("payload_json", serialized)

    def test_tampered_audit_entry_is_reported_critical(self) -> None:
        self.store.create_campaign(owner=self.alice, name="Tamper Audit", seed=502, city_count=6)
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute(
                "UPDATE strategy_operation_audit SET details_json = '{\"status\":\"forged\"}' WHERE id = 1"
            )
            connection.commit()

        report = run_strategy_diagnostics(self.db_path, backup_dir=self.backup_dir)

        self.assertEqual(report.status, "critical")
        self.assertIn("audit_chain_invalid", {finding.code for finding in report.findings})

    def test_diagnostics_are_read_only_and_detect_checkpoint_corruption(self) -> None:
        campaign = self.store.create_campaign(owner=self.alice, name="Read Only Check", seed=503, city_count=6)
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute(
                """
                INSERT INTO strategy_battle_checkpoints
                  (room_id, campaign_id, battle_id, participant_user_ids_json, room_blob,
                   room_version, format_version, status, checkpoint_hash, created_at, updated_at, restart_count)
                VALUES ('BROKEN1', ?, 'battle-1', '[1]', X'0102', 1, 1, 'active', ?, 1, 1, 0)
                """,
                (campaign.campaign_id, "0" * 64),
            )
            connection.commit()
        before = self.db_path.read_bytes()

        report = run_strategy_diagnostics(self.db_path, backup_dir=self.backup_dir)

        self.assertEqual(before, self.db_path.read_bytes())
        self.assertEqual(report.status, "critical")
        self.assertIn("checkpoint_hash_mismatch", {finding.code for finding in report.findings})

    def test_healthy_database_and_cli_return_success(self) -> None:
        self.store.create_campaign(owner=self.alice, name="Healthy Audit", seed=504, city_count=6)
        self.store.create_backup(reason="diagnostic-test")

        report = run_strategy_diagnostics(self.db_path, backup_dir=self.backup_dir)
        output = io.StringIO()
        with redirect_stdout(output):
            status = diagnostics_main([
                "--db", str(self.db_path), "--backup-dir", str(self.backup_dir), "check",
            ])
        payload = json.loads(output.getvalue())

        self.assertEqual(report.status, "healthy")
        self.assertEqual(status, 0)
        self.assertEqual(payload["status"], "healthy")
        self.assertEqual(payload["checks"]["campaigns"], 1)


if __name__ == "__main__":
    unittest.main()
