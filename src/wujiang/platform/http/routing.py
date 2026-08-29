"""Route registry shared by the HTTP kernel and the domain API modules.

The kernel owns dispatch but knows nothing about individual endpoints; each
domain registers its own routes through :func:`get` / :func:`post`. This is the
seam that lets the tactical and strategic domains ship endpoints without
touching a shared request handler.
"""
from __future__ import annotations

from typing import Callable

from wujiang.platform.http.context import RequestContext

RouteHandler = Callable[[RequestContext], None]

_ROUTES: dict[tuple[str, str], RouteHandler] = {}
_ROUTE_DOMAINS: dict[tuple[str, str], str] = {}


class RouteConflictError(RuntimeError):
    """Raised when two domains claim the same method + path."""


def register(method: str, path: str, handler: RouteHandler, *, domain: str = "") -> RouteHandler:
    key = (method.upper(), path)
    if key in _ROUTES:
        raise RouteConflictError(
            f"{method.upper()} {path} is already registered by "
            f"{_ROUTE_DOMAINS.get(key) or 'another module'}."
        )
    _ROUTES[key] = handler
    _ROUTE_DOMAINS[key] = domain or getattr(handler, "__module__", "")
    return handler


def get(path: str, *, domain: str = "") -> Callable[[RouteHandler], RouteHandler]:
    def decorator(handler: RouteHandler) -> RouteHandler:
        return register("GET", path, handler, domain=domain)

    return decorator


def post(path: str, *, domain: str = "") -> Callable[[RouteHandler], RouteHandler]:
    def decorator(handler: RouteHandler) -> RouteHandler:
        return register("POST", path, handler, domain=domain)

    return decorator


def resolve(method: str, path: str) -> RouteHandler | None:
    return _ROUTES.get((method.upper(), path))


def registered_routes() -> dict[tuple[str, str], str]:
    """Method + path to owning domain, used by diagnostics and tests."""
    return dict(_ROUTE_DOMAINS)


def clear() -> None:
    """Test-only hook so a suite can rebuild the table from scratch."""
    _ROUTES.clear()
    _ROUTE_DOMAINS.clear()
