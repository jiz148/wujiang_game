from __future__ import annotations

import hmac
import json
import os
import secrets
import sys
import threading
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, TextIO
from urllib.parse import urlparse


PUBLIC_ROUTE_TEMPLATES = {
    "/api/action", "/api/analytics/events", "/api/analytics/funnel", "/api/analytics/strategy",
    "/api/auth/login", "/api/auth/logout", "/api/auth/me", "/api/auth/register",
    "/api/health/live", "/api/health/ready", "/api/heroes", "/api/matches/recent",
    "/api/matches/replay", "/api/metrics", "/api/new-game", "/api/progression/overview",
    "/api/rooms", "/api/state",
}

ROOM_ROUTE_TEMPLATES = {
    "/api/rooms/action", "/api/rooms/apply-recommended-roster", "/api/rooms/create",
    "/api/rooms/delete", "/api/rooms/join", "/api/rooms/leave", "/api/rooms/quick-ai-start",
    "/api/rooms/rematch", "/api/rooms/replay", "/api/rooms/select-hero",
    "/api/rooms/set-default-ai-difficulty", "/api/rooms/set-mode", "/api/rooms/set-random-roster-size",
    "/api/rooms/set-ready", "/api/rooms/set-seat-ai-difficulty", "/api/rooms/set-seat-controller",
    "/api/rooms/set-seat-count", "/api/rooms/set-hero-limit", "/api/rooms/set-turn-timeout",
    "/api/rooms/set-seat-random-quota",
    "/api/rooms/set-seat-team",
    "/api/rooms/simulation-control", "/api/rooms/start", "/api/rooms/state",
    "/api/rooms/surrender", "/api/rooms/tutorial-retry", "/api/rooms/tutorial-select-unit",
    "/api/rooms/tutorial-start",
}

STRATEGY_ROUTE_TEMPLATES = {
    "/api/strategy/campaigns", "/api/strategy/campaigns/advance-month",
    "/api/strategy/campaigns/archive", "/api/strategy/campaigns/choose-hero-path",
    "/api/strategy/campaigns/close-month-deadline", "/api/strategy/campaigns/continue-sandbox",
    "/api/strategy/campaigns/create", "/api/strategy/campaigns/declare-attack",
    "/api/strategy/campaigns/enter", "/api/strategy/campaigns/guide-action",
    "/api/strategy/campaigns/join", "/api/strategy/campaigns/leave",
    "/api/strategy/campaigns/lock", "/api/strategy/campaigns/month-ready",
    "/api/strategy/campaigns/office-change/request", "/api/strategy/campaigns/office-change/respond",
    "/api/strategy/campaigns/office-takeover/grant", "/api/strategy/campaigns/office-takeover/revoke",
    "/api/strategy/campaigns/queue-action", "/api/strategy/campaigns/resolve-strategic-battle",
    "/api/strategy/campaigns/resolve-world-crisis-showdown",
    "/api/strategy/campaigns/restart-battle-from-snapshot", "/api/strategy/campaigns/resume",
    "/api/strategy/campaigns/revoke-join-code", "/api/strategy/campaigns/rotate-join-code",
    "/api/strategy/campaigns/set-battle-defense-hero", "/api/strategy/campaigns/set-city-policy",
    "/api/strategy/campaigns/set-defense-hero", "/api/strategy/campaigns/unlock-tactic-tech",
}


def route_template(raw_path: str) -> str:
    path = urlparse(str(raw_path or "")).path
    if path in PUBLIC_ROUTE_TEMPLATES:
        return path
    if path in ROOM_ROUTE_TEMPLATES:
        return path
    if path.startswith("/api/rooms/"):
        return "/api/rooms/:unknown"
    if path in STRATEGY_ROUTE_TEMPLATES:
        return path
    if path.startswith("/api/strategy/"):
        return "/api/strategy/:unknown"
    if path.startswith("/api/"):
        return "/api/:unknown"
    return "/static" if path not in {"", "/"} else "/"


@dataclass(frozen=True, slots=True)
class ObservabilityConfig:
    environment: str = "development"
    request_logs_enabled: bool = False
    ops_token: str = ""
    slow_request_ms: int = 1_500
    alert_window_seconds: int = 300
    error_rate_threshold: float = 0.10
    minimum_error_rate_samples: int = 20
    slow_request_alert_count: int = 5
    rate_limit_alert_count: int = 20

    @classmethod
    def from_environment(cls, environment: str = "development") -> ObservabilityConfig:
        log_raw = str(os.environ.get("WUJIANG_REQUEST_LOGS") or "").strip().lower()
        return cls(
            environment=environment,
            request_logs_enabled=(environment == "production") if not log_raw else log_raw in {"1", "true", "yes", "on"},
            ops_token=str(os.environ.get("WUJIANG_OPS_TOKEN") or "").strip(),
            slow_request_ms=int(os.environ.get("WUJIANG_SLOW_REQUEST_MS") or 1_500),
        ).validated()

    def validated(self) -> ObservabilityConfig:
        if self.environment not in {"development", "test", "production"}:
            raise ValueError("Observability environment must be development, test, or production.")
        if self.ops_token and len(self.ops_token) < 16:
            raise ValueError("WUJIANG_OPS_TOKEN must contain at least 16 characters.")
        if self.slow_request_ms < 1 or self.alert_window_seconds < 1:
            raise ValueError("Observability timing values must be positive.")
        if not 0 < self.error_rate_threshold <= 1:
            raise ValueError("Error-rate threshold must be between 0 and 1.")
        return self


class Observability:
    def __init__(
        self,
        config: ObservabilityConfig | None = None,
        *,
        stream: TextIO | None = None,
        monotonic_clock=time.monotonic,
        wall_clock=time.time,
    ) -> None:
        self.config = (config or ObservabilityConfig()).validated()
        self._stream = stream or sys.stdout
        self._monotonic = monotonic_clock
        self._wall_clock = wall_clock
        self._started_at = self._monotonic()
        self._lock = threading.RLock()
        self._requests_total = 0
        self._requests_in_flight = 0
        self._status_classes: Counter[str] = Counter()
        self._routes: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
        self._recent: deque[tuple[float, int, bool, bool]] = deque(maxlen=10_000)
        self._readiness: dict[str, bool] = {}

    def begin_request(self) -> tuple[str, float]:
        with self._lock:
            self._requests_in_flight += 1
        return secrets.token_hex(8), self._monotonic()

    def cancel_request(self) -> None:
        with self._lock:
            self._requests_in_flight = max(0, self._requests_in_flight - 1)

    def record_request(
        self, *, request_id: str, method: str, raw_path: str, status: int,
        started_at: float, rate_limit_scope: str = "",
    ) -> None:
        now = self._monotonic()
        duration_ms = max(0.0, (now - started_at) * 1000)
        route = route_template(raw_path)
        status_code = int(status or 500)
        is_error = status_code >= 500
        is_slow = duration_ms >= self.config.slow_request_ms
        is_rate_limited = status_code == 429
        with self._lock:
            self._requests_in_flight = max(0, self._requests_in_flight - 1)
            self._requests_total += 1
            self._status_classes[f"{status_code // 100}xx"] += 1
            counter = self._routes[(method or "UNKNOWN", route)]
            counter["requests"] += 1
            counter["errors"] += int(is_error)
            counter["rate_limited"] += int(is_rate_limited)
            counter["slow"] += int(is_slow)
            counter["duration_ms_total"] += round(duration_ms)
            counter["duration_ms_max"] = max(counter["duration_ms_max"], round(duration_ms))
            self._recent.append((now, status_code, is_slow, is_rate_limited))
        if self.config.request_logs_enabled:
            self._write_log({
                "timestamp": datetime.fromtimestamp(self._wall_clock(), timezone.utc).isoformat(),
                "level": "error" if is_error else "info",
                "event": "http_request",
                "request_id": request_id,
                "method": method or "UNKNOWN",
                "route": route,
                "status": status_code,
                "duration_ms": round(duration_ms, 2),
                "rate_limit_scope": rate_limit_scope or None,
            })

    def record_readiness(self, dependencies: dict[str, bool]) -> None:
        with self._lock:
            self._readiness = dict(dependencies)

    def metrics(self) -> dict[str, Any]:
        now = self._monotonic()
        cutoff = now - self.config.alert_window_seconds
        with self._lock:
            recent = [entry for entry in self._recent if entry[0] > cutoff]
            requests_total = self._requests_total
            routes = []
            for (method, route), values in sorted(self._routes.items()):
                count = values["requests"]
                routes.append({
                    "method": method, "route": route, "requests": count,
                    "errors": values["errors"], "rate_limited": values["rate_limited"],
                    "slow": values["slow"],
                    "duration_ms_avg": round(values["duration_ms_total"] / count, 2) if count else 0,
                    "duration_ms_max": values["duration_ms_max"],
                })
            readiness = dict(self._readiness)
            in_flight = self._requests_in_flight
            status_classes = dict(self._status_classes)
        errors = sum(1 for _time, status, _slow, _limited in recent if status >= 500)
        slow = sum(1 for _time, _status, is_slow, _limited in recent if is_slow)
        limited = sum(1 for _time, _status, _slow, is_limited in recent if is_limited)
        error_rate = errors / len(recent) if recent else 0.0
        alerts = []
        if readiness and not all(readiness.values()):
            alerts.append({"code": "dependency_not_ready", "severity": "critical"})
        if len(recent) >= self.config.minimum_error_rate_samples and error_rate >= self.config.error_rate_threshold:
            alerts.append({"code": "elevated_server_error_rate", "severity": "critical"})
        if slow >= self.config.slow_request_alert_count:
            alerts.append({"code": "sustained_slow_requests", "severity": "warning"})
        if limited >= self.config.rate_limit_alert_count:
            alerts.append({"code": "rate_limit_pressure", "severity": "warning"})
        return {
            "service": "wujiang", "uptime_seconds": round(now - self._started_at, 3),
            "requests_total": requests_total, "requests_in_flight": in_flight,
            "responses_by_status_class": status_classes, "routes": routes,
            "recent_window": {
                "seconds": self.config.alert_window_seconds, "requests": len(recent),
                "server_errors": errors, "error_rate": round(error_rate, 4),
                "slow_requests": slow, "rate_limited_requests": limited,
            },
            "readiness": {name: "ok" if value else "failed" for name, value in readiness.items()},
            "alerts": alerts,
        }

    def allows_metrics(self, supplied_token: str) -> bool:
        if self.config.environment != "production":
            return True
        return bool(self.config.ops_token) and hmac.compare_digest(self.config.ops_token, str(supplied_token or ""))

    def _write_log(self, payload: dict[str, Any]) -> None:
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            try:
                self._stream.write(f"{line}\n")
                self._stream.flush()
            except (OSError, ValueError):
                return
