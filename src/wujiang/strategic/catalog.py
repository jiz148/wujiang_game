"""Load the human-editable world catalog for true-campaign setup."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from wujiang.strategic.errors import StrategyError
from wujiang.strategic.objectives import (
    CAMPAIGN_BALANCE_VERSION,
    CAMPAIGN_CONTENT_VERSION,
    CAMPAIGN_OPENING_VARIANTS,
    DEFAULT_CAMPAIGN_VARIANT_ID,
)

CATALOG_PATH = Path(__file__).resolve().parents[3] / "data" / "world_catalog.json"
TRUE_CAMPAIGN_MODE = "true_campaign"
RANDOM_CAMPAIGN_MODE = "random_campaign"
HERO_QUALITY_ORDER = ("high", "mid_high", "medium")
MAX_WORLD_CITIES = 128
MIN_INDEPENDENT_CITIES = 4
MAX_INDEPENDENT_CITIES = 28
INDEPENDENT_CITY_RATIO = 0.22


def _as_int_dict(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    result: dict[str, int] = {}
    for key, value in raw.items():
        try:
            result[str(key)] = int(value)
        except (TypeError, ValueError):
            result[str(key)] = 0
    return result


def _roster_codes(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    codes: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            code = str(item.get("code") or "").strip()
        else:
            code = str(item or "").strip()
        if code and code not in codes:
            codes.append(code)
    return codes


def _roster_public(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, dict):
            code = str(item.get("code") or "").strip()
            name = str(item.get("name") or code).strip() or code
        else:
            code = str(item or "").strip()
            name = code
        if not code or code in seen:
            continue
        seen.add(code)
        result.append({"code": code, "name": name})
    return result


@lru_cache(maxsize=1)
def load_world_catalog() -> dict[str, Any]:
    if not CATALOG_PATH.exists():
        raise StrategyError(f"找不到世界目录：{CATALOG_PATH}")
    payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise StrategyError("世界目录格式无效。")
    return payload


def reload_world_catalog() -> dict[str, Any]:
    load_world_catalog.cache_clear()
    return load_world_catalog()


def rare_resource_defs() -> list[dict[str, Any]]:
    catalog = load_world_catalog()
    items = catalog.get("rare_resources")
    if not isinstance(items, list):
        return []
    result: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or not str(item.get("id") or "").strip():
            continue
        result.append(
            {
                "id": str(item["id"]).strip(),
                "name": str(item.get("name") or item["id"]).strip(),
                "building_id": str(item.get("building_id") or "").strip(),
                "building_name": str(item.get("building_name") or "").strip(),
                "flavor": str(item.get("flavor") or "").strip(),
                "base_price": max(1, int(item.get("base_price") or 1)),
                "upgrade_cost": max(0, int(item.get("upgrade_cost") or 0)),
                "yield_per_vein": max(0, int(item.get("yield_per_vein") or 0)),
            }
        )
    return result


def rare_resource_ids() -> list[str]:
    return [item["id"] for item in rare_resource_defs()]


def rare_resource_def(resource_id: str) -> dict[str, Any] | None:
    return next((item for item in rare_resource_defs() if item["id"] == str(resource_id)), None)


def rare_resource_for_building(building_id: str) -> dict[str, Any] | None:
    return next(
        (item for item in rare_resource_defs() if item["building_id"] == str(building_id)),
        None,
    )


def trade_rules() -> dict[str, Any]:
    catalog = load_world_catalog()
    raw = catalog.get("trade") if isinstance(catalog.get("trade"), dict) else {}
    basics: list[dict[str, Any]] = []
    for item in raw.get("basic_goods") or []:
        if not isinstance(item, dict) or not str(item.get("id") or "").strip():
            continue
        basics.append(
            {
                "id": str(item["id"]).strip(),
                "name": str(item.get("name") or item["id"]).strip(),
                "base_price": max(1, int(item.get("base_price") or 1)),
            }
        )
    if not basics:
        basics = [
            {"id": "food", "name": "粮食", "base_price": 2},
            {"id": "ether", "name": "以太", "base_price": 8},
        ]
    return {
        "quote_reference_stock": max(1, int(raw.get("quote_reference_stock") or 40)),
        "min_price_factor": float(raw.get("min_price_factor") or 0.45),
        "max_price_factor": float(raw.get("max_price_factor") or 2.4),
        "offer_duration_months": max(1, int(raw.get("offer_duration_months") or 3)),
        "basic_goods": basics,
    }


def random_campaign_vein_rules() -> dict[str, Any]:
    catalog = load_world_catalog()
    raw = catalog.get("random_campaign") if isinstance(catalog.get("random_campaign"), dict) else {}
    major = raw.get("veins_per_major_faction") if isinstance(raw.get("veins_per_major_faction"), dict) else {}
    independent = raw.get("veins_per_independent") if isinstance(raw.get("veins_per_independent"), dict) else {}
    return {
        "veins_per_major_faction": {
            "min": max(0, int(major.get("min") or 5)),
            "max": max(0, int(major.get("max") or 9)),
        },
        "veins_per_independent": {
            "min": max(0, int(independent.get("min") or 0)),
            "max": max(0, int(independent.get("max") or 2)),
        },
        "max_vein_types_per_city": max(1, int(raw.get("max_vein_types_per_city") or 3)),
    }


def catalog_nations() -> list[dict[str, Any]]:
    catalog = load_world_catalog()
    items = catalog.get("nations")
    if not isinstance(items, list):
        return []
    result: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or not str(item.get("id") or "").strip():
            continue
        quality = str(item.get("hero_quality") or "medium").strip().lower()
        if quality not in HERO_QUALITY_ORDER:
            quality = "medium"
        result.append(
            {
                "id": str(item["id"]).strip(),
                "name": str(item.get("name") or item["id"]).strip(),
                "color": str(item.get("color") or "").strip(),
                "city_count": max(1, int(item.get("city_count") or 1)),
                "hero_count": max(1, int(item.get("hero_count") or 1)),
                "hero_quality": quality,
                "blurb": str(item.get("blurb") or "").strip(),
                "starting_resources": _as_int_dict(item.get("starting_resources")),
                "starting_rare": _as_int_dict(item.get("starting_rare")),
                "veins": _as_int_dict(item.get("veins")),
                "roster": _roster_public(item.get("roster")),
                "roster_codes": _roster_codes(item.get("roster")),
            }
        )
    return result


def catalog_nation(nation_id: str) -> dict[str, Any] | None:
    return next((item for item in catalog_nations() if item["id"] == str(nation_id)), None)


def _scale_city_counts(sizes: list[int], budget: int) -> list[int]:
    count = len(sizes)
    if count == 0 or budget <= 0:
        return [0] * count
    if budget <= count:
        return [1 if index < budget else 0 for index in range(count)]
    total = max(1, sum(max(1, size) for size in sizes))
    quotas = [max(1, size) * budget / total for size in sizes]
    floors = [max(1, int(quota)) for quota in quotas]
    while sum(floors) > budget:
        richest = max(range(count), key=lambda index: floors[index])
        if floors[richest] <= 1:
            break
        floors[richest] -= 1
    leftover = budget - sum(floors)
    order = sorted(range(count), key=lambda index: quotas[index] - int(quotas[index]), reverse=True)
    cursor = 0
    while leftover > 0:
        floors[order[cursor % count]] += 1
        leftover -= 1
        cursor += 1
    return floors


def resolve_world_city_budget(
    nation_city_counts: list[int],
    requested_city_count: int = 0,
    *,
    max_cities: int = MAX_WORLD_CITIES,
) -> tuple[list[int], int, int]:
    """各国城数 + 独立城邦，总城数不超过 128。"""
    sizes = [max(1, int(size or 1)) for size in nation_city_counts]
    owned = sum(sizes)
    ceiling = max(1, int(max_cities or MAX_WORLD_CITIES))
    requested = max(0, int(requested_city_count or 0))
    if requested > owned:
        independents = requested - owned
    else:
        independents = min(
            MAX_INDEPENDENT_CITIES,
            max(MIN_INDEPENDENT_CITIES, int(round(owned * INDEPENDENT_CITY_RATIO))),
        )
    independents = max(1, independents)
    total = owned + independents
    if total <= ceiling:
        return sizes, total, independents
    if owned < ceiling:
        independents = max(1, ceiling - owned)
        return sizes, ceiling, independents
    reserved = 1 if len(sizes) < ceiling else 0
    sizes = _scale_city_counts(sizes, ceiling - reserved)
    return sizes, ceiling, reserved


def catalog_scenarios() -> list[dict[str, Any]]:
    catalog = load_world_catalog()
    items = catalog.get("scenarios")
    if not isinstance(items, list):
        return []
    nations_by_id = {item["id"]: item for item in catalog_nations()}
    result: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or not str(item.get("id") or "").strip():
            continue
        nation_ids = [str(nation_id).strip() for nation_id in item.get("nation_ids") or [] if str(nation_id).strip()]
        nations = [dict(nations_by_id[nation_id]) for nation_id in nation_ids if nation_id in nations_by_id]
        sizes, city_count, independents = resolve_world_city_budget(
            [nation["city_count"] for nation in nations],
            int(item.get("city_count") or 0),
        )
        for nation, size in zip(nations, sizes):
            nation["city_count"] = size
        result.append(
            {
                "id": str(item["id"]).strip(),
                "name": str(item.get("name") or item["id"]).strip(),
                "mode": str(item.get("mode") or TRUE_CAMPAIGN_MODE).strip() or TRUE_CAMPAIGN_MODE,
                "blurb": str(item.get("blurb") or "").strip(),
                "default": bool(item.get("default")),
                "city_count": city_count,
                "nation_ids": [nation["id"] for nation in nations],
                "nations": nations,
                "major_faction_count": len(nations),
                "neutral_city_state_count": independents,
                "month_limit": max(0, int(item.get("month_limit") or 0)),
                "opening_hero_fill": str(item.get("opening_hero_fill") or "quota").strip() or "quota",
            }
        )
    return result


def default_true_scenario() -> dict[str, Any]:
    scenarios = catalog_scenarios()
    if not scenarios:
        raise StrategyError("世界目录里没有真实战役场景。")
    return next((item for item in scenarios if item.get("default")), scenarios[0])


def true_campaign_scenario(scenario_id: str | None = None) -> dict[str, Any]:
    if not scenario_id:
        return default_true_scenario()
    scenario = next((item for item in catalog_scenarios() if item["id"] == str(scenario_id)), None)
    if scenario is None:
        raise StrategyError("未知的真实战役场景。")
    return scenario


def true_campaign_contract(
    scenario_id: str | None = None,
    variant_id: str = DEFAULT_CAMPAIGN_VARIANT_ID,
) -> dict[str, Any]:
    normalized_variant_id = str(variant_id or DEFAULT_CAMPAIGN_VARIANT_ID).strip().lower()
    variant = CAMPAIGN_OPENING_VARIANTS.get(normalized_variant_id)
    if variant is None:
        raise StrategyError("未知的战役开局变体。")
    scenario = true_campaign_scenario(scenario_id)
    nations = []
    for index, nation in enumerate(scenario["nations"], start=1):
        snapshot = dict(nation)
        snapshot["faction_id"] = f"faction_{index}"
        nations.append(snapshot)
    return {
        "id": scenario["id"],
        "name": scenario["name"],
        "mode": TRUE_CAMPAIGN_MODE,
        "content_version": CAMPAIGN_CONTENT_VERSION,
        "balance_version": CAMPAIGN_BALANCE_VERSION,
        "opening_variant": dict(variant),
        "city_count": int(scenario["city_count"]),
        "major_faction_count": int(scenario["major_faction_count"]),
        "neutral_city_state_count": int(scenario["neutral_city_state_count"]),
        "month_limit": int(scenario.get("month_limit") or 0),
        "expected_duration_minutes": [90, 150],
        "available_victory_routes": [
            "unify_cities",
            "eliminate_enemy_factions",
            "peaceful_integration",
            "world_mainline_victory",
            "time_limit_assessment",
        ],
        "locked_systems": [],
        "opening_hero_fill": scenario["opening_hero_fill"],
        "nations": nations,
        "blurb": scenario["blurb"],
    }


def opening_hero_fill_mode(world: Any) -> str:
    contract = getattr(world, "campaign_contract", None) or {}
    mode = str(contract.get("opening_hero_fill") or "").strip()
    if mode:
        return mode
    if str(contract.get("mode") or "") == TRUE_CAMPAIGN_MODE:
        return "quota"
    return "fill_offices"


def contract_nation_for_faction(world: Any, faction_id: str) -> dict[str, Any] | None:
    contract = getattr(world, "campaign_contract", None) or {}
    for item in contract.get("nations") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("faction_id") or "") == str(faction_id):
            return item
        if str(item.get("id") or "") == str(getattr(
            next((faction for faction in getattr(world, "factions", []) if faction.faction_id == faction_id), None),
            "nation_id",
            "",
        ) or ""):
            return item
    faction = next((item for item in getattr(world, "factions", []) if item.faction_id == faction_id), None)
    if faction is not None and getattr(faction, "nation_id", ""):
        nation = catalog_nation(faction.nation_id)
        if nation is not None:
            snapshot = dict(nation)
            snapshot["faction_id"] = faction_id
            return snapshot
    return None


def world_catalog_public() -> dict[str, Any]:
    catalog = load_world_catalog()
    return {
        "path": "wujiang_game/data/world_catalog.json",
        "readme": list(catalog.get("_readme") or []),
        "rare_resources": rare_resource_defs(),
        "trade": trade_rules(),
        "random_campaign": random_campaign_vein_rules(),
        "nations": catalog_nations(),
        "scenarios": [
            {
                "id": scenario["id"],
                "name": scenario["name"],
                "mode": scenario["mode"],
                "blurb": scenario["blurb"],
                "default": scenario["default"],
                "city_count": scenario["city_count"],
                "major_faction_count": scenario["major_faction_count"],
                "neutral_city_state_count": scenario["neutral_city_state_count"],
                "month_limit": scenario["month_limit"],
                "opening_hero_fill": scenario["opening_hero_fill"],
                "nations": [
                    {
                        "id": nation["id"],
                        "name": nation["name"],
                        "color": nation["color"],
                        "city_count": nation["city_count"],
                        "hero_count": nation["hero_count"],
                        "hero_quality": nation["hero_quality"],
                        "blurb": nation["blurb"],
                        "veins": dict(nation["veins"]),
                        "roster": list(nation["roster"]),
                    }
                    for nation in scenario["nations"]
                ],
            }
            for scenario in catalog_scenarios()
        ],
    }
