from __future__ import annotations

import random
from typing import Any

from wujiang.strategy.models import City, EventLogEntry, Faction, MapNode, ResourceBundle, StrategyError, WorldState


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
)


def _city_name(index: int) -> str:
    base = CITY_NAME_PARTS[(index - 1) % len(CITY_NAME_PARTS)]
    return f"{base}城"


def _support_for_owner(faction_ids: list[str], owner_faction_id: str) -> dict[str, int]:
    support: dict[str, int] = {}
    for faction_id in faction_ids:
        support[faction_id] = 70 if faction_id == owner_faction_id else 35
    support["local_autonomy"] = 45
    return support


def generate_random_world(
    *,
    seed: int,
    city_count: int = 8,
    faction_count: int = 2,
    neutral_city_states: bool = False,
    campaign_contract: dict[str, Any] | None = None,
) -> WorldState:
    if city_count < 2:
        raise StrategyError("随机战略地图至少需要 2 座城市。")
    if faction_count < 1:
        raise StrategyError("随机战略地图至少需要 1 个势力。")
    if faction_count > city_count:
        raise StrategyError("势力数量不能超过城市数量。")
    if neutral_city_states and city_count - faction_count <= faction_count:
        raise StrategyError("中立城邦数量必须多于玩家与主要 AI 的起始城市总数。")

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

    for index in range(1, city_count + 1):
        node_id = f"node_{index}"
        connections[node_id] = set()
        nodes.append(
            MapNode(
                node_id=node_id,
                name=_city_name(index),
                node_type="city",
                x=rng.randint(0, 100),
                y=rng.randint(0, 100),
                traits=[],
            )
        )

    # A path guarantees connectivity; extra local links make the graph less linear.
    for index in range(1, city_count):
        left = f"node_{index}"
        right = f"node_{index + 1}"
        connections[left].add(right)
        connections[right].add(left)
    for index in range(1, city_count + 1):
        if rng.random() < 0.45:
            target = rng.randint(1, city_count)
            if target != index:
                left = f"node_{index}"
                right = f"node_{target}"
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
                defense=rng.randint(2, 6) + level,
                governor_id=(f"officer:neutral_city_state_{index}:governor" if neutral_city_states and index > faction_count else None),
                buildings=["政厅", "fields", "barracks", "ritual_site"],
                building_levels={"fields": 1, "barracks": 1, "ritual_site": 1},
                support_by_faction=_support_for_owner(faction_ids, owner_faction_id),
                local_factions=["local_autonomy"],
                traits=(["主城候选"] if index <= faction_count else (["中立城邦"] if neutral_city_states else [])),
                troop_features=[troop_feature],
            )
        )

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
        campaign_contract=dict(campaign_contract or {}),
    )
    from wujiang.strategy.story import open_monthly_story_events

    from wujiang.strategy.heroes import ensure_strategic_hero_system
    from wujiang.strategy.offices import ensure_office_system

    return ensure_strategic_hero_system(ensure_office_system(open_monthly_story_events(world)))
