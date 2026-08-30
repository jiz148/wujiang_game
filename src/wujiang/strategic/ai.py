from __future__ import annotations

import copy

from wujiang.strategic.administration import (
    BUILDING_PROJECTS,
    GARRISON_LEVY,
    city_building_max_level,
    construct_city_building,
    increase_city_troops,
)
from wujiang.strategic.battles import (
    MIN_ATTACK_TROOPS,
    city_attack_commitment,
    declare_city_attack,
    declare_strategic_battle,
    hero_is_stationed_in_city,
)
from wujiang.strategic.ai_goals import (
    ensure_ai_strategic_goal,
    preferred_attack_for_goal,
    preferred_policy_for_goal,
    update_ai_strategic_goal,
)
from wujiang.strategic.command import FACTION_MONTHLY_COMMAND_POINTS
from wujiang.strategic.exile import faction_is_exiled
from wujiang.strategic.heroes import (
    active_strategic_hero_codes_for_faction,
    appoint_strategic_hero_to_office,
    assign_strategic_hero_duty,
    hero_ritual_capacity,
    perform_hero_ritual,
    set_strategic_defender_hero,
    strategic_heroes_for_faction_public,
)
from wujiang.strategic.models import City, EventLogEntry, Faction, StrategyError, WorldState
from wujiang.strategic.neutral_city_states import incitement_attack_pair
from wujiang.strategic.offices import ai_office_for_action, ensure_office_system
from wujiang.strategic.political_ai import ai_peace_treasury_reserve, apply_major_political_ai_actions
from wujiang.strategic.relics import (
    bind_relic,
    ensure_relic_system,
    relic_system_enabled,
    relic_system_public,
    release_relic,
    repair_relic,
    search_relic,
    transfer_relic,
)
from wujiang.strategic.simulation import POLICIES, rebellion_risk
from wujiang.strategic.story import choose_ai_story_choice, pending_story_event_for_faction, resolve_story_event
from wujiang.strategic.tactics import TACTIC_TECH_TREE, set_city_policy, unlock_tactic_tech
from wujiang.strategic.world_crisis import (
    SNOW_GHOST_CRISIS_ID,
    _in_mobilization_window,
    resolve_world_crisis_choice,
    set_world_crisis_showdown_resolution,
    world_crisis_pair_key,
)


def _clone_world(world: WorldState) -> WorldState:
    return WorldState.from_dict(copy.deepcopy(world.to_dict()))


def _policy_containing(fragment: str, *, fallback: str = "") -> str:
    for policy in sorted(POLICIES):
        if fragment in policy:
            return policy
    return fallback or sorted(POLICIES)[0]


POLICY_STABLE = _policy_containing("稳定")
POLICY_FOOD = _policy_containing("粮食", fallback=POLICY_STABLE)
POLICY_MONEY = _policy_containing("金钱", fallback=POLICY_STABLE)
POLICY_RECRUIT = _policy_containing("征兵", fallback=POLICY_STABLE)
POLICY_ETHER = _policy_containing("以太", fallback=POLICY_STABLE)
POLICY_DEFENSE = _policy_containing("城防", fallback=POLICY_STABLE)


POLICY_SUPPRESSION = _policy_containing("镇压", fallback=POLICY_STABLE)
POLICY_AUTONOMY = _policy_containing("自治", fallback=POLICY_STABLE)
RELIC_AI_PROACTIVE_SEARCH_MONTH = 8


def _cities_for_faction(world: WorldState, faction_id: str) -> list[City]:
    return [city for city in world.cities if city.owner_faction_id == faction_id]


def _nodes_by_city(world: WorldState) -> dict[str, set[str]]:
    nodes_by_id = {node.node_id: node for node in world.nodes}
    city_nodes = {city.city_id: city.node_id for city in world.cities}
    adjacent: dict[str, set[str]] = {}
    for city in world.cities:
        node = nodes_by_id.get(city.node_id)
        if node is None:
            adjacent[city.city_id] = set()
            continue
        adjacent[city.city_id] = {
            other_city_id
            for other_city_id, other_node_id in city_nodes.items()
            if other_node_id in set(node.connected_node_ids)
        }
    return adjacent


def _city_food_need(city: City) -> int:
    return max(1, city.resources.population // 80 + city.resources.troops // 120)


def _city_policy_urgency(city: City, faction: Faction) -> int:
    food_shortage = city.resources.food < _city_food_need(city)
    risk = rebellion_risk(city, food_shortage=food_shortage)
    if food_shortage:
        return 1000 + risk
    if risk >= 55:
        return 900 + risk
    if risk >= 45:
        return 800 + risk
    if city.resources.troops < max(MIN_ATTACK_TROOPS * 3, city.resources.population // 25):
        return 700
    if city.resources.food < max(120, city.resources.population // 8):
        return 600
    if faction.resources.money < 120 or faction.resources.ether < 20:
        return 500
    if city.defense < city.level + 3:
        return 400
    return 100


def _choose_city_policy(city: City, faction: Faction) -> str:
    food_shortage = city.resources.food < _city_food_need(city)
    risk = rebellion_risk(city, food_shortage=food_shortage)
    minimum_security_troops = max(MIN_ATTACK_TROOPS * 3, city.resources.population // 25)
    if food_shortage:
        return POLICY_FOOD
    if risk >= 55:
        if city.resources.troops >= minimum_security_troops:
            return POLICY_SUPPRESSION
        return POLICY_AUTONOMY
    if risk >= 45:
        if city.resources.troops < minimum_security_troops:
            return POLICY_RECRUIT
        return POLICY_AUTONOMY
    if city.resources.troops < minimum_security_troops:
        return POLICY_RECRUIT
    if city.resources.food < max(120, city.resources.population // 8):
        return POLICY_FOOD
    if faction.resources.money < 120:
        return POLICY_MONEY
    if faction.resources.ether < 20:
        return POLICY_ETHER
    if city.defense < city.level + 3:
        return POLICY_DEFENSE
    return POLICY_STABLE


def _best_policy_city(world: WorldState, faction: Faction) -> tuple[City, str] | None:
    candidates: list[tuple[int, str, City, str]] = []
    for city in _cities_for_faction(world, faction.faction_id):
        policy = _choose_city_policy(city, faction)
        if city.policy != policy:
            candidates.append((_city_policy_urgency(city, faction), city.city_id, city, policy))
    if not candidates:
        return None
    _, _, city, policy = max(candidates, key=lambda item: (item[0], item[1]))
    return city, policy


def _first_affordable_tech(faction: Faction) -> str | None:
    unlocked = set(faction.tactic_techs)
    for tech in TACTIC_TECH_TREE:
        if tech.tech_id in unlocked:
            continue
        if any(prereq not in unlocked for prereq in tech.prerequisites):
            continue
        if faction.resources.money >= tech.money_cost and faction.resources.ether >= tech.ether_cost:
            return tech.tech_id
    return None


def _best_attack(world: WorldState, faction: Faction) -> tuple[str, str] | None:
    adjacent = _nodes_by_city(world)
    cities_by_id = {city.city_id: city for city in world.cities}
    sources = sorted(
        _cities_for_faction(world, faction.faction_id),
        key=lambda city: (-city.resources.troops, city.city_id),
    )
    for source in sources:
        if source.resources.troops < MIN_ATTACK_TROOPS * 6:
            continue
        targets = [
            cities_by_id[target_id]
            for target_id in sorted(adjacent.get(source.city_id, set()))
            if cities_by_id[target_id].owner_faction_id != faction.faction_id
        ]
        viable_targets = [
            target
            for target in targets
            if city_attack_commitment(source.resources.troops) >= (
                target.resources.troops + target.defense * 80 + int(target.support_by_faction.get(target.owner_faction_id, 50)) * 3
            )
        ]
        if viable_targets:
            target = min(viable_targets, key=lambda city: (city.resources.troops + city.defense * 80, city.city_id))
            return source.city_id, target.city_id
    return None


def _best_attack_against_cities(
    world: WorldState,
    faction: Faction,
    target_city_ids: set[str],
) -> tuple[str, str] | None:
    adjacent = _nodes_by_city(world)
    cities_by_id = {city.city_id: city for city in world.cities}
    candidates: list[tuple[int, int, str, str]] = []
    altar_progress = {
        str(row["city_id"]): int(row.get("bound_count", 0))
        for row in relic_system_public(world).get("altars", [])
    }
    for source in _cities_for_faction(world, faction.faction_id):
        if source.resources.troops < MIN_ATTACK_TROOPS * 6:
            continue
        for target_id in sorted(adjacent.get(source.city_id, set()).intersection(target_city_ids)):
            target = cities_by_id[target_id]
            if target.owner_faction_id == faction.faction_id:
                continue
            defense_score = (
                target.resources.troops
                + target.defense * 80
                + int(target.support_by_faction.get(target.owner_faction_id, 50)) * 3
            )
            if city_attack_commitment(source.resources.troops) >= defense_score:
                candidates.append(
                    (
                        altar_progress.get(target_id, 0),
                        -defense_score,
                        source.city_id,
                        target.city_id,
                    )
                )
    if not candidates:
        return None
    _, _, source_city_id, target_city_id = max(candidates)
    return source_city_id, target_city_id


def _relic_counter_attack(world: WorldState, faction: Faction) -> tuple[str, str] | None:
    if not relic_system_enabled(world):
        return None
    target_city_ids = {
        str(row["city_id"])
        for row in relic_system_public(world).get("altars", [])
        if row.get("owner_faction_id") != faction.faction_id
        and int(row.get("bound_count", 0)) >= 1
    }
    if not target_city_ids:
        return None
    return _best_attack_against_cities(world, faction, target_city_ids)


def _owned_relic_altar_city_id(world: WorldState, faction_id: str) -> str | None:
    candidates = [
        row
        for row in relic_system_public(world).get("altars", [])
        if row.get("owner_faction_id") == faction_id
    ]
    if not candidates:
        return None
    capital_id = next(
        (faction.capital_city_id for faction in world.factions if faction.faction_id == faction_id),
        "",
    )
    chosen = max(
        candidates,
        key=lambda row: (
            int(row.get("capacity", 1)) - int(row.get("bound_count", 0)),
            1 if str(row.get("city_id") or "") == str(capital_id or "") else 0,
            str(row.get("city_id") or ""),
        ),
    )
    return str(chosen["city_id"])


def _owned_city_distances(world: WorldState, faction_id: str, target_city_id: str) -> dict[str, int]:
    adjacent = _nodes_by_city(world)
    owned_ids = {
        city.city_id
        for city in _cities_for_faction(world, faction_id)
    }
    if target_city_id not in owned_ids:
        return {}
    distances = {target_city_id: 0}
    frontier = [target_city_id]
    while frontier:
        current = frontier.pop(0)
        for neighbor in sorted(adjacent.get(current, set()).intersection(owned_ids)):
            if neighbor in distances:
                continue
            distances[neighbor] = distances[current] + 1
            frontier.append(neighbor)
    return distances


def _altar_faces_overwhelming_threat(world: WorldState, faction_id: str, city_id: str) -> bool:
    city = next(item for item in world.cities if item.city_id == city_id)
    adjacent = _nodes_by_city(world)
    defense_score = (
        city.resources.troops
        + city.defense * 80
        + int(city.support_by_faction.get(faction_id, 50)) * 3
    )
    enemies = [
        item
        for item in world.cities
        if item.city_id in adjacent.get(city_id, set())
        and item.owner_faction_id != faction_id
    ]
    return any(
        enemy.resources.troops >= MIN_ATTACK_TROOPS * 6
        and city_attack_commitment(enemy.resources.troops) * 2 >= defense_score * 3
        for enemy in enemies
    )


def _relic_maintenance_reserve_by_city(world: WorldState, faction_id: str) -> dict[str, int]:
    if not relic_system_enabled(world):
        return {}
    return {
        str(row["city_id"]): int(row.get("monthly_maintenance_ether", 0))
        for row in relic_system_public(world).get("altars", [])
        if row.get("owner_faction_id") == faction_id and int(row.get("bound_count", 0)) > 0
    }


def _apply_relic_ai_action(
    world: WorldState,
    *,
    faction_id: str,
    command_remaining: int,
) -> tuple[WorldState, int, str | None, str | None]:
    if command_remaining < 1 or not relic_system_enabled(world):
        return world, command_remaining, None, None
    next_world = ensure_relic_system(world)
    public = relic_system_public(next_world)
    intel = public.get("intel_by_faction", {}).get(faction_id, {})
    altar_city_id = _owned_relic_altar_city_id(next_world, faction_id)
    if altar_city_id is None:
        return next_world, command_remaining, None, None

    chosen_type = ""
    chosen: dict = {}
    reason = ""
    release_options = [
        row for row in intel.get("release_options", []) if row.get("available")
    ]
    threatened_release = next(
        (
            row for row in release_options
            if str(row.get("city_id")) == altar_city_id
            and _altar_faces_overwhelming_threat(next_world, faction_id, altar_city_id)
        ),
        None,
    )
    binding_options = [
        row for row in intel.get("binding_options", []) if row.get("available")
    ]
    repair_options = [
        row for row in intel.get("repair_options", []) if row.get("available")
    ]
    distances = _owned_city_distances(next_world, faction_id, altar_city_id)
    transfer_candidates: list[tuple[int, str, str, dict]] = []
    for row in intel.get("transfer_options", []):
        if not row.get("available"):
            continue
        for target in row.get("targets", []):
            target_id = str(target.get("city_id") or "")
            if target_id in distances:
                transfer_candidates.append(
                    (
                        distances[target_id],
                        str(row.get("relic_id") or ""),
                        target_id,
                        row,
                    )
                )
    search_candidates = (
        [
            (row, origin)
            for row in intel.get("search_options", [])
            for origin in row.get("origins", [])
            if origin.get("available")
        ]
        if next_world.current_month >= RELIC_AI_PROACTIVE_SEARCH_MONTH
        else []
    )

    if threatened_release is not None:
        chosen_type, chosen = "release_relic", threatened_release
        reason = "祭坛面临相邻压倒性兵力且无可靠守势，主动释放以避免敌方直接夺取。"
    elif binding_options:
        chosen_type, chosen = "bind_relic", sorted(
            binding_options,
            key=lambda row: (int(row.get("maintenance_ether_cost", 0)), str(row.get("relic_id") or "")),
        )[0]
        reason = "完整圣物已抵达己方祭坛，开始公开的三次连续维护竞速。"
    elif repair_options:
        chosen_type, chosen = "repair_relic", sorted(
            repair_options,
            key=lambda row: (
                0 if str(row.get("city_id")) == altar_city_id else 1,
                distances.get(str(row.get("city_id")), 999),
                str(row.get("relic_id") or ""),
            ),
        )[0]
        reason = "已发现圣物受损，先按公开成本修复以满足祭坛绑定条件。"
    elif transfer_candidates:
        _, _, target_city_id, row = min(transfer_candidates)
        chosen_type = "transfer_relic"
        chosen = {**row, "target_city_id": target_city_id}
        reason = "沿一条己方相邻路线把圣物向祭坛转移。"
    elif search_candidates:
        row, origin = sorted(
            search_candidates,
            key=lambda item: (
                distances.get(str(item[1].get("city_id")), 999),
                str(item[0].get("relic_id") or ""),
                str(item[1].get("hero_code") or ""),
            ),
        )[0]
        chosen_type = "search_relic"
        chosen = {**row, **origin}
        reason = "使用本势力已经发现的线索与可行动英灵发起搜索。"
    else:
        return next_world, command_remaining, None, None

    office = ai_office_for_action(
        next_world,
        faction_id=faction_id,
        action_type=chosen_type,
        payload={
            "city_id": str(chosen.get("city_id") or ""),
            "relic_id": str(chosen.get("relic_id") or ""),
            "altar_id": str(chosen.get("altar_id") or ""),
            "target_city_id": str(chosen.get("target_city_id") or ""),
        },
    )
    if office is None:
        return next_world, command_remaining, None, None

    relic_id = str(chosen["relic_id"])
    if chosen_type == "release_relic":
        next_world = release_relic(
            next_world,
            faction_id=faction_id,
            relic_id=relic_id,
            issuer_office_id=office.office_id,
        )
        action = f"relic:release:{relic_id}"
    elif chosen_type == "bind_relic":
        next_world = bind_relic(
            next_world,
            faction_id=faction_id,
            relic_id=relic_id,
            altar_id=str(chosen["altar_id"]),
            issuer_office_id=office.office_id,
        )
        action = f"relic:bind:{relic_id}:{chosen['altar_id']}"
    elif chosen_type == "repair_relic":
        next_world = repair_relic(
            next_world,
            faction_id=faction_id,
            relic_id=relic_id,
            issuer_office_id=office.office_id,
        )
        action = f"relic:repair:{relic_id}"
    elif chosen_type == "transfer_relic":
        next_world = transfer_relic(
            next_world,
            faction_id=faction_id,
            relic_id=relic_id,
            target_city_id=str(chosen["target_city_id"]),
            issuer_office_id=office.office_id,
        )
        action = f"relic:transfer:{relic_id}->{chosen['target_city_id']}"
    else:
        next_world = search_relic(
            next_world,
            faction_id=faction_id,
            relic_id=relic_id,
            hero_code=str(chosen["hero_code"]),
            city_id=str(chosen["city_id"]),
            issuer_office_id=office.office_id,
        )
        action = f"relic:search:{relic_id}:{chosen['hero_code']}"

    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="strategy_ai_relic_decision",
            message=f"{faction_id}执行圣物行动：{reason}",
            related_ids=[faction_id, office.office_id, action],
        )
    )
    return next_world, command_remaining - 1, action, f"{office.office_id}:{action}"


def _best_summon_hero_code(world: WorldState, faction: Faction) -> str | None:
    candidates = [
        hero
        for hero in strategic_heroes_for_faction_public(world, faction.faction_id)
        if hero.get("status") == "available"
        and faction.resources.ether >= int(hero.get("summon_cost_ether", 0) or 0)
    ]
    if not candidates:
        return None
    chosen = max(
        candidates,
        key=lambda hero: (
            int(hero.get("level", 1) or 1),
            -int(hero.get("summon_cost_ether", 0) or 0),
            str(hero.get("code") or ""),
        ),
    )
    return str(chosen.get("code") or "")


def _has_configured_defender(world: WorldState, faction_id: str) -> bool:
    return any(
        hero.get("defender_assigned")
        for hero in strategic_heroes_for_faction_public(world, faction_id)
    )


def _world_crisis_ai_priority(
    world: WorldState,
    faction_id: str,
    strategic_goal: dict | None,
) -> tuple[str, str]:
    crisis = next(
        (item for item in world.world_crises if item.crisis_id == SNOW_GHOST_CRISIS_ID),
        None,
    )
    owned_city_ids = {city.city_id for city in _cities_for_faction(world, faction_id)}
    threatened_owned = owned_city_ids.intersection(set(crisis.threatened_city_ids if crisis else []))
    if threatened_owned:
        names = "、".join(
            city.name for city in world.cities if city.city_id in threatened_owned
        )
        return "survival", f"{names}正受雪鬼威胁，先保住现有领地与联军防线。"
    if (strategic_goal or {}).get("goal_type") == "capture_city":
        return "expansion", "当前公开目标是扩张，且本势力城市未直接受袭，优先保留军需争取扩张窗口。"
    return "mainline", "当前没有直接生存危机，投入北境主线可积累公开功绩并改善最终评议。"


def _record_world_crisis_ai_avoid(
    world: WorldState,
    *,
    faction_id: str,
    priority: str,
    rationale: str,
) -> None:
    crisis = next(
        (item for item in world.world_crises if item.crisis_id == SNOW_GHOST_CRISIS_ID),
        None,
    )
    if crisis is None or any(
        int(item.get("month", 0)) == world.current_month
        and item.get("faction_id") == faction_id
        for item in crisis.decisions
    ):
        return
    faction = next(item for item in world.factions if item.faction_id == faction_id)
    decision_id = f"crisis-decision:{world.current_month}:{len(crisis.decisions) + 1}"
    crisis.decisions.append(
        {
            "id": decision_id,
            "month": world.current_month,
            "faction_id": faction_id,
            "choice_id": "avoid",
            "target_faction_id": "",
            "contribution_delta": 0,
            "target_contribution_delta": 0,
            "relation_delta": 0,
            "reputation_delta": 0,
            "issuer_office_id": "",
            "decision_origin": "ai",
            "ai_priority": priority,
            "ai_rationale": rationale,
        }
    )
    crisis.history.append(
        {
            "month": world.current_month,
            "stage": "mobilization",
            "event": "world_crisis_ai_avoided",
            "faction_id": faction_id,
            "ai_priority": priority,
        }
    )
    world.event_log.append(
        EventLogEntry(
            month=world.current_month,
            category="world_crisis_ai_avoided",
            message=f"{faction.name}公开选择暂不投入北境动员：{rationale}",
            related_ids=[crisis.crisis_id, faction_id, decision_id],
        )
    )


def _world_crisis_ai_reserve(world: WorldState) -> tuple[int, int]:
    crisis = next(
        (item for item in world.world_crises if item.crisis_id == SNOW_GHOST_CRISIS_ID),
        None,
    )
    if crisis is None or crisis.stage not in {"omen", "border_pressure", "spread", "mobilization"}:
        return 0, 0
    return 50, 100


def _apply_world_crisis_ai_choice(
    world: WorldState,
    *,
    faction_id: str,
    strategic_goal: dict | None,
    command_remaining: int,
) -> tuple[WorldState, int, str | None]:
    crisis = next(
        (item for item in world.world_crises if item.crisis_id == SNOW_GHOST_CRISIS_ID),
        None,
    )
    if (
        crisis is None
        or crisis.stage != "mobilization"
        or not _in_mobilization_window(world, crisis)
        or any(
            int(item.get("month", 0)) == world.current_month
            and item.get("faction_id") == faction_id
            for item in crisis.decisions
        )
    ):
        return world, command_remaining, None

    priority, rationale = _world_crisis_ai_priority(world, faction_id, strategic_goal)
    faction = next(item for item in world.factions if item.faction_id == faction_id)
    other_majors = sorted(
        (
            item for item in world.factions
            if item.is_major and item.faction_id != faction_id
        ),
        key=lambda item: (
            -int(crisis.cooperation_targets_by_faction.get(item.faction_id) == faction_id),
            -int(item.faction_id in {
                city.owner_faction_id
                for city in world.cities
                if city.city_id in crisis.threatened_city_ids
            }),
            -int(faction.relations.get(item.faction_id, 0)),
            item.faction_id,
        ),
    )
    active_partners = [
        item
        for item in other_majors
        if world_crisis_pair_key(faction_id, item.faction_id) in crisis.cooperation_pairs
        and world_crisis_pair_key(faction_id, item.faction_id) not in crisis.broken_cooperation_pairs
    ]
    incoming = [
        item for item in other_majors
        if crisis.cooperation_targets_by_faction.get(item.faction_id) == faction_id
        and world_crisis_pair_key(faction_id, item.faction_id) not in crisis.broken_cooperation_pairs
    ]

    choice_id = ""
    target_faction_id = ""
    if command_remaining >= 1:
        if priority == "expansion":
            if active_partners:
                choice_id = "betray"
                target_faction_id = max(
                    active_partners,
                    key=lambda item: (
                        int(crisis.contributions_by_faction.get(item.faction_id, 0)),
                        item.faction_id,
                    ),
                ).faction_id
                rationale = (
                    f"{rationale} 与目标的危机合作已经成立，背约可转移公开贡献，"
                    "但会承担关系与信誉惩罚。"
                )
        elif incoming and faction.resources.food >= 80 and faction.resources.money >= 40:
            choice_id = "cooperate"
            target_faction_id = incoming[0].faction_id
            rationale = f"{rationale} 对方已公开承诺，响应可立即成立合作并获得双向奖励。"
        elif priority == "survival" and world.current_month == crisis.stage_started_month and other_majors and faction.resources.food >= 80 and faction.resources.money >= 40:
            choice_id = "cooperate"
            target_faction_id = other_majors[0].faction_id
            rationale = f"{rationale} 主动寻求合作比单独承担前线压力更安全。"
        elif faction.resources.food >= 100 and faction.resources.money >= 50:
            choice_id = "contribute"
            rationale = f"{rationale} 当前资源足以支付 100 粮食与 50 金钱。"

    if not choice_id:
        if command_remaining < 1:
            rationale = f"{rationale} 本月已无可用军令，因此公开回避危机行动。"
        elif priority != "expansion":
            rationale = f"{rationale} 当前资源不足以支付任何合法动员选项，因此公开回避。"
        _record_world_crisis_ai_avoid(
            world,
            faction_id=faction_id,
            priority=priority,
            rationale=rationale,
        )
        return world, command_remaining, f"crisis:{priority}:avoid"

    lord = next(
        (
            office for office in world.offices
            if office.faction_id == faction_id
            and office.office_type == "lord"
            and office.status == "active"
        ),
        None,
    )
    if lord is None:
        _record_world_crisis_ai_avoid(
            world,
            faction_id=faction_id,
            priority=priority,
            rationale=f"{rationale} 当前没有在任主公，无法合法签发危机选择。",
        )
        return world, command_remaining, f"crisis:{priority}:avoid"
    resolved = resolve_world_crisis_choice(
        world,
        faction_id=faction_id,
        choice_id=choice_id,
        target_faction_id=target_faction_id,
        issuer_office_id=lord.office_id,
        decision_origin="ai",
        ai_priority=priority,
        ai_rationale=rationale,
    )
    return resolved, command_remaining - 1, f"crisis:{priority}:{choice_id}"


def apply_strategy_ai_showdown_action(
    world: WorldState,
    *,
    controlled_faction_ids: set[str] | frozenset[str] | list[str] | tuple[str, ...],
) -> WorldState:
    controlled = {str(faction_id) for faction_id in controlled_faction_ids}
    crisis = next(
        (
            item for item in world.world_crises
            if item.crisis_id == SNOW_GHOST_CRISIS_ID and item.stage == "showdown"
        ),
        None,
    )
    if (
        crisis is None
        or not crisis.showdown_leader_faction_id
        or crisis.showdown_leader_faction_id in controlled
    ):
        return world
    battle = next(
        (item for item in world.pending_battles if item.battle_id == crisis.showdown_battle_id),
        None,
    )
    if battle is None or battle.status != "pending" or battle.battle_room_id:
        return world
    lord = next(
        (
            office for office in world.offices
            if office.faction_id == crisis.showdown_leader_faction_id
            and office.office_type == "lord"
            and office.status == "active"
        ),
        None,
    )
    if lord is None:
        return world
    leader = next(
        item for item in world.factions
        if item.faction_id == crisis.showdown_leader_faction_id
    )
    prepared = _clone_world(world)
    prepared_crisis = next(
        item for item in prepared.world_crises if item.crisis_id == crisis.crisis_id
    )
    prepared_crisis.history.append(
        {
            "month": prepared.current_month,
            "stage": "showdown",
            "event": "world_crisis_ai_showdown_selected",
            "faction_id": leader.faction_id,
            "resolution_mode": "quick",
            "ai_priority": "mainline",
        }
    )
    prepared.event_log.append(
        EventLogEntry(
            month=prepared.current_month,
            category="world_crisis_ai_showdown_selected",
            message=f"{leader.name}作为联军领袖公开选择快速结算北境决战，以免主线阻塞最终评议。",
            related_ids=[crisis.crisis_id, battle.battle_id, leader.faction_id, lord.office_id],
        )
    )
    return set_world_crisis_showdown_resolution(
        prepared,
        faction_id=leader.faction_id,
        issuer_office_id=lord.office_id,
        resolution_mode="quick",
        auto_resolve=True,
    )


def _frontline_cities(world: WorldState, faction_id: str) -> list[City]:
    adjacent = _nodes_by_city(world)
    cities_by_id = {city.city_id: city for city in world.cities}
    owned = _cities_for_faction(world, faction_id)
    frontline: list[City] = []
    for city in owned:
        if any(
            cities_by_id[neighbor].owner_faction_id != faction_id
            for neighbor in adjacent.get(city.city_id, set())
            if neighbor in cities_by_id
        ):
            frontline.append(city)
    return sorted(frontline or owned, key=lambda city: (city.resources.troops, city.city_id))


def _station_ai_heroes(
    world: WorldState,
    *,
    faction_id: str,
    attack: tuple[str, str] | None,
) -> tuple[WorldState, list[str], list[str]]:
    lord = ai_office_for_action(
        world,
        faction_id=faction_id,
        action_type="assign_strategic_hero_duty",
        payload={},
    )
    if lord is None:
        return world, [], []
    serving = [
        hero
        for hero in world.strategic_heroes
        if hero.faction_id == faction_id and hero.status == "serving"
    ]
    if not serving:
        return world, [], []
    garrison_cities = _frontline_cities(world, faction_id)
    next_world = world
    actions: list[str] = []
    office_actions: list[str] = []
    for index, hero in enumerate(sorted(serving, key=lambda item: item.hero_code)):
        if attack is not None and index == 0:
            city_id = attack[0]
            assignment = "campaign"
        else:
            city_id = garrison_cities[index % len(garrison_cities)].city_id
            assignment = "garrison"
        if hero.city_id == city_id and hero.assignment_type in {assignment, "garrison", "campaign"}:
            continue
        try:
            next_world = assign_strategic_hero_duty(
                next_world,
                faction_id=faction_id,
                issuer_office_id=lord.office_id,
                hero_code=hero.hero_code,
                assignment_type=assignment,
                target_id=city_id,
            )
        except StrategyError:
            continue
        action = f"duty:{hero.hero_code}:{assignment}:{city_id}"
        actions.append(action)
        office_actions.append(f"{lord.office_id}:{action}")
        hero = next(item for item in next_world.strategic_heroes if item.hero_code == hero.hero_code)
    return next_world, actions, office_actions


def _apply_ai_city_development(
    world: WorldState,
    *,
    faction_id: str,
    command_remaining: int,
    attack_reserve: int,
) -> tuple[WorldState, int, list[str], list[str]]:
    if command_remaining < 1 or command_remaining - 1 < attack_reserve:
        return world, command_remaining, [], []
    cities = _frontline_cities(world, faction_id)
    if not cities:
        return world, command_remaining, [], []
    next_world = world
    actions: list[str] = []
    office_actions: list[str] = []

    levy_city = next(
        (
            city
            for city in cities
            if city.resources.troops < max(MIN_ATTACK_TROOPS * 4, city.resources.population // 20)
            and city.resources.population >= GARRISON_LEVY["population"]
            and city.resources.food >= GARRISON_LEVY["food"]
            and city.resources.money >= GARRISON_LEVY["money"]
        ),
        None,
    )
    if levy_city is not None:
        office = ai_office_for_action(
            next_world,
            faction_id=faction_id,
            action_type="increase_city_troops",
            payload={"city_id": levy_city.city_id},
        )
        if office is not None:
            try:
                next_world = increase_city_troops(
                    next_world,
                    faction_id=faction_id,
                    city_id=levy_city.city_id,
                    issuer_office_id=office.office_id,
                )
                action = f"levy:{levy_city.city_id}"
                actions.append(action)
                office_actions.append(f"{office.office_id}:{action}")
                return next_world, command_remaining - 1, actions, office_actions
            except StrategyError:
                pass

    for city in cities:
        for building_id in ("barracks", "ritual_site"):
            current_level = int(city.building_levels.get(building_id, 0))
            maximum_level = city_building_max_level(city, building_id)
            if current_level >= maximum_level or maximum_level <= 0:
                continue
            project = BUILDING_PROJECTS[building_id]
            next_level = current_level + 1
            if city.resources.money < int(project["money"]) * next_level or city.resources.food < int(project["food"]) * next_level:
                continue
            office = ai_office_for_action(
                next_world,
                faction_id=faction_id,
                action_type="construct_city_building",
                payload={"city_id": city.city_id},
            )
            if office is None:
                continue
            try:
                next_world = construct_city_building(
                    next_world,
                    faction_id=faction_id,
                    city_id=city.city_id,
                    building_id=building_id,
                    issuer_office_id=office.office_id,
                )
                action = f"build:{city.city_id}:{building_id}"
                actions.append(action)
                office_actions.append(f"{office.office_id}:{action}")
                return next_world, command_remaining - 1, actions, office_actions
            except StrategyError:
                continue
    return next_world, command_remaining, actions, office_actions


def apply_strategy_ai_monthly_actions(
    world: WorldState,
    *,
    controlled_faction_ids: set[str] | frozenset[str] | list[str] | tuple[str, ...],
    enable_attacks: bool = True,
    command_remaining_by_faction: dict[str, int] | None = None,
) -> WorldState:
    controlled = {str(faction_id) for faction_id in controlled_faction_ids}
    next_world = ensure_office_system(_clone_world(world))

    for faction_id in sorted(faction.faction_id for faction in next_world.factions):
        if faction_id in controlled or faction_is_exiled(next_world, faction_id):
            continue
        faction = next(faction for faction in next_world.factions if faction.faction_id == faction_id)
        if faction.is_world_crisis:
            continue
        if not _cities_for_faction(next_world, faction_id):
            continue

        pending_sources = {
            (battle.source_kind, battle.source_entity_id)
            for battle in next_world.pending_battles
            if battle.status == "pending"
        }
        siege_battle = next(
            (
                siege for siege in next_world.sieges
                if siege.status in {"breached", "battle_pending"}
                and siege.battle_trigger in {"assault", "breakout"}
                and faction_id in {siege.attacker_faction_id, siege.defender_faction_id}
                and ("siege", siege.siege_id) not in pending_sources
                and not any(
                    army.army_id in siege.attacker_army_ids and army.status == "retreating"
                    for army in next_world.armies
                )
            ),
            None,
        )
        encounter_battle = next(
            (
                encounter for encounter in next_world.encounters
                if encounter.status == "active"
                and len(encounter.faction_army_ids) == 2
                and faction_id in encounter.faction_army_ids
                and ("encounter", encounter.encounter_id) not in pending_sources
                and not any(
                    army.army_id in {army_id for ids in encounter.faction_army_ids.values() for army_id in ids}
                    and army.status == "retreating"
                    for army in next_world.armies
                )
            ),
            None,
        )
        if siege_battle is not None:
            next_world = declare_strategic_battle(
                next_world,
                faction_id=faction_id,
                source_kind="siege",
                source_entity_id=siege_battle.siege_id,
                resolution_mode="quick",
            )
        elif encounter_battle is not None:
            next_world = declare_strategic_battle(
                next_world,
                faction_id=faction_id,
                source_kind="encounter",
                source_entity_id=encounter_battle.encounter_id,
                resolution_mode="quick",
            )

        if faction.is_neutral_city_state:
            actions: list[str] = []
            office_actions: list[str] = []
            command_remaining = max(
                0,
                int((command_remaining_by_faction or {}).get(
                    faction_id,
                    FACTION_MONTHLY_COMMAND_POINTS,
                )),
            )
            command_start = command_remaining
            policy_choice = _best_policy_city(next_world, faction)
            if policy_choice is not None:
                city, policy = policy_choice
                office = ai_office_for_action(
                    next_world,
                    faction_id=faction_id,
                    action_type="set_city_policy",
                    payload={"city_id": city.city_id},
                )
                if office is not None:
                    next_world = set_city_policy(next_world, faction_id=faction_id, city_id=city.city_id, policy=policy)
                    action = f"policy:{city.city_id}:{policy}"
                    actions.append(action)
                    office_actions.append(f"{office.office_id}:{action}")
                    command_remaining -= 1

            attack = incitement_attack_pair(next_world, faction_id)
            if attack is not None and command_remaining >= 2:
                source_city_id, target_city_id = attack
                source = next(city for city in next_world.cities if city.city_id == source_city_id)
                attack_office = ai_office_for_action(
                    next_world,
                    faction_id=faction_id,
                    action_type="declare_attack",
                    payload={"source_city_id": source_city_id, "target_city_id": target_city_id},
                )
                if source.resources.troops >= MIN_ATTACK_TROOPS and attack_office is not None:
                    next_world = declare_city_attack(
                        next_world,
                        faction_id=faction_id,
                        source_city_id=source_city_id,
                        target_city_id=target_city_id,
                        resolution_mode="quick",
                        auto_resolve=True,
                        attacker_hero_codes=[],
                        attacker_office_id=attack_office.office_id,
                    )
                    neutral = next(item for item in next_world.factions if item.faction_id == faction_id)
                    neutral.incited_against_faction_id = None
                    neutral.incited_by_faction_id = None
                    action = f"incited_attack:{source_city_id}->{target_city_id}"
                    actions.append(action)
                    office_actions.append(f"{attack_office.office_id}:{action}")
                    command_remaining -= 2
                    next_world.event_log.append(
                        EventLogEntry(
                            month=next_world.current_month,
                            category="neutral_city_state_incitement_spent",
                            message=f"{neutral.name}响应教唆出兵，教唆意图已解除。",
                            related_ids=[faction_id, source_city_id, target_city_id],
                        )
                    )

            if office_actions:
                next_world.event_log.append(
                    EventLogEntry(
                        month=next_world.current_month,
                        category="strategy_ai_office_trace",
                        message=f"{faction_id} neutral governor decisions: {', '.join(office_actions)}.",
                        related_ids=[faction_id, *office_actions],
                    )
                )
            next_world.event_log.append(
                EventLogEntry(
                    month=next_world.current_month,
                    category="strategy_ai_plan",
                    message=(
                        f"{faction_id} neutral city-state plan "
                        f"({command_start - command_remaining}/{command_start} command): "
                        f"{', '.join(actions) if actions else 'defend and hold'}."
                    ),
                    related_ids=[faction_id, *actions],
                )
            )
            continue

        actions: list[str] = []
        office_actions: list[str] = []
        command_remaining = max(
            0,
            int((command_remaining_by_faction or {}).get(
                faction_id,
                FACTION_MONTHLY_COMMAND_POINTS,
            )),
        )
        command_start = command_remaining
        strategic_goal = ensure_ai_strategic_goal(next_world, faction_id)
        next_world, command_remaining, crisis_action = _apply_world_crisis_ai_choice(
            next_world,
            faction_id=faction_id,
            strategic_goal=strategic_goal,
            command_remaining=command_remaining,
        )
        if crisis_action is not None:
            actions.append(crisis_action)
            faction = next(item for item in next_world.factions if item.faction_id == faction_id)
        next_world, command_remaining, relic_action, relic_office_action = _apply_relic_ai_action(
            next_world,
            faction_id=faction_id,
            command_remaining=command_remaining,
        )
        if relic_action is not None:
            actions.append(relic_action)
            if relic_office_action is not None:
                office_actions.append(relic_office_action)
            faction = next(item for item in next_world.factions if item.faction_id == faction_id)
        counter_attack = _relic_counter_attack(next_world, faction) if enable_attacks else None
        goal_attack = preferred_attack_for_goal(next_world, faction_id, strategic_goal) if enable_attacks else None
        initial_attack = counter_attack or goal_attack
        if initial_attack is None and enable_attacks and (strategic_goal or {}).get("goal_type") != "ritual_reinforcement":
            initial_attack = _best_attack(next_world, faction)
        attack_reserve = 2 if initial_attack is not None else 0
        crisis_money_reserve, crisis_food_reserve = _world_crisis_ai_reserve(next_world)
        next_world, command_remaining, political_actions, political_office_actions = apply_major_political_ai_actions(
            next_world,
            faction_id=faction_id,
            command_remaining=command_remaining,
            attack_reserve=attack_reserve,
            strategic_goal=strategic_goal,
            money_reserve=crisis_money_reserve,
            food_reserve=crisis_food_reserve,
        )
        actions.extend(political_actions)
        office_actions.extend(political_office_actions)
        faction = next(faction for faction in next_world.factions if faction.faction_id == faction_id)
        normal_policy_choice = _best_policy_city(next_world, faction)
        policy_choice = (
            normal_policy_choice
            if normal_policy_choice is not None and _city_policy_urgency(normal_policy_choice[0], faction) >= 800
            else None
        )
        goal_policy = preferred_policy_for_goal(strategic_goal)
        if policy_choice is None and goal_policy is not None:
            goal_city_id, policy_fragment = goal_policy
            goal_city = next(
                (city for city in next_world.cities if city.city_id == goal_city_id and city.owner_faction_id == faction_id),
                None,
            )
            goal_policy_name = _policy_containing(policy_fragment, fallback=POLICY_STABLE)
            if goal_city is not None and goal_city.policy != goal_policy_name:
                policy_choice = (goal_city, goal_policy_name)
        if policy_choice is None:
            policy_choice = normal_policy_choice
        if (
            policy_choice is not None
            and command_remaining >= 1
            and command_remaining - 1 >= attack_reserve
        ):
            city, policy = policy_choice
            office = ai_office_for_action(
                next_world,
                faction_id=faction_id,
                action_type="set_city_policy",
                payload={"city_id": city.city_id},
            )
            if office is not None:
                next_world = set_city_policy(next_world, faction_id=faction_id, city_id=city.city_id, policy=policy)
                action = f"policy:{city.city_id}:{policy}"
                actions.append(action)
                office_actions.append(f"{office.office_id}:{action}")
                command_remaining -= 1
                faction = next(faction for faction in next_world.factions if faction.faction_id == faction_id)

        story_event = pending_story_event_for_faction(next_world, faction_id)
        if (
            story_event is not None
            and command_remaining >= 1
            and command_remaining - 1 >= attack_reserve
        ):
            story_choice = choose_ai_story_choice(next_world, story_event)
            office = ai_office_for_action(
                next_world,
                faction_id=faction_id,
                action_type="resolve_story_event",
                payload={"event_id": story_event.event_id},
            )
            if story_choice is not None and office is not None:
                next_world = resolve_story_event(
                    next_world,
                    faction_id=faction_id,
                    event_id=story_event.event_id,
                    choice_id=story_choice.choice_id,
                )
                action = f"story:{story_event.event_id}:{story_choice.choice_id}"
                actions.append(action)
                office_actions.append(f"{office.office_id}:{action}")
                command_remaining -= 1
                faction = next(faction for faction in next_world.factions if faction.faction_id == faction_id)

        tech_id = _first_affordable_tech(faction)
        tech = next((item for item in TACTIC_TECH_TREE if item.tech_id == tech_id), None)
        peace_treasury_reserve = max(
            ai_peace_treasury_reserve(next_world, faction_id),
            crisis_money_reserve,
        )
        tech_office = ai_office_for_action(
            next_world,
            faction_id=faction_id,
            action_type="unlock_tactic_tech",
            payload={"tech_id": tech_id or ""},
        )
        if (
            tech_id is not None
            and tech is not None
            and tech_office is not None
            and not (faction.researching or {}).get("tech_id")
            and command_remaining - 1 >= attack_reserve
            and faction.resources.money - tech.money_cost >= peace_treasury_reserve
        ):
            next_world = unlock_tactic_tech(next_world, faction_id=faction_id, tech_id=tech_id)
            action = f"tech:{tech_id}"
            actions.append(action)
            office_actions.append(f"{tech_office.office_id}:{action}")
            command_remaining -= 1
            faction = next(faction for faction in next_world.factions if faction.faction_id == faction_id)

        relic_maintenance_reserve = _relic_maintenance_reserve_by_city(next_world, faction_id)
        ritual_city = next(
            (
                city
                for city in sorted(_cities_for_faction(next_world, faction_id), key=lambda item: (-item.resources.ether, item.city_id))
                if int(city.building_levels.get("ritual_site", 0)) > 0
                and city.resources.ether - 30 >= relic_maintenance_reserve.get(city.city_id, 0)
            ),
            None,
        )
        ritual_office = ai_office_for_action(
            next_world,
            faction_id=faction_id,
            action_type="perform_hero_ritual",
            payload={"city_id": ritual_city.city_id if ritual_city is not None else ""},
        )
        if (
            ritual_city is not None
            and ritual_office is not None
            and hero_ritual_capacity(next_world, faction_id)["remaining"] > 0
            and command_remaining - 1 >= attack_reserve
        ):
            before_codes = {hero.hero_code for hero in next_world.strategic_heroes if hero.faction_id == faction_id}
            next_world = perform_hero_ritual(
                next_world,
                faction_id=faction_id,
                city_id=ritual_city.city_id,
                issuer_office_id=ritual_office.office_id,
            )
            summoned = next(
                hero
                for hero in next_world.strategic_heroes
                if hero.faction_id == faction_id and hero.hero_code not in before_codes
            )
            action = f"ritual:{ritual_city.city_id}:{summoned.hero_code}"
            actions.append(action)
            office_actions.append(f"{ritual_office.office_id}:{action}")
            command_remaining -= 1
            vacancy = next(
                (
                    office
                    for office in next_world.offices
                    if office.faction_id == faction_id and office.office_type != "lord" and office.status == "vacant"
                ),
                None,
            )
            if vacancy is not None and ritual_office.office_type == "lord" and command_remaining - 1 >= attack_reserve:
                next_world = appoint_strategic_hero_to_office(
                    next_world,
                    faction_id=faction_id,
                    issuer_office_id=ritual_office.office_id,
                    target_office_id=vacancy.office_id,
                    hero_code=summoned.hero_code,
                )
                appointment = f"appoint:{summoned.hero_code}:{vacancy.office_id}"
                actions.append(appointment)
                office_actions.append(f"{ritual_office.office_id}:{appointment}")
                command_remaining -= 1
            faction = next(faction for faction in next_world.factions if faction.faction_id == faction_id)

        active_hero_codes = active_strategic_hero_codes_for_faction(next_world, faction_id)
        defense_office = ai_office_for_action(
            next_world,
            faction_id=faction_id,
            action_type="set_strategic_defender_hero",
            payload={},
        )
        if active_hero_codes and defense_office is not None and not _has_configured_defender(next_world, faction_id):
            defender_code = active_hero_codes[0]
            next_world = set_strategic_defender_hero(
                next_world,
                faction_id=faction_id,
                hero_code=defender_code,
            )
            action = f"defender:{defender_code}"
            actions.append(action)
            office_actions.append(f"{defense_office.office_id}:{action}")
            faction = next(faction for faction in next_world.factions if faction.faction_id == faction_id)

        planned_attack = None
        if enable_attacks and command_remaining >= 2:
            planned_attack = _relic_counter_attack(next_world, faction)
            if planned_attack is None:
                planned_attack = preferred_attack_for_goal(next_world, faction_id, strategic_goal)
            if planned_attack is None and (strategic_goal or {}).get("goal_type") != "capture_city":
                planned_attack = _best_attack(next_world, faction)
        next_world, duty_actions, duty_office_actions = _station_ai_heroes(
            next_world,
            faction_id=faction_id,
            attack=planned_attack,
        )
        actions.extend(duty_actions)
        office_actions.extend(duty_office_actions)
        next_world, command_remaining, develop_actions, develop_office_actions = _apply_ai_city_development(
            next_world,
            faction_id=faction_id,
            command_remaining=command_remaining,
            attack_reserve=2 if planned_attack is not None else 0,
        )
        actions.extend(develop_actions)
        office_actions.extend(develop_office_actions)
        faction = next(faction for faction in next_world.factions if faction.faction_id == faction_id)

        if enable_attacks and command_remaining >= 2 and planned_attack is not None:
            attack = planned_attack
            if attack is not None:
                source_city_id, target_city_id = attack
                attack_office = ai_office_for_action(
                    next_world,
                    faction_id=faction_id,
                    action_type="declare_attack",
                    payload={"source_city_id": source_city_id, "target_city_id": target_city_id},
                )
                if attack_office is not None:
                    stationed_codes = [
                        code
                        for code in active_strategic_hero_codes_for_faction(next_world, faction_id)
                        if hero_is_stationed_in_city(next_world, code, source_city_id)
                    ]
                    next_world = declare_city_attack(
                        next_world,
                        faction_id=faction_id,
                        source_city_id=source_city_id,
                        target_city_id=target_city_id,
                        resolution_mode="quick",
                        auto_resolve=True,
                        attacker_hero_codes=stationed_codes,
                        attacker_office_id=attack_office.office_id,
                    )
                    action = f"attack:{source_city_id}->{target_city_id}"
                    actions.append(action)
                    office_actions.append(f"{attack_office.office_id}:{action}")
                    command_remaining -= 2

        update_ai_strategic_goal(next_world, faction_id, actions)

        if office_actions:
            next_world.event_log.append(
                EventLogEntry(
                    month=next_world.current_month,
                    category="strategy_ai_office_trace",
                    message=f"{faction_id} office AI decisions: {', '.join(office_actions)}.",
                    related_ids=[faction_id, *office_actions],
                )
            )
        next_world.event_log.append(
            EventLogEntry(
                month=next_world.current_month,
                category="strategy_ai_plan",
                message=(
                    f"{faction_id} monthly AI plan "
                    f"({command_start - command_remaining}/{command_start} command): "
                    f"{', '.join(actions) if actions else 'hold'}."
                ),
                related_ids=[faction_id, *actions],
            )
        )
        next_world.validate()

    return next_world
