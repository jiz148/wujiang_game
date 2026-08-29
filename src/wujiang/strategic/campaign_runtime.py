"""Campaign store singleton and the helpers shared across the campaign seam.

Both ``wujiang.strategic.service`` and ``wujiang.bridge.battle_bridge`` need
these, so they live below both to keep the dependency graph acyclic.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler
from typing import Any

from wujiang.platform.http.runtime import json_response
from wujiang.platform.http.runtime import register_dependency
from wujiang.strategic import StrategyError
from wujiang.strategic import StrategyStore
from wujiang.platform.http import runtime

STRATEGY_STORE = StrategyStore()

# A city may receive at most this many player orders per month.
CITY_MONTHLY_ORDER_LIMIT = 2

# Opt the campaign database into the platform readiness probe.
register_dependency("strategy", lambda: STRATEGY_STORE)


def record_strategy_snapshot_safe(campaign: Any, *, checkpoint: str) -> None:
    try:
        runtime.ANALYTICS_STORE.record_strategy_snapshot(campaign, checkpoint=checkpoint)
    except Exception:
        # Product analytics must never block or alter authoritative gameplay.
        return



def strategy_error_response(handler: BaseHTTPRequestHandler, exc: StrategyError) -> None:
    json_response(handler, int(exc.status), {"error": str(exc)})



def strategy_city_name(campaign, city_id: str) -> str:
    for city in campaign.world.cities:
        if getattr(city, "city_id", "") == city_id:
            return city.name
    return str(city_id)
