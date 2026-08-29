from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import urlencode
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
from wujiang.platform.rate_limit import RateLimiter, RateLimitPolicy  # noqa: E402
from wujiang.platform.security import SecurityConfig, effective_client_ip, effective_host, effective_scheme  # noqa: E402
from wujiang.platform.http.server import (  # noqa: E402
    WujiangHandler, configure_public_base_url, configure_rate_limiter, configure_security,
)


class WebSecurityUnitTests(unittest.TestCase):
    def test_forwarded_headers_are_ignored_outside_trusted_proxy_networks(self) -> None:
        handler = SimpleNamespace(
            client_address=("203.0.113.9", 1234),
            headers={"Host": "local.example:8000", "X-Forwarded-Host": "evil.example", "X-Forwarded-Proto": "https"},
            connection=SimpleNamespace(),
        )
        config = SecurityConfig(environment="production", require_https=True, trusted_proxy_networks=("127.0.0.1/32",))

        self.assertEqual(effective_scheme(handler, config), "http")
        self.assertEqual(effective_host(handler, config), "local.example:8000")
        self.assertEqual(effective_client_ip(handler, config), "203.0.113.9")

    def test_only_trusted_proxy_can_supply_rate_limit_client_ip(self) -> None:
        handler = SimpleNamespace(
            client_address=("127.0.0.1", 1234),
            headers={"X-Forwarded-For": "198.51.100.24"},
        )
        config = SecurityConfig(environment="test", trusted_proxy_networks=("127.0.0.1/32",))
        self.assertEqual(effective_client_ip(handler, config), "198.51.100.24")
        handler.headers["X-Forwarded-For"] = "192.0.2.99, 198.51.100.24"
        self.assertEqual(effective_client_ip(handler, config), "198.51.100.24")
        handler.client_address = ("203.0.113.9", 1234)
        self.assertEqual(effective_client_ip(handler, config), "203.0.113.9")


class WebSecurityBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        configure_public_base_url(None)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), WujiangHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        configure_security(SecurityConfig(environment="test", require_https=False))
        configure_public_base_url(None)
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
        configure_public_base_url(None)
        configure_security(SecurityConfig(environment="test", require_https=False, max_json_body_bytes=1024))
        configure_rate_limiter()

    def tearDown(self) -> None:
        configure_security(SecurityConfig(environment="test", require_https=False))
        configure_rate_limiter()
        configure_public_base_url(None)
        self.tmpdir.cleanup()

    def _request(
        self, path: str, *, method: str = "GET", payload: dict | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict, dict[str, str]]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request_headers = dict(headers or {})
        if body is not None:
            request_headers.setdefault("Content-Type", "application/json")
        request = Request(
            f"http://127.0.0.1:{self.port}{path}", data=body,
            headers=request_headers, method=method,
        )
        try:
            with urlopen(request) as response:
                raw = response.read()
                return response.status, json.loads(raw.decode("utf-8")) if raw else {}, dict(response.headers)
        except HTTPError as exc:
            raw = exc.read()
            return exc.code, json.loads(raw.decode("utf-8")) if raw else {}, dict(exc.headers)

    def test_public_http_responses_include_security_and_no_store_headers(self) -> None:
        status, payload, headers = self._request("/api/heroes")

        self.assertEqual(status, 200)
        self.assertIn("heroes", payload)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["Server"].strip(), "Wujiang")

    def test_oversized_non_json_and_cross_origin_posts_fail_before_dispatch(self) -> None:
        status, _, _ = self._request(
            "/api/auth/register", method="POST", payload={"username": "A" * 2000, "password": "secret123"},
        )
        self.assertEqual(status, 413)
        status, _, _ = self._request(
            "/api/auth/register", method="POST", payload={"username": "Alice", "password": "secret123"},
            headers={"Content-Type": "text/plain"},
        )
        self.assertEqual(status, 415)
        status, _, _ = self._request(
            "/api/auth/register", method="POST", payload={"username": "Alice", "password": "secret123"},
            headers={"Origin": "https://evil.example"},
        )
        self.assertEqual(status, 403)

    def test_production_requires_trusted_https_and_disables_query_auth_tokens(self) -> None:
        configure_security(SecurityConfig(
            environment="production", require_https=True,
            trusted_proxy_networks=("127.0.0.1/32",),
            allowed_origins=("https://game.example",),
            max_json_body_bytes=1024, allow_query_auth_tokens=False,
        ))
        status, _, _ = self._request("/api/heroes")
        self.assertEqual(status, 426)
        proxy_headers = {
            "X-Forwarded-Proto": "https", "X-Forwarded-Host": "game.example",
            "Origin": "https://game.example",
        }
        status, registered, headers = self._request(
            "/api/auth/register", method="POST",
            payload={"username": "Alice", "password": "secret123"}, headers=proxy_headers,
        )
        self.assertEqual(status, 200)
        self.assertIn("max-age=31536000", headers["Strict-Transport-Security"])
        token = registered["session_token"]
        query = urlencode({"session_token": token})
        status, anonymous, _ = self._request(
            f"/api/auth/me?{query}", headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "game.example"},
        )
        self.assertEqual(status, 200)
        self.assertIsNone(anonymous["user"])
        status, authenticated, _ = self._request(
            "/api/auth/me", headers={
                "X-Forwarded-Proto": "https", "X-Forwarded-Host": "game.example",
                "Authorization": f"Bearer {token}",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(authenticated["user"]["username"], "Alice")

    def test_auth_rate_limit_is_account_scoped_and_returns_retry_headers(self) -> None:
        policies = {
            "auth_ip": RateLimitPolicy(10, 60),
            "auth_identity": RateLimitPolicy(2, 60),
            "join": RateLimitPolicy(10, 60),
            "analytics": RateLimitPolicy(10, 60),
            "mutation": RateLimitPolicy(10, 60),
        }
        configure_rate_limiter(RateLimiter(policies))
        for _ in range(2):
            status, _, _ = self._request(
                "/api/auth/login", method="POST",
                payload={"username": "Target", "password": "wrongpass"},
            )
            self.assertEqual(status, 401)
        status, payload, headers = self._request(
            "/api/auth/login", method="POST",
            payload={"username": "Target", "password": "wrongpass"},
        )
        self.assertEqual(status, 429)
        self.assertGreaterEqual(int(headers["Retry-After"]), 1)
        self.assertEqual(headers["RateLimit-Remaining"], "0")
        self.assertIn("retry_after", payload)

        status, _, _ = self._request(
            "/api/auth/login", method="POST",
            payload={"username": "Different", "password": "wrongpass"},
        )
        self.assertEqual(status, 401)


if __name__ == "__main__":
    unittest.main()
