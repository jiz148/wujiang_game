from __future__ import annotations

import io
import json
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import wujiang.platform.http.server as server_module  # noqa: E402
import wujiang.strategic.campaign_runtime as campaign_runtime  # noqa: E402
import wujiang.platform.http.runtime as http_runtime  # noqa: E402
from wujiang.strategic import StrategyStore  # noqa: E402
from wujiang.platform.analytics import AnalyticsStore  # noqa: E402
from wujiang.platform.auth import UserStore  # noqa: E402
from wujiang.platform.match_history import MatchHistoryStore  # noqa: E402
from wujiang.platform.observability import Observability, ObservabilityConfig, route_template  # noqa: E402
from wujiang.platform.security import SecurityConfig  # noqa: E402
from wujiang.platform.http.server import WujiangHandler, configure_observability, configure_security  # noqa: E402


class ObservabilityUnitTests(unittest.TestCase):
    def test_unknown_routes_and_queries_never_enter_route_labels(self) -> None:
        self.assertEqual(
            route_template("/api/rooms/supersecret?session_token=raw-secret"),
            "/api/rooms/:unknown",
        )
        self.assertEqual(
            route_template("/api/strategy/privatecredential?join_code=ABCD"),
            "/api/strategy/:unknown",
        )

    def test_structured_log_and_metrics_are_sanitized_and_bounded(self) -> None:
        monotonic = [10.0]
        stream = io.StringIO()
        observer = Observability(
            ObservabilityConfig(environment="test", request_logs_enabled=True, slow_request_ms=100),
            stream=stream, monotonic_clock=lambda: monotonic[0], wall_clock=lambda: 1_700_000_000,
        )
        request_id, started = observer.begin_request()
        monotonic[0] += 0.2
        observer.record_request(
            request_id=request_id, method="POST",
            raw_path="/api/auth/login?session_token=raw-secret&password=bad",
            status=429, started_at=started, rate_limit_scope="auth_identity",
        )

        log_line = stream.getvalue().strip()
        payload = json.loads(log_line)
        self.assertEqual(payload["route"], "/api/auth/login")
        self.assertEqual(payload["status"], 429)
        self.assertEqual(payload["rate_limit_scope"], "auth_identity")
        self.assertNotIn("raw-secret", log_line)
        self.assertNotIn("password", log_line)
        metrics = observer.metrics()
        self.assertEqual(metrics["requests_total"], 1)
        self.assertEqual(metrics["routes"][0]["rate_limited"], 1)
        self.assertEqual(metrics["routes"][0]["slow"], 1)

    def test_alert_baseline_and_production_metrics_authorization(self) -> None:
        monotonic = [1.0]
        observer = Observability(
            ObservabilityConfig(
                environment="production", ops_token="0123456789abcdef",
                slow_request_ms=10, minimum_error_rate_samples=2,
                error_rate_threshold=0.5, slow_request_alert_count=2,
                rate_limit_alert_count=2,
            ),
            monotonic_clock=lambda: monotonic[0],
        )
        observer.record_readiness({"auth": True, "strategy": False})
        for _ in range(2):
            request_id, started = observer.begin_request()
            monotonic[0] += 0.02
            observer.record_request(
                request_id=request_id, method="GET", raw_path="/api/heroes",
                status=503, started_at=started,
            )
        alert_codes = {alert["code"] for alert in observer.metrics()["alerts"]}
        self.assertEqual(
            alert_codes,
            {"dependency_not_ready", "elevated_server_error_rate", "sustained_slow_requests"},
        )
        self.assertFalse(observer.allows_metrics("wrong"))
        self.assertTrue(observer.allows_metrics("0123456789abcdef"))


class ObservabilityBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), WujiangHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        root = Path(self.tmpdir.name)
        http_runtime.AUTH_STORE = UserStore(root / "auth.sqlite3")
        http_runtime.ANALYTICS_STORE = AnalyticsStore(root / "analytics.sqlite3")
        http_runtime.MATCH_HISTORY_STORE = MatchHistoryStore(root / "history.sqlite3")
        campaign_runtime.STRATEGY_STORE = StrategyStore(root / "strategy.sqlite3")
        configure_security(SecurityConfig(environment="test", require_https=False))
        configure_observability(Observability(ObservabilityConfig(environment="test")))

    def tearDown(self) -> None:
        configure_observability(Observability(ObservabilityConfig(environment="test")))
        self.tmpdir.cleanup()

    def _get(self, path: str, headers: dict[str, str] | None = None) -> tuple[int, dict, dict[str, str]]:
        request = Request(f"http://127.0.0.1:{self.port}{path}", headers=headers or {})
        try:
            with urlopen(request) as response:
                return response.status, json.loads(response.read()), dict(response.headers)
        except HTTPError as exc:
            return exc.code, json.loads(exc.read()), dict(exc.headers)

    def test_liveness_and_readiness_are_distinct_and_expose_request_id(self) -> None:
        status, live, headers = self._get("/api/health/live")
        self.assertEqual(status, 200)
        self.assertEqual(live["status"], "ok")
        self.assertRegex(headers["X-Request-ID"], r"^[0-9a-f]{16}$")

        status, ready, _ = self._get("/api/health/ready")
        self.assertEqual(status, 200)
        self.assertEqual(ready["status"], "ready")
        self.assertTrue(all(value == "ok" for value in ready["dependencies"].values()))

        http_runtime.AUTH_STORE = SimpleNamespace(healthcheck=lambda: (_ for _ in ()).throw(OSError("secret path")))
        status, unavailable, _ = self._get("/api/health/ready")
        self.assertEqual(status, 503)
        self.assertEqual(unavailable["dependencies"]["auth"], "failed")
        self.assertNotIn("secret path", json.dumps(unavailable))

    def test_metrics_fail_closed_in_production_without_exact_ops_token(self) -> None:
        configure_observability(Observability(ObservabilityConfig(
            environment="production", ops_token="0123456789abcdef",
        )))
        status, _, _ = self._get("/api/metrics")
        self.assertEqual(status, 403)
        status, metrics, _ = self._get(
            "/api/metrics", headers={"X-Wujiang-Ops-Token": "0123456789abcdef"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(metrics["service"], "wujiang")
        self.assertIn("alerts", metrics)
        self.assertNotIn("0123456789abcdef", json.dumps(metrics))


if __name__ == "__main__":
    unittest.main()
