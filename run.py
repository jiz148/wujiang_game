from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wujiang.platform.security import SecurityConfig, normalize_origin  # noqa: E402
from wujiang.platform.http.server import normalize_public_base_url, run_server  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Wujiang online test server.")
    parser.add_argument("--host", default="0.0.0.0", help="Host interface to bind. Default: 0.0.0.0")
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on. Default: 8000")
    parser.add_argument(
        "--public-base-url",
        default=os.environ.get("WUJIANG_PUBLIC_BASE_URL", ""),
        help="Public homepage URL to share with friends, e.g. http://203.0.113.10:8000 or https://game.example.com",
    )
    parser.add_argument(
        "--environment", choices=("development", "test", "production"), default="",
        help="Security profile. Production requires HTTPS and disables query-string auth tokens.",
    )
    parser.add_argument(
        "--require-https", action="store_true",
        help="Reject requests not received through TLS or a trusted HTTPS proxy.",
    )
    parser.add_argument(
        "--trusted-proxy-network", action="append", default=[],
        help="Trusted proxy IP/CIDR allowed to supply X-Forwarded-*; repeat as needed.",
    )
    parser.add_argument(
        "--allowed-origin", action="append", default=[],
        help="Additional exact browser Origin allowed for POST; repeat as needed.",
    )
    parser.add_argument(
        "--max-json-body-bytes", type=int, default=0,
        help="Maximum POST JSON body size; default 262144 bytes.",
    )
    args = parser.parse_args()
    try:
        args.public_base_url = normalize_public_base_url(args.public_base_url)
    except ValueError as exc:
        parser.error(str(exc))
    try:
        defaults = SecurityConfig.from_environment()
        environment = args.environment or defaults.environment
        normalized_origins = tuple(
            normalize_origin(value) for value in (args.allowed_origin or defaults.allowed_origins)
        )
        if any(not value for value in normalized_origins):
            raise ValueError("--allowed-origin must be an exact http(s) origin without path, query, or credentials.")
        args.security_config = SecurityConfig(
            environment=environment,
            require_https=bool(args.require_https or defaults.require_https or environment == "production"),
            trusted_proxy_networks=tuple(args.trusted_proxy_network or defaults.trusted_proxy_networks),
            allowed_origins=normalized_origins,
            max_json_body_bytes=int(args.max_json_body_bytes or defaults.max_json_body_bytes),
            allow_query_auth_tokens=environment != "production" and defaults.allow_query_auth_tokens,
        ).validated()
    except ValueError as exc:
        parser.error(str(exc))
    return args


if __name__ == "__main__":
    args = parse_args()
    run_server(
        host=args.host,
        port=args.port,
        public_base_url=args.public_base_url,
        security_config=args.security_config,
    )
