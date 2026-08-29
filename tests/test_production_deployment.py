from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import wujiang.platform.http.server as server_module  # noqa: E402
import wujiang.strategic.campaign_runtime as campaign_runtime  # noqa: E402
import wujiang.platform.http.runtime as http_runtime  # noqa: E402
from wujiang.platform.deployment import run_production_audit, validate_production_configuration  # noqa: E402
from wujiang.strategic import StrategyStore  # noqa: E402
from wujiang.platform.analytics import AnalyticsStore  # noqa: E402
from wujiang.platform.auth import UserStore  # noqa: E402
from wujiang.platform.match_history import MatchHistoryStore  # noqa: E402
from wujiang.platform.observability import Observability, ObservabilityConfig  # noqa: E402
from wujiang.platform.security import SecurityConfig  # noqa: E402


def production_configuration() -> tuple[SecurityConfig, ObservabilityConfig]:
    security = SecurityConfig(
        environment="production", require_https=True,
        trusted_proxy_networks=("127.0.0.1/32",),
        allowed_origins=("https://game.example",),
        allow_query_auth_tokens=False,
    )
    observability = ObservabilityConfig(
        environment="production", request_logs_enabled=True,
        ops_token="correct-horse-battery-staple-ops",
    )
    return security, observability


class ProductionDeploymentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.shared_db = self.root / "wujiang.sqlite3"
        self.analytics_db = self.root / "analytics.sqlite3"
        self.history_db = self.root / "history.sqlite3"
        self.backup_dir = self.root / "backups"
        self.replay_dir = self.root / "replays"
        self.replay_dir.mkdir()
        self.auth = UserStore(self.shared_db)
        self.analytics = AnalyticsStore(self.analytics_db)
        self.history = MatchHistoryStore(self.history_db)
        self.strategy = StrategyStore(self.shared_db, backup_dir=self.backup_dir)
        self.auth.healthcheck()
        self.analytics.healthcheck()
        self.history.healthcheck()
        self.strategy.healthcheck()

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def _audit(self):
        security, observability = production_configuration()
        return run_production_audit(
            public_base_url="https://game.example",
            security=security, observability=observability,
            auth_db=self.shared_db, analytics_db=self.analytics_db,
            match_history_db=self.history_db, strategy_db=self.shared_db,
            backup_dir=self.backup_dir, replay_dir=self.replay_dir,
        )

    def test_production_configuration_fails_closed(self) -> None:
        security, observability = production_configuration()
        validate_production_configuration(
            public_base_url="https://game.example", security=security, observability=observability,
        )
        with self.assertRaises(ValueError):
            validate_production_configuration(
                public_base_url="http://game.example", security=security, observability=observability,
            )
        with self.assertRaises(ValueError):
            validate_production_configuration(
                public_base_url="https://game.example", security=security,
                observability=ObservabilityConfig(environment="production", request_logs_enabled=True),
            )
        with self.assertRaises(ValueError):
            validate_production_configuration(
                public_base_url="https://game.example", security=security,
                observability=ObservabilityConfig(
                    environment="production", request_logs_enabled=True,
                    ops_token="replace-with-a-real-token",
                ),
            )

    def test_clean_data_backup_restore_and_privacy_audit_pass(self) -> None:
        _user, raw_session_token = self.auth.register("Alice", "secret123")
        self.strategy.create_backup(reason="pre_release_audit")
        (self.replay_dir / "clean.json").write_text(
            json.dumps({"match_id": "m1", "steps": []}), encoding="utf-8",
        )

        report = self._audit()

        serialized = json.dumps(report.to_dict())
        self.assertEqual(report.status, "passed")
        self.assertEqual(report.checks["latest_backup_restore_drill"], "passed")
        self.assertNotIn(raw_session_token, serialized)
        self.assertEqual(report.checks["replay_files_checked"], 1)

    def test_audit_detects_credential_leaks_without_echoing_secret(self) -> None:
        self.strategy.create_backup(reason="pre_release_audit")
        secret = "raw-secret-that-must-not-echo"
        with closing(sqlite3.connect(self.analytics_db)) as connection:
            connection.execute(
                "INSERT INTO analytics_events (event_name, anonymous_session_id, occurred_at, properties_json) VALUES (?, ?, 1, ?)",
                ("campaign_create", "anon", json.dumps({"session_token": secret})),
            )
            connection.commit()
        (self.replay_dir / "leaked.json").write_text(
            json.dumps({"player_token": secret}), encoding="utf-8",
        )

        report = self._audit()

        serialized = json.dumps(report.to_dict())
        self.assertEqual(report.status, "failed")
        codes = {finding.code for finding in report.findings}
        self.assertIn("analytics_contains_credentials", codes)
        self.assertIn("replay_contains_credentials", codes)
        self.assertNotIn(secret, serialized)

    def test_maintenance_failure_removes_readiness_and_restart_recovers(self) -> None:
        http_runtime.AUTH_STORE = self.auth
        http_runtime.ANALYTICS_STORE = self.analytics
        http_runtime.MATCH_HISTORY_STORE = self.history
        campaign_runtime.STRATEGY_STORE = self.strategy
        http_runtime.OBSERVABILITY = Observability(ObservabilityConfig(environment="test"))
        ready, dependencies = server_module.readiness_status()
        self.assertTrue(ready)
        marker = self.shared_db.with_name(f"{self.shared_db.name}.maintenance.json")
        marker.write_text("{}", encoding="utf-8")
        ready, dependencies = server_module.readiness_status()
        self.assertFalse(ready)
        self.assertEqual(dependencies["strategy"], "failed")
        marker.unlink()
        campaign_runtime.STRATEGY_STORE = StrategyStore(self.shared_db, backup_dir=self.backup_dir)
        ready, dependencies = server_module.readiness_status()
        self.assertTrue(ready)
        self.assertEqual(dependencies["strategy"], "ok")

    def test_deployment_files_keep_app_private_and_persist_runtime_state(self) -> None:
        compose = (ROOT / "compose.production.yml").read_text(encoding="utf-8")
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        caddy = (ROOT / "deploy" / "Caddyfile").read_text(encoding="utf-8")
        self.assertNotIn("8000:8000", compose)
        self.assertIn("wujiang_data:/app/var", compose)
        self.assertIn("wujiang_replays:/app/replays", compose)
        self.assertIn("read_only: true", compose)
        self.assertIn("USER 10001:10001", dockerfile)
        self.assertIn("reverse_proxy app:8000", caddy)

    def test_local_compose_binds_only_loopback_and_persists_runtime_state(self) -> None:
        compose = (ROOT / "compose.local.yml").read_text(encoding="utf-8")
        self.assertIn('\"127.0.0.1:${WUJIANG_LOCAL_PORT:-8000}:8000\"', compose)
        self.assertNotIn('\"0.0.0.0:8000:8000\"', compose)
        self.assertIn("wujiang_local_data:/app/var", compose)
        self.assertIn("wujiang_local_replays:/app/replays", compose)
        self.assertIn('WUJIANG_REQUIRE_HTTPS: \"false\"', compose)
        self.assertIn("http://127.0.0.1:${WUJIANG_LOCAL_PORT:-8000}", compose)
        self.assertIn("read_only: true", compose)


if __name__ == "__main__":
    unittest.main()
