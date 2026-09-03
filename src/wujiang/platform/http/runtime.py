"""Process-wide HTTP runtime: configuration, shared stores and request helpers.

This module is deliberately free of gameplay knowledge. Domain packages pull
what they need from here; nothing here imports ``wujiang.tactical`` or
``wujiang.strategic``, which is what keeps the two game domains independent.
"""
from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from wujiang.platform.analytics import AnalyticsStore
from wujiang.platform.auth import AuthError, UserStore
from wujiang.platform.match_history import MatchHistoryStore
from wujiang.platform.observability import Observability, ObservabilityConfig
from wujiang.platform.rate_limit import RateLimiter
from wujiang.platform.security import (
    SecurityConfig,
    effective_host,
    effective_scheme,
    read_json_body,
)

PROJECT_ROOT = Path(__file__).resolve().parents[4]
STATIC_ROOT = PROJECT_ROOT / "static"
PUBLIC_BASE_URL: str | None = None
SECURITY_CONFIG = SecurityConfig.from_environment()
RATE_LIMITER = RateLimiter()
OBSERVABILITY = Observability(ObservabilityConfig.from_environment(SECURITY_CONFIG.environment))
AUTH_STORE = UserStore()
ANALYTICS_STORE = AnalyticsStore()
MATCH_HISTORY_STORE = MatchHistoryStore()

_DEPENDENCIES: dict[str, Callable[[], Any]] = {}


def register_dependency(name: str, provider: Callable[[], Any]) -> None:
    """Let a domain opt its own store into the readiness probe.

    Health checks used to hard-code the four known stores, so adding a domain
    also meant editing the platform. Domains now register themselves at import
    time. The provider is called per probe rather than stored directly, because
    tests and maintenance drills swap these singletons at runtime.
    """
    _DEPENDENCIES[name] = provider


register_dependency("auth", lambda: AUTH_STORE)
register_dependency("analytics", lambda: ANALYTICS_STORE)
register_dependency("match_history", lambda: MATCH_HISTORY_STORE)


def readiness_status() -> tuple[bool, dict[str, str]]:
    dependencies: dict[str, bool] = {}
    for name, provider in _DEPENDENCIES.items():
        try:
            provider().healthcheck()
            dependencies[name] = True
        except Exception:
            dependencies[name] = False
    OBSERVABILITY.record_readiness(dependencies)
    return all(dependencies.values()), {
        name: "ok" if ready else "failed" for name, ready in dependencies.items()
    }



def client_disconnected(exc: BaseException) -> bool:
    if isinstance(exc, (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)):
        return True
    return isinstance(exc, OSError) and getattr(exc, "winerror", None) in {10053, 10054, 10058}


def json_response(handler: BaseHTTPRequestHandler, status: int, payload: Any) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    try:
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
    except Exception as exc:
        if client_disconnected(exc):
            return
        raise

def normalize_public_base_url(base_url: str | None) -> str | None:
    raw = str(base_url or "").strip()
    if not raw:
        return None
    candidate = raw.rstrip("/")
    if "://" not in candidate:
        candidate = f"http://{candidate}"
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("`--public-base-url` 必须是像 `http://203.0.113.10:8000` 这样的完整地址。")
    if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment or " " in parsed.netloc:
        raise ValueError("`--public-base-url` 只能填写站点根地址，不能包含路径、查询参数或空格。")
    return candidate


def configure_public_base_url(base_url: str | None) -> str | None:
    global PUBLIC_BASE_URL
    PUBLIC_BASE_URL = normalize_public_base_url(base_url)
    return PUBLIC_BASE_URL


def configure_security(config: SecurityConfig | None = None) -> SecurityConfig:
    global SECURITY_CONFIG
    SECURITY_CONFIG = (config or SecurityConfig.from_environment()).validated()
    return SECURITY_CONFIG


def configure_rate_limiter(rate_limiter: RateLimiter | None = None) -> RateLimiter:
    global RATE_LIMITER
    RATE_LIMITER = rate_limiter or RateLimiter()
    return RATE_LIMITER


def reset_rate_limiter() -> None:
    RATE_LIMITER.reset()

def configure_observability(observability: Observability | None = None) -> Observability:
    global OBSERVABILITY
    OBSERVABILITY = observability or Observability(
        ObservabilityConfig.from_environment(SECURITY_CONFIG.environment)
    )
    return OBSERVABILITY

def request_base_url(handler: BaseHTTPRequestHandler) -> str | None:
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL
    host = effective_host(handler, SECURITY_CONFIG)
    if not host:
        return None
    scheme = effective_scheme(handler, SECURITY_CONFIG)
    return f"{scheme}://{host}"


def request_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    return read_json_body(handler, SECURITY_CONFIG)


def auth_token_from_request(
    handler: BaseHTTPRequestHandler,
    *,
    payload: dict[str, Any] | None = None,
    query: dict[str, list[str]] | None = None,
) -> str:
    auth_header = str(handler.headers.get("Authorization") or "").strip()
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    if payload is not None:
        token = payload.get("session_token") or payload.get("auth_token")
        if token:
            return str(token)
    if query is not None and SECURITY_CONFIG.allow_query_auth_tokens:
        token_values = query.get("session_token") or query.get("auth_token") or []
        if token_values:
            return str(token_values[0])
    return ""


def auth_error_response(handler: BaseHTTPRequestHandler, exc: AuthError) -> None:
    json_response(handler, int(exc.status), {"error": str(exc)})

def authenticated_user_from_request(
    handler: BaseHTTPRequestHandler,
    *,
    payload: dict[str, Any] | None = None,
    query: dict[str, list[str]] | None = None,
):
    token = auth_token_from_request(handler, payload=payload, query=query)
    if not token:
        raise AuthError("请先登录账号。", status=HTTPStatus.UNAUTHORIZED)
    return AUTH_STORE.user_for_session(token)
