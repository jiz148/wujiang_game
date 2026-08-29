"""Account, analytics and operational endpoints."""
from __future__ import annotations

from http import HTTPStatus

from wujiang.platform.analytics import AnalyticsError
from wujiang.platform.auth import AuthError
from wujiang.platform.http.runtime import auth_error_response
from wujiang.platform.http.runtime import auth_token_from_request
from wujiang.platform.http.runtime import json_response
from wujiang.platform.http.runtime import readiness_status
from wujiang.platform.http.context import RequestContext
from wujiang.platform.http.routing import get, post
from wujiang.platform.http import runtime


@get("/api/health/live")
def get_health_live(ctx: RequestContext) -> None:
    handler = ctx.handler
    json_response(
        handler, HTTPStatus.OK,
        {"status": "ok", "service": "wujiang", "uptime_seconds": runtime.OBSERVABILITY.metrics()["uptime_seconds"]},
    )
    return


@get("/api/health/ready")
def get_health_ready(ctx: RequestContext) -> None:
    handler = ctx.handler
    ready, dependencies = readiness_status()
    json_response(
        handler, HTTPStatus.OK if ready else HTTPStatus.SERVICE_UNAVAILABLE,
        {"status": "ready" if ready else "not_ready", "dependencies": dependencies},
    )
    return


@get("/api/metrics")
def get_metrics(ctx: RequestContext) -> None:
    handler = ctx.handler
    supplied_token = str(handler.headers.get("X-Wujiang-Ops-Token") or "")
    if not runtime.OBSERVABILITY.allows_metrics(supplied_token):
        json_response(handler, HTTPStatus.FORBIDDEN, {"error": "无权访问运维指标。"})
        return
    json_response(handler, HTTPStatus.OK, runtime.OBSERVABILITY.metrics())
    return


@get("/api/auth/me")
def get_auth_me(ctx: RequestContext) -> None:
    handler = ctx.handler
    query = ctx.query
    token = auth_token_from_request(handler, query=query)
    if not token:
        json_response(handler, HTTPStatus.OK, {"user": None})
        return
    try:
        user = runtime.AUTH_STORE.user_for_session(token)
    except AuthError as exc:
        auth_error_response(handler, exc)
        return
    json_response(handler, HTTPStatus.OK, {"user": user.to_public_dict()})
    return


@get("/api/analytics/funnel")
def get_analytics_funnel(ctx: RequestContext) -> None:
    handler = ctx.handler
    json_response(handler, HTTPStatus.OK, runtime.ANALYTICS_STORE.funnel())
    return


@get("/api/analytics/strategy")
def get_analytics_strategy(ctx: RequestContext) -> None:
    handler = ctx.handler
    query = ctx.query
    filters = {
        key: (query.get(key) or [""])[0]
        for key in (
            "content_version", "balance_version", "variant_id", "seed",
            "human_count", "victory_route", "crisis_id", "crisis_stage", "month",
        )
    }
    try:
        dashboard = runtime.ANALYTICS_STORE.strategy_dashboard(filters)
    except (AnalyticsError, TypeError, ValueError):
        json_response(handler, HTTPStatus.BAD_REQUEST, {"error": "战役分析筛选条件无效。"})
        return
    json_response(handler, HTTPStatus.OK, dashboard)
    return


@get("/favicon.ico")
def get_favicon_ico(ctx: RequestContext) -> None:
    handler = ctx.handler
    handler.send_response(HTTPStatus.NO_CONTENT)
    handler.send_header("Content-Length", "0")
    handler.end_headers()
    return


@post("/api/auth/register")
def post_auth_register(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    try:
        user, session_token = runtime.AUTH_STORE.register(
            str(payload.get("username") or ""),
            str(payload.get("password") or ""),
        )
    except AuthError as exc:
        auth_error_response(handler, exc)
        return
    json_response(
        handler,
        HTTPStatus.OK,
        {"user": user.to_public_dict(), "session_token": session_token},
    )
    return


@post("/api/analytics/events")
def post_analytics_events(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    try:
        event_id = runtime.ANALYTICS_STORE.record(
            str(payload.get("event_name") or ""),
            str(payload.get("anonymous_session_id") or ""),
            payload.get("properties"),
        )
    except (AnalyticsError, TypeError, ValueError) as exc:
        json_response(handler, HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        return
    json_response(handler, HTTPStatus.CREATED, {"accepted": True, "event_id": event_id})
    return


@post("/api/auth/login")
def post_auth_login(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    try:
        user, session_token = runtime.AUTH_STORE.authenticate(
            str(payload.get("username") or ""),
            str(payload.get("password") or ""),
        )
    except AuthError as exc:
        auth_error_response(handler, exc)
        return
    json_response(
        handler,
        HTTPStatus.OK,
        {"user": user.to_public_dict(), "session_token": session_token},
    )
    return


@post("/api/auth/logout")
def post_auth_logout(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    runtime.AUTH_STORE.logout(auth_token_from_request(handler, payload=payload))
    json_response(handler, HTTPStatus.OK, {"ok": True})
    return
