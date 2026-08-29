from __future__ import annotations

import copy
from typing import Any

from wujiang.strategic.models import (
    EventLogEntry,
    Faction,
    Office,
    PendingBattle,
    ResourceBundle,
    StrategicArmy,
    StrategyError,
    WorldCrisis,
    WorldState,
)
from wujiang.strategic.objectives import FIRST_CAMPAIGN_SCENARIO_ID


SNOW_GHOST_CRISIS_ID = "snow_ghost_north_v1"
SNOW_GHOST_OMEN_MONTH = 3
SNOW_GHOST_BORDER_PRESSURE_MONTH = 5
SNOW_GHOST_SPREAD_MONTH = 7
SNOW_GHOST_MOBILIZATION_MONTH = 9
SNOW_GHOST_SHOWDOWN_MONTH = 11
SNOW_GHOST_OMEN_MEMORY_TAG = "world_crisis:snow_ghost_north_v1:omen"
SNOW_GHOST_BORDER_PRESSURE_MEMORY_TAG = "world_crisis:snow_ghost_north_v1:border_pressure"
SNOW_GHOST_SPREAD_MEMORY_TAG = "world_crisis:snow_ghost_north_v1:spread"
SNOW_GHOST_MOBILIZATION_MEMORY_TAG = "world_crisis:snow_ghost_north_v1:mobilization"
SNOW_GHOST_SHOWDOWN_MEMORY_TAG = "world_crisis:snow_ghost_north_v1:showdown"
SNOW_GHOST_FACTION_ID = "snow_ghost_horde_v1"
SNOW_GHOST_GENERAL_OFFICE_ID = "snow_ghost_general_v1"
SNOW_GHOST_VANGUARD_ARMY_ID = "snow_ghost_vanguard_v1"
SNOW_GHOST_COLD_ROUTE_MIN_SUPPLY = 80
SNOW_GHOST_COLD_ROUTE_SUPPLY_COST = 20
SNOW_GHOST_COLD_ROUTE_MORALE_LOSS = 5
SNOW_GHOST_COLD_ROUTE_SHORTAGE_MORALE_LOSS = 10

CRISIS_STAGE_LABELS = {
    "dormant": "潜伏",
    "omen": "北境预兆",
    "border_pressure": "边境压力",
    "spread": "寒潮扩散",
    "mobilization": "联军动员",
    "showdown": "北境决战",
    "aftermath": "危机余波",
    "resolved": "已经平息",
}

SHOWDOWN_BRANCH_LABELS = {
    "united_counteroffensive": "联军反攻",
    "rival_vanguards": "竞逐先锋",
    "shattered_line": "破碎防线",
}

SHOWDOWN_BRANCH_SPECS = {
    "united_counteroffensive": {"threshold": 80, "coalition_units": 8, "snow_ghost_units": 7},
    "rival_vanguards": {"threshold": 70, "coalition_units": 6, "snow_ghost_units": 8},
    "shattered_line": {"threshold": 0, "coalition_units": 4, "snow_ghost_units": 10},
}


def _clone_world(world: WorldState) -> WorldState:
    return WorldState.from_dict(copy.deepcopy(world.to_dict()))


def _fixed_campaign_enabled(world: WorldState) -> bool:
    return str(world.campaign_contract.get("id") or "") == FIRST_CAMPAIGN_SCENARIO_ID


def _node_sort_key(node: Any) -> tuple[int, int, str]:
    return (int(node.y), int(node.x), str(node.node_id))


def _snow_ghost_frontier(world: WorldState) -> tuple[str, list[str]]:
    origin = min(world.nodes, key=_node_sort_key)
    nodes_by_id = {node.node_id: node for node in world.nodes}
    frontier = [
        nodes_by_id[node_id]
        for node_id in {origin.node_id, *origin.connected_node_ids}
        if node_id in nodes_by_id
    ]
    return origin.node_id, [node.node_id for node in sorted(frontier, key=_node_sort_key)]


def strategic_route_key(source_node_id: str, target_node_id: str) -> str:
    return "::".join(sorted((str(source_node_id), str(target_node_id))))


def _snow_ghost_cold_route_keys(crisis: WorldCrisis) -> list[str]:
    return sorted(
        strategic_route_key(crisis.origin_node_id, node_id)
        for node_id in crisis.frontier_node_ids
        if node_id != crisis.origin_node_id
    )


def snow_ghost_cold_route_keys(world: WorldState) -> set[str]:
    crisis = next(
        (item for item in world.world_crises if item.crisis_id == SNOW_GHOST_CRISIS_ID),
        None,
    )
    if crisis is None or crisis.stage not in {
        "border_pressure",
        "spread",
        "mobilization",
        "showdown",
        "aftermath",
    }:
        return set()
    return set(crisis.affected_route_keys)


def _activate_snow_ghost_omen(world: WorldState, crisis: WorldCrisis) -> None:
    crisis.status = "active"
    crisis.stage = "omen"
    crisis.started_month = SNOW_GHOST_OMEN_MONTH
    crisis.stage_started_month = SNOW_GHOST_OMEN_MONTH
    crisis.next_stage_month = SNOW_GHOST_BORDER_PRESSURE_MONTH
    crisis.pressure = 10
    if not any(item.get("event") == "northern_omen_confirmed" for item in crisis.history):
        crisis.history.append(
            {
                "month": SNOW_GHOST_OMEN_MONTH,
                "stage": "omen",
                "event": "northern_omen_confirmed",
            }
        )
    if SNOW_GHOST_OMEN_MEMORY_TAG not in world.memory_tags:
        world.memory_tags.append(SNOW_GHOST_OMEN_MEMORY_TAG)
    if not any(event.category == "world_crisis_omen" for event in world.event_log):
        origin_name = next(
            node.name for node in world.nodes if node.node_id == crisis.origin_node_id
        )
        world.event_log.append(
            EventLogEntry(
                month=world.current_month,
                category="world_crisis_omen",
                message=f"{origin_name}以北出现无法解释的寒潮与雪鬼踪迹，北境预兆已经确认。",
                related_ids=[crisis.crisis_id, *crisis.frontier_node_ids],
            )
        )


def _activate_snow_ghost_border_pressure(world: WorldState, crisis: WorldCrisis) -> None:
    crisis.status = "active"
    crisis.stage = "border_pressure"
    crisis.stage_started_month = SNOW_GHOST_BORDER_PRESSURE_MONTH
    crisis.next_stage_month = SNOW_GHOST_SPREAD_MONTH
    crisis.pressure = 30
    crisis.affected_route_keys = _snow_ghost_cold_route_keys(crisis)
    if not any(item.get("event") == "border_pressure_started" for item in crisis.history):
        crisis.history.append(
            {
                "month": SNOW_GHOST_BORDER_PRESSURE_MONTH,
                "stage": "border_pressure",
                "event": "border_pressure_started",
                "affected_route_keys": list(crisis.affected_route_keys),
            }
        )
    if SNOW_GHOST_BORDER_PRESSURE_MEMORY_TAG not in world.memory_tags:
        world.memory_tags.append(SNOW_GHOST_BORDER_PRESSURE_MEMORY_TAG)
    if not any(event.category == "world_crisis_border_pressure" for event in world.event_log):
        world.event_log.append(
            EventLogEntry(
                month=world.current_month,
                category="world_crisis_border_pressure",
                message="北境寒潮压过边界，起源地周边路线进入严寒状态；军队必须备足粮草或寻找安全改道。",
                related_ids=[crisis.crisis_id, *crisis.affected_route_keys],
            )
        )


def _snow_ghost_spread_route_keys(world: WorldState, crisis: WorldCrisis) -> list[str]:
    frontier_ids = set(crisis.frontier_node_ids)
    return sorted(
        {
            strategic_route_key(node.node_id, target_id)
            for node in world.nodes
            for target_id in node.connected_node_ids
            if node.node_id in frontier_ids or target_id in frontier_ids
        }
    )


def _ensure_snow_ghost_vanguard(world: WorldState, crisis: WorldCrisis) -> StrategicArmy:
    faction = next(
        (item for item in world.factions if item.faction_id == SNOW_GHOST_FACTION_ID),
        None,
    )
    if faction is None:
        faction = Faction(
            faction_id=SNOW_GHOST_FACTION_ID,
            name="北境雪鬼",
            is_ai=True,
            resources=ResourceBundle(food=0, money=0, population=0, ether=0, troops=0),
            faction_type="world_crisis",
            memory_tags=["world_crisis_faction:snow_ghost"],
        )
        world.factions.append(faction)
    office = next(
        (item for item in world.offices if item.office_id == SNOW_GHOST_GENERAL_OFFICE_ID),
        None,
    )
    if office is None:
        office = Office(
            office_id=SNOW_GHOST_GENERAL_OFFICE_ID,
            faction_id=SNOW_GHOST_FACTION_ID,
            office_type="general",
            holder_id="snow_ghost_warlord_v1",
            holder_type="world_crisis",
            controller_type="ai",
            permissions=["world_crisis_army"],
            duties=["invade_origin"],
        )
        world.offices.append(office)
    army = next(
        (item for item in world.armies if item.army_id == SNOW_GHOST_VANGUARD_ARMY_ID),
        None,
    )
    if army is None:
        origin_city = next(
            (city for city in world.cities if city.node_id == crisis.origin_node_id),
            None,
        )
        if origin_city is None:
            raise ValueError("雪鬼起源节点没有可袭击城市。")
        army = StrategicArmy(
            army_id=SNOW_GHOST_VANGUARD_ARMY_ID,
            faction_id=SNOW_GHOST_FACTION_ID,
            commander_office_id=SNOW_GHOST_GENERAL_OFFICE_ID,
            commander_hero_code="snow_ghost_warlord_v1",
            location_node_id=crisis.origin_node_id,
            home_city_id=origin_city.city_id,
            name="北境雪鬼先锋",
            army_kind="snow_ghost",
            unit_inventory={"snow_ghost": 6},
            manpower=600,
            supply=600,
            supply_capacity=600,
            morale=90,
            status="deployed",
            current_order="hold",
            created_month=SNOW_GHOST_SPREAD_MONTH,
            march_origin_node_id=crisis.origin_node_id,
            destination_node_id=crisis.origin_node_id,
            route_node_ids=[crisis.origin_node_id],
            route_progress_index=0,
            departure_month=SNOW_GHOST_SPREAD_MONTH,
            estimated_arrival_month=SNOW_GHOST_SPREAD_MONTH,
            supply_line_status="none",
        )
        world.armies.append(army)
    if army.army_id not in crisis.spawned_army_ids:
        crisis.spawned_army_ids.append(army.army_id)
    return army


def _activate_snow_ghost_spread(world: WorldState, crisis: WorldCrisis) -> None:
    crisis.status = "active"
    crisis.stage = "spread"
    crisis.stage_started_month = SNOW_GHOST_SPREAD_MONTH
    crisis.next_stage_month = 9
    crisis.pressure = 60
    crisis.affected_route_keys = _snow_ghost_spread_route_keys(world, crisis)
    crisis.threatened_city_ids = sorted(
        city.city_id for city in world.cities if city.node_id in set(crisis.frontier_node_ids)
    )
    army = _ensure_snow_ghost_vanguard(world, crisis)
    if not any(item.get("event") == "snow_ghost_spread_started" for item in crisis.history):
        crisis.history.append(
            {
                "month": SNOW_GHOST_SPREAD_MONTH,
                "stage": "spread",
                "event": "snow_ghost_spread_started",
                "affected_route_keys": list(crisis.affected_route_keys),
                "threatened_city_ids": list(crisis.threatened_city_ids),
                "spawned_army_ids": list(crisis.spawned_army_ids),
            }
        )
    if SNOW_GHOST_SPREAD_MEMORY_TAG not in world.memory_tags:
        world.memory_tags.append(SNOW_GHOST_SPREAD_MEMORY_TAG)
    if not any(event.category == "world_crisis_snow_ghost_spread" for event in world.event_log):
        origin_city = next(city for city in world.cities if city.node_id == crisis.origin_node_id)
        world.event_log.append(
            EventLogEntry(
                month=world.current_month,
                category="world_crisis_snow_ghost_spread",
                message=f"寒潮沿北境前线扩散，{army.name}已经在{origin_city.name}现身并发动袭击。",
                related_ids=[
                    crisis.crisis_id,
                    army.army_id,
                    origin_city.city_id,
                    *crisis.affected_route_keys,
                ],
            )
        )


def _activate_snow_ghost_mobilization(world: WorldState, crisis: WorldCrisis) -> None:
    crisis.status = "active"
    crisis.stage = "mobilization"
    crisis.stage_started_month = SNOW_GHOST_MOBILIZATION_MONTH
    crisis.next_stage_month = SNOW_GHOST_SHOWDOWN_MONTH
    crisis.pressure = 80
    crisis.affected_route_keys = _snow_ghost_spread_route_keys(world, crisis)
    crisis.threatened_city_ids = sorted(
        city.city_id for city in world.cities if city.node_id in set(crisis.frontier_node_ids)
    )
    _ensure_snow_ghost_vanguard(world, crisis)
    for faction in world.factions:
        if faction.is_major:
            crisis.contributions_by_faction.setdefault(faction.faction_id, 0)
    if not any(item.get("event") == "snow_ghost_mobilization_started" for item in crisis.history):
        crisis.history.append(
            {
                "month": SNOW_GHOST_MOBILIZATION_MONTH,
                "stage": "mobilization",
                "event": "snow_ghost_mobilization_started",
            }
        )
    if SNOW_GHOST_MOBILIZATION_MEMORY_TAG not in world.memory_tags:
        world.memory_tags.append(SNOW_GHOST_MOBILIZATION_MEMORY_TAG)
    if not any(event.category == "world_crisis_mobilization" for event in world.event_log):
        world.event_log.append(
            EventLogEntry(
                month=world.current_month,
                category="world_crisis_mobilization",
                message="北境防线进入联军动员。各主要势力必须决定独立贡献、寻求合作，或背弃已经成立的危机合作。",
                related_ids=[crisis.crisis_id, *sorted(crisis.contributions_by_faction)],
            )
        )


def _showdown_branch(crisis: WorldCrisis) -> str:
    total_contribution = sum(max(0, int(value)) for value in crisis.contributions_by_faction.values())
    active_pairs = [
        pair_key
        for pair_key in crisis.cooperation_pairs
        if pair_key not in crisis.broken_cooperation_pairs
    ]
    if active_pairs and total_contribution >= 80:
        return "united_counteroffensive"
    if not crisis.broken_cooperation_pairs and total_contribution >= 70:
        return "rival_vanguards"
    return "shattered_line"


def _showdown_leader_faction_id(world: WorldState, crisis: WorldCrisis) -> str:
    city_counts = {
        faction.faction_id: sum(
            1 for city in world.cities if city.owner_faction_id == faction.faction_id
        )
        for faction in world.factions
        if faction.is_major
    }
    candidates = [
        faction
        for faction in world.factions
        if faction.is_major and city_counts.get(faction.faction_id, 0) > 0
    ]
    if not candidates:
        candidates = [faction for faction in world.factions if faction.is_major]
    if not candidates:
        raise StrategyError("北境决战没有可担任联军领袖的主要势力。")
    return min(
        candidates,
        key=lambda faction: (
            -int(crisis.contributions_by_faction.get(faction.faction_id, 0)),
            -int(faction.diplomatic_reputation),
            faction.faction_id,
        ),
    ).faction_id


def _coalition_registered_units(unit_count: int) -> dict[str, int]:
    total = max(1, min(12, int(unit_count)))
    cavalry = total // 4
    archers = total // 3
    infantry = total - cavalry - archers
    return {
        unit_type: count
        for unit_type, count in {
            "infantry": infantry,
            "archer": archers,
            "cavalry": cavalry,
        }.items()
        if count > 0
    }


def _activate_snow_ghost_showdown(world: WorldState, crisis: WorldCrisis) -> None:
    if crisis.showdown_outcome is not None:
        return
    crisis.status = "active"
    crisis.stage = "showdown"
    crisis.stage_started_month = SNOW_GHOST_SHOWDOWN_MONTH
    crisis.next_stage_month = None
    crisis.pressure = 100
    crisis.affected_route_keys = _snow_ghost_spread_route_keys(world, crisis)
    crisis.threatened_city_ids = sorted(
        city.city_id for city in world.cities if city.node_id in set(crisis.frontier_node_ids)
    )
    _ensure_snow_ghost_vanguard(world, crisis)
    if crisis.showdown_branch is None:
        crisis.showdown_branch = _showdown_branch(crisis)
    if crisis.showdown_leader_faction_id is None:
        crisis.showdown_leader_faction_id = _showdown_leader_faction_id(world, crisis)

    branch = crisis.showdown_branch
    spec = SHOWDOWN_BRANCH_SPECS[branch]
    total_contribution = sum(max(0, int(value)) for value in crisis.contributions_by_faction.values())
    bonus_units = min(2, max(0, (total_contribution - int(spec["threshold"])) // 35))
    coalition_count = min(12, int(spec["coalition_units"]) + bonus_units)
    snow_ghost_count = int(spec["snow_ghost_units"])
    battle_id = crisis.showdown_battle_id or f"battle_{SNOW_GHOST_SHOWDOWN_MONTH}_world_crisis_{crisis.crisis_id}"
    battle = next((item for item in world.pending_battles if item.battle_id == battle_id), None)
    if battle is None:
        origin_city = next(
            (city for city in world.cities if city.node_id == crisis.origin_node_id),
            None,
        )
        if origin_city is None:
            raise StrategyError("北境决战起源节点没有可绑定的城市。")
        from wujiang.strategic.heroes import active_strategic_hero_codes_for_faction

        leader_office = next(
            (
                office
                for office in world.offices
                if office.faction_id == crisis.showdown_leader_faction_id
                and office.office_type == "lord"
                and office.status == "active"
            ),
            None,
        )
        battle = PendingBattle(
            battle_id=battle_id,
            month=SNOW_GHOST_SHOWDOWN_MONTH,
            attacker_faction_id=crisis.showdown_leader_faction_id,
            defender_faction_id=SNOW_GHOST_FACTION_ID,
            source_city_id=origin_city.city_id,
            target_city_id=origin_city.city_id,
            resolution_mode="unselected",
            attacker_troops=coalition_count * 100,
            defender_troops=snow_ghost_count * 100,
            attacker_hero_codes=active_strategic_hero_codes_for_faction(
                world, crisis.showdown_leader_faction_id
            ),
            defender_hero_codes=[],
            attacker_office_id=leader_office.office_id if leader_office is not None else None,
            attacker_registered_units=_coalition_registered_units(coalition_count),
            defender_registered_units={"snow_ghost": snow_ghost_count},
            source_kind="world_crisis",
            source_entity_id=crisis.crisis_id,
            battle_trigger=branch,
            battle_node_id=crisis.origin_node_id,
            report=[
                f"北境决战进入“{SHOWDOWN_BRANCH_LABELS[branch]}”分支。",
                f"联军投入 {coalition_count} 个格子单位，雪鬼投入 {snow_ghost_count} 个格子单位。",
            ],
        )
        world.pending_battles.append(battle)
    crisis.showdown_battle_id = battle.battle_id
    if not any(item.get("event") == "snow_ghost_showdown_started" for item in crisis.history):
        crisis.history.append(
            {
                "month": SNOW_GHOST_SHOWDOWN_MONTH,
                "stage": "showdown",
                "event": "snow_ghost_showdown_started",
                "branch": branch,
                "leader_faction_id": crisis.showdown_leader_faction_id,
                "battle_id": battle.battle_id,
                "total_contribution": total_contribution,
                "coalition_units": coalition_count,
                "snow_ghost_units": snow_ghost_count,
            }
        )
    if SNOW_GHOST_SHOWDOWN_MEMORY_TAG not in world.memory_tags:
        world.memory_tags.append(SNOW_GHOST_SHOWDOWN_MEMORY_TAG)
    if not any(event.category == "world_crisis_showdown" for event in world.event_log):
        leader = next(
            faction
            for faction in world.factions
            if faction.faction_id == crisis.showdown_leader_faction_id
        )
        world.event_log.append(
            EventLogEntry(
                month=world.current_month,
                category="world_crisis_showdown",
                message=(
                    f"北境寒潮达到顶峰，{leader.name}率军进入"
                    f"“{SHOWDOWN_BRANCH_LABELS[branch]}”决战。"
                ),
                related_ids=[crisis.crisis_id, battle.battle_id, leader.faction_id],
            )
        )
def world_crisis_pair_key(first_faction_id: str, second_faction_id: str) -> str:
    return "::".join(sorted((str(first_faction_id), str(second_faction_id))))


def _major_faction(world: WorldState, faction_id: str) -> Faction:
    faction = next(
        (item for item in world.factions if item.faction_id == str(faction_id) and item.is_major),
        None,
    )
    if faction is None:
        raise StrategyError("只有主要势力可以参与雪鬼危机动员。")
    return faction


def _mobilization_crisis(world: WorldState) -> WorldCrisis:
    crisis = next(
        (item for item in world.world_crises if item.crisis_id == SNOW_GHOST_CRISIS_ID),
        None,
    )
    if crisis is None or crisis.stage != "mobilization" or world.current_month not in {9, 10}:
        raise StrategyError("雪鬼联军动员只在第 9～10 月开放。")
    return crisis


def _choice_already_resolved(crisis: WorldCrisis, faction_id: str, month: int) -> bool:
    return any(
        int(item.get("month", 0)) == int(month)
        and str(item.get("faction_id") or "") == str(faction_id)
        for item in crisis.decisions
    )


def world_crisis_choice_options(world: WorldState, faction_id: str) -> list[dict[str, Any]]:
    faction = _major_faction(world, faction_id)
    crisis = next(
        (item for item in world.world_crises if item.crisis_id == SNOW_GHOST_CRISIS_ID),
        None,
    )
    window_open = bool(
        crisis is not None and crisis.stage == "mobilization" and world.current_month in {9, 10}
    )
    already_chosen = bool(
        crisis is not None and _choice_already_resolved(crisis, faction.faction_id, world.current_month)
    )
    targets = [
        item for item in world.factions
        if item.is_major and item.faction_id != faction.faction_id
    ]
    options: list[dict[str, Any]] = [
        {
            "id": "contribute",
            "name": "独立贡献",
            "description": "独立筹集军需，争取更多主线功绩，但不建立合作承诺。",
            "command_cost": 1,
            "food_cost": 100,
            "money_cost": 50,
            "contribution_gain": 35,
            "requires_target": False,
            "available": window_open and not already_chosen and faction.resources.food >= 100 and faction.resources.money >= 50,
            "reason": (
                "本月已经完成危机选择。"
                if already_chosen
                else "需要 100 粮食与 50 金钱。"
                if faction.resources.food < 100 or faction.resources.money < 50
                else ""
            ),
        },
        {
            "id": "cooperate",
            "name": "提出合作",
            "description": "双向承诺成立时，双方获得额外贡献、关系与信誉。",
            "command_cost": 1,
            "food_cost": 80,
            "money_cost": 40,
            "contribution_gain": 25,
            "requires_target": True,
            "available": window_open and not already_chosen and bool(targets) and faction.resources.food >= 80 and faction.resources.money >= 40,
            "reason": (
                "本月已经完成危机选择。"
                if already_chosen
                else "需要另一主要势力作为合作目标。"
                if not targets
                else "需要 80 粮食与 40 金钱。"
                if faction.resources.food < 80 or faction.resources.money < 40
                else ""
            ),
        },
        {
            "id": "betray",
            "name": "背弃合作",
            "description": "转移合作方最多 20 点贡献，但会破坏关系并重创外交信誉。",
            "command_cost": 1,
            "food_cost": 0,
            "money_cost": 0,
            "contribution_gain": 20,
            "requires_target": True,
            "available": window_open and not already_chosen and bool(
                crisis and any(
                    world_crisis_pair_key(faction.faction_id, target.faction_id) in crisis.cooperation_pairs
                    and world_crisis_pair_key(faction.faction_id, target.faction_id) not in crisis.broken_cooperation_pairs
                    for target in targets
                )
            ),
            "reason": (
                "本月已经完成危机选择。"
                if already_chosen
                else "当前没有可以背弃的有效危机合作。"
            ),
        },
    ]
    for option in options:
        option["targets"] = [
            {
                "faction_id": target.faction_id,
                "faction_name": target.name,
                "available": (
                    option["id"] != "betray"
                    or (
                        crisis is not None
                        and world_crisis_pair_key(faction.faction_id, target.faction_id) in crisis.cooperation_pairs
                        and world_crisis_pair_key(faction.faction_id, target.faction_id) not in crisis.broken_cooperation_pairs
                    )
                ),
            }
            for target in targets
        ]
    return options


def validate_world_crisis_choice(
    world: WorldState,
    *,
    faction_id: str,
    choice_id: str,
    target_faction_id: str = "",
) -> None:
    faction = _major_faction(world, faction_id)
    crisis = _mobilization_crisis(world)
    if _choice_already_resolved(crisis, faction.faction_id, world.current_month):
        raise StrategyError("本势力本月已经完成危机选择。")
    choice = str(choice_id or "").strip()
    if choice not in {"contribute", "cooperate", "betray"}:
        raise StrategyError("危机选择无效。")
    target = None
    if choice in {"cooperate", "betray"}:
        target = _major_faction(world, target_faction_id)
        if target.faction_id == faction.faction_id:
            raise StrategyError("不能把本势力设为危机合作目标。")
    if choice == "contribute":
        if faction.resources.food < 100 or faction.resources.money < 50:
            raise StrategyError("独立贡献需要 100 粮食与 50 金钱。")
    elif choice == "cooperate":
        if faction.resources.food < 80 or faction.resources.money < 40:
            raise StrategyError("提出合作需要 80 粮食与 40 金钱。")
        pair_key = world_crisis_pair_key(faction.faction_id, target.faction_id)
        if pair_key in crisis.broken_cooperation_pairs:
            raise StrategyError("这组危机合作已经因背约永久破裂。")
    else:
        pair_key = world_crisis_pair_key(faction.faction_id, target.faction_id)
        if pair_key not in crisis.cooperation_pairs or pair_key in crisis.broken_cooperation_pairs:
            raise StrategyError("只能背弃已经成立且尚未破裂的危机合作。")


def resolve_world_crisis_choice(
    world: WorldState,
    *,
    faction_id: str,
    choice_id: str,
    target_faction_id: str = "",
    issuer_office_id: str,
    decision_origin: str = "player",
    ai_priority: str = "",
    ai_rationale: str = "",
) -> WorldState:
    validate_world_crisis_choice(
        world,
        faction_id=faction_id,
        choice_id=choice_id,
        target_faction_id=target_faction_id,
    )
    next_world = _clone_world(world)
    faction = _major_faction(next_world, faction_id)
    crisis = _mobilization_crisis(next_world)
    office = next(
        (
            item for item in next_world.offices
            if item.office_id == str(issuer_office_id)
            and item.faction_id == faction.faction_id
            and item.office_type == "lord"
            and item.status == "active"
        ),
        None,
    )
    if office is None:
        raise StrategyError("只有本势力在任主公可以签发危机选择。")
    choice = str(choice_id)
    target = _major_faction(next_world, target_faction_id) if target_faction_id else None
    contribution_before = int(crisis.contributions_by_faction.get(faction.faction_id, 0))
    contribution_delta = 0
    target_contribution_delta = 0
    relation_delta = 0
    reputation_delta = 0
    category = "world_crisis_contribution"
    message = ""

    if choice == "contribute":
        faction.resources.food -= 100
        faction.resources.money -= 50
        contribution_delta = 35
        message = f"{faction.name}独立筹集北境军需，危机贡献增加 35。"
    elif choice == "cooperate" and target is not None:
        faction.resources.food -= 80
        faction.resources.money -= 40
        contribution_delta = 25
        crisis.cooperation_targets_by_faction[faction.faction_id] = target.faction_id
        pair_key = world_crisis_pair_key(faction.faction_id, target.faction_id)
        reciprocal = crisis.cooperation_targets_by_faction.get(target.faction_id) == faction.faction_id
        if reciprocal and pair_key not in crisis.cooperation_pairs and pair_key not in crisis.broken_cooperation_pairs:
            crisis.cooperation_pairs.append(pair_key)
            contribution_delta += 15
            crisis.contributions_by_faction[target.faction_id] = (
                int(crisis.contributions_by_faction.get(target.faction_id, 0)) + 15
            )
            faction.diplomatic_reputation = min(100, faction.diplomatic_reputation + 5)
            target.diplomatic_reputation = min(100, target.diplomatic_reputation + 5)
            faction.relations[target.faction_id] = min(100, faction.relations.get(target.faction_id, 0) + 10)
            target.relations[faction.faction_id] = min(100, target.relations.get(faction.faction_id, 0) + 10)
            relation_delta = 10
            reputation_delta = 5
            category = "world_crisis_cooperation_formed"
            message = f"{faction.name}与{target.name}完成双向危机承诺，合作正式成立。"
        else:
            category = "world_crisis_cooperation_pledged"
            message = f"{faction.name}向{target.name}提出北境危机合作，等待对方回应。"
    elif choice == "betray" and target is not None:
        pair_key = world_crisis_pair_key(faction.faction_id, target.faction_id)
        stolen = min(20, int(crisis.contributions_by_faction.get(target.faction_id, 0)))
        contribution_delta = stolen
        target_contribution_delta = -stolen
        crisis.contributions_by_faction[target.faction_id] = (
            int(crisis.contributions_by_faction.get(target.faction_id, 0)) - stolen
        )
        if pair_key not in crisis.broken_cooperation_pairs:
            crisis.broken_cooperation_pairs.append(pair_key)
        faction.diplomatic_reputation = max(0, faction.diplomatic_reputation - 20)
        faction.relations[target.faction_id] = max(-100, faction.relations.get(target.faction_id, 0) - 30)
        target.relations[faction.faction_id] = max(-100, target.relations.get(faction.faction_id, 0) - 30)
        relation_delta = -30
        reputation_delta = -20
        category = "world_crisis_cooperation_betrayed"
        message = f"{faction.name}背弃与{target.name}的北境合作，转移了 {stolen} 点危机贡献。"

    crisis.contributions_by_faction[faction.faction_id] = contribution_before + contribution_delta
    decision = {
        "id": f"crisis-decision:{next_world.current_month}:{len(crisis.decisions) + 1}",
        "month": next_world.current_month,
        "faction_id": faction.faction_id,
        "choice_id": choice,
        "target_faction_id": target.faction_id if target is not None else "",
        "contribution_delta": contribution_delta,
        "target_contribution_delta": target_contribution_delta,
        "relation_delta": relation_delta,
        "reputation_delta": reputation_delta,
        "issuer_office_id": office.office_id,
        "decision_origin": str(decision_origin or "player"),
        "ai_priority": str(ai_priority or ""),
        "ai_rationale": str(ai_rationale or ""),
    }
    crisis.decisions.append(decision)
    crisis.history.append(
        {
            "month": next_world.current_month,
            "stage": "mobilization",
            "event": category,
            "faction_id": faction.faction_id,
            "target_faction_id": target.faction_id if target is not None else "",
            "contribution_delta": contribution_delta,
        }
    )
    memory_tag = (
        f"world_crisis:{crisis.crisis_id}:{choice}:{next_world.current_month}:"
        f"{faction.faction_id}:{target.faction_id if target is not None else 'none'}"
    )
    next_world.memory_tags.append(memory_tag)
    faction.memory_tags.append(memory_tag)
    if target is not None:
        target.memory_tags.append(memory_tag)
    related_ids = [crisis.crisis_id, faction.faction_id]
    if target is not None:
        related_ids.append(target.faction_id)
    related_ids.append(decision["id"])
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category=category,
            message=message,
            related_ids=related_ids,
        )
    )
    from wujiang.strategic.hero_personal import record_hero_crisis_choice

    record_hero_crisis_choice(
        next_world,
        faction_id=faction.faction_id,
        choice_id=choice,
        decision_id=str(decision["id"]),
    )
    next_world.validate()
    return next_world


def ensure_world_crises(world: WorldState) -> WorldState:
    next_world = _clone_world(world)
    if not _fixed_campaign_enabled(next_world):
        return next_world
    crisis = next(
        (item for item in next_world.world_crises if item.crisis_id == SNOW_GHOST_CRISIS_ID),
        None,
    )
    if crisis is None:
        origin_node_id, frontier_node_ids = _snow_ghost_frontier(next_world)
        crisis = WorldCrisis(
            crisis_id=SNOW_GHOST_CRISIS_ID,
            crisis_type="snow_ghost",
            status="dormant",
            stage="dormant",
            stage_started_month=1,
            next_stage_month=SNOW_GHOST_OMEN_MONTH,
            pressure=0,
            origin_node_id=origin_node_id,
            frontier_node_ids=frontier_node_ids,
            history=[
                {
                    "month": 1,
                    "stage": "dormant",
                    "event": "crisis_clock_started",
                }
            ],
        )
        next_world.world_crises.append(crisis)
    if next_world.current_month >= SNOW_GHOST_OMEN_MONTH and crisis.stage == "dormant":
        _activate_snow_ghost_omen(next_world, crisis)
    if next_world.current_month >= SNOW_GHOST_BORDER_PRESSURE_MONTH and crisis.stage == "omen":
        _activate_snow_ghost_border_pressure(next_world, crisis)
    if next_world.current_month >= SNOW_GHOST_SPREAD_MONTH and crisis.stage == "border_pressure":
        _activate_snow_ghost_spread(next_world, crisis)
    elif crisis.stage == "spread":
        _activate_snow_ghost_spread(next_world, crisis)
    if next_world.current_month >= SNOW_GHOST_MOBILIZATION_MONTH and crisis.stage == "spread":
        _activate_snow_ghost_mobilization(next_world, crisis)
    elif crisis.stage == "mobilization":
        _activate_snow_ghost_mobilization(next_world, crisis)
    if next_world.current_month >= SNOW_GHOST_SHOWDOWN_MONTH and crisis.stage == "mobilization":
        _activate_snow_ghost_showdown(next_world, crisis)
    elif crisis.stage == "showdown":
        _activate_snow_ghost_showdown(next_world, crisis)
    next_world.validate()
    return next_world


def advance_world_crises(world: WorldState) -> WorldState:
    return ensure_world_crises(world)


def require_world_crisis_showdown_complete_before_advance(world: WorldState) -> None:
    for crisis in world.world_crises:
        if crisis.stage != "showdown":
            continue
        battle = next(
            (
                item
                for item in world.pending_battles
                if item.battle_id == crisis.showdown_battle_id
            ),
            None,
        )
        if battle is None or battle.status != "resolved":
            raise StrategyError("北境决战尚未完成，不能进入第 12 月。")


def set_world_crisis_showdown_resolution(
    world: WorldState,
    *,
    faction_id: str,
    issuer_office_id: str,
    resolution_mode: str,
    auto_resolve: bool = True,
) -> WorldState:
    if resolution_mode not in {"manual", "ai_auto", "watch_ai", "quick"}:
        raise StrategyError("北境决战处理方式无效。")
    next_world = _clone_world(world)
    faction = _major_faction(next_world, faction_id)
    office = next(
        (
            item
            for item in next_world.offices
            if item.office_id == str(issuer_office_id)
            and item.faction_id == faction.faction_id
            and item.office_type == "lord"
            and item.status == "active"
        ),
        None,
    )
    if office is None:
        raise StrategyError("只有主要势力的在任主公可以开启北境决战。")
    crisis = next(
        (
            item
            for item in next_world.world_crises
            if item.crisis_id == SNOW_GHOST_CRISIS_ID and item.stage == "showdown"
        ),
        None,
    )
    if crisis is None or not crisis.showdown_battle_id:
        raise StrategyError("当前没有可开启的北境决战。")
    battle = next(
        (
            item
            for item in next_world.pending_battles
            if item.battle_id == crisis.showdown_battle_id
        ),
        None,
    )
    if battle is None or battle.status != "pending":
        raise StrategyError("北境决战已经结束或记录不存在。")
    if battle.battle_room_id:
        raise StrategyError("北境决战已经创建真实格子战房间。")
    battle.resolution_mode = str(resolution_mode)
    battle.report.append(f"北境决战选择 {resolution_mode} 处理。")
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="world_crisis_showdown_resolution_selected",
            message=f"{faction.name}主公选择以 {resolution_mode} 处理北境决战。",
            related_ids=[
                crisis.crisis_id,
                battle.battle_id,
                faction.faction_id,
                office.office_id,
            ],
        )
    )
    if resolution_mode == "quick" and auto_resolve:
        from wujiang.strategic.battles import resolve_pending_battle

        return resolve_pending_battle(next_world, battle_id=battle.battle_id)
    next_world.validate()
    return next_world


def resolve_world_crisis_showdown_outcome(
    world: WorldState,
    *,
    crisis_id: str,
    attacker_wins: bool,
) -> WorldState:
    next_world = _clone_world(world)
    crisis = next(
        (item for item in next_world.world_crises if item.crisis_id == str(crisis_id)),
        None,
    )
    if crisis is None or crisis.stage != "showdown" or not crisis.showdown_battle_id:
        raise StrategyError("当前没有可结算的北境决战。")
    if crisis.showdown_outcome is not None:
        return next_world
    leader_id = str(crisis.showdown_leader_faction_id or "")
    if not leader_id:
        raise StrategyError("北境决战缺少联军领袖。")

    if attacker_wins:
        winners = [leader_id]
        if crisis.showdown_branch == "united_counteroffensive":
            valid_pair_members = {
                faction_id
                for pair_key in crisis.cooperation_pairs
                if pair_key not in crisis.broken_cooperation_pairs
                for faction_id in pair_key.split("::")
                if int(crisis.contributions_by_faction.get(faction_id, 0)) >= 25
            }
            winners = sorted(valid_pair_members) or [leader_id]
        reward_by_branch = {
            "united_counteroffensive": (8, 10),
            "rival_vanguards": (5, 6),
            "shattered_line": (3, 3),
        }
        support_gain, reputation_gain = reward_by_branch[str(crisis.showdown_branch)]
        crisis.status = "resolved"
        crisis.stage = "resolved"
        crisis.stage_started_month = next_world.current_month
        crisis.next_stage_month = None
        crisis.pressure = 0
        crisis.showdown_outcome = "victory"
        crisis.mainline_winner_faction_ids = winners
        crisis.affected_route_keys = []
        crisis.threatened_city_ids = []
        for army in next_world.armies:
            if army.army_id in crisis.spawned_army_ids or army.faction_id == SNOW_GHOST_FACTION_ID:
                army.unit_inventory = {}
                army.manpower = 0
                army.supply = 0
                army.status = "destroyed"
                army.current_order = "hold"
                army.target_army_id = None
                army.target_encounter_id = None
        for faction in next_world.factions:
            if faction.faction_id not in winners:
                continue
            faction.diplomatic_reputation = min(
                100, faction.diplomatic_reputation + reputation_gain
            )
            for city in next_world.cities:
                if city.owner_faction_id == faction.faction_id:
                    city.support_by_faction[faction.faction_id] = min(
                        100,
                        city.support_by_faction.get(faction.faction_id, 50) + support_gain,
                    )
        crisis.aftermath = {
            "result": "victory",
            "support_gain": support_gain,
            "reputation_gain": reputation_gain,
            "winner_faction_ids": list(winners),
        }
        category = "world_crisis_resolved"
        message = "北境联军赢得决战，寒潮与雪鬼危机军势已经平息。"
    else:
        damaged_city_ids: list[str] = []
        for city in next_world.cities:
            if city.city_id not in crisis.threatened_city_ids:
                continue
            city.resources.food = max(0, city.resources.food - 80)
            owner_id = city.owner_faction_id
            city.support_by_faction[owner_id] = max(
                0, city.support_by_faction.get(owner_id, 50) - 10
            )
            damaged_city_ids.append(city.city_id)
        crisis.status = "active"
        crisis.stage = "aftermath"
        crisis.stage_started_month = next_world.current_month
        crisis.next_stage_month = None
        crisis.pressure = 100
        crisis.showdown_outcome = "defeat"
        crisis.mainline_winner_faction_ids = []
        crisis.aftermath = {
            "result": "defeat",
            "damaged_city_ids": damaged_city_ids,
            "food_loss_per_city": 80,
            "owner_support_loss_per_city": 10,
        }
        category = "world_crisis_aftermath"
        message = "北境联军决战失利，寒潮余波冲击前线城邦；第 12 月仍将进入正常评议。"

    crisis.history.append(
        {
            "month": next_world.current_month,
            "stage": crisis.stage,
            "event": category,
            "outcome": crisis.showdown_outcome,
            "winner_faction_ids": list(crisis.mainline_winner_faction_ids),
            "aftermath": dict(crisis.aftermath),
        }
    )
    memory_tag = f"world_crisis:{crisis.crisis_id}:showdown:{crisis.showdown_outcome}"
    if memory_tag not in next_world.memory_tags:
        next_world.memory_tags.append(memory_tag)
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category=category,
            message=message,
            related_ids=[
                crisis.crisis_id,
                crisis.showdown_battle_id,
                *crisis.mainline_winner_faction_ids,
            ],
        )
    )
    next_world.validate()
    return next_world


def world_crises_public(world: WorldState) -> list[dict[str, Any]]:
    nodes_by_id = {node.node_id: node for node in world.nodes}
    cities_by_node = {city.node_id: city for city in world.cities}
    payload: list[dict[str, Any]] = []
    for crisis in world.world_crises:
        origin = nodes_by_id.get(crisis.origin_node_id)
        frontier = []
        for node_id in crisis.frontier_node_ids:
            node = nodes_by_id.get(node_id)
            city = cities_by_node.get(node_id)
            if node is None:
                continue
            frontier.append(
                {
                    "node_id": node_id,
                    "node_name": node.name,
                    "city_id": city.city_id if city is not None else None,
                    "city_name": city.name if city is not None else None,
                }
            )
        if crisis.stage == "dormant":
            effect_summary = "寒兆尚未证实；当前没有路线封锁、敌军或行动限制。"
        elif crisis.stage == "omen":
            effect_summary = "预兆已经确认；当前仍没有路线封锁、雪鬼军队或行动限制。"
        elif crisis.stage == "border_pressure":
            effect_summary = (
                "边境严寒已经改变军队行军：低补给军队必须安全改道，高补给过境会损失粮草与士气；"
                "当前尚未出现雪鬼军队。"
            )
        elif crisis.stage == "spread":
            active_crisis_armies = [
                army
                for army in world.armies
                if army.army_id in crisis.spawned_army_ids
                and army.status not in {"disbanded", "destroyed"}
            ]
            effect_summary = (
                f"寒潮已经扩散到北境前线，{len(active_crisis_armies)} 支雪鬼军队仍在活动；"
                "受袭城市将通过真实遭遇、围城与战斗决定归属。"
            )
        elif crisis.stage == "mobilization":
            active_crisis_armies = [
                army
                for army in world.armies
                if army.army_id in crisis.spawned_army_ids
                and army.status not in {"disbanded", "destroyed"}
            ]
            effect_summary = (
                f"北境进入联军动员，{len(active_crisis_armies)} 支雪鬼军队仍在活动；"
                "第 9～10 月主要势力可独立贡献、建立双向合作，或背弃已成立的合作。"
            )
        elif crisis.stage == "showdown":
            effect_summary = "第 11 月北境决战已经开启；必须通过统一战斗入口结算，决战完成前不能进入第 12 月。"
        elif crisis.stage == "aftermath":
            effect_summary = "北境决战失利，寒潮余波仍在前线持续；第 12 月将按正常战役规则进入评议。"
        else:
            effect_summary = "北境决战获胜，寒潮、受威胁路线与雪鬼危机军队已经平息。"
        route_effects = []
        for route_key in crisis.affected_route_keys:
            source_node_id, target_node_id = route_key.split("::")
            source_node = nodes_by_id[source_node_id]
            target_node = nodes_by_id[target_node_id]
            source_city = cities_by_node.get(source_node_id)
            target_city = cities_by_node.get(target_node_id)
            route_effects.append(
                {
                    "route_key": route_key,
                    "source_node_id": source_node_id,
                    "source_name": source_city.name if source_city is not None else source_node.name,
                    "target_node_id": target_node_id,
                    "target_name": target_city.name if target_city is not None else target_node.name,
                    "minimum_supply": SNOW_GHOST_COLD_ROUTE_MIN_SUPPLY,
                    "supply_cost": SNOW_GHOST_COLD_ROUTE_SUPPLY_COST,
                    "morale_loss": SNOW_GHOST_COLD_ROUTE_MORALE_LOSS,
                    "shortage_morale_loss": SNOW_GHOST_COLD_ROUTE_SHORTAGE_MORALE_LOSS,
                }
            )
        item = crisis.to_dict()
        threatened_cities = []
        for city_id in crisis.threatened_city_ids:
            city = next((candidate for candidate in world.cities if candidate.city_id == city_id), None)
            if city is None:
                continue
            siege = next(
                (
                    candidate
                    for candidate in world.sieges
                    if candidate.city_id == city.city_id
                    and candidate.status in {"active", "contested", "breached"}
                ),
                None,
            )
            encounter = next(
                (
                    candidate
                    for candidate in world.encounters
                    if candidate.node_id == city.node_id and candidate.status == "active"
                ),
                None,
            )
            threatened_cities.append(
                {
                    "city_id": city.city_id,
                    "city_name": city.name,
                    "node_id": city.node_id,
                    "owner_faction_id": city.owner_faction_id,
                    "is_origin_target": city.node_id == crisis.origin_node_id,
                    "threat_status": (
                        "encounter"
                        if encounter is not None
                        else "siege"
                        if siege is not None
                        else "threatened"
                    ),
                }
            )
        crisis_armies = []
        for army_id in crisis.spawned_army_ids:
            army = next((candidate for candidate in world.armies if candidate.army_id == army_id), None)
            if army is None:
                continue
            army_payload = army.to_dict()
            army_payload["location_name"] = nodes_by_id[army.location_node_id].name
            crisis_armies.append(army_payload)
        showdown_battle = next(
            (
                battle
                for battle in world.pending_battles
                if battle.battle_id == crisis.showdown_battle_id
            ),
            None,
        )
        leader = next(
            (
                faction
                for faction in world.factions
                if faction.faction_id == crisis.showdown_leader_faction_id
            ),
            None,
        )
        item.update(
            {
                "name": "北方雪鬼危机",
                "stage_label": CRISIS_STAGE_LABELS.get(crisis.stage, crisis.stage),
                "origin_name": origin.name if origin is not None else crisis.origin_node_id,
                "frontier": frontier,
                "effect_summary": effect_summary,
                "route_effects": route_effects,
                "threatened_cities": threatened_cities,
                "crisis_armies": crisis_armies,
                "contribution_rows": [
                    {
                        "faction_id": faction.faction_id,
                        "faction_name": faction.name,
                        "contribution": int(crisis.contributions_by_faction.get(faction.faction_id, 0)),
                        "pledged_target_faction_id": crisis.cooperation_targets_by_faction.get(faction.faction_id),
                    }
                    for faction in world.factions
                    if faction.is_major
                ],
                "ai_intent_rows": [
                    {
                        **dict(decision),
                        "faction_name": next(
                            (
                                faction.name
                                for faction in world.factions
                                if faction.faction_id == decision.get("faction_id")
                            ),
                            str(decision.get("faction_id") or ""),
                        ),
                        "target_faction_name": next(
                            (
                                faction.name
                                for faction in world.factions
                                if faction.faction_id == decision.get("target_faction_id")
                            ),
                            None,
                        ),
                    }
                    for decision in sorted(
                        (
                            max(
                                (
                                    item
                                    for item in crisis.decisions
                                    if item.get("decision_origin") == "ai"
                                    and item.get("faction_id") == faction.faction_id
                                ),
                                key=lambda item: (int(item.get("month", 0)), str(item.get("id") or "")),
                                default=None,
                            )
                            for faction in world.factions
                            if faction.is_major
                        ),
                        key=lambda item: str((item or {}).get("faction_id") or ""),
                    )
                    if decision is not None
                ],
                "cooperations": [
                    {
                        "pair_key": pair_key,
                        "faction_ids": pair_key.split("::"),
                        "status": "broken" if pair_key in crisis.broken_cooperation_pairs else "active",
                    }
                    for pair_key in crisis.cooperation_pairs
                ],
                "choice_options_by_faction": {
                    faction.faction_id: world_crisis_choice_options(world, faction.faction_id)
                    for faction in world.factions
                    if faction.is_major
                },
                "showdown": (
                    {
                        "branch": crisis.showdown_branch,
                        "branch_label": SHOWDOWN_BRANCH_LABELS.get(
                            str(crisis.showdown_branch), str(crisis.showdown_branch or "")
                        ),
                        "leader_faction_id": crisis.showdown_leader_faction_id,
                        "leader_faction_name": leader.name if leader is not None else None,
                        "battle_id": showdown_battle.battle_id if showdown_battle is not None else None,
                        "battle_status": showdown_battle.status if showdown_battle is not None else None,
                        "resolution_mode": (
                            showdown_battle.resolution_mode if showdown_battle is not None else None
                        ),
                        "battle_room_id": (
                            showdown_battle.battle_room_id if showdown_battle is not None else None
                        ),
                        "battle_room_invite_path": (
                            showdown_battle.battle_room_invite_path
                            if showdown_battle is not None
                            else None
                        ),
                        "coalition_units": (
                            sum(showdown_battle.attacker_registered_units.values())
                            if showdown_battle is not None
                            else 0
                        ),
                        "snow_ghost_units": (
                            sum(showdown_battle.defender_registered_units.values())
                            if showdown_battle is not None
                            else 0
                        ),
                        "outcome": crisis.showdown_outcome,
                        "winner_faction_ids": list(crisis.mainline_winner_faction_ids),
                    }
                    if crisis.showdown_branch is not None
                    else None
                ),
                "can_locate": bool(frontier),
            }
        )
        payload.append(item)
    return payload
