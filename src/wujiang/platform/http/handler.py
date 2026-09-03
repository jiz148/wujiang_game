"""The HTTP kernel: transport policy, rate limiting and route dispatch.

The kernel owns *how* a request is admitted and answered. It owns no endpoint;
routes are contributed by the domain packages through
``wujiang.platform.http.routing``.
"""
from __future__ import annotations

import hashlib
import mimetypes
import signal
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from wujiang.platform.auth import AuthError, normalized_username_key, session_token_hash
from wujiang.platform.deployment import validate_production_configuration
from wujiang.platform.http import runtime
from wujiang.platform.http.context import RequestContext
from wujiang.platform.http.routing import resolve
from wujiang.platform.http.runtime import (
    auth_error_response,
    auth_token_from_request,
    authenticated_user_from_request,
    configure_observability,
    configure_public_base_url,
    configure_security,
    json_response,
    request_json,
)
from wujiang.platform.security import (
    RequestSecurityError,
    SecurityConfig,
    effective_client_ip,
    enforce_post_origin,
    enforce_transport,
    response_security_headers,
)

# Endpoints that must resolve an account before the route body runs.
AUTHENTICATED_POST_PREFIXES = ("/api/rooms/", "/api/strategy/")
AUTHENTICATED_POST_PATHS = {"/api/new-game", "/api/action"}


class WujiangHandler(BaseHTTPRequestHandler):
    server_version = "Wujiang"
    sys_version = ""

    def handle_one_request(self) -> None:
        request_id, started_at = runtime.OBSERVABILITY.begin_request()
        self.command = None
        self._request_id = request_id
        self._response_status = 500
        self._rate_limit_scope = ""
        try:
            super().handle_one_request()
        except Exception as exc:
            if runtime.client_disconnected(exc):
                return
            raise
        finally:
            if getattr(self, "command", None):
                runtime.OBSERVABILITY.record_request(
                    request_id=request_id,
                    method=str(self.command),
                    raw_path=str(getattr(self, "path", "")),
                    status=int(getattr(self, "_response_status", 500)),
                    started_at=started_at,
                    rate_limit_scope=str(getattr(self, "_rate_limit_scope", "")),
                )
            else:
                runtime.OBSERVABILITY.cancel_request()

    def send_response(self, code: int, message: str | None = None) -> None:
        self._response_status = int(code)
        super().send_response(code, message)

    def end_headers(self) -> None:
        self.send_header("X-Request-ID", str(getattr(self, "_request_id", "")))
        for name, value in getattr(self, "_rate_limit_headers", {}).items():
            self.send_header(name, value)
        for name, value in response_security_headers(self, runtime.SECURITY_CONFIG).items():
            self.send_header(name, value)
        super().end_headers()

    def _enforce_transport(self) -> bool:
        try:
            enforce_transport(self, runtime.SECURITY_CONFIG)
        except RequestSecurityError as exc:
            json_response(self, int(exc.status), {"error": str(exc)})
            return False
        return True

    def _rate_limit(self, path: str, payload: dict[str, Any]) -> bool:
        client_key = effective_client_ip(self, runtime.SECURITY_CONFIG) or "unknown"
        checks: list[tuple[str, str]] = []
        if path in {"/api/auth/register", "/api/auth/login"}:
            identity = normalized_username_key(str(payload.get("username") or ""))
            identity_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()
            checks = [("auth_ip", client_key), ("auth_identity", f"{path}:{identity_hash}")]
        elif path == "/api/strategy/campaigns/join":
            token = auth_token_from_request(self, payload=payload)
            checks = [("join", client_key), ("join", f"actor:{session_token_hash(token)}")]
        elif path == "/api/analytics/events":
            checks = [("analytics", client_key)]
        elif path.startswith("/api/"):
            token = auth_token_from_request(self, payload=payload)
            checks = [("mutation", client_key)]
            if token:
                checks.append(("mutation", f"actor:{session_token_hash(token)}"))
        for scope, key in checks:
            decision = runtime.RATE_LIMITER.check(scope, key)
            self._rate_limit_headers = {
                "RateLimit-Limit": str(decision.limit),
                "RateLimit-Remaining": str(decision.remaining),
                "RateLimit-Reset": str(decision.reset_after_seconds),
            }
            if not decision.allowed:
                self._rate_limit_scope = scope
                self._rate_limit_headers["Retry-After"] = str(decision.reset_after_seconds)
                json_response(
                    self,
                    HTTPStatus.TOO_MANY_REQUESTS,
                    {"error": "请求过于频繁，请稍后再试。", "retry_after": decision.reset_after_seconds},
                )
                return False
        return True

    def do_GET(self) -> None:  # noqa: N802
        self._rate_limit_headers = {}
        if not self._enforce_transport():
            return
        parsed = urlparse(self.path)
        route = resolve("GET", parsed.path)
        if route is None:
            self.serve_static(parsed.path)
            return
        route(
            RequestContext(
                handler=self,
                method="GET",
                path=parsed.path,
                query=parse_qs(parsed.query),
            )
        )

    def do_POST(self) -> None:  # noqa: N802
        self._rate_limit_headers = {}
        if not self._enforce_transport():
            return
        parsed = urlparse(self.path)
        try:
            enforce_post_origin(
                self,
                runtime.SECURITY_CONFIG,
                additional_origins=(runtime.PUBLIC_BASE_URL,) if runtime.PUBLIC_BASE_URL else (),
            )
            payload = request_json(self)
        except RequestSecurityError as exc:
            json_response(self, int(exc.status), {"error": str(exc)})
            return

        if not self._rate_limit(parsed.path, payload):
            return

        route = resolve("POST", parsed.path)
        if route is None:
            json_response(self, HTTPStatus.NOT_FOUND, {"error": "未知接口。"})
            return

        auth_user = None
        if parsed.path in AUTHENTICATED_POST_PATHS or parsed.path.startswith(AUTHENTICATED_POST_PREFIXES):
            try:
                auth_user = authenticated_user_from_request(self, payload=payload)
            except AuthError as exc:
                auth_error_response(self, exc)
                return

        route(
            RequestContext(
                handler=self,
                method="POST",
                path=parsed.path,
                query=parse_qs(parsed.query),
                payload=payload,
                auth_user=auth_user,
            )
        )

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return None

    def serve_static(self, url_path: str) -> None:
        relative = "index.html" if url_path in {"", "/"} else url_path.lstrip("/")
        file_path = (runtime.STATIC_ROOT / relative).resolve()
        static_root = runtime.STATIC_ROOT.resolve()
        if not str(file_path).startswith(str(static_root)) or not file_path.exists() or not file_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        mime_type, _ = mimetypes.guess_type(file_path.name)
        data = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{mime_type or 'application/octet-stream'}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        # ES module imports carry no cache-busting query, so the browser has to
        # revalidate them rather than serve a mix of old and new modules.
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)


def run_server(
    host: str = "127.0.0.1",
    port: int = 8000,
    public_base_url: str | None = None,
    security_config: SecurityConfig | None = None,
) -> None:
    configure_security(security_config)
    configure_observability()
    share_base_url = configure_public_base_url(public_base_url)
    validate_production_configuration(
        public_base_url=share_base_url,
        security=runtime.SECURITY_CONFIG,
        observability=runtime.OBSERVABILITY.config,
    )
    if runtime.SECURITY_CONFIG.require_https and share_base_url and not share_base_url.startswith("https://"):
        raise ValueError("正式环境启用 HTTPS 强制时，public base URL 必须使用 https://。")
    httpd = ThreadingHTTPServer((host, port), WujiangHandler)
    httpd.daemon_threads = True
    print(f"Wujiang server running at http://{host}:{port}")
    if host == "0.0.0.0":
        print(f"Local browser URL: http://127.0.0.1:{port}")
    if share_base_url:
        print(f"Share this homepage with friends: {share_base_url}/")
        print(f"Copied room invite links will use: {share_base_url}/?room=ROOMID")
    elif host == "0.0.0.0":
        print(f"Share your LAN/public IP manually, for example: http://<your-ip>:{port}/")
    previous_handlers: dict[int, Any] = {}
    if threading.current_thread() is threading.main_thread():
        def request_shutdown(_signum: int, _frame: Any) -> None:
            threading.Thread(target=httpd.shutdown, daemon=True).start()

        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, request_shutdown)
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
