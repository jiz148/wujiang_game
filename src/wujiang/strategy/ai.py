from __future__ import annotations

import copy

from wujiang.strategy.battles import MIN_ATTACK_TROOPS, city_attack_commitment, declare_city_attack
from wujiang.strategy.ai_goals import (
    ensure_ai_strategic_goal,
    preferred_attack_for_goal,
    preferred_policy_for_goal,
    update_ai_strategic_goal,
)
from wujiang.strategy.command import FACTION_MONTHLY_COMMAND_POINTS
from wujiang.strategy.exile import faction_is_exiled
from wujiang.strategy.heroes import (
    active_strategic_hero_codes_for_faction,
    appoint_strategic_hero_to_office,
    hero_ritual_capacity,
    perform_hero_ritual,
    set_strategic_defender_hero,
    strategic_heroes_for_faction_public,
)
from wujiang.strategy.models import City, EventLogEntry, Faction, WorldState
from wujiang.strategy.neutral_city_states import incitement_attack_pair
from wujiang.strategy.offices import ai_office_for_action, ensure_office_system
from wujiang.strategy.political_ai import ai_peace_treasury_reserve, apply_major_political_ai_actions
from wujiang.strategy.simulation import POLICIES, rebellion_risk
from wujiang.strategy.story import choose_ai_story_choice, pending_story_event_for_faction, resolve_story_event
from wujiang.strategy.tactics import TACTIC_TECH_TREE, set_city_policy, unlock_tactic_tech


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


def apply_strategy_ai_monthly_actions(
    world: WorldState,
    *,
    controlled_faction_ids: set[str] | frozenset[str] | list[str] | tuple[str, ...],
    enable_attacks: bool = True,
) -> WorldState:
    controlled = {str(faction_id) for faction_id in controlled_faction_ids}
    next_world = ensure_office_system(_clone_world(world))

    for faction_id in sorted(faction.faction_id for faction in next_world.factions):
        if faction_id in controlled or faction_is_exiled(next_world, faction_id):
            continue
        faction = next(faction for faction in next_world.factions if faction.faction_id == faction_id)
        if not _cities_for_faction(next_world, faction_id):
            continue

        if faction.is_neutral_city_state:
            actions: list[str] = []
            office_actions: list[str] = []
            command_remaining = FACTION_MONTHLY_COMMAND_POINTS
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
                        f"({FACTION_MONTHLY_COMMAND_POINTS - command_remaining}/{FACTION_MONTHLY_COMMAND_POINTS} command): "
                        f"{', '.join(actions) if actions else 'defend and hold'}."
                    ),
                    related_ids=[faction_id, *actions],
                )
            )
            continue

        actions: list[str] = []
        office_actions: list[str] = []
        command_remaining = FACTION_MONTHLY_COMMAND_POINTS
        strategic_goal = ensure_ai_strategic_goal(next_world, faction_id)
        goal_attack = preferred_attack_for_goal(next_world, faction_id, strategic_goal) if enable_attacks else None
        initial_attack = goal_attack
        if initial_attack is None and enable_attacks and (strategic_goal or {}).get("goal_type") != "ritual_reinforcement":
            initial_attack = _best_attack(next_world, faction)
        attack_reserve = 2 if initial_attack is not None else 0
        next_world, command_remaining, political_actions, political_office_actions = apply_major_political_ai_actions(
            next_world,
            faction_id=faction_id,
            command_remaining=command_remaining,
            attack_reserve=attack_reserve,
            strategic_goal=strategic_goal,
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
        if policy_choice is not None and command_remaining >= 1:
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
        if story_event is not None and command_remaining >= 1:
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
        peace_treasury_reserve = ai_peace_treasury_reserve(next_world, faction_id)
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
            and command_remaining - 1 >= attack_reserve
            and faction.resources.money - tech.money_cost >= peace_treasury_reserve
        ):
            next_world = unlock_tactic_tech(next_world, faction_id=faction_id, tech_id=tech_id)
            action = f"tech:{tech_id}"
            actions.append(action)
            office_actions.append(f"{tech_office.office_id}:{action}")
            command_remaining -= 1
            faction = next(faction for faction in next_world.factions if faction.faction_id == faction_id)

        ritual_city = next(
            (
                city
                for city in sorted(_cities_for_faction(next_world, faction_id), key=lambda item: (-item.resources.ether, item.city_id))
                if int(city.building_levels.get("ritual_site", 0)) > 0 and city.resources.ether >= 30
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

        if enable_attacks and command_remaining >= 2:
            attack = preferred_attack_for_goal(next_world, faction_id, strategic_goal)
            if attack is None and (strategic_goal or {}).get("goal_type") != "capture_city":
                attack = _best_attack(next_world, faction)
            if attack is not None:
                source_city_id, target_city_id = attack
                attack_office = ai_office_for_action(
                    next_world,
                    faction_id=faction_id,
                    action_type="declare_attack",
                    payload={"source_city_id": source_city_id, "target_city_id": target_city_id},
                )
                if attack_office is not None:
                    next_world = declare_city_attack(
                        next_world,
                        faction_id=faction_id,
                        source_city_id=source_city_id,
                        target_city_id=target_city_id,
                        resolution_mode="quick",
                        auto_resolve=True,
                        attacker_hero_codes=active_strategic_hero_codes_for_faction(next_world, faction_id),
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
                    f"({FACTION_MONTHLY_COMMAND_POINTS - command_remaining}/{FACTION_MONTHLY_COMMAND_POINTS} command): "
                    f"{', '.join(actions) if actions else 'hold'}."
                ),
                related_ids=[faction_id, *actions],
            )
        )
        next_world.validate()

    return next_world
