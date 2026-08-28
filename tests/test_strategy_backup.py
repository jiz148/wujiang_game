from __future__ import annotations

import hashlib
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

from wujiang.strategy import StrategyError, StrategyStore  # noqa: E402
from wujiang.strategy.backup import StrategyBackupManager  # noqa: E402
from wujiang.web.auth import AuthUser  # noqa: E402
from wujiang.tools.strategy_backup import main as strategy_backup_main  # noqa: E402


class StrategyBackupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.db_path = self.root / "wujiang.sqlite3"
        self.backup_dir = self.root / "backups"
        self.store = StrategyStore(self.db_path, backup_dir=self.backup_dir)
        self.alice = AuthUser(user_id=1, username="Alice", created_at=1.0)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_manual_snapshot_drill_validates_campaigns_checkpoints_and_leaves_live_db_untouched(self) -> None:
        campaign = self.store.create_campaign(owner=self.alice, name="恢复演练", seed=401, city_count=6)
        blob = b"authoritative-checkpoint"
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute(
                """
                INSERT INTO strategy_battle_checkpoints
                  (room_id, campaign_id, battle_id, participant_user_ids_json, room_blob,
                   room_version, format_version, status, checkpoint_hash, created_at, updated_at, restart_count)
                VALUES ('DRILL1', ?, 'battle-drill', '[1]', ?, 1, 1, 'active', ?, 1, 1, 0)
                """,
                (campaign.campaign_id, blob, hashlib.sha256(blob).hexdigest()),
            )
            connection.commit()
        backup = self.store.create_backup(reason="operator-drill")
        live = self.store.get_campaign_for_user(campaign.campaign_id, self.alice.user_id)
        live.world.current_month = 2
        self.store.update_world(campaign.campaign_id, self.alice.user_id, live.world)

        report = self.store.drill_backup_restore(backup.path)

        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["campaigns_validated"], 1)
        self.assertEqual(report["battle_checkpoints_validated"], 1)
        self.assertTrue(report["live_database_untouched"])
        self.assertEqual(
            self.store.get_campaign_for_user(campaign.campaign_id, self.alice.user_id).world.current_month,
            2,
        )
        self.assertEqual(self.store.backups.verify_backup(backup.path).sha256, backup.sha256)

    def test_digest_mismatch_is_rejected_before_restore_drill(self) -> None:
        self.store.create_campaign(owner=self.alice, name="损坏备份", seed=402, city_count=6)
        backup = self.store.create_backup(reason="corruption-test")
        with backup.path.open("ab") as target:
            target.write(b"corrupt")

        with self.assertRaises(StrategyError) as raised:
            self.store.drill_backup_restore(backup.path)

        self.assertIn("清单不匹配", str(raised.exception))

    def test_retention_prunes_only_old_automatic_backups(self) -> None:
        self.store.create_campaign(owner=self.alice, name="保留策略", seed=403, city_count=6)
        manager = StrategyBackupManager(
            self.db_path,
            backup_dir=self.backup_dir,
            automatic_retention=2,
        )
        manual = manager.create_backup(reason="manual-keep", automatic=False, strategy_schema_version=2)
        for index in range(3):
            manager.create_backup(
                reason=f"automatic-{index}",
                automatic=True,
                strategy_schema_version=2,
            )

        records = manager.list_backups()

        self.assertTrue(manual.path.exists())
        self.assertEqual(sum(record.automatic for record in records), 2)
        self.assertEqual(sum(not record.automatic for record in records), 1)

    def test_legacy_schema_is_backed_up_before_schema_migration(self) -> None:
        legacy_path = self.root / "legacy.sqlite3"
        legacy_backups = self.root / "legacy-backups"
        with closing(sqlite3.connect(legacy_path)) as connection:
            connection.execute(
                """
                CREATE TABLE strategy_campaigns (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  name TEXT NOT NULL,
                  owner_user_id INTEGER NOT NULL,
                  status TEXT NOT NULL,
                  current_month INTEGER NOT NULL,
                  world_json TEXT NOT NULL,
                  created_at REAL NOT NULL,
                  updated_at REAL NOT NULL
                )
                """
            )
            connection.commit()
        legacy_store = StrategyStore(legacy_path, backup_dir=legacy_backups)

        self.assertEqual(legacy_store.list_campaigns_for_user(self.alice.user_id), [])
        records = legacy_store.list_backups()

        self.assertEqual(len(records), 1)
        self.assertTrue(records[0].automatic)
        self.assertEqual(records[0].reason, "pre_schema_migration")
        self.assertEqual(records[0].strategy_schema_version, 0)
        report = legacy_store.drill_backup_restore(records[0].path)
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["campaigns_validated"], 0)

    def test_backup_cli_creates_and_drills_managed_snapshot(self) -> None:
        self.store.create_campaign(owner=self.alice, name="CLI 恢复演练", seed=404, city_count=6)
        create_output = io.StringIO()
        with redirect_stdout(create_output):
            create_status = strategy_backup_main([
                "--db", str(self.db_path),
                "--backup-dir", str(self.backup_dir),
                "create", "--reason", "cli-drill",
            ])
        created = json.loads(create_output.getvalue())
        drill_output = io.StringIO()
        with redirect_stdout(drill_output):
            drill_status = strategy_backup_main([
                "--db", str(self.db_path),
                "--backup-dir", str(self.backup_dir),
                "drill", created["path"],
            ])
        drilled = json.loads(drill_output.getvalue())

        self.assertEqual(create_status, 0)
        self.assertEqual(drill_status, 0)
        self.assertEqual(drilled["status"], "passed")
        self.assertEqual(drilled["campaigns_validated"], 1)


if __name__ == "__main__":
    unittest.main()
