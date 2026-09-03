from __future__ import annotations

import math
import random
from typing import Any

from wujiang.strategic.models import City, EventLogEntry, Faction, MapNode, ResourceBundle, StrategyError, WorldState


CITY_NAME_PARTS = (
    "晨星",
    "雾港",
    "赤砂",
    "白塔",
    "北境",
    "龙脊",
    "青炉",
    "银湾",
    "星坠",
    "黑石",
    "风铃",
    "云砦",
    "霜原",
    "铁门",
    "月津",
    "苍藤",
    "琥珀",
    "芦洲",
    "雁门",
    "桑榆",
    "石梁",
    "桐溪",
    "鹤汀",
    "紫塞",
    "兰渚",
)

CITY_TROOP_FEATURES = (
    "守备兵",
    "弓兵",
    "骑兵",
    "山地兵",
    "以太侦察兵",
    "城墙工兵",
)

NEUTRAL_GOVERNOR_NAMES = (
    "顾临川", "陆怀安", "沈砚", "苏明远", "裴照", "温行舟",
    "谢云岚", "林朔", "闻人策", "白景澄", "萧长宁", "叶知秋",
    "韩望舒", "乔晚晴", "孟衡", "容止", "岑暮雪", "尹长川",
)


def _city_name(index: int) -> str:
    base = CITY_NAME_PARTS[(index - 1) % len(CITY_NAME_PARTS)]
    if index > len(CITY_NAME_PARTS):
        return f"{base}{1 + (index - 1) // len(CITY_NAME_PARTS)}城"
    return f"{base}城"


def _map_grid_size(city_count: int) -> tuple[int, int]:
    cols = max(2, math.ceil(math.sqrt(city_count)))
    rows = max(2, math.ceil(city_count / cols))
    return cols, rows


def _map_world_span(city_count: int) -> float:
    return max(100.0, math.sqrt(max(1, city_count)) * 21.0)


def _snake_cell(index: int, cols: int) -> tuple[int, int]:
    step = index - 1
    row = step // cols
    col = step % cols
    if row % 2:
        col = cols - 1 - col
    return col, row


def _farthest_point_indices(
    points: list[tuple[float, float]],
    count: int,
    rng: random.Random,
) -> list[int]:
    remaining = list(range(len(points)))
    if not remaining or count <= 0:
        return []
    chosen = [remaining.pop(rng.randrange(len(remaining)))]
    while len(chosen) < min(count, len(points)) and remaining:
        def _score(index: int) -> float:
            px, py = points[index]
            return min(math.hypot(px - points[item][0], py - points[item][1]) for item in chosen)

        remaining.sort(key=lambda index: (_score(index), index))
        window = remaining[max(0, len(remaining) - max(2, len(remaining) // 2)) :]
        pick = rng.choice(window)
        remaining.remove(pick)
        chosen.append(pick)
    return chosen


def _clamp_map_point(x: float, y: float, span: float = 100.0) -> tuple[float, float]:
    margin = max(4.0, float(span) * 0.03)
    limit = max(margin + 1.0, float(span) - margin)
    return (max(margin, min(limit, x)), max(margin, min(limit, y)))


def _separate_city_coordinates(
    placed: dict[int, tuple[float, float]],
    *,
    min_distance: float,
    rounds: int = 8,
    span: float = 100.0,
) -> dict[int, tuple[float, float]]:
    for _ in range(rounds):
        indexes = list(placed)
        for left_pos, left_index in enumerate(indexes):
            ax, ay = placed[left_index]
            for right_index in indexes[left_pos + 1:]:
                bx, by = placed[right_index]
                dx = ax - bx
                dy = ay - by
                distance = math.hypot(dx, dy)
                if distance < 0.01 or distance >= min_distance:
                    continue
                push = (min_distance - distance) / 2
                nx = dx / distance
                ny = dy / distance
                ax, ay = _clamp_map_point(ax + nx * push, ay + ny * push, span)
                placed[right_index] = _clamp_map_point(bx - nx * push, by - ny * push, span)
            placed[left_index] = (ax, ay)
    return placed


def _nudge_remote_frontier_cities(
    placed: dict[int, tuple[float, float]],
    rng: random.Random,
    *,
    span: float = 100.0,
    min_distance: float = 12.0,
) -> dict[int, tuple[float, float]]:
    if len(placed) < 4:
        return placed
    cx = sum(point[0] for point in placed.values()) / len(placed)
    cy = sum(point[1] for point in placed.values()) / len(placed)
    remote_count = max(1, len(placed) // 4)
    farthest = sorted(
        placed,
        key=lambda index: -math.hypot(placed[index][0] - cx, placed[index][1] - cy),
    )[:remote_count]
    for index in farthest:
        x, y = placed[index]
        dx = x - cx
        dy = y - cy
        distance = math.hypot(dx, dy) or 1.0
        push = rng.uniform(span * 0.08, span * 0.13)
        placed[index] = _clamp_map_point(x + dx / distance * push, y + dy / distance * push, span)
    return _separate_city_coordinates(placed, min_distance=min_distance, rounds=4, span=span)


def _place_city_coordinates(
    city_count: int,
    rng: random.Random,
    *,
    capital_count: int | None = None,
) -> dict[int, tuple[float, float]]:
    span = _map_world_span(city_count)
    cols, rows = _map_grid_size(city_count)
    margin = max(4.0, span * 0.03)
    usable = max(margin + 1.0, span - margin * 2)
    cell = usable / max(cols, rows)
    min_distance = min(22.0, cell * 0.78)
    cells: list[tuple[float, float]] = []
    for index in range(1, city_count + 1):
        col, row = _snake_cell(index, cols)
        jitter_x = rng.uniform(-0.42, 0.42)
        jitter_y = rng.uniform(-0.42, 0.42)
        if rng.random() < 0.45:
            jitter_x += rng.uniform(-0.22, 0.22)
            jitter_y += rng.uniform(-0.22, 0.22)
        x = margin + ((col + 0.5 + jitter_x) / cols) * usable
        y = margin + ((row + 0.5 + jitter_y) / rows) * usable
        cells.append(_clamp_map_point(x, y, span))
    placed: dict[int, tuple[float, float]] = {index + 1: cell for index, cell in enumerate(cells)}
    placed = _separate_city_coordinates(placed, min_distance=min_distance, span=span)
    placed = _nudge_remote_frontier_cities(placed, rng, span=span, min_distance=max(12.0, min_distance * 0.85))
    spread_count = max(1, min(city_count, int(capital_count or 1)))
    points = [placed[index] for index in range(1, city_count + 1)]
    capital_slots = _farthest_point_indices(points, spread_count, rng)
    leftover_slots = [index for index in range(city_count) if index not in set(capital_slots)]
    rng.shuffle(capital_slots)
    rng.shuffle(leftover_slots)
    remapped: dict[int, tuple[float, float]] = {}
    for offset, slot in enumerate(capital_slots):
        remapped[offset + 1] = points[slot]
    for offset, slot in enumerate(leftover_slots):
        remapped[spread_count + offset + 1] = points[slot]
    return remapped


def _nearby_connection_pairs(
    city_count: int,
    coordinates: dict[int, tuple[float, float]],
) -> list[tuple[int, int]]:
    return _mst_connection_pairs(city_count, coordinates)


def _mst_connection_pairs(
    city_count: int,
    coordinates: dict[int, tuple[float, float]],
) -> list[tuple[int, int]]:
    if city_count < 2:
        return []
    edges: list[tuple[float, int, int]] = []
    for left in range(1, city_count + 1):
        ax, ay = coordinates[left]
        for right in range(left + 1, city_count + 1):
            bx, by = coordinates[right]
            edges.append((math.hypot(ax - bx, ay - by), left, right))
    edges.sort()
    parent = {index: index for index in range(1, city_count + 1)}

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    pairs: list[tuple[int, int]] = []
    for _distance, left, right in edges:
        left_root = find(left)
        right_root = find(right)
        if left_root == right_root:
            continue
        parent[right_root] = left_root
        pairs.append((left, right))
        if len(pairs) >= city_count - 1:
            break

    degree = {index: 0 for index in range(1, city_count + 1)}
    seen = {(min(left, right), max(left, right)) for left, right in pairs}
    for left, right in pairs:
        degree[left] += 1
        degree[right] += 1
    for index in range(1, city_count + 1):
        if degree[index] != 1:
            continue
        ax, ay = coordinates[index]
        nearest = sorted(
            (other for other in range(1, city_count + 1) if other != index),
            key=lambda other: math.hypot(ax - coordinates[other][0], ay - coordinates[other][1]),
        )
        if len(nearest) < 2:
            continue
        first = nearest[0]
        second = nearest[1]
        first_dist = math.hypot(ax - coordinates[first][0], ay - coordinates[first][1])
        second_dist = math.hypot(ax - coordinates[second][0], ay - coordinates[second][1])
        if second_dist > first_dist * 1.28:
            continue
        key = (min(index, second), max(index, second))
        if key in seen:
            continue
        seen.add(key)
        pairs.append(key)
    return pairs


def _pick_city_settlement(rng: random.Random, *, level: int, defense: int, is_capital: bool) -> str:  # noqa: ARG001
    if is_capital:
        return "fortress" if defense >= 7 else "city"
    if defense >= 8:
        return "fortress"
    roll = rng.random()
    if roll < 0.34:
        return "village"
    if roll < 0.68:
        return "town"
    if roll < 0.90:
        return "city"
    return "fortress"


def _support_for_owner(faction_ids: list[str], owner_faction_id: str) -> dict[str, int]:
    support: dict[str, int] = {}
    for faction_id in faction_ids:
        support[faction_id] = 70 if faction_id == owner_faction_id else 35
    support["local_autonomy"] = 45
    return support


def _finalize_generated_world(world: WorldState) -> WorldState:
    from wujiang.strategic.story import open_monthly_story_events

    from wujiang.strategic.heroes import ensure_strategic_hero_system
    from wujiang.strategic.objectives import apply_campaign_opening_variant
    from wujiang.strategic.offices import ensure_office_system
    from wujiang.strategic.quick_campaign import apply_quick_campaign_setup
    from wujiang.strategic.relics import ensure_relic_system
    from wujiang.strategic.world_crisis import ensure_world_crises

    finalized = ensure_world_crises(
        ensure_relic_system(
            ensure_strategic_hero_system(
                ensure_office_system(
                    open_monthly_story_events(apply_quick_campaign_setup(apply_campaign_opening_variant(world)))
                )
            )
        )
    )
    from wujiang.strategic.vision import initialize_world_vision

    initialize_world_vision(finalized)
    finalized.validate()
    return finalized


def _grow_nation_clusters(
    city_count: int,
    connections: dict[str, set[str]],
    coordinates: dict[int, tuple[float, float]],
    nation_sizes: list[int],
) -> list[list[int]]:
    assigned: dict[int, int] = {}
    clusters: list[list[int]] = [[] for _ in nation_sizes]
    for nation_index, _size in enumerate(nation_sizes):
        capital = nation_index + 1
        assigned[capital] = nation_index
        clusters[nation_index].append(capital)
    for nation_index, size in enumerate(nation_sizes):
        frontier = list(clusters[nation_index])
        while len(clusters[nation_index]) < size:
            candidates: list[tuple[float, int, int]] = []
            for owner in frontier:
                ox, oy = coordinates[owner]
                for neighbor_id in connections.get(f"node_{owner}", ()):
                    neighbor = int(str(neighbor_id).split("_")[-1])
                    if neighbor in assigned:
                        continue
                    nx, ny = coordinates[neighbor]
                    candidates.append((math.hypot(ox - nx, oy - ny), owner, neighbor))
            if not candidates:
                leftovers = [index for index in range(1, city_count + 1) if index not in assigned]
                if not leftovers:
                    break
                cx, cy = coordinates[clusters[nation_index][0]]
                leftovers.sort(key=lambda index: math.hypot(coordinates[index][0] - cx, coordinates[index][1] - cy))
                pick = leftovers[0]
            else:
                candidates.sort()
                pick = candidates[0][2]
            assigned[pick] = nation_index
            clusters[nation_index].append(pick)
            frontier.append(pick)
    return clusters


def generate_true_campaign_world(*, seed: int, campaign_contract: dict[str, Any]) -> WorldState:
    from wujiang.strategic.catalog import TRUE_CAMPAIGN_MODE, resolve_world_city_budget
    from wujiang.strategic.rare_resources import normalize_rare_stock, place_configured_veins, place_random_faction_veins

    contract = dict(campaign_contract or {})
    nations = [item for item in contract.get("nations") or [] if isinstance(item, dict)]
    if len(nations) < 1:
        raise StrategyError("真实战役至少需要 1 个国家。")

    nation_sizes, city_count, _independents = resolve_world_city_budget(
        [max(1, int(nation.get("city_count") or 1)) for nation in nations],
        int(contract.get("city_count") or 0),
    )
    for nation, size in zip(nations, nation_sizes):
        nation["city_count"] = size

    rng = random.Random(int(seed))
    faction_count = len(nations)
    major_faction_ids = [str(nation.get("faction_id") or f"faction_{index}") for index, nation in enumerate(nations, start=1)]
    coordinates = _place_city_coordinates(city_count, rng, capital_count=faction_count)
    nodes: list[MapNode] = []
    connections: dict[str, set[str]] = {}
    for index in range(1, city_count + 1):
        node_id = f"node_{index}"
        connections[node_id] = set()
        x, y = coordinates[index]
        nodes.append(
            MapNode(
                node_id=node_id,
                name=_city_name(index),
                node_type="city",
                x=int(round(x)),
                y=int(round(y)),
                traits=[],
            )
        )
    for left_index, right_index in _mst_connection_pairs(city_count, coordinates):
        left = f"node_{left_index}"
        right = f"node_{right_index}"
        connections[left].add(right)
        connections[right].add(left)
    for node in nodes:
        node.connected_node_ids = sorted(connections[node.node_id])

    clusters = _grow_nation_clusters(city_count, connections, coordinates, nation_sizes)
    owner_by_city: dict[int, str] = {}
    for nation_index, city_indexes in enumerate(clusters):
        for city_index in city_indexes:
            owner_by_city[city_index] = major_faction_ids[nation_index]
    leftover_indexes = [index for index in range(1, city_count + 1) if index not in owner_by_city]
    leftover_indexes.sort()
    independent_ids = [f"neutral_city_state_{index}" for index in leftover_indexes]
    for city_index, faction_id in zip(leftover_indexes, independent_ids):
        owner_by_city[city_index] = faction_id

    factions: list[Faction] = []
    for nation_index, nation in enumerate(nations):
        starting = nation.get("starting_resources") if isinstance(nation.get("starting_resources"), dict) else {}
        factions.append(
            Faction(
                faction_id=major_faction_ids[nation_index],
                name=str(nation.get("name") or f"第{nation_index + 1}势力"),
                is_ai=nation_index != 0,
                capital_city_id=f"city_{clusters[nation_index][0]}",
                resources=ResourceBundle(
                    food=int(starting.get("food", 300) or 300),
                    money=int(starting.get("money", 300) or 300),
                    population=int(starting.get("population", 0) or 0),
                    ether=int(starting.get("ether", 50) or 50),
                    troops=int(starting.get("troops", 200) or 200),
                ),
                faction_type="major",
                color=str(nation.get("color") or ""),
                nation_id=str(nation.get("id") or ""),
                rare_resources=normalize_rare_stock(nation.get("starting_rare")),
            )
        )
    for offset, city_index in enumerate(leftover_indexes):
        city_name = _city_name(city_index)
        governor_name = NEUTRAL_GOVERNOR_NAMES[offset % len(NEUTRAL_GOVERNOR_NAMES)]
        factions.append(
            Faction(
                faction_id=independent_ids[offset],
                name=f"{city_name}城邦",
                is_ai=True,
                capital_city_id=f"city_{city_index}",
                resources=ResourceBundle(food=160, money=120, population=0, ether=0, troops=120),
                faction_type="neutral_city_state",
                governor_name=governor_name,
                relations={major_faction_id: 0 for major_faction_id in major_faction_ids},
                influence_by_faction={major_faction_id: 0 for major_faction_id in major_faction_ids},
                rare_resources=normalize_rare_stock({}),
            )
        )

    faction_ids = [faction.faction_id for faction in factions]
    cities: list[City] = []
    for index in range(1, city_count + 1):
        owner_faction_id = owner_by_city[index]
        is_capital = any(faction.capital_city_id == f"city_{index}" and faction.is_major for faction in factions)
        is_independent = owner_faction_id.startswith("neutral_city_state_")
        level = 1 + (1 if is_capital else rng.randint(0, 2))
        population = rng.randint(800, 1800) * level
        troops = rng.randint(180, 420) * level
        defense = rng.randint(2, 6) + level
        troop_feature = CITY_TROOP_FEATURES[(index - 1) % len(CITY_TROOP_FEATURES)]
        traits = ["主城候选"] if is_capital else (["中立城邦"] if is_independent else [])
        cities.append(
            City(
                city_id=f"city_{index}",
                node_id=f"node_{index}",
                name=_city_name(index),
                owner_faction_id=owner_faction_id,
                level=level,
                resources=ResourceBundle(
                    food=rng.randint(500, 900) * level,
                    money=rng.randint(300, 700) * level,
                    population=population,
                    ether=rng.randint(20, 80) * level,
                    troops=troops,
                ),
                defense=defense,
                governor_id=(f"officer:{owner_faction_id}:governor" if is_independent else None),
                buildings=["政厅", "fields", "barracks", "ritual_site", "city_defense"],
                building_levels={"fields": 1, "barracks": 1, "ritual_site": 1, "city_defense": 1},
                support_by_faction=_support_for_owner(faction_ids, owner_faction_id),
                local_factions=["local_autonomy"],
                traits=traits,
                troop_features=[troop_feature],
                settlement=_pick_city_settlement(
                    rng,
                    level=level,
                    defense=defense,
                    is_capital=is_capital,
                ),
                cannon_stock=1 if is_capital else 0,
            )
        )

    for nation_index, nation in enumerate(nations):
        owned = [city for city in cities if city.owner_faction_id == major_faction_ids[nation_index]]
        place_configured_veins(owned, dict(nation.get("veins") or {}), rng)
    independent_cities = [city for city in cities if city.owner_faction_id.startswith("neutral_city_state_")]
    place_random_faction_veins(independent_cities, rng, minimum=0, maximum=2)

    contract = dict(contract)
    contract["mode"] = TRUE_CAMPAIGN_MODE
    contract.setdefault("opening_hero_fill", "quota")
    world = WorldState(
        seed=int(seed),
        current_month=1,
        nodes=nodes,
        cities=cities,
        factions=factions,
        event_log=[
            EventLogEntry(
                month=1,
                category="world",
                message=f"{contract.get('name') or '五国争衡'}开战。",
                visibility="player_visible",
            )
        ],
        memory_tags=["campaign_started", "true_campaign"],
        campaign_contract=contract,
    )
    return _finalize_generated_world(world)


def generate_random_world(
    *,
    seed: int,
    city_count: int = 8,
    faction_count: int = 2,
    neutral_city_states: bool = False,
    campaign_contract: dict[str, Any] | None = None,
) -> WorldState:
    contract = dict(campaign_contract or {})
    if str(contract.get("mode") or "") == "true_campaign":
        return generate_true_campaign_world(seed=seed, campaign_contract=contract)
    if contract:
        city_count = int(contract.get("city_count", city_count))
        faction_count = int(contract.get("major_faction_count", faction_count))
        if "neutral_city_state_count" in contract:
            neutral_city_states = int(contract["neutral_city_state_count"]) > 0
    from wujiang.strategic.catalog import MAX_WORLD_CITIES

    city_count = min(MAX_WORLD_CITIES, int(city_count))
    if city_count < 2:
        raise StrategyError("随机战略地图至少需要 2 座城市。")
    if faction_count < 1:
        raise StrategyError("随机战略地图至少需要 1 个势力。")
    if faction_count > city_count:
        raise StrategyError("势力数量不能超过城市数量。")

    rng = random.Random(int(seed))
    major_faction_ids = [f"faction_{index}" for index in range(1, faction_count + 1)]
    neutral_faction_ids = [
        f"neutral_city_state_{index}"
        for index in range(faction_count + 1, city_count + 1)
    ] if neutral_city_states else []
    faction_ids = [*major_faction_ids, *neutral_faction_ids]
    nodes: list[MapNode] = []
    cities: list[City] = []
    connections: dict[str, set[str]] = {}
    coordinates = _place_city_coordinates(city_count, rng, capital_count=faction_count)

    for index in range(1, city_count + 1):
        node_id = f"node_{index}"
        connections[node_id] = set()
        x, y = coordinates[index]
        nodes.append(
            MapNode(
                node_id=node_id,
                name=_city_name(index),
                node_type="city",
                x=int(round(x)),
                y=int(round(y)),
                traits=[],
            )
        )

    # Geographic MST keeps the map connected without the numbered backbone
    # stretching long chords across the middle of the map.
    for left_index, right_index in _mst_connection_pairs(city_count, coordinates):
        left = f"node_{left_index}"
        right = f"node_{right_index}"
        connections[left].add(right)
        connections[right].add(left)

    for node in nodes:
        node.connected_node_ids = sorted(connections[node.node_id])

    factions: list[Faction] = []
    for index, faction_id in enumerate(major_faction_ids, start=1):
        factions.append(
            Faction(
                faction_id=faction_id,
                name=f"第{index}势力",
                is_ai=index != 1,
                capital_city_id=f"city_{index}",
                resources=ResourceBundle(food=300, money=300, population=0, ether=50, troops=200),
                faction_type="major",
            )
        )

    if neutral_city_states:
        for index in range(faction_count + 1, city_count + 1):
            faction_id = f"neutral_city_state_{index}"
            city_name = _city_name(index)
            governor_name = NEUTRAL_GOVERNOR_NAMES[(index - faction_count - 1) % len(NEUTRAL_GOVERNOR_NAMES)]
            factions.append(
                Faction(
                    faction_id=faction_id,
                    name=f"{city_name}城邦",
                    is_ai=True,
                    capital_city_id=f"city_{index}",
                    resources=ResourceBundle(food=160, money=120, population=0, ether=0, troops=120),
                    faction_type="neutral_city_state",
                    governor_name=governor_name,
                    relations={major_faction_id: 0 for major_faction_id in major_faction_ids},
                    influence_by_faction={major_faction_id: 0 for major_faction_id in major_faction_ids},
                )
            )

    for index in range(1, city_count + 1):
        owner_faction_id = (
            major_faction_ids[index - 1]
            if index <= faction_count
            else (f"neutral_city_state_{index}" if neutral_city_states else major_faction_ids[(index - 1) % faction_count])
        )
        level = 1 + (1 if index <= faction_count else rng.randint(0, 2))
        population = rng.randint(800, 1800) * level
        troops = rng.randint(180, 420) * level
        defense = rng.randint(2, 6) + level
        troop_feature = CITY_TROOP_FEATURES[(index - 1) % len(CITY_TROOP_FEATURES)]
        cities.append(
            City(
                city_id=f"city_{index}",
                node_id=f"node_{index}",
                name=_city_name(index),
                owner_faction_id=owner_faction_id,
                level=level,
                resources=ResourceBundle(
                    food=rng.randint(500, 900) * level,
                    money=rng.randint(300, 700) * level,
                    population=population,
                    ether=rng.randint(20, 80) * level,
                    troops=troops,
                ),
                defense=defense,
                governor_id=(f"officer:neutral_city_state_{index}:governor" if neutral_city_states and index > faction_count else None),
                buildings=["政厅", "fields", "barracks", "ritual_site", "city_defense"],
                building_levels={"fields": 1, "barracks": 1, "ritual_site": 1, "city_defense": 1},
                support_by_faction=_support_for_owner(faction_ids, owner_faction_id),
                local_factions=["local_autonomy"],
                traits=(["主城候选"] if index <= faction_count else (["中立城邦"] if neutral_city_states else [])),
                troop_features=[troop_feature],
                settlement=_pick_city_settlement(
                    rng,
                    level=level,
                    defense=defense,
                    is_capital=index <= faction_count,
                ),
                cannon_stock=1 if index <= faction_count else 0,
            )
        )

    from wujiang.strategic.rare_resources import scatter_random_campaign_veins

    world = WorldState(
        seed=int(seed),
        current_month=1,
        nodes=nodes,
        cities=cities,
        factions=factions,
        event_log=[
            EventLogEntry(
                month=1,
                category="world",
                message="英灵城邦战役开始。",
                visibility="player_visible",
            )
        ],
        memory_tags=["campaign_started"],
        campaign_contract=contract,
    )
    scatter_random_campaign_veins(world, rng)
    return _finalize_generated_world(world)
