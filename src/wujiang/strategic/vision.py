"""Per-faction campaign vision: adjacency, explore orders, and diplomacy reveals.

Visibility is stored per faction and only expands. Owned cities and their road
neighbors are always merged in. Farther cities are added by explore orders or
successful diplomacy. Public payloads must be masked for the viewing faction so
multiplayer clients cannot read the hidden map from JSON.
"""
from __future__ import annotations

import copy
from typing import Any

from wujiang.strategic.errors import StrategyError
from wujiang.strategic.models import City, EventLogEntry, MapNode, WorldState


FRIENDLY_VISION_RELATION = 40
HIDDEN_EVENT_MESSAGE = "边境传来未探明的消息。"


def _city_ids(world: WorldState) -> set[str]:
    return {city.city_id for city in world.cities}


def _faction_ids(world: WorldState) -> set[str]:
    return {faction.faction_id for faction in world.factions}


def _cities_by_id(world: WorldState) -> dict[str, City]:
    return {city.city_id: city for city in world.cities}


def _cities_by_node(world: WorldState) -> dict[str, City]:
    return {city.node_id: city for city in world.cities}


def _nodes_by_id(world: WorldState) -> dict[str, MapNode]:
    return {node.node_id: node for node in world.nodes}


def _owned_city_ids(world: WorldState, faction_id: str) -> set[str]:
    return {city.city_id for city in world.cities if city.owner_faction_id == str(faction_id)}


def adjacent_city_ids(world: WorldState, city_id: str) -> set[str]:
    cities = _cities_by_id(world)
    city = cities.get(str(city_id))
    if city is None:
        return set()
    node = _nodes_by_id(world).get(city.node_id)
    if node is None:
        return set()
    by_node = _cities_by_node(world)
    return {
        neighbor.city_id
        for target_id in node.connected_node_ids
        if (neighbor := by_node.get(target_id)) is not None
    }


def _stored_known(world: WorldState, faction_id: str) -> set[str]:
    raw = (world.known_city_ids_by_faction or {}).get(str(faction_id)) or []
    valid = _city_ids(world)
    return {str(city_id) for city_id in raw if str(city_id) in valid}


def _write_known(world: WorldState, faction_id: str, city_ids: set[str]) -> None:
    valid = _city_ids(world)
    world.known_city_ids_by_faction[str(faction_id)] = sorted(city_id for city_id in city_ids if city_id in valid)


def prune_known_city_ids(world: WorldState) -> None:
    valid_factions = _faction_ids(world)
    valid_cities = _city_ids(world)
    cleaned: dict[str, list[str]] = {}
    for faction_id, city_ids in (world.known_city_ids_by_faction or {}).items():
        if str(faction_id) not in valid_factions:
            continue
        cleaned[str(faction_id)] = sorted({str(city_id) for city_id in city_ids if str(city_id) in valid_cities})
    world.known_city_ids_by_faction = cleaned


def reveal_city_ids(world: WorldState, faction_id: str, city_ids: Any) -> list[str]:
    if not faction_id or str(faction_id) not in _faction_ids(world):
        return []
    known = _stored_known(world, faction_id)
    added: list[str] = []
    valid = _city_ids(world)
    for raw_id in city_ids or ():
        city_id = str(raw_id or "")
        if not city_id or city_id not in valid or city_id in known:
            continue
        known.add(city_id)
        added.append(city_id)
    if added:
        _write_known(world, faction_id, known)
    return added


def refresh_territory_vision(world: WorldState, faction_id: str) -> list[str]:
    owned = _owned_city_ids(world, faction_id)
    extra: set[str] = set()
    for city_id in owned:
        extra.add(city_id)
        extra.update(adjacent_city_ids(world, city_id))
    return reveal_city_ids(world, faction_id, extra)


def refresh_all_territory_vision(world: WorldState) -> None:
    prune_known_city_ids(world)
    for faction in world.factions:
        refresh_territory_vision(world, faction.faction_id)


def initialize_world_vision(world: WorldState) -> None:
    world.known_city_ids_by_faction = {}
    refresh_all_territory_vision(world)


def world_map_bounds(world: WorldState) -> dict[str, int]:
    xs = [int(node.x) for node in world.nodes]
    ys = [int(node.y) for node in world.nodes]
    if not xs or not ys:
        return {"min_x": 0, "min_y": 0, "max_x": 100, "max_y": 100}
    return {
        "min_x": min(xs),
        "min_y": min(ys),
        "max_x": max(xs),
        "max_y": max(ys),
    }


def refresh_after_city_control_change(world: WorldState, *, city_id: str, new_faction_id: str) -> None:
    reveal_city_ids(world, new_faction_id, [city_id])
    refresh_territory_vision(world, new_faction_id)
    previous_owners = [
        faction.faction_id
        for faction in world.factions
        if faction.faction_id != str(new_faction_id)
    ]
    for faction_id in previous_owners:
        if city_id in _stored_known(world, faction_id) or city_id in adjacent_city_ids(world, city_id):
            reveal_city_ids(world, faction_id, [city_id])


def visible_city_ids(world: WorldState, faction_id: str) -> set[str]:
    known = _stored_known(world, faction_id)
    owned = _owned_city_ids(world, faction_id)
    visible = set(known)
    visible.update(owned)
    for city_id in owned:
        visible.update(adjacent_city_ids(world, city_id))
    return visible


def city_is_visible(world: WorldState, faction_id: str, city_id: str) -> bool:
    return str(city_id) in visible_city_ids(world, faction_id)


def frontier_city_ids(world: WorldState, faction_id: str) -> set[str]:
    visible = visible_city_ids(world, faction_id)
    frontier: set[str] = set()
    for city_id in visible:
        for neighbor_id in adjacent_city_ids(world, city_id):
            if neighbor_id not in visible:
                frontier.add(neighbor_id)
    return frontier


def explore_from_city_ids(world: WorldState, faction_id: str, target_city_id: str) -> list[str]:
    visible = visible_city_ids(world, faction_id)
    if str(target_city_id) in visible:
        return []
    return sorted(
        city_id
        for city_id in adjacent_city_ids(world, target_city_id)
        if city_id in visible
    )


def explore_options(world: WorldState, faction_id: str) -> list[dict[str, str]]:
    cities = _cities_by_id(world)
    options: list[dict[str, str]] = []
    for target_id in sorted(frontier_city_ids(world, faction_id)):
        sources = explore_from_city_ids(world, faction_id, target_id)
        if not sources:
            continue
        target = cities.get(target_id)
        source = cities.get(sources[0])
        options.append(
            {
                "target_city_id": target_id,
                "from_city_id": sources[0],
                "target_name": target.name if target is not None else target_id,
                "from_name": source.name if source is not None else sources[0],
            }
        )
    return options


def require_visible_city(world: WorldState, faction_id: str, city_id: str, *, action: str = "查看") -> City:
    city = _cities_by_id(world).get(str(city_id))
    if city is None:
        raise StrategyError("城市不存在。")
    if not city_is_visible(world, faction_id, city.city_id):
        raise StrategyError(f"尚未探明目标，不能{action}。")
    return city


def validate_explore_city(
    world: WorldState,
    *,
    faction_id: str,
    target_city_id: str,
    from_city_id: str = "",
) -> dict[str, str]:
    target = _cities_by_id(world).get(str(target_city_id or "").strip())
    if target is None:
        raise StrategyError("要探索的城市不存在。")
    if city_is_visible(world, faction_id, target.city_id):
        raise StrategyError(f"{target.name}已经在已知视野中。")
    sources = explore_from_city_ids(world, faction_id, target.city_id)
    if not sources:
        raise StrategyError("只能沿已知道路探索相邻的未探明城市。")
    chosen = str(from_city_id or "").strip()
    if chosen and chosen not in sources:
        raise StrategyError("斥候必须从与目标相邻的已知城市出发。")
    source_id = chosen or sources[0]
    source = _cities_by_id(world).get(source_id)
    return {
        "target_city_id": target.city_id,
        "from_city_id": source_id,
        "target_name": target.name,
        "from_name": source.name if source is not None else source_id,
    }


def apply_explore_city(
    world: WorldState,
    *,
    faction_id: str,
    target_city_id: str,
    from_city_id: str = "",
) -> WorldState:
    preview = validate_explore_city(
        world,
        faction_id=faction_id,
        target_city_id=target_city_id,
        from_city_id=from_city_id,
    )
    next_world = WorldState.from_dict(copy.deepcopy(world.to_dict()))
    reveal_city_ids(next_world, faction_id, [preview["target_city_id"]])
    faction_name = next(
        (item.name for item in next_world.factions if item.faction_id == str(faction_id)),
        str(faction_id),
    )
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="explore",
            message=f"{faction_name}斥候自{preview['from_name']}出发，探明了{preview['target_name']}。",
            related_ids=[faction_id, preview["from_city_id"], preview["target_city_id"]],
            visibility="player_visible",
        )
    )
    next_world.validate()
    return next_world


def _capital_city_id(world: WorldState, faction_id: str) -> str:
    faction = next((item for item in world.factions if item.faction_id == str(faction_id)), None)
    if faction is None:
        return ""
    if faction.capital_city_id:
        return str(faction.capital_city_id)
    owned = _owned_city_ids(world, faction_id)
    return next(iter(sorted(owned)), "")


def reveal_faction_capitals(world: WorldState, left_faction_id: str, right_faction_id: str) -> None:
    left_capital = _capital_city_id(world, left_faction_id)
    right_capital = _capital_city_id(world, right_faction_id)
    if right_capital:
        reveal_city_ids(world, left_faction_id, [right_capital])
    if left_capital:
        reveal_city_ids(world, right_faction_id, [left_capital])


def reveal_faction_owned_cities(world: WorldState, viewer_faction_id: str, target_faction_id: str) -> None:
    reveal_city_ids(world, viewer_faction_id, _owned_city_ids(world, target_faction_id))


def reveal_diplomatic_contact(
    world: WorldState,
    actor_faction_id: str,
    target_faction_id: str,
    *,
    realm: bool = False,
    relation_score: int | None = None,
) -> None:
    score = 0 if relation_score is None else int(relation_score)
    if realm or score >= FRIENDLY_VISION_RELATION:
        reveal_faction_owned_cities(world, actor_faction_id, target_faction_id)
        reveal_faction_owned_cities(world, target_faction_id, actor_faction_id)
        return
    reveal_faction_capitals(world, actor_faction_id, target_faction_id)


def _empty_resources() -> dict[str, int]:
    return {"food": 0, "money": 0, "population": 0, "ether": 0, "troops": 0}


def _hidden_city_payload(city: City, from_city_ids: list[str]) -> dict[str, Any]:
    return {
        "id": city.city_id,
        "node_id": city.node_id,
        "name": "",
        "owner_faction_id": "",
        "visibility": "hidden",
        "level": 1,
        "resources": _empty_resources(),
        "defense": 0,
        "governor_id": None,
        "policy": "",
        "buildings": [],
        "building_levels": {},
        "registered_units": {},
        "relics_stored": [],
        "altars": [],
        "support_by_faction": {},
        "local_factions": [],
        "traits": [],
        "event_states": [],
        "troop_features": [],
        "occupation": {},
        "settlement": "",
        "settlement_label": "",
        "cannon_stock": 0,
        "economy_class": "",
        "veins": [],
        "vein_labels": [],
        "explore_from_city_ids": list(from_city_ids),
    }


def _hidden_node_payload(node: MapNode, connected_ids: list[str]) -> dict[str, Any]:
    return {
        "id": node.node_id,
        "name": "",
        "type": "unknown",
        "x": node.x,
        "y": node.y,
        "connected_node_ids": list(connected_ids),
        "traits": [],
    }


def _payload_id(item: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if value:
            return str(value)
    return ""


def _action_mentions_unknown_city(action: dict[str, Any], city_ids: set[str], visible: set[str]) -> bool:
    payload = action.get("payload") if isinstance(action.get("payload"), dict) else {}
    keys = (
        "city_id",
        "target_city_id",
        "source_city_id",
        "from_city_id",
        "home_city_id",
        "supply_source_city_id",
    )
    mentioned = [str(payload.get(key) or "") for key in keys]
    mentioned = [city_id for city_id in mentioned if city_id in city_ids]
    return any(city_id not in visible for city_id in mentioned)


def mask_world_public_for_faction(
    payload: dict[str, Any],
    world: WorldState,
    viewer_faction_id: str,
) -> dict[str, Any]:
    if not viewer_faction_id or str(viewer_faction_id) not in _faction_ids(world):
        return payload
    masked = copy.deepcopy(payload)
    visible = visible_city_ids(world, viewer_faction_id)
    frontier = frontier_city_ids(world, viewer_faction_id)
    public_city_ids = visible | frontier
    cities_by_id = _cities_by_id(world)
    nodes_by_id = _nodes_by_id(world)
    public_node_ids = {
        cities_by_id[city_id].node_id
        for city_id in public_city_ids
        if city_id in cities_by_id
    }

    public_cities: list[dict[str, Any]] = []
    for city_payload in masked.get("cities") or []:
        city_id = _payload_id(city_payload, "id", "city_id")
        if city_id not in public_city_ids:
            continue
        if city_id in visible:
            city_payload["visibility"] = "known"
            city_payload.pop("explore_from_city_ids", None)
            public_cities.append(city_payload)
            continue
        city = cities_by_id.get(city_id)
        if city is None:
            continue
        public_cities.append(_hidden_city_payload(city, explore_from_city_ids(world, viewer_faction_id, city_id)))
    masked["cities"] = public_cities

    public_nodes: list[dict[str, Any]] = []
    for node_payload in masked.get("nodes") or []:
        node_id = _payload_id(node_payload, "id", "node_id")
        if node_id not in public_node_ids:
            continue
        connected = [
            target_id
            for target_id in (node_payload.get("connected_node_ids") or [])
            if str(target_id) in public_node_ids
        ]
        city = _cities_by_node(world).get(node_id)
        if city is not None and city.city_id not in visible:
            node = nodes_by_id.get(node_id)
            if node is None:
                continue
            public_nodes.append(_hidden_node_payload(node, connected))
            continue
        node_payload["connected_node_ids"] = connected
        public_nodes.append(node_payload)
    masked["nodes"] = public_nodes

    masked["map_bounds"] = world_map_bounds(world)
    masked["known_city_ids_by_faction"] = {
        str(viewer_faction_id): sorted(visible),
    }
    masked["vision"] = {
        "known_city_ids": sorted(visible),
        "frontier_city_ids": sorted(frontier),
        "hidden_city_count": max(0, len(world.cities) - len(visible)),
    }

    city_ids = _city_ids(world)
    redacted_events: list[dict[str, Any]] = []
    for event in masked.get("event_log") or []:
        related = [str(item) for item in (event.get("related_ids") or [])]
        related_cities = [item for item in related if item in city_ids]
        if related_cities and any(city_id not in visible for city_id in related_cities):
            event = dict(event)
            event["message"] = HIDDEN_EVENT_MESSAGE
            event["related_ids"] = [item for item in related if item not in city_ids or item in visible]
        redacted_events.append(event)
    masked["event_log"] = redacted_events

    def _keep_located(item: dict[str, Any], *, own_key: str = "faction_id") -> bool:
        node_id = _payload_id(item, "location_node_id", "node_id")
        city_id = _payload_id(item, "city_id", "home_city_id")
        if str(item.get(own_key) or "") == str(viewer_faction_id):
            return True
        if city_id and city_id in visible:
            return True
        if node_id and node_id in public_node_ids:
            city = _cities_by_node(world).get(node_id)
            return city is None or city.city_id in visible
        return False

    masked["armies"] = [item for item in (masked.get("armies") or []) if _keep_located(item)]
    masked["encounters"] = [item for item in (masked.get("encounters") or []) if _keep_located(item)]
    masked["sieges"] = [item for item in (masked.get("sieges") or []) if _keep_located(item)]
    masked["pending_battles"] = [
        item
        for item in (masked.get("pending_battles") or [])
        if str(item.get("attacker_faction_id") or "") == str(viewer_faction_id)
        or str(item.get("defender_faction_id") or "") == str(viewer_faction_id)
        or _payload_id(item, "city_id", "target_city_id") in visible
    ]

    for faction_payload in masked.get("factions") or []:
        faction_id = _payload_id(faction_payload, "id", "faction_id")
        capital_id = str(faction_payload.get("capital_city_id") or "")
        if capital_id and capital_id not in visible:
            faction_payload["capital_city_id"] = None
        if faction_id != str(viewer_faction_id):
            faction_payload.pop("resource_board", None)
            rare = faction_payload.get("rare_resources")
            if isinstance(rare, dict):
                faction_payload["rare_resources"] = {}

    briefings = masked.get("monthly_briefings")
    if isinstance(briefings, dict):
        masked["monthly_briefings"] = {
            key: value
            for key, value in briefings.items()
            if str(key) == str(viewer_faction_id)
        }

    goals = masked.get("ai_strategic_goals")
    if isinstance(goals, dict):
        masked["ai_strategic_goals"] = {
            key: value
            for key, value in goals.items()
            if str(key) == str(viewer_faction_id)
        }

    return masked


def mask_campaign_public_for_faction(
    payload: dict[str, Any],
    world: WorldState,
    viewer_faction_id: str,
) -> dict[str, Any]:
    if not viewer_faction_id:
        return payload
    masked = copy.deepcopy(payload)
    world_payload = masked.get("world")
    if isinstance(world_payload, dict):
        masked["world"] = mask_world_public_for_faction(world_payload, world, viewer_faction_id)
    visible = visible_city_ids(world, viewer_faction_id)
    city_ids = _city_ids(world)
    queued: list[dict[str, Any]] = []
    for action in masked.get("queued_actions") or []:
        if str(action.get("faction_id") or "") == str(viewer_faction_id):
            queued.append(action)
            continue
        if _action_mentions_unknown_city(action, city_ids, visible):
            continue
        queued.append(action)
    masked["queued_actions"] = queued
    return masked
