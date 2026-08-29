from __future__ import annotations

import ipaddress
import json
import os
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any, Iterable
from urllib.parse import urlparse


DEFAULT_MAX_JSON_BODY_BYTES = 256 * 1024


class RequestSecurityError(Exception):
    def __init__(self, message: str, *, status: HTTPStatus) -> None:
        super().__init__(message)
        self.status = status


def _csv(raw: str | None) -> tuple[str, ...]:
    return tuple(value.strip() for value in str(raw or "").split(",") if value.strip())


def normalize_origin(raw: str | None) -> str:
    value = str(raw or "").strip().rstrip("/")
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    if parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        return ""
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


@dataclass(frozen=True, slots=True)
class SecurityConfig:
    environment: str = "development"
    require_https: bool = False
    trusted_proxy_networks: tuple[str, ...] = ("127.0.0.1/32", "::1/128")
    allowed_origins: tuple[str, ...] = ()
    max_json_body_bytes: int = DEFAULT_MAX_JSON_BODY_BYTES
    allow_query_auth_tokens: bool = True

    @classmethod
    def from_environment(cls) -> SecurityConfig:
        environment = str(os.environ.get("WUJIANG_ENVIRONMENT") or "development").strip().lower()
        production = environment == "production"
        require_raw = str(os.environ.get("WUJIANG_REQUIRE_HTTPS") or "").strip().lower()
        require_https = production if not require_raw else require_raw in {"1", "true", "yes", "on"}
        max_bytes = int(os.environ.get("WUJIANG_MAX_JSON_BODY_BYTES") or DEFAULT_MAX_JSON_BODY_BYTES)
        proxies = _csv(os.environ.get("WUJIANG_TRUSTED_PROXY_NETWORKS")) or ("127.0.0.1/32", "::1/128")
        raw_origins = _csv(os.environ.get("WUJIANG_ALLOWED_ORIGINS"))
        origins = tuple(normalize_origin(value) for value in raw_origins)
        if any(not value for value in origins):
            raise ValueError("WUJIANG_ALLOWED_ORIGINS contains an invalid origin.")
        query_raw = str(os.environ.get("WUJIANG_ALLOW_QUERY_AUTH_TOKENS") or "").strip().lower()
        allow_query = not production if not query_raw else query_raw in {"1", "true", "yes", "on"}
        return cls(
            environment=environment, require_https=require_https,
            trusted_proxy_networks=tuple(proxies), allowed_origins=origins,
            max_json_body_bytes=max(1024, min(max_bytes, 4 * 1024 * 1024)),
            allow_query_auth_tokens=allow_query,
        ).validated()

    def validated(self) -> SecurityConfig:
        if self.environment not in {"development", "test", "production"}:
            raise ValueError("Security environment must be development, test, or production.")
        for value in self.trusted_proxy_networks:
            ipaddress.ip_network(value, strict=False)
        for value in self.allowed_origins:
            if normalize_origin(value) != value.lower().rstrip("/"):
                raise ValueError(f"Invalid allowed origin: {value}")
        if not 1024 <= int(self.max_json_body_bytes) <= 4 * 1024 * 1024:
            raise ValueError("JSON request limit must be between 1 KiB and 4 MiB.")
        return self

    def trusts_client(self, client_ip: str) -> bool:
        try:
            address = ipaddress.ip_address(str(client_ip or ""))
        except ValueError:
            return False
        return any(address in ipaddress.ip_network(value, strict=False) for value in self.trusted_proxy_networks)


def first_header_value(raw_value: str | None) -> str:
    return str(raw_value or "").split(",", 1)[0].strip()


def safe_host(raw_host: str | None) -> str:
    value = first_header_value(raw_host).strip().lower()
    if not value or any(char in value for char in ("/", "\\", "@", " ", "\r", "\n")):
        return ""
    parsed = urlparse(f"http://{value}")
    try:
        _ = parsed.port
    except ValueError:
        return ""
    return value if parsed.hostname else ""


def client_ip(handler: Any) -> str:
    return str(handler.client_address[0] if handler.client_address else "")


def effective_client_ip(handler: Any, config: SecurityConfig) -> str:
    direct_ip = client_ip(handler)
    if not config.trusts_client(direct_ip):
        return direct_ip
    forwarded_chain = [value.strip() for value in str(handler.headers.get("X-Forwarded-For") or "").split(",")]
    forwarded_chain = [value for value in forwarded_chain if value]
    if not forwarded_chain:
        return direct_ip
    try:
        addresses = [str(ipaddress.ip_address(value)) for value in forwarded_chain]
    except ValueError:
        return direct_ip
    effective_ip = direct_ip
    for forwarded_ip in reversed(addresses):
        if not config.trusts_client(effective_ip):
            break
        effective_ip = forwarded_ip
    return effective_ip


def effective_scheme(handler: Any, config: SecurityConfig) -> str:
    if config.trusts_client(client_ip(handler)):
        forwarded = first_header_value(handler.headers.get("X-Forwarded-Proto")).lower()
        if forwarded in {"http", "https"}:
            return forwarded
    return "https" if getattr(handler.connection, "cipher", None) else "http"


def effective_host(handler: Any, config: SecurityConfig) -> str:
    if config.trusts_client(client_ip(handler)):
        forwarded = safe_host(handler.headers.get("X-Forwarded-Host"))
        if forwarded:
            return forwarded
    return safe_host(handler.headers.get("Host"))


def request_origin(handler: Any, config: SecurityConfig) -> str:
    host = effective_host(handler, config)
    return f"{effective_scheme(handler, config)}://{host}" if host else ""


def enforce_transport(handler: Any, config: SecurityConfig) -> None:
    if config.require_https and effective_scheme(handler, config) != "https":
        raise RequestSecurityError("正式环境只接受 HTTPS 请求。", status=HTTPStatus.UPGRADE_REQUIRED)


def enforce_post_origin(
    handler: Any, config: SecurityConfig, *, additional_origins: Iterable[str] = (),
) -> None:
    raw_origin = str(handler.headers.get("Origin") or "").strip()
    if not raw_origin:
        return
    origin = normalize_origin(raw_origin)
    allowed = {normalize_origin(value) for value in (*config.allowed_origins, *additional_origins)}
    allowed.add(normalize_origin(request_origin(handler, config)))
    allowed.discard("")
    if not origin or origin not in allowed:
        raise RequestSecurityError("请求来源未获允许。", status=HTTPStatus.FORBIDDEN)


def read_json_body(handler: Any, config: SecurityConfig) -> dict[str, Any]:
    raw_length = str(handler.headers.get("Content-Length") or "0").strip()
    try:
        content_length = int(raw_length)
    except ValueError as exc:
        raise RequestSecurityError("Content-Length 无效。", status=HTTPStatus.BAD_REQUEST) from exc
    if content_length < 0:
        raise RequestSecurityError("Content-Length 无效。", status=HTTPStatus.BAD_REQUEST)
    if content_length > config.max_json_body_bytes:
        raise RequestSecurityError("JSON 请求体超过大小限制。", status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
    content_type = str(handler.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
    if content_length and content_type != "application/json":
        raise RequestSecurityError("POST 接口只接受 application/json。", status=HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
    body = handler.rfile.read(content_length) if content_length else b"{}"
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise RequestSecurityError("请求体不是有效 JSON。", status=HTTPStatus.BAD_REQUEST) from exc
    if not isinstance(payload, dict):
        raise RequestSecurityError("JSON 请求体必须是对象。", status=HTTPStatus.BAD_REQUEST)
    return payload


def response_security_headers(handler: Any, config: SecurityConfig) -> dict[str, str]:
    headers = {
        "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
        "Content-Security-Policy": (
            "default-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; "
            "script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "connect-src 'self'; font-src 'self'"
        ),
        "Cross-Origin-Opener-Policy": "same-origin",
        "Cross-Origin-Resource-Policy": "same-origin",
    }
    if str(getattr(handler, "path", "")).startswith("/api/"):
        headers["Cache-Control"] = "no-store"
    if config.environment == "production" and effective_scheme(handler, config) == "https":
        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return headers
