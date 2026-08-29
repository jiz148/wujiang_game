"""Composition root for the HTTP server.

Importing this module is what assembles a running application: the kernel
supplies transport and dispatch, and each domain package contributes its own
endpoints by being imported here. Nothing else in the codebase imports all
three domains at once, which is what keeps tactical and strategic development
independent of each other.

Adding a domain means adding one import below and nothing more.
"""
from __future__ import annotations

from wujiang.platform.http.handler import WujiangHandler, run_server
from wujiang.platform.http.routing import registered_routes
from wujiang.platform.http.runtime import (
    auth_error_response,
    auth_token_from_request,
    authenticated_user_from_request,
    configure_observability,
    configure_public_base_url,
    configure_rate_limiter,
    configure_security,
    json_response,
    normalize_public_base_url,
    readiness_status,
    request_base_url,
    request_json,
    reset_rate_limiter,
)

# Route registration happens as an import side effect; order fixes the order of
# the readiness probe's dependency report.
import wujiang.platform.api  # noqa: F401
import wujiang.tactical.api  # noqa: F401
import wujiang.strategic.api  # noqa: F401
from wujiang.tactical.session import SESSION

__all__ = [
    "SESSION",
    "WujiangHandler",
    "auth_error_response",
    "auth_token_from_request",
    "authenticated_user_from_request",
    "configure_observability",
    "configure_public_base_url",
    "configure_rate_limiter",
    "configure_security",
    "json_response",
    "normalize_public_base_url",
    "readiness_status",
    "registered_routes",
    "request_base_url",
    "request_json",
    "reset_rate_limiter",
    "run_server",
]
