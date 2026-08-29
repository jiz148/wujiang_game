"""Per-request data passed from the HTTP kernel to domain route handlers.

Domain modules (``wujiang.tactical.api``, ``wujiang.strategic.api``,
``wujiang.platform.api``) receive one of these instead of reaching into the
``BaseHTTPRequestHandler`` themselves, so a route handler never needs to know
how the request was parsed or authenticated.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler
from typing import Any


@dataclass
class RequestContext:
    handler: BaseHTTPRequestHandler
    method: str
    path: str
    query: dict[str, list[str]] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)
    auth_user: Any = None

    def query_value(self, name: str, default: str = "") -> str:
        values = self.query.get(name) or []
        return str(values[0]) if values else default
