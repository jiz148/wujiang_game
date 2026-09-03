from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from http import HTTPStatus
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wujiang.strategic import (  # noqa: E402
    FACTION_MONTHLY_COMMAND_POINTS,
    SNOW_GHOST_COLD_ROUTE_MIN_SUPPLY,
    SNOW_GHOST_FACTION_ID,
    SNOW_GHOST_MOBILIZATION_MONTH,
    SNOW_GHOST_SHOWDOWN_MONTH,
    SNOW_GHOST_VANGUARD_ARMY_ID,
    STRATEGIC_HERO_BATTLE_SLEEP_MONTHS,
    RELIC_CATALOG,
    StrategyError,
    StrategyStore,
    active_strategic_hero_codes_for_faction,
    advance_hero_personal_states,
    advance_month,
    advance_relic_maintenance,
    advance_story_events,
    apply_rebellion_action,
    apply_rebellion_battle,
    apply_strategy_ai_monthly_actions,
    apply_strategy_ai_showdown_action,
    ai_strategic_goals_public,
    apply_exile_action,
    apply_office_order,
    apply_neutral_diplomacy_action,
    apply_peaceful_integration,
    apply_occupation_policy,
    occupation_status_public,
    apply_rebellion_funding,
    appoint_strategic_hero_to_office,
    assign_strategic_hero_duty,
    bind_relic,
    hero_ritual_capacity,
    hero_command_accepts,
    hero_skills_for_code,
    STRATEGIC_SKILLS,
    perform_hero_ritual,
    unbind_strategic_hero,
    accept_hero_recruitment,
    recommend_hero_recruitment,
    attach_battle_room,
    auto_battle_composition,
    city_troop_conversion,
    choose_player_hero_path,
    archive_campaign,
    continue_campaign_as_sandbox,
    declare_city_attack,
    declare_strategic_battle,
    evaluate_strategic_status,
    ensure_office_system,
    ensure_relic_system,
    ensure_strategic_hero_system,
    ensure_world_crises,
    faction_command_points,
    form_or_reinforce_army,
    advance_army_encounters,
    advance_army_movements,
    advance_army_retreats,
    advance_army_supply,
    advance_sieges,
    army_supply_plan,
    disband_army,
    halt_army_march,
    order_army_march,
    order_army_intercept,
    order_army_reinforce,
    order_army_retreat,
    order_siege_attacker_stance,
    order_siege_defender_stance,
    load_army_supply,
    shortest_army_route,
    snow_ghost_cold_route_keys,
    first_campaign_contract,
    quick_campaign_contract,
    quick_campaign_opening_status,
    quick_campaign_recommendations,
    apply_quick_campaign_opening_choice,
    generate_random_world,
    monthly_briefings_public,
    neutral_city_state_profile,
    neutral_diplomacy_options_public,
    peaceful_integration_option,
    campaign_assessment_rankings,
    issue_hero_recruitment,
    levy_field_troops,
    levy_city_garrison,
    increase_city_troops,
    incite_neutral_city_state,
    register_city_soldiers,
    transfer_registered_units,
    request_registered_units,
    approve_registered_unit_request,
    apply_explore_city,
    apply_faction_diplomacy_action,
    city_is_visible,
    explore_options,
    mask_world_public_for_faction,
    visible_city_ids,
    city_building_max_level,
    city_building_monthly_bonus,
    construct_city_building,
    start_city_work,
    upgrade_city_settlement,
    nearby_roaming_hero_codes,
    grand_general_capacity,
    general_capacity_per_grand_general,
    open_monthly_story_events,
    open_spontaneous_allegiance_request,
    rebellion_action_choices_public,
    rebellion_force_troops,
    rebellion_risk,
    relic_system_public,
    repair_relic,
    release_relic,
    record_strategic_hero_battle_losses,
    search_relic,
    transfer_relic,
    record_strategic_status_events,
    require_campaign_orders_open,
    resolve_battle_room_result,
    resolve_pending_battle,
    simulate_formula_city_attack,
    retreat_pending_battle,
    convert_pending_battle_to_siege,
    set_pending_battle_composition,
    resolve_action_office,
    resolve_story_event,
    resolve_world_crisis_choice,
    set_world_crisis_showdown_resolution,
    roster_for_city_troops,
    roster_for_registered_units,
    set_battle_defender_hero,
    set_city_policy,
    set_strategic_defender_hero,
    strategic_defender_hero_codes_for_faction,
    strategic_hero_deployment_limit,
    strategic_hero_home_faction_id,
    strategic_hero_pool_public,
    strategy_battle_rosters,
    strategy_action_command_cost,
    story_events_public,
    summon_strategic_hero,
    normalize_strategic_hero_deployment,
    tactic_tech_tree_public,
    unlock_tactic_tech,
    cancel_tactic_research,
    advance_tactic_research,
    validate_rebellion_action,
    validate_rebellion_battle,
    validate_relic_search,
    validate_bind_relic,
    validate_release_relic,
    validate_summon_strategic_hero,
    validate_story_event_choice,
    validate_world_crisis_choice,
    validate_exile_action,
    strategic_route_key,
    world_crises_public,
)
from wujiang.strategic.models import City, DiplomaticAgreement, Faction, MapNode, PendingBattle, ResourceBundle, StoryEvent, WorldState  # noqa: E402
from wujiang.strategic.migrations import CURRENT_STRATEGY_SAVE_VERSION, migrate_world_payload  # noqa: E402
from wujiang.strategic.monthly_cycle import forecast_city_month, monthly_cycle_public, record_monthly_report  # noqa: E402
from wujiang.strategic.campaign_tutorial import campaign_tutorial_public, update_campaign_tutorial  # noqa: E402
from wujiang.strategic.office_automation import apply_player_office_automation, office_coordination_public  # noqa: E402
from wujiang.strategic.occupation import mark_city_captured  # noqa: E402
from wujiang.strategic.political_ai import apply_major_political_ai_actions  # noqa: E402
from wujiang.strategic.rebellion import set_rebellion_force_troops  # noqa: E402
from wujiang.platform.auth import AuthUser  # noqa: E402
from wujiang.tactical.heroes.registry import create_battle, list_heroes  # noqa: E402


def _ensure_city_road(world: WorldState, source_city_id: str, target_city_id: str) -> None:
    source = next(city for city in world.cities if city.city_id == source_city_id)
    target = next(city for city in world.cities if city.city_id == target_city_id)
    source_node = next(node for node in world.nodes if node.node_id == source.node_id)
    target_node = next(node for node in world.nodes if node.node_id == target.node_id)
    if target_node.node_id not in source_node.connected_node_ids:
        source_node.connected_node_ids.append(target_node.node_id)
        source_node.connected_node_ids.sort()
    if source_node.node_id not in target_node.connected_node_ids:
        target_node.connected_node_ids.append(source_node.node_id)
        target_node.connected_node_ids.sort()


def _neutral_bordering_faction(world: WorldState, target_faction_id: str) -> str:
    from wujiang.strategic.neutral_city_states import adjacent_city_ids

    target_city_ids = {city.city_id for city in world.cities if city.owner_faction_id == target_faction_id}
    for faction in world.factions:
        if not faction.is_neutral_city_state:
            continue
        owned = [city for city in world.cities if city.owner_faction_id == faction.faction_id]
        if any(adjacent_city_ids(world, city.city_id) & target_city_ids for city in owned):
            return faction.faction_id
    raise AssertionError(f"no neutral city state borders {target_faction_id}")


class StrategyGenerationTests(unittest.TestCase):
    def test_r1_quick_campaign_is_compact_and_opening_choice_changes_real_state_once(self) -> None:
        world = generate_random_world(seed=42, campaign_contract=quick_campaign_contract())

        self.assertEqual(len(world.cities), 5)
        self.assertEqual(len([item for item in world.factions if item.is_major]), 2)
        self.assertEqual(len([item for item in world.factions if item.is_neutral_city_state]), 3)
        self.assertEqual(world.campaign_contract["month_limit"], 6)
        self.assertEqual(world.campaign_contract["expected_duration_minutes"], [25, 35])
        self.assertEqual(world.world_crises, [])
        self.assertEqual(world.relics, [])
        quick_status = evaluate_strategic_status(world)
        locked_conditions = {
            item["id"]: item["implemented"]
            for item in quick_status["victory_conditions"]
            if item["id"] == "world_mainline"
        }
        self.assertEqual(locked_conditions, {"world_mainline": False})
        self.assertFalse(any(item["id"] == "relic_altar" for item in quick_status["victory_conditions"]))

        opening = quick_campaign_opening_status(world, "faction_1")
        assert opening is not None
        self.assertTrue(opening["available"])
        self.assertEqual([item["id"] for item in opening["choices"]], ["stabilize", "diplomacy", "mobilize"])
        capital = next(item for item in world.cities if item.city_id == "city_1")
        before_troops = capital.resources.troops
        before_food = next(item for item in world.factions if item.faction_id == "faction_1").resources.food

        updated = apply_quick_campaign_opening_choice(
            world,
            faction_id="faction_1",
            choice_id="mobilize",
        )

        updated_capital = next(item for item in updated.cities if item.city_id == "city_1")
        updated_faction = next(item for item in updated.factions if item.faction_id == "faction_1")
        self.assertEqual(updated_capital.resources.troops, before_troops + 180)
        self.assertEqual(updated_faction.resources.food, before_food + 100)
        self.assertFalse(quick_campaign_opening_status(updated, "faction_1")["available"])
        self.assertTrue(any(event.category == "quick_campaign_opening_choice" for event in updated.event_log))
        pacing = quick_campaign_recommendations(updated, "faction_1")
        assert pacing is not None
        self.assertLessEqual(len(pacing["recommendations"]), 3)
        self.assertEqual(pacing["recommendations"][0]["kind"], "military")
        self.assertTrue(pacing["conflict_window"]["available"])
        self.assertEqual(pacing["conflict_window"]["expected_month"], 2)
        self.assertEqual(
            {item["kind"] for item in pacing["recommendations"]},
            {"governance", "diplomacy", "military"},
        )
        self.assertTrue(all(item["available"] for item in pacing["recommendations"]))
        military = pacing["recommendations"][0]
        self.assertEqual(military["recommended_action"]["action_type"], "declare_attack")
        self.assertEqual(military["recommended_action"]["command_cost"], 2)
        with self.assertRaisesRegex(StrategyError, "已经完成"):
            apply_quick_campaign_opening_choice(updated, faction_id="faction_1", choice_id="stabilize")
        updated.current_month = 4
        later_pacing = quick_campaign_recommendations(updated, "faction_1")
        assert later_pacing is not None
        self.assertEqual(later_pacing["conflict_window"]["expected_month"], 4)
        self.assertIn("本月可执行", later_pacing["conflict_window"]["summary"])
        updated.current_month = 6
        conclusion = evaluate_strategic_status(updated)["conclusion"]
        self.assertEqual(conclusion["reason"], "time_limit")
        self.assertEqual(conclusion["result_label"], "六月评议")

    def test_random_world_is_deterministic_and_connected(self) -> None:
        first = generate_random_world(seed=42, city_count=7, faction_count=3)
        second = generate_random_world(seed=42, city_count=7, faction_count=3)

        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(len(first.cities), 7)
        self.assertEqual(len(first.factions), 3)
        self.assertTrue(all(city.troop_features for city in first.cities))
        self.assertTrue(all(faction.color.startswith("#") for faction in first.factions))
        self.assertEqual(len({faction.color for faction in first.factions}), 3)
        self.assertTrue(all(city.settlement in {"village", "town", "city", "fortress"} for city in first.cities))

        graph = {node.node_id: set(node.connected_node_ids) for node in first.nodes}
        seen = set()
        stack = [first.nodes[0].node_id]
        while stack:
            node_id = stack.pop()
            if node_id in seen:
                continue
            seen.add(node_id)
            stack.extend(sorted(graph[node_id] - seen))
        self.assertEqual(seen, set(graph))

    def test_first_campaign_builds_deterministic_relic_archive_and_capital_altars(self) -> None:
        first = generate_random_world(
            seed=611,
            city_count=8,
            faction_count=2,
            neutral_city_states=True,
            campaign_contract=first_campaign_contract(),
        )
        second = generate_random_world(
            seed=611,
            city_count=8,
            faction_count=2,
            neutral_city_states=True,
            campaign_contract=first_campaign_contract(),
        )

        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(len(first.cities), 20)
        self.assertEqual(len(first.nodes), 20)
        self.assertGreaterEqual(max(node.x for node in first.nodes) - min(node.x for node in first.nodes), 60)
        self.assertGreaterEqual(max(node.y for node in first.nodes) - min(node.y for node in first.nodes), 50)
        self.assertEqual(len(first.relics), len(RELIC_CATALOG))
        self.assertEqual({relic.effect_id for relic in first.relics}, set(RELIC_CATALOG))
        ritual_cities = {
            city.city_id
            for city in first.cities
            if int(city.building_levels.get("ritual_site", 0) or 0) >= 1
        }
        self.assertEqual(len(first.relic_altars), len(ritual_cities))
        self.assertTrue(all(altar.state == "dormant" for altar in first.relic_altars))
        self.assertTrue(all(relic.state == "scattered" for relic in first.relics))
        self.assertTrue(all(relic.location_node_id for relic in first.relics))
        self.assertEqual({altar.city_id for altar in first.relic_altars}, ritual_cities)
        public = first.to_public_dict()
        self.assertNotIn("relics", public)
        self.assertNotIn("relic_altars", public)
        self.assertEqual(public["relic_system"]["phase"], "relic_bonus")
        self.assertNotIn("relic_altar_victory", first.campaign_contract["available_victory_routes"])
        for faction in (item for item in first.factions if item.is_major):
            intel = public["relic_system"]["intel_by_faction"][faction.faction_id]
            self.assertEqual(intel["known_count"], 1)
            self.assertEqual(intel["unknown_count"], len(first.relics) - 1)
            self.assertTrue(intel["known_relics"][0]["location_city_name"])
            self.assertTrue(intel["search_options"])

    def test_campaign_opening_variants_persist_versions_and_apply_only_declared_modifiers(self) -> None:
        classic = generate_random_world(
            seed=812,
            city_count=8,
            faction_count=2,
            neutral_city_states=True,
            campaign_contract=first_campaign_contract("classic_frontier"),
        )
        hungry = generate_random_world(
            seed=812,
            city_count=8,
            faction_count=2,
            neutral_city_states=True,
            campaign_contract=first_campaign_contract("hungry_frontier"),
        )
        fortified = generate_random_world(
            seed=812,
            city_count=8,
            faction_count=2,
            neutral_city_states=True,
            campaign_contract=first_campaign_contract("fortified_leagues"),
        )
        ether = generate_random_world(
            seed=812,
            city_count=8,
            faction_count=2,
            neutral_city_states=True,
            campaign_contract=first_campaign_contract("ether_tide"),
        )

        self.assertTrue(classic.campaign_contract["content_version"])
        self.assertTrue(classic.campaign_contract["balance_version"])
        self.assertEqual(hungry.campaign_contract["opening_variant"]["id"], "hungry_frontier")
        for classic_city, hungry_city in zip(classic.cities, hungry.cities, strict=True):
            self.assertEqual(hungry_city.resources.food, classic_city.resources.food * 70 // 100)
        for classic_faction, hungry_faction in zip(classic.factions, hungry.factions, strict=True):
            expected = classic_faction.resources.food * 75 // 100 if classic_faction.is_major else classic_faction.resources.food
            self.assertEqual(hungry_faction.resources.food, expected)
        classic_neutral = next(city for city in classic.cities if city.owner_faction_id.startswith("neutral_city_state_"))
        fortified_neutral = next(city for city in fortified.cities if city.city_id == classic_neutral.city_id)
        self.assertEqual(fortified_neutral.defense, classic_neutral.defense + 2)
        self.assertEqual(fortified_neutral.resources.troops, classic_neutral.resources.troops + 120)
        self.assertEqual(
            fortified_neutral.support_by_faction["local_autonomy"],
            min(100, classic_neutral.support_by_faction["local_autonomy"] + 15),
        )
        for classic_city, ether_city in zip(classic.cities, ether.cities, strict=True):
            self.assertEqual(ether_city.resources.ether, classic_city.resources.ether + 60)
        for classic_faction, ether_faction in zip(classic.factions, ether.factions, strict=True):
            if classic_faction.is_major:
                self.assertEqual(ether_faction.resources.ether, classic_faction.resources.ether + 30)
                self.assertEqual(ether_faction.resources.money, classic_faction.resources.money - 80)
        with self.assertRaises(StrategyError):
            first_campaign_contract("unknown_variant")

    def test_relic_search_consumes_hero_action_and_stores_result_deterministically(self) -> None:
        world = generate_random_world(
            seed=611,
            city_count=8,
            faction_count=2,
            neutral_city_states=True,
            campaign_contract=first_campaign_contract(),
        )
        faction = next(item for item in world.factions if item.faction_id == "faction_1")
        city = next(item for item in world.cities if item.city_id == faction.capital_city_id)
        lord = next(
            item for item in world.offices
            if item.faction_id == faction.faction_id and item.office_type == "lord"
        )
        hero = next(
            item for item in world.strategic_heroes
            if item.faction_id == faction.faction_id and item.status == "serving" and item.city_id == city.city_id
        )
        relic = next(
            item for item in world.relics
            if faction.faction_id in item.discovered_by_faction_ids
        )
        food_before = city.resources.food

        searched = search_relic(
            world,
            faction_id=faction.faction_id,
            relic_id=relic.relic_id,
            hero_code=hero.hero_code,
            city_id=city.city_id,
            issuer_office_id=lord.office_id,
        )
        stored = next(item for item in searched.relics if item.relic_id == relic.relic_id)
        searched_city = next(item for item in searched.cities if item.city_id == city.city_id)
        searched_hero = next(item for item in searched.strategic_heroes if item.hero_code == hero.hero_code)
        self.assertEqual((stored.state, stored.location_city_id, stored.owner_faction_id), ("stored", city.city_id, faction.faction_id))
        self.assertIn(stored.relic_id, searched_city.relics_stored)
        self.assertEqual(searched_city.resources.food, food_before - 20)
        self.assertEqual(searched_hero.last_personal_action_month, world.current_month)
        alternate = next(item for item in searched.relics if item.state == "scattered")
        alternate.location_node_id = city.node_id
        alternate.discovered_by_faction_ids.append(faction.faction_id)
        with self.assertRaises(StrategyError):
            validate_relic_search(
                searched,
                faction_id=faction.faction_id,
                relic_id=alternate.relic_id,
                hero_code=hero.hero_code,
                city_id=city.city_id,
                issuer_office_id=lord.office_id,
            )

    def test_relic_transfer_moves_one_owned_edge_and_repair_spends_real_resources(self) -> None:
        world = generate_random_world(
            seed=611,
            city_count=8,
            faction_count=2,
            neutral_city_states=True,
            campaign_contract=first_campaign_contract(),
        )
        faction = next(item for item in world.factions if item.faction_id == "faction_1")
        source = next(item for item in world.cities if item.city_id == faction.capital_city_id)
        source_node = next(item for item in world.nodes if item.node_id == source.node_id)
        target = next(item for item in world.cities if item.node_id in source_node.connected_node_ids)
        target.owner_faction_id = faction.faction_id
        lord = next(
            item for item in world.offices
            if item.faction_id == faction.faction_id and item.office_type == "lord"
        )
        hero = next(
            item for item in world.strategic_heroes
            if item.faction_id == faction.faction_id and item.status == "serving" and item.city_id == source.city_id
        )
        relic = next(item for item in world.relics if faction.faction_id in item.discovered_by_faction_ids)
        stored_world = search_relic(
            world,
            faction_id=faction.faction_id,
            relic_id=relic.relic_id,
            hero_code=hero.hero_code,
            city_id=source.city_id,
            issuer_office_id=lord.office_id,
        )
        transferred = transfer_relic(
            stored_world,
            faction_id=faction.faction_id,
            relic_id=relic.relic_id,
            target_city_id=target.city_id,
            issuer_office_id=lord.office_id,
        )
        moved = next(item for item in transferred.relics if item.relic_id == relic.relic_id)
        self.assertEqual(moved.location_city_id, target.city_id)
        self.assertNotIn(relic.relic_id, next(item for item in transferred.cities if item.city_id == source.city_id).relics_stored)
        self.assertIn(relic.relic_id, next(item for item in transferred.cities if item.city_id == target.city_id).relics_stored)

        moved.condition = "damaged"
        faction_after = next(item for item in transferred.factions if item.faction_id == faction.faction_id)
        target_after = next(item for item in transferred.cities if item.city_id == target.city_id)
        money_before = faction_after.resources.money
        ether_before = target_after.resources.ether
        repaired = repair_relic(
            transferred,
            faction_id=faction.faction_id,
            relic_id=relic.relic_id,
            issuer_office_id=lord.office_id,
        )
        repaired_relic = next(item for item in repaired.relics if item.relic_id == relic.relic_id)
        self.assertEqual(repaired_relic.condition, "intact")
        self.assertEqual(
            next(item for item in repaired.factions if item.faction_id == faction.faction_id).resources.money,
            money_before - 40,
        )
        self.assertEqual(
            next(item for item in repaired.cities if item.city_id == target.city_id).resources.ether,
            ether_before - 20,
        )

    def test_relic_altar_binding_maintenance_failure_recovery_and_release_form_a_cycle(self) -> None:
        world = generate_random_world(
            seed=611,
            city_count=8,
            faction_count=2,
            neutral_city_states=True,
            campaign_contract=first_campaign_contract(),
        )
        faction = next(item for item in world.factions if item.faction_id == "faction_1")
        city = next(item for item in world.cities if item.city_id == faction.capital_city_id)
        altar = next(item for item in world.relic_altars if item.city_id == city.city_id)
        lord = next(
            item for item in world.offices
            if item.faction_id == faction.faction_id and item.office_type == "lord"
        )
        hero = next(
            item for item in world.strategic_heroes
            if item.faction_id == faction.faction_id and item.status == "serving" and item.city_id == city.city_id
        )
        relic = next(item for item in world.relics if faction.faction_id in item.discovered_by_faction_ids)
        stored_world = search_relic(
            world,
            faction_id=faction.faction_id,
            relic_id=relic.relic_id,
            hero_code=hero.hero_code,
            city_id=city.city_id,
            issuer_office_id=lord.office_id,
        )
        next(item for item in stored_world.relics if item.relic_id == relic.relic_id).condition = "intact"
        bound_world = bind_relic(
            stored_world,
            faction_id=faction.faction_id,
            relic_id=relic.relic_id,
            altar_id=altar.altar_id,
            issuer_office_id=lord.office_id,
        )
        bound_relic = next(item for item in bound_world.relics if item.relic_id == relic.relic_id)
        bound_altar = next(item for item in bound_world.relic_altars if item.altar_id == altar.altar_id)
        bound_city = next(item for item in bound_world.cities if item.city_id == city.city_id)
        self.assertEqual(bound_relic.state, "bound_to_altar")
        self.assertEqual(bound_relic.altar_id, altar.altar_id)
        self.assertNotIn(relic.relic_id, bound_city.relics_stored)
        self.assertEqual(bound_altar.bound_relic_ids, [relic.relic_id])
        self.assertEqual((bound_altar.state, bound_altar.actions_used), ("active", 1))
        with self.assertRaises(StrategyError):
            validate_release_relic(
                bound_world,
                faction_id=faction.faction_id,
                relic_id=relic.relic_id,
                issuer_office_id=lord.office_id,
            )

        bound_world.current_month += 1
        bound_city.resources.ether = 50
        maintained = advance_relic_maintenance(bound_world)
        maintained_city = next(item for item in maintained.cities if item.city_id == city.city_id)
        self.assertEqual(maintained_city.resources.ether, 40)
        self.assertEqual(
            next(item for item in maintained.relic_altars if item.altar_id == altar.altar_id).state,
            "active",
        )
        self.assertEqual(
            next(item for item in advance_relic_maintenance(maintained).cities if item.city_id == city.city_id).resources.ether,
            40,
        )

        maintained.current_month += 1
        maintained_city.resources.ether = 7
        failed = advance_relic_maintenance(maintained)
        self.assertEqual(
            next(item for item in failed.cities if item.city_id == city.city_id).resources.ether,
            7,
        )
        self.assertEqual(
            next(item for item in failed.relic_altars if item.altar_id == altar.altar_id).state,
            "damaged",
        )

        failed.current_month += 1
        next(item for item in failed.cities if item.city_id == city.city_id).resources.ether = 20
        recovered = advance_relic_maintenance(failed)
        self.assertEqual(
            next(item for item in recovered.cities if item.city_id == city.city_id).resources.ether,
            10,
        )
        self.assertEqual(
            next(item for item in recovered.relic_altars if item.altar_id == altar.altar_id).state,
            "active",
        )

        released = release_relic(
            recovered,
            faction_id=faction.faction_id,
            relic_id=relic.relic_id,
            issuer_office_id=lord.office_id,
        )
        released_again = release_relic(
            WorldState.from_dict(recovered.to_dict()),
            faction_id=faction.faction_id,
            relic_id=relic.relic_id,
            issuer_office_id=lord.office_id,
        )
        released_relic = next(item for item in released.relics if item.relic_id == relic.relic_id)
        self.assertEqual(released_relic.state, "released")
        self.assertIsNone(released_relic.owner_faction_id)
        self.assertIsNone(released_relic.location_city_id)
        self.assertIsNone(released_relic.altar_id)
        self.assertNotEqual(released_relic.location_node_id, city.node_id)
        self.assertIn(faction.faction_id, released_relic.discovered_by_faction_ids)
        self.assertEqual(
            released_relic.location_node_id,
            next(item for item in released_again.relics if item.relic_id == relic.relic_id).location_node_id,
        )
        self.assertEqual(
            next(item for item in released.relic_altars if item.altar_id == altar.altar_id).state,
            "dormant",
        )

    def test_releasing_a_sleeping_hero_relic_preserves_sleep_and_unbinds_the_hero(self) -> None:
        world = generate_random_world(
            seed=613,
            city_count=8,
            faction_count=2,
            neutral_city_states=True,
            campaign_contract=first_campaign_contract(),
        )
        faction = next(item for item in world.factions if item.faction_id == "faction_1")
        city = next(item for item in world.cities if item.city_id == faction.capital_city_id)
        lord = next(
            item for item in world.offices
            if item.faction_id == faction.faction_id and item.office_type == "lord"
        )
        hero = next(
            item for item in world.strategic_heroes
            if item.faction_id == faction.faction_id
            and item.status == "serving"
            and item.hero_code != lord.holder_id
        )
        relic = next(item for item in world.relics if item.state == "scattered")
        relic.hero_code = hero.hero_code
        altar = next(item for item in world.relic_altars if item.city_id == city.city_id)
        relic.state = "stored"
        relic.condition = "intact"
        relic.location_node_id = city.node_id
        relic.location_city_id = city.city_id
        relic.owner_faction_id = faction.faction_id
        relic.discovered_by_faction_ids.append(faction.faction_id)
        city.relics_stored.append(relic.relic_id)
        bound = bind_relic(
            world,
            faction_id=faction.faction_id,
            relic_id=relic.relic_id,
            altar_id=altar.altar_id,
            issuer_office_id=lord.office_id,
        )
        bound_hero = next(item for item in bound.strategic_heroes if item.hero_code == hero.hero_code)
        bound_hero.status = "sleeping"
        bound_hero.sleeping_until_month = bound.current_month + 2
        sleeping_until = bound_hero.sleeping_until_month
        bound.current_month += 1

        released = release_relic(
            bound,
            faction_id=faction.faction_id,
            relic_id=relic.relic_id,
            issuer_office_id=lord.office_id,
        )
        released_hero = next(item for item in released.strategic_heroes if item.hero_code == hero.hero_code)
        self.assertEqual(released_hero.status, "sleeping")
        self.assertEqual(released_hero.sleeping_until_month, sleeping_until)
        self.assertIsNone(released_hero.faction_id)
        self.assertIsNone(released_hero.office_id)
        self.assertIsNone(released_hero.ritual_city_id)
        self.assertTrue(
            any(event.category == "hero_relic_unbound_while_sleeping" for event in released.event_log)
        )

    def test_city_capture_transfers_stored_and_bound_relics_and_disrupts_altar(self) -> None:
        world = generate_random_world(
            seed=614,
            city_count=8,
            faction_count=2,
            neutral_city_states=True,
            campaign_contract=first_campaign_contract(),
        )
        attacker = next(item for item in world.factions if item.faction_id == "faction_1")
        defender = next(item for item in world.factions if item.faction_id == "faction_2")
        source = next(item for item in world.cities if item.city_id == attacker.capital_city_id)
        target = next(item for item in world.cities if item.city_id == defender.capital_city_id)
        source_node = next(item for item in world.nodes if item.node_id == source.node_id)
        target_node = next(item for item in world.nodes if item.node_id == target.node_id)
        if target.node_id not in source_node.connected_node_ids:
            source_node.connected_node_ids.append(target.node_id)
            target_node.connected_node_ids.append(source.node_id)
        source.resources.troops = 2400
        target.resources.troops = 0
        target.registered_units = {}
        target.defense = 0
        target.support_by_faction[defender.faction_id] = 0
        defender_lord = next(
            item for item in world.offices
            if item.faction_id == defender.faction_id and item.office_type == "lord"
        )
        sleeping_hero = next(
            item for item in world.strategic_heroes
            if item.faction_id == defender.faction_id
            and item.status == "serving"
            and item.hero_code != defender_lord.holder_id
        )
        alternate_anchor = next(
            item
            for item in world.cities
            if item.city_id not in {source.city_id, target.city_id}
        )
        alternate_anchor.owner_faction_id = defender.faction_id
        sleeping_hero.ritual_city_id = alternate_anchor.city_id
        sleeping_hero.city_id = alternate_anchor.city_id
        sleeping_hero.status = "sleeping"
        sleeping_hero.sleeping_until_month = world.current_month + 2
        bound_relic = next(item for item in world.relics if item.state == "scattered")
        bound_relic.hero_code = sleeping_hero.hero_code
        stored_relic = next(
            item for item in world.relics
            if item.relic_id != bound_relic.relic_id
        )
        altar = next(item for item in world.relic_altars if item.city_id == target.city_id)
        bound_relic.state = "bound_to_altar"
        bound_relic.condition = "intact"
        bound_relic.location_node_id = target.node_id
        bound_relic.location_city_id = target.city_id
        bound_relic.owner_faction_id = defender.faction_id
        bound_relic.altar_id = altar.altar_id
        altar.bound_relic_ids = [bound_relic.relic_id]
        altar.state = "active"
        stored_relic.state = "stored"
        stored_relic.condition = "damaged"
        stored_relic.location_node_id = target.node_id
        stored_relic.location_city_id = target.city_id
        stored_relic.owner_faction_id = defender.faction_id
        target.relics_stored = [stored_relic.relic_id]

        resolved = declare_city_attack(
            world,
            faction_id=attacker.faction_id,
            source_city_id=source.city_id,
            target_city_id=target.city_id,
            resolution_mode="quick",
        )
        captured = {
            relic.relic_id: relic
            for relic in resolved.relics
            if relic.relic_id in {stored_relic.relic_id, bound_relic.relic_id}
        }
        resolved_altar = next(item for item in resolved.relic_altars if item.altar_id == altar.altar_id)
        released_hero = next(
            item for item in resolved.strategic_heroes if item.hero_code == sleeping_hero.hero_code
        )
        defender_lord_after = next(
            item for item in resolved.strategic_heroes if item.hero_code == defender_lord.holder_id
        )
        control_change = resolved.pending_battles[-1].battle_result["city_control_change"]
        self.assertTrue(all(item.owner_faction_id == attacker.faction_id for item in captured.values()))
        self.assertEqual(captured[stored_relic.relic_id].condition, "damaged")
        self.assertEqual(captured[bound_relic.relic_id].altar_id, altar.altar_id)
        self.assertEqual(resolved_altar.state, "damaged")
        self.assertEqual(resolved_altar.action_month, resolved.current_month)
        self.assertEqual(resolved_altar.actions_used, 1)
        self.assertEqual(released_hero.status, "sleeping")
        self.assertEqual(released_hero.sleeping_until_month, world.current_month + 2)
        self.assertIsNone(released_hero.faction_id)
        self.assertEqual(defender_lord_after.faction_id, defender.faction_id)
        self.assertCountEqual(
            control_change["captured_relic_ids"],
            [stored_relic.relic_id, bound_relic.relic_id],
        )
        self.assertIn(sleeping_hero.hero_code, control_change["unbound_hero_codes"])
        self.assertTrue(
            any(event.category == "relics_captured_on_city_control_change" for event in resolved.event_log)
        )

    def test_hero_defeated_after_an_earlier_relic_loss_becomes_sleeping_and_roaming(self) -> None:
        world = generate_random_world(
            seed=615,
            city_count=8,
            faction_count=2,
            neutral_city_states=True,
            campaign_contract=first_campaign_contract(),
        )
        faction_id = "faction_1"
        lord = next(
            item for item in world.offices
            if item.faction_id == faction_id and item.office_type == "lord"
        )
        hero = next(
            item for item in world.strategic_heroes
            if item.faction_id == faction_id
            and item.status == "serving"
            and item.hero_code != lord.holder_id
        )
        relic = next(item for item in world.relics if item.state == "scattered")
        relic.hero_code = hero.hero_code
        enemy_city = next(item for item in world.cities if item.owner_faction_id == "faction_2")
        relic.state = "stored"
        relic.condition = "intact"
        relic.owner_faction_id = "faction_2"
        relic.location_node_id = enemy_city.node_id
        relic.location_city_id = enemy_city.city_id
        relic.altar_id = None
        enemy_city.relics_stored.append(relic.relic_id)
        relic.history.append(
            {
                "month": world.current_month,
                "event": "captured_on_city_control_change",
                "previous_faction_id": faction_id,
                "new_faction_id": "faction_2",
            }
        )
        resolved, result = record_strategic_hero_battle_losses(
            world,
            attacker_faction_id=faction_id,
            defender_faction_id="faction_2",
            surviving_hero_codes_by_team={1: set(), 2: set()},
            committed_hero_codes_by_team={1: [hero.hero_code], 2: []},
        )
        defeated = next(item for item in resolved.strategic_heroes if item.hero_code == hero.hero_code)
        self.assertEqual(result["attacker"]["sleeping"], [hero.hero_code])
        self.assertEqual(defeated.status, "sleeping")
        self.assertIsNone(defeated.faction_id)
        self.assertIsNone(defeated.ritual_city_id)
        self.assertEqual(defeated.sleeping_until_month, world.current_month + STRATEGIC_HERO_BATTLE_SLEEP_MONTHS)

    def test_relic_archive_round_trips_and_legacy_sandbox_stays_disabled(self) -> None:
        world = generate_random_world(
            seed=612,
            city_count=8,
            faction_count=2,
            neutral_city_states=True,
            campaign_contract=first_campaign_contract(),
        )
        restored = WorldState.from_dict(world.to_dict())
        self.assertEqual(restored.to_dict(), world.to_dict())
        self.assertIs(ensure_relic_system(restored), restored)
        removed = restored.relics.pop()
        known_before = {
            faction.faction_id: sum(
                faction.faction_id in relic.discovered_by_faction_ids for relic in restored.relics
            )
            for faction in restored.factions
            if faction.is_major
        }
        expanded = ensure_relic_system(restored)
        self.assertIn(removed.effect_id, {relic.effect_id for relic in expanded.relics})
        self.assertIn(removed.relic_id, {relic.relic_id for relic in expanded.relics})
        self.assertEqual(
            known_before,
            {
                faction.faction_id: sum(
                    faction.faction_id in relic.discovered_by_faction_ids for relic in expanded.relics
                )
                for faction in expanded.factions
                if faction.is_major
            },
        )

        sandbox = generate_random_world(seed=612, city_count=6, faction_count=2)
        self.assertFalse(sandbox.relics)
        self.assertFalse(sandbox.relic_altars)
        self.assertFalse(relic_system_public(sandbox)["enabled"])

    def _store_relic_in_city(self, world, relic, city, faction):
        relic.state = "stored"
        relic.condition = "intact"
        relic.location_node_id = city.node_id
        relic.location_city_id = city.city_id
        relic.owner_faction_id = faction.faction_id
        if relic.relic_id not in city.relics_stored:
            city.relics_stored.append(relic.relic_id)
        city.resources.ether = max(city.resources.ether, 100)

    def test_bound_city_relic_pays_monthly_bonus_and_does_not_end_campaign(self) -> None:
        world = generate_random_world(
            seed=619,
            city_count=8,
            faction_count=2,
            neutral_city_states=True,
            campaign_contract=first_campaign_contract(),
        )
        faction = next(item for item in world.factions if item.faction_id == "faction_1")
        city = next(item for item in world.cities if item.city_id == faction.capital_city_id)
        altar = next(item for item in world.relic_altars if item.city_id == city.city_id)
        lord = next(
            item for item in world.offices
            if item.faction_id == faction.faction_id and item.office_type == "lord"
        )
        relic = next(item for item in world.relics if item.effect_id == "harvest_cup")
        self._store_relic_in_city(world, relic, city, faction)
        bound = bind_relic(
            world,
            faction_id=faction.faction_id,
            relic_id=relic.relic_id,
            altar_id=altar.altar_id,
            issuer_office_id=lord.office_id,
        )
        bound_relic = next(item for item in bound.relics if item.relic_id == relic.relic_id)
        self.assertTrue(bound_relic.effect_active)
        food_before = next(item for item in bound.cities if item.city_id == city.city_id).resources.food
        maintained = advance_relic_maintenance(bound)
        food_after = next(item for item in maintained.cities if item.city_id == city.city_id).resources.food
        self.assertEqual(food_after, food_before + 80)
        self.assertFalse(any(item["id"] == "relic_altar" for item in evaluate_strategic_status(maintained)["victory_conditions"]))
        self.assertIsNone(maintained.campaign_conclusion.get("reason"))

    def test_bound_faction_relic_pays_treasury_bonus(self) -> None:
        world = generate_random_world(
            seed=626,
            city_count=8,
            faction_count=2,
            neutral_city_states=True,
            campaign_contract=first_campaign_contract(),
        )
        faction = next(item for item in world.factions if item.faction_id == "faction_1")
        city = next(item for item in world.cities if item.city_id == faction.capital_city_id)
        altar = next(item for item in world.relic_altars if item.city_id == city.city_id)
        lord = next(
            item for item in world.offices
            if item.faction_id == faction.faction_id and item.office_type == "lord"
        )
        relic = next(item for item in world.relics if item.effect_id == "hegemony_seal")
        self._store_relic_in_city(world, relic, city, faction)
        money_before = faction.resources.money
        bound = bind_relic(
            world,
            faction_id=faction.faction_id,
            relic_id=relic.relic_id,
            altar_id=altar.altar_id,
            issuer_office_id=lord.office_id,
        )
        paid = advance_relic_maintenance(bound)
        self.assertEqual(
            next(item for item in paid.factions if item.faction_id == faction.faction_id).resources.money,
            money_before + 40,
        )

    def test_bound_faction_relic_and_ward_stone_pause_after_failed_maintenance(self) -> None:
        world = generate_random_world(
            seed=620,
            city_count=8,
            faction_count=2,
            neutral_city_states=True,
            campaign_contract=first_campaign_contract(),
        )
        faction = next(item for item in world.factions if item.faction_id == "faction_1")
        city = next(item for item in world.cities if item.city_id == faction.capital_city_id)
        altar = next(item for item in world.relic_altars if item.city_id == city.city_id)
        lord = next(
            item for item in world.offices
            if item.faction_id == faction.faction_id and item.office_type == "lord"
        )
        ward = next(item for item in world.relics if item.effect_id == "ward_stone")
        self._store_relic_in_city(world, ward, city, faction)
        bound = bind_relic(
            world,
            faction_id=faction.faction_id,
            relic_id=ward.relic_id,
            altar_id=altar.altar_id,
            issuer_office_id=lord.office_id,
        )
        bound_city = next(item for item in bound.cities if item.city_id == city.city_id)
        defense_after_bind = bound_city.defense
        self.assertEqual(defense_after_bind, city.defense + 2)

        bound_city.resources.ether = 0
        failed = advance_relic_maintenance(bound)
        failed_city = next(item for item in failed.cities if item.city_id == city.city_id)
        failed_altar = next(item for item in failed.relic_altars if item.altar_id == altar.altar_id)
        failed_ward = next(item for item in failed.relics if item.relic_id == ward.relic_id)
        self.assertEqual(failed_altar.state, "damaged")
        self.assertFalse(failed_ward.effect_active)
        self.assertEqual(failed_city.defense, defense_after_bind - 2)

        failed_defense = failed_city.defense
        failed.current_month += 1
        failed_city.resources.ether = 40
        recovered = advance_relic_maintenance(failed)
        recovered_city = next(item for item in recovered.cities if item.city_id == city.city_id)
        recovered_ward = next(item for item in recovered.relics if item.relic_id == ward.relic_id)
        self.assertEqual(
            next(item for item in recovered.relic_altars if item.altar_id == altar.altar_id).state,
            "active",
        )
        self.assertTrue(recovered_ward.effect_active)
        self.assertEqual(recovered_city.defense, failed_defense + 2)

    def test_fixed_campaign_contract_migrates_off_relic_victory(self) -> None:
        world = generate_random_world(
            seed=621,
            city_count=8,
            faction_count=2,
            neutral_city_states=True,
            campaign_contract=first_campaign_contract(),
        )
        world.campaign_contract["available_victory_routes"] = [
            *world.campaign_contract["available_victory_routes"],
            "relic_altar_victory",
        ]
        world.campaign_contract["locked_systems"] = ["relic_altar"]
        world.memory_tags = [
            item for item in world.memory_tags if item not in {"relic_altar_p6_6_v1", "relic_bonus_p1_v1"}
        ]

        migrated = ensure_relic_system(world)
        self.assertNotIn(
            "relic_altar_victory",
            migrated.campaign_contract["available_victory_routes"],
        )
        self.assertNotIn("relic_altar", migrated.campaign_contract["locked_systems"])
        self.assertIn("relic_bonus_p1_v1", migrated.memory_tags)
        self.assertFalse(
            any(
                item["id"] == "relic_altar"
                for item in evaluate_strategic_status(migrated)["victory_conditions"]
            )
        )

    def test_random_world_rejects_invalid_sizes(self) -> None:
        with self.assertRaises(StrategyError):
            generate_random_world(seed=1, city_count=1)
        with self.assertRaises(StrategyError):
            generate_random_world(seed=1, city_count=2, faction_count=0)
        with self.assertRaises(StrategyError):
            generate_random_world(seed=1, city_count=2, faction_count=3)

    def test_new_campaign_map_can_generate_more_neutral_city_states_than_major_cities(self) -> None:
        world = generate_random_world(
            seed=7,
            city_count=8,
            faction_count=2,
            neutral_city_states=True,
        )
        major_factions = [faction for faction in world.factions if not faction.is_neutral_city_state]
        neutral_factions = [faction for faction in world.factions if faction.is_neutral_city_state]

        self.assertEqual(len(major_factions), 2)
        self.assertEqual(len(neutral_factions), 6)
        self.assertGreater(len(neutral_factions), len(major_factions))
        self.assertTrue(all(faction.governor_name for faction in neutral_factions))
        self.assertTrue(all(
            len([city for city in world.cities if city.owner_faction_id == faction.faction_id]) == 1
            for faction in neutral_factions
        ))
        self.assertFalse(any(
            hero.faction_id in {faction.faction_id for faction in neutral_factions}
            for hero in world.strategic_heroes
        ))

        compact = generate_random_world(seed=7, city_count=4, faction_count=2, neutral_city_states=True)
        self.assertEqual(len([item for item in compact.factions if item.is_major]), 2)
        self.assertEqual(len([item for item in compact.factions if item.is_neutral_city_state]), 2)

    def test_random_world_can_generate_up_to_ten_major_factions(self) -> None:
        world = generate_random_world(seed=11, city_count=20, faction_count=10, neutral_city_states=True)
        majors = [faction for faction in world.factions if faction.is_major]
        neutrals = [faction for faction in world.factions if faction.is_neutral_city_state]
        self.assertEqual(len(majors), 10)
        self.assertEqual(len(neutrals), 10)
        self.assertEqual(len({faction.color for faction in majors}), 10)

    def test_major_capitals_are_spread_and_player_is_not_always_top_left(self) -> None:
        positions: list[tuple[float, float]] = []
        min_capital_gaps: list[float] = []
        for seed in range(1, 13):
            world = generate_random_world(seed=seed, city_count=8, faction_count=3, neutral_city_states=True)
            capitals: list[tuple[float, float]] = []
            for faction in world.factions:
                if not faction.is_major:
                    continue
                city = next(item for item in world.cities if item.city_id == faction.capital_city_id)
                node = next(item for item in world.nodes if item.node_id == city.node_id)
                capitals.append((float(node.x), float(node.y)))
                if faction.faction_id == "faction_1":
                    positions.append((float(node.x), float(node.y)))
            gaps = [
                ((left[0] - right[0]) ** 2 + (left[1] - right[1]) ** 2) ** 0.5
                for index, left in enumerate(capitals)
                for right in capitals[index + 1 :]
            ]
            min_capital_gaps.append(min(gaps))
        self.assertGreater(max(x for x, _y in positions) - min(x for x, _y in positions), 18)
        self.assertFalse(all(x < 28 and y < 28 for x, y in positions))
        self.assertGreater(sum(min_capital_gaps) / len(min_capital_gaps), 20)

    def test_first_campaign_contract_scales_major_factions(self) -> None:
        contract = first_campaign_contract(major_faction_count=8)
        self.assertEqual(contract["major_faction_count"], 8)
        self.assertEqual(contract["city_count"], 20)
        self.assertEqual(contract["neutral_city_state_count"], 12)
        world = generate_random_world(seed=9, campaign_contract=contract)
        self.assertEqual(len(world.cities), 20)
        self.assertEqual(len([faction for faction in world.factions if faction.is_major]), 8)
        self.assertEqual(len([faction for faction in world.factions if faction.is_neutral_city_state]), 12)

    def test_neutral_city_state_politics_follow_city_conditions_without_fixed_personality(self) -> None:
        world = generate_random_world(seed=7, city_count=8, faction_count=2, neutral_city_states=True)
        neutral = next(faction for faction in world.factions if faction.faction_id == "neutral_city_state_3")
        city = next(city for city in world.cities if city.owner_faction_id == neutral.faction_id)

        initial = neutral_city_state_profile(world, neutral.faction_id)
        self.assertEqual({item["score"] for item in initial["relationships"]}, {0})
        self.assertEqual(len(initial["relationships"]), 2)

        neutral.relations["faction_1"] = 30
        city.resources.food = 0
        city.resources.population = 100_000
        city.resources.troops = 3_000_000
        hungry = neutral_city_state_profile(world, neutral.faction_id)
        relation = next(item for item in hungry["relationships"] if item["faction_id"] == "faction_1")
        self.assertEqual((relation["score"], relation["label"]), (30, "友好"))
        self.assertEqual(hungry["posture"]["id"], "seeking_aid")
        self.assertEqual(hungry["current_need"]["id"], "food_relief")
        self.assertEqual(hungry["fear"]["type"], "shortage")
        self.assertEqual(hungry["governor_position"]["id"], "pragmatic_aid")

        saved = world.to_dict()
        for faction in saved["factions"]:
            faction.pop("relations", None)
        restored = WorldState.from_dict(saved)
        restored_profile = neutral_city_state_profile(restored, neutral.faction_id)
        self.assertEqual({item["score"] for item in restored_profile["relationships"]}, {0})

    def test_neutral_diplomacy_uses_shared_cost_acceptance_and_resource_rules(self) -> None:
        world = generate_random_world(seed=7, city_count=8, faction_count=2, neutral_city_states=True)
        actor = next(item for item in world.factions if item.faction_id == "faction_1")
        neutral = next(
            item for item in world.factions
            if item.is_neutral_city_state
            and any(
                option["id"] == "aid" and option["can_propose"]
                for option in neutral_diplomacy_options_public(
                    world,
                    actor_faction_id=actor.faction_id,
                    neutral_faction_id=item.faction_id,
                )
            )
        )
        city = next(item for item in world.cities if item.owner_faction_id == neutral.faction_id)
        actor_before = actor.resources.to_dict()
        city_before = city.resources.to_dict()

        aided = apply_neutral_diplomacy_action(
            world,
            actor_faction_id=actor.faction_id,
            neutral_faction_id=neutral.faction_id,
            action_id="aid",
        )
        aided_actor = next(item for item in aided.factions if item.faction_id == actor.faction_id)
        aided_neutral = next(item for item in aided.factions if item.faction_id == neutral.faction_id)
        aided_city = next(item for item in aided.cities if item.owner_faction_id == neutral.faction_id)
        self.assertEqual(aided_actor.resources.money, actor_before["money"] - 60)
        self.assertEqual(aided_actor.resources.food, actor_before["food"] - 80)
        self.assertEqual(aided_city.resources.money, city_before["money"] + 60)
        self.assertEqual(aided_city.resources.food, city_before["food"] + 80)
        self.assertEqual(aided_neutral.relations[actor.faction_id], 18)

        aided_neutral.relations[actor.faction_id] = 20
        protected = apply_neutral_diplomacy_action(
            aided,
            actor_faction_id=actor.faction_id,
            neutral_faction_id=neutral.faction_id,
            action_id="protection",
        )
        protected_actor = next(item for item in protected.factions if item.faction_id == actor.faction_id)
        protected_neutral = next(item for item in protected.factions if item.faction_id == neutral.faction_id)
        self.assertEqual(protected_neutral.relations[actor.faction_id], 35)
        self.assertEqual(protected_actor.resources.troops, actor_before["troops"] - 60)
        self.assertTrue(any(
            item.agreement_type == "protection"
            and item.major_faction_id == actor.faction_id
            and item.neutral_faction_id == neutral.faction_id
            for item in protected.diplomatic_agreements
        ))
        options = neutral_diplomacy_options_public(
            protected,
            actor_faction_id=actor.faction_id,
            neutral_faction_id=neutral.faction_id,
        )
        tribute = next(item for item in options if item["id"] == "demand_tribute")
        self.assertFalse(tribute["can_propose"])
        self.assertIn("保护对象", tribute["blocked_reason"])

        weak_world = generate_random_world(seed=7, city_count=8, faction_count=2, neutral_city_states=True)
        weak_actor = next(item for item in weak_world.factions if item.faction_id == "faction_1")
        weak_neutral = next(item for item in weak_world.factions if item.faction_id == neutral.faction_id)
        weak_city = next(item for item in weak_world.cities if item.owner_faction_id == weak_neutral.faction_id)
        weak_node = next(item for item in weak_world.nodes if item.node_id == weak_city.node_id)
        for border_city in weak_world.cities:
            if border_city.node_id in weak_node.connected_node_ids and border_city.owner_faction_id == weak_actor.faction_id:
                border_city.resources.troops = 10_000
        intimidated = apply_neutral_diplomacy_action(
            weak_world,
            actor_faction_id=weak_actor.faction_id,
            neutral_faction_id=weak_neutral.faction_id,
            action_id="intimidate",
        )
        intimidated_neutral = next(item for item in intimidated.factions if item.faction_id == weak_neutral.faction_id)
        intimidation_event = intimidated.event_log[-1]
        self.assertEqual(intimidation_event.category, "neutral_diplomacy_accepted")
        self.assertEqual(intimidated_neutral.relations[weak_actor.faction_id], -12)
        intimidated_actor = next(item for item in intimidated.factions if item.faction_id == weak_actor.faction_id)
        intimidated_city = next(item for item in intimidated.cities if item.owner_faction_id == weak_neutral.faction_id)
        actor_money_before_tribute = intimidated_actor.resources.money
        city_money_before_tribute = intimidated_city.resources.money
        tribute_paid = min(70, city_money_before_tribute)
        tribute_world = apply_neutral_diplomacy_action(
            intimidated,
            actor_faction_id=weak_actor.faction_id,
            neutral_faction_id=weak_neutral.faction_id,
            action_id="demand_tribute",
        )
        tribute_actor = next(item for item in tribute_world.factions if item.faction_id == weak_actor.faction_id)
        tribute_neutral = next(item for item in tribute_world.factions if item.faction_id == weak_neutral.faction_id)
        tribute_city = next(item for item in tribute_world.cities if item.owner_faction_id == weak_neutral.faction_id)
        self.assertEqual(tribute_actor.resources.money, actor_money_before_tribute + tribute_paid)
        self.assertEqual(tribute_city.resources.money, city_money_before_tribute - tribute_paid)
        self.assertEqual(tribute_neutral.relations[weak_actor.faction_id], -30)

    def test_neutral_promises_expire_reward_reputation_and_breach_is_remembered(self) -> None:
        world = generate_random_world(seed=7, city_count=8, faction_count=2, neutral_city_states=True)
        actor = next(item for item in world.factions if item.faction_id == "faction_1")
        neutral = next(
            item for item in world.factions
            if item.is_neutral_city_state
            and next(
                option for option in neutral_diplomacy_options_public(
                    world, actor_faction_id=actor.faction_id, neutral_faction_id=item.faction_id,
                ) if option["id"] == "non_aggression"
            )["can_propose"]
        )
        neutral.relations[actor.faction_id] = 20
        promised = apply_neutral_diplomacy_action(
            world, actor_faction_id=actor.faction_id,
            neutral_faction_id=neutral.faction_id, action_id="non_aggression",
        )
        agreement = promised.diplomatic_agreements[-1]
        self.assertEqual(agreement.expires_month, promised.current_month + 3)
        self.assertEqual(next(item for item in promised.factions if item.faction_id == actor.faction_id).diplomatic_reputation, 50)

        fulfilled = advance_month(advance_month(advance_month(promised)))
        fulfilled_agreement = fulfilled.diplomatic_agreements[-1]
        fulfilled_actor = next(item for item in fulfilled.factions if item.faction_id == actor.faction_id)
        fulfilled_neutral = next(item for item in fulfilled.factions if item.faction_id == neutral.faction_id)
        self.assertEqual((fulfilled_agreement.status, fulfilled_agreement.end_reason), ("ended", "fulfilled"))
        self.assertEqual(fulfilled_actor.diplomatic_reputation, 54)
        self.assertEqual(fulfilled_neutral.relations[actor.faction_id], 31)
        self.assertTrue(any(item["category"] == "agreement_fulfilled" for item in fulfilled.diplomatic_memory))

        breach_world = generate_random_world(seed=7, city_count=8, faction_count=2, neutral_city_states=True)
        breach_actor = next(item for item in breach_world.factions if item.faction_id == actor.faction_id)
        breach_neutral = next(item for item in breach_world.factions if item.faction_id == neutral.faction_id)
        breach_neutral.relations[breach_actor.faction_id] = 20
        breach_world = apply_neutral_diplomacy_action(
            breach_world, actor_faction_id=breach_actor.faction_id,
            neutral_faction_id=breach_neutral.faction_id, action_id="non_aggression",
        )
        source = next(city for city in breach_world.cities if city.owner_faction_id == breach_actor.faction_id)
        target = next(city for city in breach_world.cities if city.owner_faction_id == breach_neutral.faction_id)
        breach_world.pending_battles.append(PendingBattle(
            battle_id="promise_breach", month=breach_world.current_month,
            attacker_faction_id=breach_actor.faction_id, defender_faction_id=breach_neutral.faction_id,
            source_city_id=source.city_id, target_city_id=target.city_id,
            resolution_mode="quick", attacker_troops=1, defender_troops=1,
        ))
        broken = advance_month(breach_world)
        self.assertEqual((broken.diplomatic_agreements[-1].status, broken.diplomatic_agreements[-1].end_reason), ("broken", "treaty_breach"))
        self.assertEqual(next(item for item in broken.factions if item.faction_id == actor.faction_id).diplomatic_reputation, 35)
        self.assertTrue(any(item["category"] == "treaty_breach" for item in broken.diplomatic_memory))

        failed_world = generate_random_world(seed=7, city_count=8, faction_count=2, neutral_city_states=True)
        failed_actor = next(item for item in failed_world.factions if item.faction_id == actor.faction_id)
        failed_neutral = next(item for item in failed_world.factions if item.faction_id == neutral.faction_id)
        failed_neutral.relations[failed_actor.faction_id] = 20
        failed_world = apply_neutral_diplomacy_action(
            failed_world, actor_faction_id=failed_actor.faction_id,
            neutral_faction_id=failed_neutral.faction_id, action_id="protection",
        )
        lost_city = next(city for city in failed_world.cities if city.owner_faction_id == failed_neutral.faction_id)
        lost_city.owner_faction_id = "faction_2"
        failed = advance_month(failed_world)
        self.assertEqual((failed.diplomatic_agreements[-1].status, failed.diplomatic_agreements[-1].end_reason), ("broken", "protection_failed"))
        self.assertEqual(next(item for item in failed.factions if item.faction_id == actor.faction_id).diplomatic_reputation, 30)
        self.assertTrue(any(item["category"] == "protection_failed" for item in failed.diplomatic_memory))

        legacy = promised.to_dict()
        legacy["factions"][0].pop("diplomatic_reputation", None)
        legacy["diplomatic_agreements"][-1].pop("expires_month", None)
        legacy.pop("diplomatic_cooldowns", None)
        legacy.pop("diplomatic_memory", None)
        restored = WorldState.from_dict(legacy)
        self.assertEqual(restored.factions[0].diplomatic_reputation, 50)
        self.assertEqual(restored.diplomatic_agreements[-1].expires_month, restored.diplomatic_agreements[-1].started_month + 3)

    def test_influence_support_and_fulfilled_promise_unlock_peaceful_integration(self) -> None:
        world = generate_random_world(seed=7, city_count=8, faction_count=2, neutral_city_states=True)
        actor = next(item for item in world.factions if item.faction_id == "faction_1")
        neutral = next(
            item for item in world.factions
            if item.is_neutral_city_state
            and peaceful_integration_option(
                world, actor_faction_id=actor.faction_id, neutral_faction_id=item.faction_id,
            )["requirements"][1]["met"]
        )
        city = next(item for item in world.cities if item.owner_faction_id == neutral.faction_id)
        blocked = peaceful_integration_option(
            world, actor_faction_id=actor.faction_id, neutral_faction_id=neutral.faction_id,
        )
        self.assertFalse(blocked["can_integrate"])
        self.assertEqual((neutral.influence_by_faction[actor.faction_id], city.support_by_faction[actor.faction_id]), (0, 35))

        aided = apply_neutral_diplomacy_action(
            world, actor_faction_id=actor.faction_id, neutral_faction_id=neutral.faction_id, action_id="aid",
        )
        aided_neutral = next(item for item in aided.factions if item.faction_id == neutral.faction_id)
        aided_city = next(item for item in aided.cities if item.owner_faction_id == neutral.faction_id)
        self.assertEqual((aided_neutral.influence_by_faction[actor.faction_id], aided_city.support_by_faction[actor.faction_id]), (18, 45))

        actor = next(item for item in world.factions if item.faction_id == actor.faction_id)
        neutral.relations[actor.faction_id] = 60
        neutral.influence_by_faction[actor.faction_id] = 60
        city.support_by_faction[actor.faction_id] = 60
        world.diplomatic_agreements.append(DiplomaticAgreement(
            agreement_id="fulfilled-for-integration",
            agreement_type="non_aggression",
            major_faction_id=actor.faction_id,
            neutral_faction_id=neutral.faction_id,
            started_month=1,
            expires_month=4,
            ended_month=4,
            status="ended",
            end_reason="fulfilled",
        ))
        option = peaceful_integration_option(
            world, actor_faction_id=actor.faction_id, neutral_faction_id=neutral.faction_id,
        )
        self.assertTrue(option["can_integrate"])
        money_before, food_before = actor.resources.money, actor.resources.food
        integrated = apply_peaceful_integration(
            world, actor_faction_id=actor.faction_id, neutral_faction_id=neutral.faction_id,
        )
        integrated_actor = next(item for item in integrated.factions if item.faction_id == actor.faction_id)
        integrated_neutral = next(item for item in integrated.factions if item.faction_id == neutral.faction_id)
        integrated_city = next(item for item in integrated.cities if item.city_id == city.city_id)
        self.assertEqual(integrated_city.owner_faction_id, actor.faction_id)
        self.assertEqual((integrated_actor.resources.money, integrated_actor.resources.food), (money_before - 100, food_before - 80))
        self.assertGreaterEqual(integrated_city.support_by_faction[actor.faction_id], 70)
        self.assertEqual(integrated_neutral.capital_city_id, None)
        self.assertEqual(integrated_neutral.influence_by_faction[actor.faction_id], 100)
        self.assertIn("和平整合", integrated_city.traits)
        self.assertTrue(any(item["category"] == "peaceful_integration" for item in integrated.diplomatic_memory))
        ranking = next(item for item in campaign_assessment_rankings(integrated) if item["faction_id"] == actor.faction_id)
        self.assertEqual((ranking["peaceful_integrations"], ranking["influence_score"]), (1, 25))

        legacy = world.to_dict()
        for faction in legacy["factions"]:
            faction.pop("influence_by_faction", None)
        restored = WorldState.from_dict(legacy)
        restored_neutral = next(item for item in restored.factions if item.faction_id == neutral.faction_id)
        self.assertEqual(restored_neutral.influence_by_faction, {})


class StrategyOfficeTests(unittest.TestCase):
    def _formed_major_armies(self, seed: int = 405) -> tuple[WorldState, dict[str, str]]:
        world = generate_random_world(seed=seed, city_count=8, faction_count=2, neutral_city_states=True)
        general_ids: dict[str, str] = {}
        for faction_id in ("faction_1", "faction_2"):
            general = next(
                item for item in world.offices
                if item.faction_id == faction_id and item.office_type == "general"
            )
            general.unit_inventory = {"infantry": 1}
            general_ids[faction_id] = general.office_id
            hero = next(item for item in world.strategic_heroes if item.office_id == general.office_id)
            city = next(item for item in world.cities if item.city_id == hero.city_id)
            city.resources.food = max(city.resources.food, 500)
        for faction_id in ("faction_1", "faction_2"):
            general_id = general_ids[faction_id]
            hero = next(item for item in world.strategic_heroes if item.office_id == general_id)
            world = form_or_reinforce_army(
                world,
                faction_id=faction_id,
                city_id=str(hero.city_id),
                unit_inventory={"infantry": 1},
                supply=100,
                issuer_office_id=general_id,
            )
        return world, general_ids

    @staticmethod
    def _place_army(world: WorldState, faction_id: str, node_id: str) -> None:
        army = next(item for item in world.armies if item.faction_id == faction_id)
        army.location_node_id = node_id
        army.status = "deployed"
        army.current_order = "hold"
        army.march_origin_node_id = node_id
        army.destination_node_id = node_id
        army.route_node_ids = [node_id]
        army.route_progress_index = 0
        army.departure_month = world.current_month
        army.estimated_arrival_month = world.current_month
        army.supply_source_city_id = None
        army.supply_line_node_ids = []
        army.supply_line_status = "unassessed"
        army.supply_distance = None

    def _active_major_siege(self, seed: int = 410) -> tuple[WorldState, dict[str, str], str]:
        world, general_ids = self._formed_major_armies(seed=seed)
        target = next(city for city in world.cities if city.owner_faction_id == "faction_2")
        self._place_army(world, "faction_1", target.node_id)
        defender_army = next(item for item in world.armies if item.faction_id == "faction_2")
        defender_army.status = "destroyed"
        defender_army.current_order = "hold"
        world.validate()
        besieged = advance_sieges(world)
        self.assertEqual(len(besieged.sieges), 1)
        return besieged, general_ids, target.city_id

    def test_enemy_army_establishes_persistent_siege_without_arrival_month_damage(self) -> None:
        besieged, _, target_city_id = self._active_major_siege()
        siege = besieged.sieges[0]
        city = next(item for item in besieged.cities if item.city_id == target_city_id)
        self.assertEqual((siege.status, siege.started_month), ("active", besieged.current_month))
        self.assertEqual(siege.fortification_remaining, max(20, city.defense * 10))
        self.assertEqual(siege.last_city_food_consumed, 0)
        self.assertEqual(besieged.armies[0].status, "besieging")
        self.assertEqual(WorldState.from_dict(besieged.to_dict()).sieges[0].to_dict(), siege.to_dict())

    def test_siege_tick_consumes_city_food_and_breach_does_not_capture_city(self) -> None:
        besieged, general_ids, target_city_id = self._active_major_siege(seed=411)
        besieged.current_month += 1
        target = next(item for item in besieged.cities if item.city_id == target_city_id)
        target.resources.food = 100
        siege = besieged.sieges[0]
        siege.fortification_remaining = 5
        siege.defender_stance = "await_relief"
        ordered = order_siege_attacker_stance(
            besieged,
            faction_id="faction_1",
            siege_id=siege.siege_id,
            stance="assault",
            issuer_office_id=general_ids["faction_1"],
        )
        resolved = advance_sieges(ordered)
        siege = resolved.sieges[0]
        target = next(item for item in resolved.cities if item.city_id == target_city_id)
        self.assertEqual((siege.status, siege.battle_trigger, siege.fortification_remaining), ("breached", "assault", 0))
        self.assertGreater(siege.last_city_food_consumed, 0)
        self.assertEqual(target.owner_faction_id, "faction_2")

    def test_governor_can_surrender_siege_into_occupation_governance(self) -> None:
        besieged, _, target_city_id = self._active_major_siege(seed=412)
        governor = next(
            item for item in besieged.offices
            if item.faction_id == "faction_2"
            and item.office_type == "governor"
            and target_city_id in item.managed_entity_ids
        )
        surrendered = order_siege_defender_stance(
            besieged,
            faction_id="faction_2",
            siege_id=besieged.sieges[0].siege_id,
            stance="surrender",
            issuer_office_id=governor.office_id,
        )
        target = next(item for item in surrendered.cities if item.city_id == target_city_id)
        self.assertEqual((surrendered.sieges[0].status, surrendered.sieges[0].outcome), ("ended", "surrendered"))
        self.assertEqual(target.owner_faction_id, "faction_1")
        self.assertEqual(target.occupation.get("status"), "pending")
        self.assertEqual(next(item for item in surrendered.armies if item.faction_id == "faction_1").status, "garrisoned")

    def test_siege_surrender_transfers_local_relics_to_the_attacker(self) -> None:
        besieged, _, target_city_id = self._active_major_siege(seed=416)
        besieged.campaign_contract = first_campaign_contract()
        besieged = ensure_relic_system(besieged)
        target = next(item for item in besieged.cities if item.city_id == target_city_id)
        relic = next(item for item in besieged.relics if item.state == "scattered")
        relic.state = "stored"
        relic.condition = "intact"
        relic.owner_faction_id = "faction_2"
        relic.location_node_id = target.node_id
        relic.location_city_id = target.city_id
        relic.altar_id = None
        target.relics_stored.append(relic.relic_id)
        governor = next(
            item for item in besieged.offices
            if item.faction_id == "faction_2"
            and item.office_type == "governor"
            and target_city_id in item.managed_entity_ids
        )

        surrendered = order_siege_defender_stance(
            besieged,
            faction_id="faction_2",
            siege_id=besieged.sieges[0].siege_id,
            stance="surrender",
            issuer_office_id=governor.office_id,
        )
        captured_relic = next(item for item in surrendered.relics if item.relic_id == relic.relic_id)
        self.assertEqual(captured_relic.owner_faction_id, "faction_1")
        self.assertTrue(
            any(event.category == "strategy_siege_relics_captured" for event in surrendered.event_log)
        )

    def test_attacker_can_order_safe_withdrawal_and_end_siege_next_month(self) -> None:
        besieged, general_ids, _ = self._active_major_siege(seed=413)
        army = next(item for item in besieged.armies if item.faction_id == "faction_1")
        node = next(item for item in besieged.nodes if item.node_id == army.location_node_id)
        destination_id = next(
            node_id for node_id in node.connected_node_ids
            if not any(
                other.faction_id == "faction_2" and other.location_node_id == node_id
                for other in besieged.armies
            )
        )
        ordered = order_siege_attacker_stance(
            besieged,
            faction_id="faction_1",
            siege_id=besieged.sieges[0].siege_id,
            stance="withdraw",
            destination_node_id=destination_id,
            issuer_office_id=general_ids["faction_1"],
        )
        retreated = advance_army_retreats(ordered)
        self.assertEqual(retreated.sieges[0].status, "ended")
        self.assertEqual(retreated.sieges[0].outcome, "withdrawn")
        self.assertEqual(next(item for item in retreated.armies if item.faction_id == "faction_1").location_node_id, destination_id)

    def test_defender_reinforcement_turns_siege_into_contested_encounter(self) -> None:
        besieged, _, target_city_id = self._active_major_siege(seed=415)
        target = next(item for item in besieged.cities if item.city_id == target_city_id)
        defender = next(item for item in besieged.armies if item.faction_id == "faction_2")
        self._place_army(besieged, "faction_2", target.node_id)
        defender.status = "deployed"
        besieged.validate()
        engaged = advance_army_encounters(besieged)
        contested = advance_sieges(engaged)
        self.assertEqual(contested.encounters[0].status, "active")
        self.assertEqual(contested.sieges[0].status, "contested")
        self.assertEqual({army.status for army in contested.armies}, {"engaged"})

    def test_encounter_quick_battle_uses_armies_and_writes_back_retreat(self) -> None:
        world, _ = self._formed_major_armies(seed=416)
        node = world.nodes[0]
        self._place_army(world, "faction_1", node.node_id)
        self._place_army(world, "faction_2", node.node_id)
        engaged = advance_army_encounters(world)
        encounter = engaged.encounters[0]

        resolved = declare_strategic_battle(
            engaged,
            faction_id="faction_1",
            source_kind="encounter",
            source_entity_id=encounter.encounter_id,
            resolution_mode="quick",
        )

        battle = resolved.pending_battles[-1]
        attacker = next(item for item in resolved.armies if item.faction_id == "faction_1")
        defender = next(item for item in resolved.armies if item.faction_id == "faction_2")
        self.assertEqual((battle.source_kind, battle.status), ("encounter", "resolved"))
        self.assertEqual(battle.battle_result["resolution_source"], "quick")
        self.assertEqual(resolved.encounters[0].outcome, "battle_resolved")
        self.assertEqual((attacker.manpower, attacker.morale), (100, 75))
        self.assertEqual((defender.manpower, defender.morale), (100, 55))
        self.assertNotEqual(defender.location_node_id, node.node_id)
        self.assertEqual(WorldState.from_dict(resolved.to_dict()).pending_battles[-1].source_entity_id, encounter.encounter_id)

    def test_encounter_real_grid_uses_same_snapshot_and_writes_survivors(self) -> None:
        world, _ = self._formed_major_armies(seed=419)
        node = world.nodes[0]
        for army in world.armies:
            army.unit_inventory = {"infantry": 2}
            army.manpower = 200
            army.supply_capacity = 200
            army.supply = 100
            self._place_army(world, army.faction_id, node.node_id)
        engaged = advance_army_encounters(world)
        pending = declare_strategic_battle(
            engaged,
            faction_id="faction_1",
            source_kind="encounter",
            source_entity_id=engaged.encounters[0].encounter_id,
            resolution_mode="manual",
            auto_resolve=False,
        )
        battle = pending.pending_battles[-1]
        rosters = strategy_battle_rosters(pending, battle)
        self.assertEqual((len(rosters.attacker.roster), len(rosters.defender.roster)), (3, 3))
        attached = attach_battle_room(pending, battle_id=battle.battle_id, room_id="P46GRID", invite_path="/room/P46GRID")
        resolved = resolve_battle_room_result(
            attached,
            battle_room_id="P46GRID",
            winner_team_id=1,
            surviving_grid_units_by_team={1: 1, 2: 0},
            surviving_hero_codes_by_team={1: set(battle.attacker_hero_codes or []), 2: set()},
        )
        attacker = next(item for item in resolved.armies if item.faction_id == "faction_1")
        defender = next(item for item in resolved.armies if item.faction_id == "faction_2")
        self.assertEqual((attacker.manpower, attacker.unit_inventory), (100, {"infantry": 1}))
        self.assertEqual((defender.status, defender.manpower), ("destroyed", 0))
        self.assertEqual(resolved.pending_battles[-1].battle_result["resolution_source"], "real_grid")

    def test_encounter_quick_result_changes_with_morale_and_supply(self) -> None:
        world, _ = self._formed_major_armies(seed=420)
        node = world.nodes[0]
        attacker = next(item for item in world.armies if item.faction_id == "faction_1")
        defender = next(item for item in world.armies if item.faction_id == "faction_2")
        attacker.morale = 10
        attacker.supply = 0
        defender.morale = 100
        defender.supply = defender.supply_capacity
        self._place_army(world, "faction_1", node.node_id)
        self._place_army(world, "faction_2", node.node_id)
        engaged = advance_army_encounters(world)

        resolved = declare_strategic_battle(
            engaged,
            faction_id="faction_1",
            source_kind="encounter",
            source_entity_id=engaged.encounters[0].encounter_id,
            resolution_mode="quick",
        )

        self.assertEqual(resolved.pending_battles[-1].winner_faction_id, "faction_2")
        self.assertEqual(next(item for item in resolved.armies if item.faction_id == "faction_1").morale, 0)

    def test_breached_siege_quick_assault_captures_city_and_occupies(self) -> None:
        besieged, _, target_city_id = self._active_major_siege(seed=417)
        siege = besieged.sieges[0]
        siege.status = "breached"
        siege.fortification_remaining = 0
        siege.battle_trigger = "assault"
        target = next(item for item in besieged.cities if item.city_id == target_city_id)
        target.resources.troops = 1
        target.registered_units = {}
        target.support_by_faction["faction_2"] = 0

        resolved = declare_strategic_battle(
            besieged,
            faction_id="faction_1",
            source_kind="siege",
            source_entity_id=siege.siege_id,
            resolution_mode="quick",
        )

        target = next(item for item in resolved.cities if item.city_id == target_city_id)
        army = next(item for item in resolved.armies if item.faction_id == "faction_1")
        self.assertEqual(target.owner_faction_id, "faction_1")
        self.assertEqual(target.occupation.get("status"), "pending")
        self.assertEqual((resolved.sieges[0].status, resolved.sieges[0].outcome), ("ended", "captured"))
        self.assertEqual(army.status, "garrisoned")
        self.assertTrue(resolved.pending_battles[-1].battle_result["city_captured"])

    def test_failed_breakout_restores_breached_siege_without_changing_owner(self) -> None:
        besieged, _, target_city_id = self._active_major_siege(seed=418)
        siege = besieged.sieges[0]
        siege.status = "battle_pending"
        siege.battle_trigger = "breakout"
        siege.fortification_remaining = 0
        target = next(item for item in besieged.cities if item.city_id == target_city_id)
        target.resources.troops = 1
        target.registered_units = {}
        target.support_by_faction["faction_2"] = 0

        resolved = declare_strategic_battle(
            besieged,
            faction_id="faction_2",
            source_kind="siege",
            source_entity_id=siege.siege_id,
            resolution_mode="quick",
        )

        target = next(item for item in resolved.cities if item.city_id == target_city_id)
        self.assertEqual(target.owner_faction_id, "faction_2")
        self.assertEqual((resolved.sieges[0].status, resolved.sieges[0].battle_trigger), ("breached", "assault"))
        self.assertEqual(next(item for item in resolved.armies if item.faction_id == "faction_1").status, "besieging")

    def test_same_node_enemy_armies_create_persistent_encounter_and_can_retreat(self) -> None:
        world, general_ids = self._formed_major_armies()
        encounter_node = world.nodes[0]
        self._place_army(world, "faction_1", encounter_node.node_id)
        self._place_army(world, "faction_2", encounter_node.node_id)
        world.validate()

        engaged = advance_army_encounters(world)
        self.assertEqual(len(engaged.encounters), 1)
        encounter = engaged.encounters[0]
        self.assertEqual((encounter.status, encounter.node_id), ("active", encounter_node.node_id))
        self.assertEqual({army.status for army in engaged.armies}, {"engaged"})
        self.assertEqual(WorldState.from_dict(engaged.to_dict()).encounters[0].to_dict(), encounter.to_dict())

        retreat_node_id = encounter_node.connected_node_ids[0]
        own_army = next(item for item in engaged.armies if item.faction_id == "faction_1")
        ordered = order_army_retreat(
            engaged,
            faction_id="faction_1",
            army_id=own_army.army_id,
            destination_node_id=retreat_node_id,
            issuer_office_id=general_ids["faction_1"],
        )
        retreated = advance_army_retreats(ordered)
        own_army = next(item for item in retreated.armies if item.faction_id == "faction_1")
        enemy_army = next(item for item in retreated.armies if item.faction_id == "faction_2")
        self.assertEqual((own_army.location_node_id, own_army.morale), (retreat_node_id, 60))
        self.assertIn(own_army.status, {"garrisoned", "deployed"})
        self.assertNotEqual(enemy_army.status, "engaged")
        self.assertEqual(retreated.encounters[0].status, "ended")

    def test_adjacent_intercept_moves_one_edge_and_starts_encounter(self) -> None:
        world, general_ids = self._formed_major_armies(seed=406)
        source_node = world.nodes[0]
        target_node_id = source_node.connected_node_ids[0]
        self._place_army(world, "faction_1", source_node.node_id)
        self._place_army(world, "faction_2", target_node_id)
        world.validate()
        interceptor = next(item for item in world.armies if item.faction_id == "faction_1")
        target = next(item for item in world.armies if item.faction_id == "faction_2")

        ordered = order_army_intercept(
            world,
            faction_id="faction_1",
            army_id=interceptor.army_id,
            target_army_id=target.army_id,
            issuer_office_id=general_ids["faction_1"],
        )
        cancelled = halt_army_march(
            ordered,
            faction_id="faction_1",
            army_id=interceptor.army_id,
            issuer_office_id=general_ids["faction_1"],
        )
        self.assertEqual((cancelled.armies[0].current_order, cancelled.armies[0].target_army_id), ("hold", None))
        resolved = advance_army_encounters(ordered)
        interceptor = next(item for item in resolved.armies if item.faction_id == "faction_1")
        self.assertEqual((interceptor.location_node_id, interceptor.status), (target_node_id, "engaged"))
        self.assertEqual(resolved.encounters[0].status, "active")
        self.assertTrue(any(event.category == "strategy_army_intercepted" for event in resolved.event_log))

    def test_adjacent_friendly_army_can_reinforce_active_encounter(self) -> None:
        world, general_ids = self._formed_major_armies(seed=407)
        encounter_node = world.nodes[0]
        reinforcement_node_id = encounter_node.connected_node_ids[0]
        self._place_army(world, "faction_1", encounter_node.node_id)
        self._place_army(world, "faction_2", encounter_node.node_id)
        engaged = advance_army_encounters(world)
        encounter = engaged.encounters[0]

        original_general = next(item for item in engaged.offices if item.office_id == general_ids["faction_1"])
        reserve_general = type(original_general).from_dict(original_general.to_dict())
        reserve_general.office_id = f"{original_general.office_id}:reserve"
        reserve_general.holder_id = "reserve_commander"
        reserve_general.unit_inventory = {}
        engaged.offices.append(reserve_general)
        original_hero = next(item for item in engaged.strategic_heroes if item.office_id == original_general.office_id)
        reserve_hero = type(original_hero).from_dict(original_hero.to_dict())
        reserve_hero.hero_code = "reserve_commander"
        reserve_hero.office_id = reserve_general.office_id
        reserve_hero.controller_type = "ai"
        reserve_hero.controller_user_id = None
        engaged.strategic_heroes.append(reserve_hero)
        original_army = next(item for item in engaged.armies if item.faction_id == "faction_1")
        reserve_army = type(original_army).from_dict(original_army.to_dict())
        reserve_army.army_id = f"{original_army.army_id}:reserve"
        reserve_army.commander_office_id = reserve_general.office_id
        reserve_army.commander_hero_code = "reserve_commander"
        reserve_army.location_node_id = reinforcement_node_id
        reserve_army.status = "deployed"
        reserve_army.current_order = "hold"
        reserve_army.route_node_ids = [reinforcement_node_id]
        reserve_army.route_progress_index = 0
        reserve_army.march_origin_node_id = reinforcement_node_id
        reserve_army.destination_node_id = reinforcement_node_id
        reserve_army.supply_source_city_id = None
        reserve_army.supply_line_node_ids = []
        reserve_army.supply_line_status = "unassessed"
        reserve_army.supply_distance = None
        engaged.armies.append(reserve_army)
        engaged.validate()

        ordered = order_army_reinforce(
            engaged,
            faction_id="faction_1",
            army_id=reserve_army.army_id,
            encounter_id=encounter.encounter_id,
            issuer_office_id=reserve_general.office_id,
        )
        reinforced = advance_army_encounters(ordered)
        reserve_army = next(item for item in reinforced.armies if item.army_id.endswith(":reserve"))
        encounter = reinforced.encounters[0]
        self.assertEqual((reserve_army.location_node_id, reserve_army.status), (encounter_node.node_id, "engaged"))
        self.assertIn(reserve_army.army_id, encounter.faction_army_ids["faction_1"])
        self.assertTrue(any(event.category == "strategy_army_reinforced_encounter" for event in reinforced.event_log))

    def test_non_aggression_prevents_automatic_encounter_and_interception(self) -> None:
        world, general_ids = self._formed_major_armies(seed=408)
        neutral = next(item for item in world.factions if item.faction_id == "faction_2")
        neutral.faction_type = "neutral_city_state"
        world.diplomatic_agreements.append(DiplomaticAgreement(
            agreement_id="agreement:p44:non-aggression",
            agreement_type="non_aggression",
            major_faction_id="faction_1",
            neutral_faction_id="faction_2",
            started_month=1,
            expires_month=4,
        ))
        source_node = world.nodes[0]
        target_node_id = source_node.connected_node_ids[0]
        self._place_army(world, "faction_1", source_node.node_id)
        self._place_army(world, "faction_2", target_node_id)
        world.validate()
        own_army = next(item for item in world.armies if item.faction_id == "faction_1")
        target_army = next(item for item in world.armies if item.faction_id == "faction_2")
        with self.assertRaises(StrategyError):
            order_army_intercept(
                world,
                faction_id="faction_1",
                army_id=own_army.army_id,
                target_army_id=target_army.army_id,
                issuer_office_id=general_ids["faction_1"],
            )
        self._place_army(world, "faction_2", source_node.node_id)
        peaceful = advance_army_encounters(world)
        self.assertFalse(peaceful.encounters)
        self.assertNotIn("engaged", {army.status for army in peaceful.armies})

    def test_army_supply_draws_real_city_food_and_manual_loading_respects_capacity(self) -> None:
        world = generate_random_world(seed=403, city_count=8, faction_count=2, neutral_city_states=True)
        general = next(item for item in world.offices if item.faction_id == "faction_1" and item.office_type == "general")
        hero = next(item for item in world.strategic_heroes if item.office_id == general.office_id)
        city = next(item for item in world.cities if item.city_id == hero.city_id)
        general.unit_inventory = {"infantry": 1}
        city.resources.food = 500
        formed = form_or_reinforce_army(
            world,
            faction_id="faction_1",
            city_id=city.city_id,
            unit_inventory={"infantry": 1},
            supply=50,
            issuer_office_id=general.office_id,
        )
        army = formed.armies[0]
        self.assertEqual((army.supply_line_status, army.supply_distance, army.monthly_supply_need), ("local", 0, 10))

        supplied = advance_army_supply(formed)
        supplied_army = supplied.armies[0]
        supplied_city = next(item for item in supplied.cities if item.city_id == city.city_id)
        self.assertEqual((supplied_army.supply, supplied_army.last_supply_received, supplied_army.last_supply_consumed), (50, 10, 10))
        self.assertEqual((supplied_city.resources.food, supplied_army.morale), (440, 72))

        loaded = load_army_supply(
            supplied,
            faction_id="faction_1",
            army_id=supplied_army.army_id,
            supply=50,
            issuer_office_id=general.office_id,
        )
        self.assertEqual(loaded.armies[0].supply, 100)
        self.assertEqual(next(item for item in loaded.cities if item.city_id == city.city_id).resources.food, 390)
        with self.assertRaises(StrategyError):
            load_army_supply(
                loaded,
                faction_id="faction_1",
                army_id=loaded.armies[0].army_id,
                supply=101,
                issuer_office_id=general.office_id,
            )

    def test_severed_supply_line_causes_morale_loss_then_real_unit_attrition(self) -> None:
        world = generate_random_world(seed=404, city_count=8, faction_count=2, neutral_city_states=True)
        general = next(item for item in world.offices if item.faction_id == "faction_1" and item.office_type == "general")
        hero = next(item for item in world.strategic_heroes if item.office_id == general.office_id)
        home = next(item for item in world.cities if item.city_id == hero.city_id)
        general.unit_inventory = {"infantry": 1}
        home.resources.food = 500
        formed = form_or_reinforce_army(
            world,
            faction_id="faction_1",
            city_id=home.city_id,
            unit_inventory={"infantry": 1},
            supply=50,
            issuer_office_id=general.office_id,
        )
        routes = [
            shortest_army_route(formed, home.node_id, node.node_id)
            for node in formed.nodes
            if node.node_id != home.node_id
        ]
        route = max(routes, key=lambda item: (len(item), item[-1]))
        self.assertGreaterEqual(len(route), 3)
        army = formed.armies[0]
        army.location_node_id = route[-1]
        army.route_node_ids = [route[-1]]
        army.route_progress_index = 0
        army.status = "deployed"
        army.supply = 0
        army.supply_source_city_id = None
        army.supply_line_node_ids = []
        army.supply_line_status = "unassessed"
        army.supply_distance = None
        army.monthly_supply_need = 0
        plan = army_supply_plan(formed, army)
        self.assertEqual(plan["status"], "severed")
        self.assertEqual(plan["distance"], len(route) - 1)

        first = advance_army_supply(formed)
        self.assertEqual((first.armies[0].starvation_months, first.armies[0].morale, first.armies[0].manpower), (1, 58, 100))
        second = advance_army_supply(first)
        self.assertEqual((second.armies[0].status, second.armies[0].manpower, second.armies[0].unit_inventory), ("destroyed", 0, {}))
        self.assertEqual(second.armies[0].morale, 46)

    def test_general_army_marches_one_route_edge_per_month_and_can_reroute_or_halt(self) -> None:
        world = generate_random_world(seed=402, city_count=8, faction_count=2, neutral_city_states=True)
        general = next(item for item in world.offices if item.faction_id == "faction_1" and item.office_type == "general")
        hero = next(item for item in world.strategic_heroes if item.office_id == general.office_id)
        city = next(item for item in world.cities if item.city_id == hero.city_id)
        general.unit_inventory = {"infantry": 1}
        city.resources.food = 200
        formed = form_or_reinforce_army(
            world,
            faction_id="faction_1",
            city_id=city.city_id,
            unit_inventory={"infantry": 1},
            supply=100,
            issuer_office_id=general.office_id,
        )
        army_id = formed.armies[0].army_id
        routes = [
            shortest_army_route(formed, city.node_id, node.node_id)
            for node in formed.nodes
            if node.node_id != city.node_id
        ]
        route = max(routes, key=lambda item: (len(item), item[-1]))
        ordered = order_army_march(
            formed,
            faction_id="faction_1",
            army_id=army_id,
            destination_node_id=route[-1],
            issuer_office_id=general.office_id,
        )
        army = ordered.armies[0]
        self.assertEqual(army.route_node_ids, route)
        self.assertEqual(army.status, "marching")
        self.assertEqual(army.estimated_arrival_month, ordered.current_month + len(route) - 1)
        self.assertEqual(WorldState.from_dict(ordered.to_dict()).armies[0].to_dict(), army.to_dict())

        progressed = advance_month(ordered)
        army = progressed.armies[0]
        self.assertEqual((army.location_node_id, army.route_progress_index), (route[1], 1))
        self.assertEqual(army.status, "garrisoned" if len(route) == 2 and next(
            city for city in progressed.cities if city.node_id == route[1]
        ).owner_faction_id == "faction_1" else ("deployed" if len(route) == 2 else "marching"))
        moved_hero = next(item for item in progressed.strategic_heroes if item.hero_code == hero.hero_code)
        self.assertEqual(moved_hero.city_id, next(city.city_id for city in progressed.cities if city.node_id == route[1]))

        if army.status == "marching":
            rerouted = order_army_march(
                progressed,
                faction_id="faction_1",
                army_id=army_id,
                destination_node_id=route[0],
                issuer_office_id=general.office_id,
            )
            self.assertEqual(rerouted.armies[0].march_origin_node_id, route[1])
            halted = halt_army_march(
                rerouted,
                faction_id="faction_1",
                army_id=army_id,
                issuer_office_id=general.office_id,
            )
            self.assertEqual(halted.armies[0].status, "deployed")
            self.assertEqual(halted.armies[0].current_order, "hold")
            self.assertEqual(halted.armies[0].destination_node_id, route[1])

        with self.assertRaises(StrategyError):
            order_army_march(
                progressed,
                faction_id="faction_1",
                army_id=army_id,
                destination_node_id=progressed.armies[0].location_node_id,
                issuer_office_id=general.office_id,
            )

    def test_general_forms_reinforces_serializes_and_disbands_one_persistent_army(self) -> None:
        world = generate_random_world(seed=401, city_count=8, faction_count=2, neutral_city_states=True)
        general = next(item for item in world.offices if item.faction_id == "faction_1" and item.office_type == "general")
        hero = next(item for item in world.strategic_heroes if item.office_id == general.office_id)
        city = next(item for item in world.cities if item.city_id == hero.city_id)
        general.unit_inventory = {"infantry": 2, "archer": 1}
        city.resources.food = 500

        formed = form_or_reinforce_army(
            world,
            faction_id="faction_1",
            city_id=city.city_id,
            unit_inventory={"infantry": 1, "archer": 1},
            supply=100,
            issuer_office_id=general.office_id,
        )
        army = formed.armies[0]
        formed_general = next(item for item in formed.offices if item.office_id == general.office_id)
        formed_city = next(item for item in formed.cities if item.city_id == city.city_id)
        self.assertEqual((army.manpower, army.supply, army.supply_capacity, army.morale), (240, 100, 240, 70))
        self.assertEqual(army.unit_inventory, {"infantry": 1, "archer": 1})
        self.assertEqual(formed_general.unit_inventory, {"infantry": 1})
        self.assertEqual(formed_city.resources.food, 400)
        self.assertEqual(WorldState.from_dict(formed.to_dict()).armies[0].to_dict(), army.to_dict())

        reinforced = form_or_reinforce_army(
            formed,
            faction_id="faction_1",
            city_id=city.city_id,
            unit_inventory={"infantry": 1},
            supply=100,
            issuer_office_id=general.office_id,
        )
        self.assertEqual(len([item for item in reinforced.armies if item.status != "disbanded"]), 1)
        self.assertEqual((reinforced.armies[0].manpower, reinforced.armies[0].supply), (340, 200))
        with self.assertRaises(StrategyError):
            form_or_reinforce_army(
                formed,
                faction_id="faction_1",
                city_id=city.city_id,
                unit_inventory={"infantry": 2},
                supply=50,
                issuer_office_id=general.office_id,
            )

        disbanded = disband_army(
            reinforced,
            faction_id="faction_1",
            army_id=reinforced.armies[0].army_id,
            issuer_office_id=general.office_id,
        )
        returned_general = next(item for item in disbanded.offices if item.office_id == general.office_id)
        returned_city = next(item for item in disbanded.cities if item.city_id == city.city_id)
        self.assertEqual(disbanded.armies[0].status, "disbanded")
        self.assertEqual(returned_general.unit_inventory, {"infantry": 2, "archer": 1})
        self.assertEqual(returned_city.resources.food, 500)

    def test_player_faction_ai_governor_automates_only_emergency_policy_with_remaining_command(self) -> None:
        world = generate_random_world(
            seed=314,
            city_count=8,
            faction_count=2,
            neutral_city_states=True,
            campaign_contract=first_campaign_contract(),
        )
        city = next(item for item in world.cities if item.owner_faction_id == "faction_1")
        city.resources.food = 0
        city.support_by_faction["faction_1"] = 20
        before_policy = city.policy

        automated = apply_player_office_automation(
            world,
            controlled_faction_ids={"faction_1"},
            queued_actions=[],
            command_remaining_by_faction={"faction_1": 1},
        )
        changed = next(item for item in automated.cities if item.city_id == city.city_id)
        self.assertNotEqual(changed.policy, before_policy)
        self.assertTrue(any(event.category == "office_automation" for event in automated.event_log))
        governor = next(item for item in automated.offices if item.office_type == "governor" and city.city_id in item.managed_entity_ids)
        self.assertTrue(all(
            duty.status == "completed"
            for duty in automated.office_duties
            if duty.office_id == governor.office_id and duty.due_month == automated.current_month
        ))

        no_command = apply_player_office_automation(
            world,
            controlled_faction_ids={"faction_1"},
            queued_actions=[],
            command_remaining_by_faction={"faction_1": 0},
        )
        self.assertEqual(next(item for item in no_command.cities if item.city_id == city.city_id).policy, before_policy)

    def test_ai_receiver_executes_tutorial_order_and_exposes_result_feedback(self) -> None:
        world = generate_random_world(
            seed=315,
            city_count=8,
            faction_count=2,
            neutral_city_states=True,
            campaign_contract=first_campaign_contract(),
        )
        city = next(item for item in world.cities if item.owner_faction_id == "faction_1")
        city.resources.food = 0
        lord = next(item for item in world.offices if item.faction_id == "faction_1" and item.office_type == "lord")
        governor = next(item for item in world.offices if item.faction_id == "faction_1" and item.office_type == "governor")
        lord.controller_type = "player"
        lord.controller_user_id = 1
        ordered = apply_office_order(
            world,
            issuer_office_id=lord.office_id,
            receiver_office_id=governor.office_id,
            order_type="order",
            objective="[引导:set_policy] 处理粮食危机",
            target_entity_id=city.city_id,
        )
        automated = apply_player_office_automation(
            ordered,
            controlled_faction_ids={"faction_1"},
            queued_actions=[],
            command_remaining_by_faction={"faction_1": 0},
        )
        order = automated.office_orders[-1]
        coordination = office_coordination_public(automated, [])["faction_1"]

        self.assertEqual(order.status, "completed")
        self.assertEqual(order.details["executor_office_id"], governor.office_id)
        self.assertIn("已由城主设为", order.details["result_summary"])
        self.assertLessEqual(len(coordination["high_consequence_decisions"]), 3)
        self.assertEqual(coordination["order_feedback"][-1]["status"], "completed")
        self.assertIn("已由城主设为", coordination["order_feedback"][-1]["result_summary"])

    def test_lord_can_issue_an_exact_persistent_city_policy_order_to_ai_governor(self) -> None:
        world = generate_random_world(
            seed=315,
            city_count=8,
            faction_count=2,
            neutral_city_states=True,
            campaign_contract=first_campaign_contract(),
        )
        city = next(item for item in world.cities if item.owner_faction_id == "faction_1")
        lord = next(item for item in world.offices if item.faction_id == "faction_1" and item.office_type == "lord")
        governor = next(
            item for item in world.offices
            if item.faction_id == "faction_1" and item.office_type == "governor" and city.city_id in item.managed_entity_ids
        )
        recruit_policy = next(policy for policy in world.to_public_dict()["policy_choices"] if "征兵" in policy)
        ordered = apply_office_order(
            world,
            issuer_office_id=lord.office_id,
            receiver_office_id=governor.office_id,
            order_type="set_policy",
            objective=f"将{city.name}设为{recruit_policy}",
            target_entity_id=city.city_id,
            details={"policy": recruit_policy},
        )
        automated = apply_player_office_automation(
            ordered,
            controlled_faction_ids={"faction_1"},
            queued_actions=[],
            command_remaining_by_faction={"faction_1": 0},
        )
        changed = next(item for item in automated.cities if item.city_id == city.city_id)
        self.assertEqual(changed.policy, recruit_policy)
        self.assertEqual(automated.office_orders[-1].status, "completed")
        self.assertEqual(automated.office_orders[-1].details["policy"], recruit_policy)

    def test_legacy_sandbox_does_not_gain_office_automation(self) -> None:
        world = generate_random_world(seed=316, city_count=4, faction_count=2)
        automated = apply_player_office_automation(
            world,
            controlled_faction_ids={"faction_1"},
            queued_actions=[],
            command_remaining_by_faction={"faction_1": 4},
        )
        self.assertIs(automated, world)
        self.assertEqual(office_coordination_public(world, []), {})

    def test_generation_builds_complete_deterministic_office_tree(self) -> None:
        world = generate_random_world(seed=131, city_count=6, faction_count=2)
        rebuilt = ensure_office_system(world)

        self.assertEqual([office.to_dict() for office in world.offices], [office.to_dict() for office in rebuilt.offices])
        for faction in world.factions:
            offices = [office for office in world.offices if office.faction_id == faction.faction_id]
            self.assertEqual(sum(office.office_type == "lord" for office in offices), 1)
            self.assertEqual(sum(office.office_type == "grand_general" for office in offices), 1)
            self.assertEqual(sum(office.office_type == "general" for office in offices), 1)
            self.assertEqual(
                sum(office.office_type == "governor" for office in offices),
                sum(city.owner_faction_id == faction.faction_id for city in world.cities),
            )
            lord = next(office for office in offices if office.office_type == "lord")
            self.assertTrue(all(office.parent_office_id == lord.office_id for office in offices if office.office_type in {"grand_general", "governor"}))
            general = next(office for office in offices if office.office_type == "general")
            self.assertEqual(next(office for office in offices if office.office_type == "grand_general").office_id, general.parent_office_id)

    def test_player_permissions_are_scoped_to_office_and_managed_city(self) -> None:
        world = generate_random_world(seed=132, city_count=4, faction_count=2)
        for office in world.offices:
            if office.faction_id == "faction_1":
                office.controller_type = "player"
                office.controller_user_id = 7
        governor = next(office for office in world.offices if office.faction_id == "faction_1" and office.office_type == "governor")
        city_id = governor.managed_entity_ids[0]

        selected = resolve_action_office(
            world,
            user_id=7,
            faction_id="faction_1",
            action_type="set_city_policy",
            payload={"city_id": city_id},
            requested_office_id=governor.office_id,
        )
        self.assertEqual(selected.office_id, governor.office_id)
        lord = next(office for office in world.offices if office.faction_id == "faction_1" and office.office_type == "lord")
        with self.assertRaises(StrategyError):
            resolve_action_office(
                world,
                user_id=7,
                faction_id="faction_1",
                action_type="set_city_policy",
                payload={"city_id": city_id},
                requested_office_id=lord.office_id,
            )
        other_city = next(city for city in world.cities if city.city_id != city_id)
        with self.assertRaises(StrategyError):
            resolve_action_office(
                world,
                user_id=7,
                faction_id="faction_1",
                action_type="set_city_policy",
                payload={"city_id": other_city.city_id},
                requested_office_id=governor.office_id,
            )

    def test_order_and_request_follow_direct_reporting_chain(self) -> None:
        world = generate_random_world(seed=133, city_count=4, faction_count=2)
        lord = next(office for office in world.offices if office.faction_id == "faction_1" and office.office_type == "lord")
        grand = next(office for office in world.offices if office.faction_id == "faction_1" and office.office_type == "grand_general")
        general = next(office for office in world.offices if office.faction_id == "faction_1" and office.office_type == "general")

        ordered = apply_office_order(world, issuer_office_id=lord.office_id, receiver_office_id=grand.office_id, order_type="order", objective="守住北线")
        requested = apply_office_order(ordered, issuer_office_id=general.office_id, receiver_office_id=grand.office_id, order_type="request", objective="请求增援")
        self.assertEqual([order.order_type for order in requested.office_orders[-2:]], ["order", "request"])
        with self.assertRaises(StrategyError):
            apply_office_order(world, issuer_office_id=lord.office_id, receiver_office_id=general.office_id, order_type="order", objective="越级命令")

    def test_technology_expands_grand_general_capacity(self) -> None:
        world = generate_random_world(seed=134, city_count=4, faction_count=2)
        faction = next(item for item in world.factions if item.faction_id == "faction_1")
        faction.tactic_techs.extend(["military_reform_1", "military_reform_2"])
        rebuilt = ensure_office_system(world)

        self.assertEqual(grand_general_capacity(rebuilt, "faction_1"), 3)
        self.assertEqual(
            sum(office.office_type == "grand_general" and office.faction_id == "faction_1" for office in rebuilt.offices),
            3,
        )

    def test_world_roundtrip_preserves_city_support_and_events(self) -> None:
        world = generate_random_world(seed=7, city_count=5, faction_count=2)
        restored = WorldState.from_dict(world.to_dict())

        self.assertEqual(restored.to_dict(), world.to_dict())
        self.assertEqual(restored.cities[0].support_by_faction["faction_1"], 70)
        self.assertEqual(restored.event_log[0].message, "英灵城邦战役开始。")

    def test_monthly_briefing_has_threat_opportunity_and_rival_intent_for_each_faction(self) -> None:
        world = generate_random_world(seed=77, city_count=6, faction_count=2)
        own_city = next(city for city in world.cities if city.owner_faction_id == "faction_1")
        own_city.event_states.append("rebellion_force:240:month:1")

        briefings = monthly_briefings_public(world)

        self.assertEqual(set(briefings), {"faction_1", "faction_2"})
        self.assertEqual(briefings["faction_1"]["month"], 1)
        entries = briefings["faction_1"]["entries"]
        self.assertEqual([entry["kind"] for entry in entries], ["threat", "opportunity", "rival_intent"])
        self.assertEqual(entries[0]["city_id"], own_city.city_id)
        self.assertIn("叛军规模 240", entries[0]["detail"])

    def test_strategy_action_command_costs_make_war_and_suppression_expensive(self) -> None:
        self.assertEqual(FACTION_MONTHLY_COMMAND_POINTS, 4)
        self.assertEqual(strategy_action_command_cost("set_city_policy"), 1)
        self.assertEqual(strategy_action_command_cost("summon_strategic_hero"), 1)
        self.assertEqual(strategy_action_command_cost("declare_attack"), 2)
        self.assertEqual(strategy_action_command_cost("rebellion_battle"), 2)
        self.assertEqual(strategy_action_command_cost("rebellion_action", {"rebellion_action_id": "appease"}), 1)
        self.assertEqual(strategy_action_command_cost("rebellion_action", {"rebellion_action_id": "suppress"}), 2)
        self.assertEqual(strategy_action_command_cost("resolve_story_event"), 1)

    def test_new_world_opens_one_deterministic_story_event_per_faction(self) -> None:
        first = generate_random_world(seed=78, city_count=6, faction_count=3)
        second = generate_random_world(seed=78, city_count=6, faction_count=3)

        self.assertEqual(first.to_dict(), second.to_dict())
        pending = [event for event in first.story_events if event.status == "pending"]
        self.assertEqual(len(pending), 3)
        self.assertEqual({event.faction_id for event in pending}, {"faction_1", "faction_2", "faction_3"})
        public_events = story_events_public(first)
        self.assertTrue(all(event["choices"] for event in public_events))
        self.assertTrue(all(choice["command_cost"] == 1 for event in public_events for choice in event["choices"]))

    def test_story_choice_applies_effect_and_delayed_consequence(self) -> None:
        world = generate_random_world(seed=79, city_count=4, faction_count=2)
        city = next(city for city in world.cities if city.owner_faction_id == "faction_1")
        faction = next(faction for faction in world.factions if faction.faction_id == "faction_1")
        faction.resources.ether = 100
        world.story_events = [
            StoryEvent("story_test_ether", "ether_flare", "faction_1", city.city_id, world.current_month)
        ]
        before_ether = city.resources.ether

        resolved = resolve_story_event(
            world,
            faction_id="faction_1",
            event_id="story_test_ether",
            choice_id="stabilize_flare",
        )

        event = resolved.story_events[0]
        self.assertEqual(event.status, "resolved")
        self.assertEqual(event.choice_id, "stabilize_flare")
        self.assertEqual(next(f for f in resolved.factions if f.faction_id == "faction_1").resources.ether, 70)
        self.assertEqual(resolved.scheduled_consequences[0].due_month, 2)
        advanced = advance_month(resolved)
        advanced_city = next(item for item in advanced.cities if item.city_id == city.city_id)
        self.assertGreaterEqual(advanced_city.resources.ether, before_ether + 80)
        self.assertEqual(advanced.scheduled_consequences[0].status, "resolved")
        self.assertTrue(any(event.category == "story_consequence" for event in advanced.event_log))

    def test_unanswered_story_event_uses_default_outcome_at_next_month(self) -> None:
        world = generate_random_world(seed=80, city_count=4, faction_count=2)
        city = next(city for city in world.cities if city.owner_faction_id == "faction_1")
        world.story_events = [
            StoryEvent("story_test_guild", "guild_dispute", "faction_1", city.city_id, world.current_month)
        ]
        before_money = city.resources.money

        advanced = advance_month(world)

        ignored = next(event for event in advanced.story_events if event.event_id == "story_test_guild")
        self.assertEqual(ignored.status, "expired")
        self.assertEqual(ignored.choice_id, "let_strike_spread")
        self.assertLess(next(item for item in advanced.cities if item.city_id == city.city_id).resources.money, before_money + 1000)
        self.assertTrue(any(event.category == "story_event_ignored" for event in advanced.event_log))
        self.assertTrue(any(event.status == "pending" and event.opened_month == 2 for event in advanced.story_events))

    def test_story_choice_rejects_unaffordable_resource_cost(self) -> None:
        world = generate_random_world(seed=81, city_count=4, faction_count=2)
        city = next(city for city in world.cities if city.owner_faction_id == "faction_1")
        next(faction for faction in world.factions if faction.faction_id == "faction_1").resources.ether = 0
        world.story_events = [StoryEvent("story_no_ether", "ether_flare", "faction_1", city.city_id, 1)]

        with self.assertRaises(StrategyError):
            validate_story_event_choice(
                world,
                faction_id="faction_1",
                event_id="story_no_ether",
                choice_id="stabilize_flare",
            )
        public_event = story_events_public(world)[0]
        stabilize = next(choice for choice in public_event["choices"] if choice["id"] == "stabilize_flare")
        self.assertFalse(stabilize["enabled"])
        self.assertIn("势力以太不足", stabilize["disabled_reason"])

    def test_world_validation_rejects_unknown_references(self) -> None:
        faction = Faction(
            faction_id="faction_1",
            name="测试势力",
            resources=ResourceBundle(0, 0, 0, 0, 0),
        )
        with self.assertRaises(StrategyError):
            WorldState(
                seed=1,
                current_month=1,
                nodes=[MapNode("node_1", "一号", "city", 0, 0, ["missing"])],
                cities=[],
                factions=[faction],
            )
        with self.assertRaises(StrategyError):
            WorldState(
                seed=1,
                current_month=1,
                nodes=[MapNode("node_1", "一号", "city", 0, 0, [])],
                cities=[
                    City(
                        city_id="city_1",
                        node_id="missing",
                        name="坏城市",
                        owner_faction_id="faction_1",
                        level=1,
                        resources=ResourceBundle(0, 0, 0, 0, 0),
                        defense=0,
                    )
                ],
                factions=[faction],
            )


class StrategyStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.store = StrategyStore(Path(self.tmpdir.name) / "strategy.sqlite3")
        self.alice = AuthUser(user_id=1, username="Alice", created_at=1.0)
        self.bob = AuthUser(user_id=2, username="Bob", created_at=2.0)
        self.carol = AuthUser(user_id=3, username="Carol", created_at=3.0)
        self.dave = AuthUser(user_id=4, username="Dave", created_at=4.0)
        self.eve = AuthUser(user_id=5, username="Eve", created_at=5.0)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_create_campaign_persists_world_and_multiple_saves_per_user(self) -> None:
        first = self.store.create_campaign(owner=self.alice, name="北境战役", seed=11, city_count=6)
        second = self.store.create_campaign(owner=self.alice, name="南境战役", seed=12, city_count=5)

        campaigns = self.store.list_campaigns_for_user(self.alice.user_id)

        self.assertEqual({campaign.name for campaign in campaigns}, {"北境战役", "南境战役"})
        self.assertEqual(len(first.join_code), 6)
        self.assertEqual(first.status, "lobby")
        self.assertEqual(first.members[0].user_id, self.alice.user_id)
        self.assertEqual(second.world.seed, 12)
        self.assertEqual(len(second.world.cities), 5)
        summary = first.to_list_dict()
        detail = first.to_public_dict()
        self.assertEqual(summary["detail"], False)
        self.assertEqual(detail["detail"], True)
        self.assertNotIn("nodes", summary["world"])
        self.assertIn("nodes", detail["world"])
        self.assertEqual(len(summary["world"]["cities"]), len(first.world.cities))

    def test_strategy_save_and_schema_migrations_are_versioned_and_idempotent(self) -> None:
        campaign = self.store.create_campaign(owner=self.alice, name="旧存档迁移", seed=301, city_count=6)
        database_path = self.store.db_path
        with closing(sqlite3.connect(database_path)) as connection:
            raw = json.loads(connection.execute(
                "SELECT world_json FROM strategy_campaigns WHERE id = ?",
                (campaign.campaign_id,),
            ).fetchone()[0])
            raw.pop("save_format_version", None)
            connection.execute(
                "UPDATE strategy_campaigns SET world_json = ? WHERE id = ?",
                (json.dumps(raw, ensure_ascii=False, sort_keys=True), campaign.campaign_id),
            )
            connection.commit()

        reopened = StrategyStore(database_path)
        migrated = reopened.get_campaign_for_user(campaign.campaign_id, self.alice.user_id)
        StrategyStore(database_path).get_campaign_for_user(campaign.campaign_id, self.alice.user_id)

        self.assertEqual(migrated.world.save_format_version, CURRENT_STRATEGY_SAVE_VERSION)
        self.assertEqual(migrate_world_payload(migrated.world.to_dict()), migrated.world.to_dict())
        automatic_backups = reopened.list_backups()
        self.assertEqual(len(automatic_backups), 1)
        self.assertEqual(automatic_backups[0].reason, "pre_save_migration")
        self.assertTrue(automatic_backups[0].automatic)
        self.assertEqual(reopened.drill_backup_restore(automatic_backups[0].path)["status"], "passed")
        with closing(sqlite3.connect(database_path)) as connection:
            stored = json.loads(connection.execute(
                "SELECT world_json FROM strategy_campaigns WHERE id = ?",
                (campaign.campaign_id,),
            ).fetchone()[0])
            schema_rows = connection.execute(
                "SELECT version, name FROM strategy_schema_migrations ORDER BY version"
            ).fetchall()
            save_rows = connection.execute(
                "SELECT from_version, to_version, before_payload, before_hash, after_hash FROM strategy_save_migrations WHERE campaign_id = ?",
                (campaign.campaign_id,),
            ).fetchall()
        self.assertEqual(stored["save_format_version"], CURRENT_STRATEGY_SAVE_VERSION)
        self.assertEqual(
            schema_rows,
            [
                (1, "campaign_invitation_baseline"),
                (2, "save_migration_rollback_payload"),
                (3, "tamper_evident_operation_audit"),
            ],
        )
        self.assertEqual(len(save_rows), 1)
        self.assertEqual(save_rows[0][:2], (0, CURRENT_STRATEGY_SAVE_VERSION))
        self.assertNotIn("save_format_version", json.loads(save_rows[0][2]))
        self.assertNotEqual(save_rows[0][3], save_rows[0][4])

    def test_future_strategy_save_is_rejected_without_rewriting_it(self) -> None:
        campaign = self.store.create_campaign(owner=self.alice, name="未来存档", seed=302, city_count=6)
        database_path = self.store.db_path
        future_version = CURRENT_STRATEGY_SAVE_VERSION + 1
        with closing(sqlite3.connect(database_path)) as connection:
            raw = json.loads(connection.execute(
                "SELECT world_json FROM strategy_campaigns WHERE id = ?",
                (campaign.campaign_id,),
            ).fetchone()[0])
            raw["save_format_version"] = future_version
            serialized = json.dumps(raw, ensure_ascii=False, sort_keys=True)
            connection.execute(
                "UPDATE strategy_campaigns SET world_json = ? WHERE id = ?",
                (serialized, campaign.campaign_id),
            )
            connection.commit()

        with self.assertRaises(StrategyError) as raised:
            StrategyStore(database_path).get_campaign_for_user(campaign.campaign_id, self.alice.user_id)

        self.assertEqual(raised.exception.status, HTTPStatus.CONFLICT)
        self.assertIn("升级服务", str(raised.exception))
        with closing(sqlite3.connect(database_path)) as connection:
            unchanged = connection.execute(
                "SELECT world_json FROM strategy_campaigns WHERE id = ?",
                (campaign.campaign_id,),
            ).fetchone()[0]
        self.assertEqual(unchanged, serialized)

    def test_campaign_name_validation(self) -> None:
        with self.assertRaises(StrategyError):
            self.store.create_campaign(owner=self.alice, name="A")
        with self.assertRaises(StrategyError):
            self.store.create_campaign(owner=self.alice, name="过长" * 30)

    def test_player_hero_faction_and_roaming_state_persist_with_member(self) -> None:
        campaign = self.store.create_campaign(owner=self.alice, name="武将道路", seed=113, city_count=6)
        current = next(
            hero
            for hero in campaign.world.strategic_heroes
            if hero.controller_type == "player" and hero.controller_user_id == self.alice.user_id
        )
        roaming = choose_player_hero_path(
            campaign.world,
            user_id=self.alice.user_id,
            hero_code=current.hero_code,
            path="roaming",
            assigned_faction_id="faction_1",
        )

        saved = self.store.update_world(campaign.campaign_id, self.alice.user_id, roaming)
        reloaded = self.store.get_campaign_for_user(campaign.campaign_id, self.alice.user_id)
        controlled = [
            hero
            for hero in reloaded.world.strategic_heroes
            if hero.controller_type == "player" and hero.controller_user_id == self.alice.user_id
        ]

        self.assertEqual(saved.members[0].faction_id, "")
        self.assertEqual(len(controlled), 1)
        self.assertEqual((controlled[0].hero_code, controlled[0].status, controlled[0].faction_id), (current.hero_code, "roaming", None))

    def test_rotate_join_code_invalidates_old_code_and_requires_owner(self) -> None:
        campaign = self.store.create_campaign(
            owner=self.alice,
            name="邀请战役",
            seed=23,
            city_count=6,
            faction_count=2,
        )
        old_code = campaign.join_code

        with self.assertRaises(StrategyError):
            self.store.rotate_join_code(campaign.campaign_id, self.bob.user_id)
        with self.assertRaises(StrategyError):
            self.store.revoke_join_code(campaign.campaign_id, self.bob.user_id)

        revoked = self.store.revoke_join_code(campaign.campaign_id, self.alice.user_id)
        self.assertFalse(revoked.join_code_enabled)
        self.assertEqual(revoked.to_public_dict()["invite"]["status"], "revoked")
        self.assertEqual(revoked.to_public_dict()["join_code"], "")
        with self.assertRaises(StrategyError):
            self.store.join_campaign_by_code(old_code, self.bob)

        rotated = self.store.rotate_join_code(campaign.campaign_id, self.alice.user_id)

        self.assertTrue(rotated.join_code_enabled)
        self.assertEqual(len(rotated.join_code), 6)
        self.assertNotEqual(rotated.join_code, old_code)
        with self.assertRaises(StrategyError):
            self.store.join_campaign_by_code(old_code, self.bob)
        joined = self.store.join_campaign_by_code(rotated.join_code, self.bob)
        self.assertEqual(joined.campaign_id, campaign.campaign_id)
        locked = self.store.lock_initial_players(campaign.campaign_id, self.alice.user_id)
        self.assertEqual(locked.to_public_dict()["invite"]["status"], "locked")
        with self.assertRaises(StrategyError):
            self.store.rotate_join_code(campaign.campaign_id, self.alice.user_id)

    def test_join_campaign_by_code_and_lock_initial_players(self) -> None:
        campaign = self.store.create_campaign(
            owner=self.alice,
            name="å¤šäººæˆ˜å½¹",
            seed=22,
            city_count=6,
            faction_count=2,
        )

        joined = self.store.join_campaign_by_code(campaign.join_code.lower(), self.bob)

        self.assertEqual(joined.campaign_id, campaign.campaign_id)
        self.assertEqual(joined.status, "lobby")
        self.assertEqual([member.user_id for member in joined.members], [1, 2])
        self.assertEqual([member.faction_id for member in joined.members], ["faction_1", "faction_2"])

        joined = self.store.join_campaign_by_code(campaign.join_code, self.carol)
        joined = self.store.join_campaign_by_code(campaign.join_code, self.dave)
        self.assertEqual(
            [member.faction_id for member in joined.members],
            ["faction_1", "faction_2", "faction_1", "faction_2"],
        )
        with self.assertRaises(StrategyError):
            self.store.join_campaign_by_code(campaign.join_code, self.eve)
        with self.assertRaises(StrategyError):
            self.store.lock_initial_players(campaign.campaign_id, self.bob.user_id)

        locked = self.store.lock_initial_players(campaign.campaign_id, self.alice.user_id)
        self.assertEqual(locked.status, "active")
        self.assertEqual(self.store.join_campaign_by_code(campaign.join_code, self.bob).campaign_id, campaign.campaign_id)
        with self.assertRaises(StrategyError):
            self.store.join_campaign_by_code(campaign.join_code, self.eve)
        player_heroes = [
            hero
            for hero in locked.world.strategic_heroes
            if hero.controller_type == "player"
        ]
        self.assertEqual(
            {int(hero.controller_user_id or 0) for hero in player_heroes},
            {1, 2, 3, 4},
        )
        self.assertEqual(len({hero.office_id for hero in player_heroes}), 4)
        faction_1_offices = {
            office.office_type
            for office in locked.world.offices
            if office.controller_type == "player"
            and int(office.controller_user_id or 0) in {1, 3}
        }
        self.assertEqual(faction_1_offices, {"lord", "governor"})

    def test_joiner_can_choose_host_faction_for_two_player_coop(self) -> None:
        campaign = self.store.create_campaign(
            owner=self.alice,
            name="同势力协作",
            seed=221,
            city_count=6,
            faction_count=2,
        )

        joined = self.store.join_campaign_by_code(
            campaign.join_code,
            self.bob,
            join_host_faction=True,
        )
        self.assertEqual([member.faction_id for member in joined.members], ["faction_1", "faction_1"])
        controlled_offices = {
            int(office.controller_user_id or 0): office.office_type
            for office in joined.world.offices
            if office.controller_type == "player"
        }
        self.assertEqual(controlled_offices, {1: "lord", 2: "governor"})

        with self.assertRaisesRegex(StrategyError, "另一名真人"):
            choose_player_hero_path(
                joined.world,
                user_id=self.bob.user_id,
                hero_code=next(
                    hero.hero_code
                    for hero in joined.world.strategic_heroes
                    if hero.controller_user_id == self.bob.user_id
                ),
                path="lord",
                assigned_faction_id="faction_1",
                allow_reselect=True,
            )

    def test_same_faction_players_confirm_handover_and_lord_requested_vacate(self) -> None:
        campaign = self.store.create_campaign(
            owner=self.alice,
            name="官职交接",
            seed=222,
            city_count=6,
            faction_count=2,
        )
        campaign = self.store.join_campaign_by_code(
            campaign.join_code,
            self.bob,
            join_host_faction=True,
        )
        campaign = self.store.lock_initial_players(campaign.campaign_id, self.alice.user_id)
        offices_by_user = {
            int(office.controller_user_id or 0): office
            for office in campaign.world.offices
            if office.controller_type == "player"
        }
        alice_office = offices_by_user[1]
        bob_office = offices_by_user[2]

        requested = self.store.request_office_change(
            campaign.campaign_id,
            self.alice.user_id,
            request_type="handover",
            office_id=alice_office.office_id,
            target_user_id=self.bob.user_id,
        )
        pending = requested.office_change_requests[0]
        self.assertEqual((pending.request_type, pending.status, pending.target_user_id), ("handover", "pending", 2))
        with self.assertRaises(StrategyError):
            self.store.respond_office_change(
                campaign.campaign_id,
                self.carol.user_id,
                request_id=pending.request_id,
                accept=True,
            )

        handed_over = self.store.respond_office_change(
            campaign.campaign_id,
            self.bob.user_id,
            request_id=pending.request_id,
            accept=True,
        )
        swapped = {
            int(office.controller_user_id or 0): office.office_type
            for office in handed_over.world.offices
            if office.controller_type == "player"
        }
        self.assertEqual(swapped, {1: bob_office.office_type, 2: "lord"})
        self.assertEqual(handed_over.office_change_requests[0].status, "accepted")

        vacate_requested = self.store.request_office_change(
            campaign.campaign_id,
            self.bob.user_id,
            request_type="vacate",
            office_id=bob_office.office_id,
        )
        vacate = vacate_requested.office_change_requests[0]
        vacated = self.store.respond_office_change(
            campaign.campaign_id,
            self.alice.user_id,
            request_id=vacate.request_id,
            accept=True,
        )
        former_office = next(
            office
            for office in vacated.world.offices
            if office.office_id == bob_office.office_id
        )
        alice_hero = next(
            hero
            for hero in vacated.world.strategic_heroes
            if hero.controller_user_id == self.alice.user_id
        )
        self.assertEqual(former_office.status, "vacant")
        self.assertIsNone(former_office.controller_user_id)
        self.assertIsNone(alice_hero.office_id)

        with self.assertRaises(StrategyError):
            self.store.grant_office_takeover(
                campaign.campaign_id,
                self.alice.user_id,
                office_id=former_office.office_id,
                delegate_user_id=self.bob.user_id,
            )
        with self.assertRaises(StrategyError):
            self.store.grant_office_takeover(
                campaign.campaign_id,
                self.bob.user_id,
                office_id=former_office.office_id,
                delegate_user_id=self.bob.user_id,
            )

        delegated = self.store.grant_office_takeover(
            campaign.campaign_id,
            self.bob.user_id,
            office_id=former_office.office_id,
            delegate_user_id=self.alice.user_id,
        )
        takeover = delegated.office_takeovers[0]
        temporary_office = next(
            office for office in delegated.world.offices if office.office_id == former_office.office_id
        )
        self.assertEqual((takeover.status, takeover.month), ("active", 1))
        self.assertEqual(
            (temporary_office.status, temporary_office.holder_type, temporary_office.controller_user_id),
            ("active", "temporary_player", self.alice.user_id),
        )
        authorized = resolve_action_office(
            delegated.world,
            user_id=self.alice.user_id,
            faction_id="faction_1",
            action_type="set_city_policy",
            payload={"city_id": temporary_office.managed_entity_ids[0]},
            requested_office_id=temporary_office.office_id,
        )
        self.assertEqual(authorized.office_id, temporary_office.office_id)

        next_world = delegated.world
        next_world.current_month = 2
        self.store.update_world(campaign.campaign_id, self.bob.user_id, next_world)
        expired = self.store.expire_office_takeovers(campaign.campaign_id, self.bob.user_id)
        expired_office = next(
            office for office in expired.world.offices if office.office_id == former_office.office_id
        )
        self.assertEqual(expired.office_takeovers[0].status, "expired")
        self.assertEqual(expired_office.status, "vacant")
        self.assertIsNone(expired_office.controller_user_id)

    def test_lock_initial_players_fills_open_factions_with_ai_members(self) -> None:
        campaign = self.store.create_campaign(
            owner=self.alice,
            name="solo with ai",
            seed=27,
            city_count=6,
            faction_count=3,
        )

        locked = self.store.lock_initial_players(campaign.campaign_id, self.alice.user_id)
        members_by_faction = {member.faction_id: member for member in locked.members}
        resume = self.store.mark_online(campaign.campaign_id, self.alice)

        self.assertEqual(locked.status, "active")
        self.assertEqual(set(members_by_faction), {"faction_1", "faction_2", "faction_3"})
        self.assertEqual(members_by_faction["faction_1"].role, "host")
        self.assertEqual(members_by_faction["faction_2"].role, "ai")
        self.assertEqual(members_by_faction["faction_3"].role, "ai")
        self.assertLess(members_by_faction["faction_2"].user_id, 0)
        self.assertEqual(resume.initial_user_ids, (self.alice.user_id,))
        self.assertTrue(resume.can_resume)
        self.assertEqual(resume.missing_initial_user_ids, ())

    def test_resume_is_async_and_month_deadline_can_proxy_only_offline_drafting_players(self) -> None:
        campaign = self.store.create_campaign(
            owner=self.alice,
            initial_players=[self.bob],
            name="双人战役",
            seed=21,
            city_count=6,
            faction_count=2,
        )

        initial = self.store.resume_status(campaign.campaign_id)
        self.assertFalse(initial.can_resume)
        self.assertEqual(initial.missing_initial_user_ids, (1, 2))

        after_alice = self.store.mark_online(campaign.campaign_id, self.alice)
        self.assertFalse(after_alice.can_resume)
        self.assertEqual(after_alice.online_initial_user_ids, (1,))
        self.assertEqual(after_alice.missing_initial_user_ids, (2,))

        with self.assertRaises(StrategyError):
            self.store.require_can_resume(campaign.campaign_id, self.alice.user_id)

        after_bob = self.store.mark_online(campaign.campaign_id, self.bob)
        self.assertFalse(after_bob.can_resume)
        self.assertEqual(after_bob.missing_initial_user_ids, ())
        with self.assertRaises(StrategyError):
            self.store.require_can_resume(campaign.campaign_id, self.bob.user_id)

        self.store.lock_initial_players(campaign.campaign_id, self.alice.user_id)
        self.assertTrue(self.store.require_can_resume(campaign.campaign_id, self.bob.user_id).can_resume)

        after_leave = self.store.mark_offline(campaign.campaign_id, self.alice.user_id)
        self.assertTrue(after_leave.can_resume)
        self.assertEqual(after_leave.missing_initial_user_ids, (1,))
        self.assertEqual(after_leave.drafting_user_ids, (1, 2))

        alice_ready = self.store.set_month_ready(campaign.campaign_id, self.alice.user_id, ready=True)
        self.assertEqual(alice_ready.ready_user_ids, (1,))
        with self.assertRaises(StrategyError):
            self.store.close_month_deadline(campaign.campaign_id, self.alice.user_id)

        self.store.mark_offline(campaign.campaign_id, self.bob.user_id)
        closed = self.store.close_month_deadline(campaign.campaign_id, self.alice.user_id)
        self.assertEqual(closed.proxy_ai_user_ids, (2,))
        self.assertTrue(closed.can_advance_month)
        self.assertEqual(
            self.store.temporary_ai_faction_ids(campaign.campaign_id, campaign.world.current_month),
            {"faction_2"},
        )

        reclaimed = self.store.set_month_ready(campaign.campaign_id, self.bob.user_id, ready=True)
        self.assertEqual(reclaimed.ready_user_ids, (1, 2))
        self.assertEqual(reclaimed.proxy_ai_user_ids, ())

    def test_ready_member_must_withdraw_before_changing_queued_actions(self) -> None:
        campaign = self.store.create_campaign(owner=self.alice, name="ready lock")
        self.store.lock_initial_players(campaign.campaign_id, self.alice.user_id)
        self.store.set_month_ready(campaign.campaign_id, self.alice.user_id, ready=True)

        with self.assertRaises(StrategyError):
            self.store.queue_action(
                campaign_id=campaign.campaign_id,
                user=self.alice,
                action_type="set_policy",
                action_key="city_1",
                payload={"city_id": "city_1", "policy": "稳定优先"},
            )

        self.store.set_month_ready(campaign.campaign_id, self.alice.user_id, ready=False)
        queued = self.store.queue_action(
            campaign_id=campaign.campaign_id,
            user=self.alice,
            action_type="set_policy",
            action_key="city_1",
            payload={"city_id": "city_1", "policy": "稳定优先"},
        )
        self.assertEqual(len(queued.queued_actions), 1)

    def test_non_member_cannot_read_or_enter_campaign(self) -> None:
        campaign = self.store.create_campaign(owner=self.alice, name="私有战役")

        with self.assertRaises(StrategyError):
            self.store.get_campaign_for_user(campaign.campaign_id, self.carol.user_id)
        with self.assertRaises(StrategyError):
            self.store.mark_online(campaign.campaign_id, self.carol)

    def test_update_world_persists_validated_state(self) -> None:
        campaign = self.store.create_campaign(owner=self.alice, name="推进战役")
        world = campaign.world
        world.current_month = 2
        world.memory_tags.append("month_2_started")

        updated = self.store.update_world(campaign.campaign_id, self.alice.user_id, world)
        reloaded = self.store.get_campaign_for_user(campaign.campaign_id, self.alice.user_id)

        self.assertEqual(updated.current_month, 2)
        self.assertEqual(reloaded.world.current_month, 2)
        self.assertIn("month_2_started", reloaded.world.memory_tags)

    def test_queue_action_persists_current_month_and_replaces_same_key(self) -> None:
        campaign = self.store.create_campaign(owner=self.alice, name="action queue", seed=24, city_count=6)

        queued = self.store.queue_action(
            campaign_id=campaign.campaign_id,
            user=self.alice,
            action_type="set_city_policy",
            action_key="city_1",
            payload={"city_id": "city_1", "policy": "recruit"},
        )
        replaced = self.store.queue_action(
            campaign_id=campaign.campaign_id,
            user=self.alice,
            action_type="set_city_policy",
            action_key="city_1",
            payload={"city_id": "city_1", "policy": "stable"},
        )
        reloaded = self.store.get_campaign_for_user(campaign.campaign_id, self.alice.user_id)

        self.assertEqual(len(queued.queued_actions), 1)
        self.assertEqual(len(replaced.queued_actions), 1)
        self.assertEqual(len(reloaded.queued_actions), 1)
        self.assertEqual(reloaded.queued_actions[0].month, 1)
        self.assertEqual(reloaded.queued_actions[0].payload["policy"], "stable")
        self.assertEqual(
            reloaded.to_public_dict()["queued_actions"][0]["action_type"],
            "set_city_policy",
        )
        cancelled = self.store.cancel_queued_action(
            campaign_id=campaign.campaign_id,
            user=self.alice,
            action_id=reloaded.queued_actions[0].action_id,
        )
        self.assertEqual(cancelled.queued_actions, ())
        emptied = self.store.get_campaign_for_user(campaign.campaign_id, self.alice.user_id)
        self.assertEqual(emptied.queued_actions, ())

        world = reloaded.world
        world.current_month = 2
        self.store.update_world(campaign.campaign_id, self.alice.user_id, world)
        self.store.mark_queued_actions_resolved(campaign.campaign_id, self.alice.user_id, 1)
        advanced = self.store.get_campaign_for_user(campaign.campaign_id, self.alice.user_id)

        self.assertEqual(advanced.queued_actions, ())


class StrategyTacticsTests(unittest.TestCase):
    def test_public_world_includes_policy_choices_tech_tree_and_troop_conversion(self) -> None:
        world = generate_random_world(seed=41, city_count=4, faction_count=2)
        public = world.to_public_dict()

        self.assertIn("稳定优先", public["policy_choices"])
        self.assertIn("suppress", {choice["id"] for choice in public["rebellion_action_choices"]})
        self.assertEqual(public["factions"][0]["tactic_tech_tree"][0]["id"], "local_militia")
        self.assertFalse(public["factions"][0]["tactic_tech_tree"][0]["unlocked"])
        self.assertEqual(public["factions"][0]["tactic_tech_tree"][0]["category"], "military")
        self.assertEqual(public["factions"][0]["tactic_tech_tree"][0]["research_months"], 1)
        self.assertEqual(public["cities"][0]["troop_conversion"][0]["source"], "city_feature")
        self.assertEqual(sum(row["ratio"] for row in public["cities"][0]["troop_conversion"]), 100)

    def test_unlock_tactic_tech_pays_cost_and_changes_city_feature_ratio(self) -> None:
        world = generate_random_world(seed=42, city_count=4, faction_count=2)
        city = world.cities[0]
        faction = world.factions[0]
        before_conversion = city_troop_conversion(city, faction)
        before_money = faction.resources.money

        unlocked = unlock_tactic_tech(world, faction_id="faction_1", tech_id="local_militia")
        unlocked_faction = unlocked.factions[0]
        after_conversion = city_troop_conversion(unlocked.cities[0], unlocked_faction)

        self.assertEqual(world.factions[0].tactic_techs, [])
        self.assertEqual(unlocked_faction.tactic_techs, ["local_militia"])
        self.assertEqual(unlocked_faction.resources.money, before_money - 80)
        self.assertGreater(after_conversion[0]["ratio"], before_conversion[0]["ratio"])
        self.assertTrue(any(event.category == "tactic_tech" for event in unlocked.event_log))

    def test_unlock_tactic_tech_requires_prerequisites_resources_and_no_duplicates(self) -> None:
        world = generate_random_world(seed=43, city_count=4, faction_count=2)

        with self.assertRaises(StrategyError):
            unlock_tactic_tech(world, faction_id="faction_1", tech_id="city_doctrine")

        unlocked = unlock_tactic_tech(world, faction_id="faction_1", tech_id="local_militia")
        with self.assertRaises(StrategyError):
            unlock_tactic_tech(unlocked, faction_id="faction_1", tech_id="local_militia")

        poor = generate_random_world(seed=44, city_count=4, faction_count=2)
        poor.factions[0].resources.money = 0
        with self.assertRaises(StrategyError):
            unlock_tactic_tech(poor, faction_id="faction_1", tech_id="local_militia")

    def test_tactic_tech_tree_public_marks_available_after_unlock(self) -> None:
        world = generate_random_world(seed=45, city_count=4, faction_count=2)
        unlocked = unlock_tactic_tech(world, faction_id="faction_1", tech_id="local_militia")
        tree = {item["id"]: item for item in tactic_tech_tree_public(unlocked.factions[0])}

        self.assertTrue(tree["local_militia"]["unlocked"])
        self.assertTrue(tree["city_doctrine"]["available"])
        self.assertTrue(tree["fortified_garrison"]["available"])
        self.assertFalse(tree["combined_arms"]["available"])

    def test_multi_month_research_starts_ticks_and_cancel_clears_progress(self) -> None:
        world = generate_random_world(seed=48, city_count=4, faction_count=2)
        world.factions[0].resources.money = 400
        started = unlock_tactic_tech(world, faction_id="faction_1", tech_id="irrigation")
        faction = started.factions[0]
        self.assertEqual(faction.researching.get("tech_id"), "irrigation")
        self.assertEqual(faction.researching.get("months_done"), 1)
        self.assertNotIn("irrigation", faction.tactic_techs)
        with self.assertRaises(StrategyError):
            unlock_tactic_tech(started, faction_id="faction_1", tech_id="granary")
        cancelled = cancel_tactic_research(started, faction_id="faction_1")
        self.assertEqual(cancelled.factions[0].researching, {})
        restarted = unlock_tactic_tech(cancelled, faction_id="faction_1", tech_id="irrigation")
        finished = advance_tactic_research(restarted)
        self.assertIn("irrigation", finished.factions[0].tactic_techs)
        self.assertEqual(finished.factions[0].researching, {})

    def test_roster_for_city_troops_maps_city_features_to_battle_hero_codes(self) -> None:
        world = generate_random_world(seed=46, city_count=4, faction_count=2)
        city = world.cities[0]
        faction = world.factions[0]

        roster = roster_for_city_troops(
            city,
            faction,
            troop_count=280,
            available_hero_codes={"strategy_garrison", "strategy_infantry", "strategy_cavalry", "strategy_archer"},
        )

        self.assertEqual(len(roster.roster), 3)
        self.assertIn("strategy_garrison", roster.roster)
        self.assertIn("strategy_infantry", roster.roster)
        self.assertTrue(any(row["source"] == "city_feature" for row in roster.manifest))

    def test_roster_for_city_troops_uses_tactic_tech_ratio_for_feature_units(self) -> None:
        world = generate_random_world(seed=47, city_count=4, faction_count=2)
        base_city = world.cities[2]
        base_faction = world.factions[0]
        base_roster = roster_for_city_troops(
            base_city,
            base_faction,
            troop_count=1000,
            available_hero_codes={"strategy_garrison", "strategy_infantry", "strategy_cavalry", "strategy_archer"},
        )

        unlocked = unlock_tactic_tech(world, faction_id="faction_1", tech_id="local_militia")
        upgraded_roster = roster_for_city_troops(
            unlocked.cities[2],
            unlocked.factions[0],
            troop_count=1000,
            available_hero_codes={"strategy_garrison", "strategy_infantry", "strategy_cavalry", "strategy_archer"},
        )

        base_feature_units = sum(row["grid_units"] for row in base_roster.manifest if row["source"] == "city_feature")
        upgraded_feature_units = sum(
            row["grid_units"] for row in upgraded_roster.manifest if row["source"] == "city_feature"
        )
        self.assertEqual(base_feature_units, 1)
        self.assertEqual(upgraded_feature_units, 2)
        self.assertIn("strategy_cavalry", upgraded_roster.roster)

    def test_strategy_soldiers_are_internal_battle_units_not_public_manual_picks(self) -> None:
        public_codes = {hero["code"] for hero in list_heroes()}
        self.assertNotIn("strategy_infantry", public_codes)

        battle = create_battle(["strategy_infantry", "strategy_archer"], ["strategy_garrison"])
        unit_codes = {unit.hero_code for unit in battle.all_units()}

        self.assertEqual(unit_codes, {"strategy_infantry", "strategy_archer", "strategy_garrison"})

    def test_set_city_policy_validates_owner_and_policy(self) -> None:
        world = generate_random_world(seed=46, city_count=4, faction_count=2)
        updated = set_city_policy(
            world,
            faction_id="faction_1",
            city_id="city_1",
            policy="征兵优先",
        )

        self.assertEqual(world.cities[0].policy, "稳定优先")
        self.assertEqual(updated.cities[0].policy, "征兵优先")
        self.assertTrue(any(event.category == "city_policy" for event in updated.event_log))

        with self.assertRaises(StrategyError):
            set_city_policy(updated, faction_id="faction_2", city_id="city_1", policy="粮食优先")
        with self.assertRaises(StrategyError):
            set_city_policy(updated, faction_id="faction_1", city_id="city_1", policy="不存在")


class StrategyBattleTests(unittest.TestCase):
    def test_declare_city_attack_resolves_adjacent_enemy_city_and_records_choice(self) -> None:
        world = generate_random_world(seed=51, city_count=4, faction_count=2)
        source = world.cities[0]
        target = world.cities[1]
        source.resources.troops = 2400
        target.resources.troops = 20
        target.defense = 0

        resolved = declare_city_attack(
            world,
            faction_id="faction_1",
            source_city_id=source.city_id,
            target_city_id=target.city_id,
            resolution_mode="quick",
        )
        battle = resolved.pending_battles[-1]

        self.assertEqual(world.cities[1].owner_faction_id, "faction_2")
        self.assertEqual(resolved.cities[1].owner_faction_id, "faction_1")
        self.assertEqual(battle.status, "resolved")
        self.assertEqual(battle.resolution_mode, "quick")
        self.assertEqual(battle.winner_faction_id, "faction_1")
        self.assertEqual(battle.battle_result["winner_side"], "attacker")
        self.assertEqual(battle.battle_result["winner_faction_id"], "faction_1")
        self.assertEqual(battle.battle_result["loser_faction_id"], "faction_2")
        self.assertTrue(battle.battle_result["city_captured"])
        self.assertEqual(battle.battle_result["resolution_source"], "sandbox")
        self.assertGreaterEqual(battle.battle_result["lost_troops_by_side"]["defender"], 0)
        self.assertGreaterEqual(battle.battle_result["remaining_troops_by_side"]["attacker"], 0)
        self.assertTrue(any(event.category == "battle_declared" for event in resolved.event_log))
        self.assertTrue(any(event.category == "battle_resolved" for event in resolved.event_log))
        self.assertEqual(resolved.cities[1].occupation["status"], "pending")
        self.assertEqual(resolved.cities[1].occupation["previous_owner_faction_id"], "faction_2")
        self.assertTrue(any(event.category == "occupation_policy_required" for event in resolved.event_log))

    def test_city_capture_stations_surviving_attacker_heroes(self) -> None:
        world = generate_random_world(seed=51, city_count=4, faction_count=2)
        source = world.cities[0]
        target = world.cities[1]
        source.resources.troops = 2400
        target.resources.troops = 20
        target.defense = 0
        hero = next(
            item
            for item in world.strategic_heroes
            if item.faction_id == "faction_1" and item.status == "serving"
        )
        lord = next(item for item in world.offices if item.faction_id == "faction_1" and item.office_type == "lord")
        gathered = assign_strategic_hero_duty(
            world,
            faction_id="faction_1",
            issuer_office_id=lord.office_id,
            hero_code=hero.hero_code,
            assignment_type="garrison",
            target_id=source.city_id,
        )
        resolved = declare_city_attack(
            gathered,
            faction_id="faction_1",
            source_city_id=source.city_id,
            target_city_id=target.city_id,
            resolution_mode="quick",
            attacker_hero_codes=[hero.hero_code],
        )
        stationed = next(item for item in resolved.strategic_heroes if item.hero_code == hero.hero_code)
        self.assertEqual(stationed.status, "serving")
        self.assertEqual(stationed.city_id, target.city_id)
        self.assertEqual(stationed.assignment_type, "garrison")
        self.assertEqual(stationed.assignment_target_id, target.city_id)

    def test_city_capture_stations_fallen_attacker_heroes(self) -> None:
        world = generate_random_world(seed=51, city_count=4, faction_count=2)
        source = world.cities[0]
        target = world.cities[1]
        source.resources.troops = 2400
        target.resources.troops = 20
        target.defense = 0
        hero = next(
            item
            for item in world.strategic_heroes
            if item.faction_id == "faction_1" and item.status == "serving"
        )
        lord = next(item for item in world.offices if item.faction_id == "faction_1" and item.office_type == "lord")
        gathered = assign_strategic_hero_duty(
            world,
            faction_id="faction_1",
            issuer_office_id=lord.office_id,
            hero_code=hero.hero_code,
            assignment_type="garrison",
            target_id=source.city_id,
        )
        pending = declare_city_attack(
            gathered,
            faction_id="faction_1",
            source_city_id=source.city_id,
            target_city_id=target.city_id,
            resolution_mode="manual",
            attacker_hero_codes=[hero.hero_code],
        )
        attached = attach_battle_room(
            pending,
            battle_id=pending.pending_battles[-1].battle_id,
            room_id="fallen_station",
            invite_path="/?room=FALLEN_STATION",
        )
        resolved = resolve_battle_room_result(
            attached,
            battle_room_id="FALLEN_STATION",
            winner_team_id=1,
            surviving_grid_units_by_team={1: 4, 2: 0},
            surviving_hero_codes_by_team={1: set(), 2: set()},
        )
        stationed = next(item for item in resolved.strategic_heroes if item.hero_code == hero.hero_code)
        self.assertTrue(resolved.pending_battles[-1].battle_result["city_captured"])
        self.assertEqual(stationed.status, "sleeping")
        self.assertEqual(stationed.sleeping_until_month, resolved.current_month + STRATEGIC_HERO_BATTLE_SLEEP_MONTHS)
        self.assertEqual(stationed.city_id, target.city_id)
        self.assertEqual(stationed.assignment_type, "garrison")
        self.assertEqual(stationed.assignment_target_id, target.city_id)
        self.assertNotIn(hero.hero_code, active_strategic_hero_codes_for_faction(resolved, "faction_1"))

    def test_wounded_hero_can_transfer_but_cannot_take_missions(self) -> None:
        world = generate_random_world(seed=51, city_count=4, faction_count=2)
        source = world.cities[0]
        other = next(item for item in world.cities if item.city_id != source.city_id)
        other.owner_faction_id = "faction_1"
        hero = next(
            item
            for item in world.strategic_heroes
            if item.faction_id == "faction_1" and item.status == "serving"
        )
        lord = next(item for item in world.offices if item.faction_id == "faction_1" and item.office_type == "lord")
        stationed = assign_strategic_hero_duty(
            world,
            faction_id="faction_1",
            issuer_office_id=lord.office_id,
            hero_code=hero.hero_code,
            assignment_type="garrison",
            target_id=source.city_id,
        )
        wounded = next(item for item in stationed.strategic_heroes if item.hero_code == hero.hero_code)
        wounded.status = "sleeping"
        wounded.sleeping_until_month = stationed.current_month + STRATEGIC_HERO_BATTLE_SLEEP_MONTHS
        transferred = assign_strategic_hero_duty(
            stationed,
            faction_id="faction_1",
            issuer_office_id=lord.office_id,
            hero_code=hero.hero_code,
            assignment_type="garrison",
            target_id=other.city_id,
        )
        moved = next(item for item in transferred.strategic_heroes if item.hero_code == hero.hero_code)
        self.assertEqual(moved.status, "sleeping")
        self.assertEqual(moved.city_id, other.city_id)
        self.assertEqual(moved.assignment_type, "garrison")
        self.assertEqual(moved.assignment_target_id, other.city_id)
        with self.assertRaises(StrategyError) as ctx:
            assign_strategic_hero_duty(
                transferred,
                faction_id="faction_1",
                issuer_office_id=lord.office_id,
                hero_code=hero.hero_code,
                assignment_type="training",
                target_id=other.city_id,
            )
        self.assertIn("负伤", str(ctx.exception))
        reserved = assign_strategic_hero_duty(
            transferred,
            faction_id="faction_1",
            issuer_office_id=lord.office_id,
            hero_code=hero.hero_code,
            assignment_type="reserve",
        )
        waiting = next(item for item in reserved.strategic_heroes if item.hero_code == hero.hero_code)
        self.assertEqual(waiting.status, "sleeping")
        self.assertEqual(waiting.assignment_type, "reserve")
        with self.assertRaises(StrategyError):
            normalize_strategic_hero_deployment(reserved, "faction_1", [hero.hero_code])

    def test_declare_city_attack_does_not_force_lord_into_the_party(self) -> None:
        world = generate_random_world(seed=51, city_count=4, faction_count=2)
        world.cities[0].resources.troops = 400
        world.cities[1].resources.troops = 80
        lord = next(item for item in world.offices if item.faction_id == "faction_1" and item.office_type == "lord")
        companion = next(
            item
            for item in world.strategic_heroes
            if item.faction_id == "faction_1"
            and item.status == "serving"
            and item.hero_code != lord.holder_id
            and item.office_id != lord.office_id
        )
        stationed = assign_strategic_hero_duty(
            world,
            faction_id="faction_1",
            issuer_office_id=lord.office_id,
            hero_code=companion.hero_code,
            assignment_type="garrison",
            target_id=world.cities[0].city_id,
        )
        pending = declare_city_attack(
            stationed,
            faction_id="faction_1",
            source_city_id=world.cities[0].city_id,
            target_city_id=world.cities[1].city_id,
            resolution_mode="pending_choice",
            attacker_hero_codes=[companion.hero_code],
        )
        self.assertEqual(pending.pending_battles[-1].attacker_hero_codes, [companion.hero_code])
        self.assertNotIn(lord.holder_id, pending.pending_battles[-1].attacker_hero_codes)

    def test_city_capture_keeps_stationed_heroes_after_month_and_hero_sync(self) -> None:
        world = generate_random_world(seed=51, city_count=4, faction_count=2)
        source = world.cities[0]
        target = world.cities[1]
        source.resources.troops = 2400
        target.resources.troops = 20
        target.defense = 0
        heroes = [
            item
            for item in world.strategic_heroes
            if item.faction_id == "faction_1" and item.status == "serving"
        ][:3]
        self.assertGreaterEqual(len(heroes), 1)
        lord = next(item for item in world.offices if item.faction_id == "faction_1" and item.office_type == "lord")
        gathered = world
        for hero in heroes:
            gathered = assign_strategic_hero_duty(
                gathered,
                faction_id="faction_1",
                issuer_office_id=lord.office_id,
                hero_code=hero.hero_code,
                assignment_type="garrison",
                target_id=source.city_id,
            )
        resolved = declare_city_attack(
            gathered,
            faction_id="faction_1",
            source_city_id=source.city_id,
            target_city_id=target.city_id,
            resolution_mode="quick",
            attacker_hero_codes=[hero.hero_code for hero in heroes],
        )
        synced = ensure_strategic_hero_system(resolved)
        advanced = advance_month(synced)
        for hero in heroes:
            stationed = next(item for item in advanced.strategic_heroes if item.hero_code == hero.hero_code)
            self.assertEqual(stationed.city_id, target.city_id)
            self.assertEqual(stationed.assignment_type, "garrison")
            self.assertEqual(stationed.assignment_target_id, target.city_id)

    def test_starting_capitals_have_one_cannon(self) -> None:
        world = generate_random_world(seed=51, city_count=4, faction_count=2)
        capitals = [city for city in world.cities if "主城候选" in (city.traits or [])]
        others = [city for city in world.cities if "主城候选" not in (city.traits or [])]
        self.assertTrue(capitals)
        self.assertTrue(all(city.cannon_stock == 1 for city in capitals))
        self.assertTrue(all(city.cannon_stock == 0 for city in others))

    def test_formula_city_attack_resolves_without_real_room_and_reduces_troops(self) -> None:
        world = generate_random_world(seed=51, city_count=4, faction_count=2)
        source = world.cities[0]
        target = world.cities[1]
        source.resources.troops = 800
        target.resources.troops = 500
        target.defense = 3
        pending = declare_city_attack(
            world,
            faction_id="faction_1",
            source_city_id=source.city_id,
            target_city_id=target.city_id,
            resolution_mode="formula",
            auto_resolve=False,
            committed_troops=400,
        )
        battle = pending.pending_battles[-1]
        composed = set_pending_battle_composition(
            pending,
            battle_id=battle.battle_id,
            composition={"infantry": 10, "archer": 5, "cavalry": 2},
        )
        composed.pending_battles[-1].resolution_mode = "formula"
        preview = simulate_formula_city_attack(composed, composed.pending_battles[-1])
        resolved = resolve_pending_battle(composed, battle_id=battle.battle_id)
        settled = resolved.pending_battles[-1]

        self.assertEqual(settled.status, "resolved")
        self.assertFalse(settled.battle_room_id)
        self.assertEqual(settled.resolution_mode, "formula")
        self.assertEqual(settled.battle_result["resolution_source"], "formula")
        self.assertEqual(settled.battle_result["lost_troops_by_side"]["attacker"], preview["attacker_losses"])
        self.assertEqual(settled.battle_result["lost_troops_by_side"]["defender"], preview["defender_losses"])
        self.assertGreater(preview["attacker_losses"] + preview["defender_losses"], 0)
        self.assertTrue(any("快速结算" in row for row in settled.report))
        self.assertTrue(any("快速结算" in event.message for event in resolved.event_log if event.category == "battle_resolved"))

    def test_watch_ai_city_attack_waits_for_real_battle_room(self) -> None:
        for mode in ("watch_ai", "ai_auto"):
            with self.subTest(mode=mode):
                world = generate_random_world(seed=55, city_count=4, faction_count=2)
                _ensure_city_road(world, "city_1", "city_2")
                world.cities[0].resources.troops = 2400
                world.cities[1].resources.troops = 20

                pending = declare_city_attack(
                    world,
                    faction_id="faction_1",
                    source_city_id="city_1",
                    target_city_id="city_2",
                    resolution_mode=mode,
                )
                battle = pending.pending_battles[-1]

                self.assertEqual(pending.cities[1].owner_faction_id, "faction_2")
                self.assertEqual(battle.status, "pending")
                self.assertEqual(battle.resolution_mode, mode)
                self.assertIsNone(battle.winner_faction_id)
                self.assertTrue(any(event.category == "battle_declared" for event in pending.event_log))
                self.assertFalse(any(event.category == "battle_resolved" for event in pending.event_log))

    def test_resolve_battle_room_result_uses_real_room_winner_and_is_idempotent(self) -> None:
        world = generate_random_world(seed=56, city_count=4, faction_count=2)
        _ensure_city_road(world, "city_1", "city_2")
        world.cities[0].resources.troops = 2400
        world.cities[1].resources.troops = 120
        pending = declare_city_attack(
            world,
            faction_id="faction_1",
            source_city_id="city_1",
            target_city_id="city_2",
            resolution_mode="manual",
        )
        attached = attach_battle_room(
            pending,
            battle_id=pending.pending_battles[-1].battle_id,
            room_id="room_test",
            invite_path="/?room=ROOM_TEST",
        )

        pending_battle = pending.pending_battles[-1]
        attacker_grid = sum(pending_battle.attacker_composition.values())
        defender_grid = sum(pending_battle.defender_composition.values())
        attacker_remaining = round(pending_battle.attacker_troops * 4 / attacker_grid)
        defender_remaining = round(pending_battle.defender_troops * 1 / defender_grid)
        source_after_declare = pending.cities[0].resources.troops

        resolved = resolve_battle_room_result(
            attached,
            battle_room_id="ROOM_TEST",
            winner_team_id=2,
            battle_summary="attacker surrendered",
            surviving_grid_units_by_team={1: 4, 2: 1},
        )
        battle = resolved.pending_battles[-1]

        self.assertEqual(battle.status, "resolved")
        self.assertEqual(battle.winner_faction_id, "faction_2")
        self.assertEqual(resolved.cities[1].owner_faction_id, "faction_2")
        self.assertEqual(resolved.cities[0].resources.troops, source_after_declare + attacker_remaining)
        self.assertEqual(resolved.cities[1].resources.troops, defender_remaining)
        self.assertEqual(battle.battle_result["winner_side"], "defender")
        self.assertEqual(battle.battle_result["loser_side"], "attacker")
        self.assertEqual(battle.battle_result["resolution_source"], "real_grid")
        self.assertFalse(battle.battle_result["city_captured"])
        self.assertEqual(battle.battle_result["lost_troops_by_side"]["attacker"], pending_battle.attacker_troops - attacker_remaining)
        self.assertEqual(battle.battle_result["lost_troops_by_side"]["defender"], pending_battle.defender_troops - defender_remaining)
        self.assertEqual(battle.battle_result["remaining_troops_by_side"]["attacker"], attacker_remaining)
        self.assertEqual(battle.battle_result["remaining_troops_by_side"]["defender"], defender_remaining)
        self.assertEqual(battle.battle_result["initial_grid_units_by_side"]["attacker"], attacker_grid)
        self.assertEqual(battle.battle_result["initial_grid_units_by_side"]["defender"], defender_grid)
        self.assertEqual(battle.battle_result["surviving_grid_units_by_side"]["attacker"], 4)
        self.assertEqual(battle.battle_result["surviving_grid_units_by_side"]["defender"], 1)
        self.assertIn("真实战场", battle.battle_result["battle_log_summary"])
        self.assertTrue(any(f"攻方 4/{attacker_grid}" in row for row in battle.report))
        self.assertTrue(any(f"守方 1/{defender_grid}" in row for row in battle.report))
        self.assertTrue(any("ROOM_TEST" in row for row in battle.report))
        self.assertEqual(
            sum(1 for event in resolved.event_log if event.category == "battle_resolved"),
            1,
        )

        resolved_again = resolve_battle_room_result(
            resolved,
            battle_room_id="ROOM_TEST",
            winner_team_id=2,
            battle_summary="duplicate poll",
        )
        self.assertEqual(resolved_again.to_dict(), resolved.to_dict())

    def test_declare_city_attack_validates_mode_owner_target_and_troops(self) -> None:
        world = generate_random_world(seed=52, city_count=4, faction_count=2)
        with self.assertRaises(StrategyError):
            declare_city_attack(
                world,
                faction_id="faction_1",
                source_city_id="city_1",
                target_city_id="city_2",
                resolution_mode="unknown",
            )
        with self.assertRaises(StrategyError):
            declare_city_attack(
                world,
                faction_id="faction_2",
                source_city_id="city_1",
                target_city_id="city_2",
                resolution_mode="quick",
            )
        world.cities[0].resources.troops = 10
        with self.assertRaises(StrategyError):
            declare_city_attack(
                world,
                faction_id="faction_1",
                source_city_id="city_1",
                target_city_id="city_2",
                resolution_mode="quick",
            )

    def test_public_world_includes_battle_resolution_modes_and_battle_records(self) -> None:
        world = generate_random_world(seed=53, city_count=4, faction_count=2)
        _ensure_city_road(world, "city_1", "city_2")
        world.cities[0].resources.troops = 2400
        world.cities[1].resources.troops = 20
        world.cities[1].defense = 0
        resolved = declare_city_attack(
            world,
            faction_id="faction_1",
            source_city_id="city_1",
            target_city_id="city_2",
            resolution_mode="quick",
        )
        public = resolved.to_public_dict()

        self.assertIn("manual", public["battle_resolution_modes"])
        self.assertIn("formula", public["battle_resolution_modes"])
        self.assertNotIn("pending_choice", public["battle_resolution_modes"])
        self.assertEqual(public["city_monthly_order_limit"], 2)
        self.assertEqual(public["battle_unit_costs"]["infantry"], 10)
        self.assertEqual(public["battle_unit_costs"]["cavalry"], 50)
        self.assertEqual(public["pending_battles"][-1]["status"], "resolved")

    def test_pending_choice_attack_can_retreat_or_compose_units(self) -> None:
        world = generate_random_world(seed=57, city_count=4, faction_count=2)
        _ensure_city_road(world, "city_1", "city_2")
        world.cities[0].resources.troops = 400
        world.cities[1].resources.troops = 80
        pending = declare_city_attack(
            world,
            faction_id="faction_1",
            source_city_id="city_1",
            target_city_id="city_2",
            resolution_mode="pending_choice",
        )
        battle = pending.pending_battles[-1]
        self.assertEqual(battle.status, "pending")
        self.assertEqual(battle.resolution_mode, "pending_choice")
        composed = set_pending_battle_composition(
            pending,
            battle_id=battle.battle_id,
            composition={"infantry": 3, "cavalry": 1},
        )
        self.assertEqual(composed.pending_battles[-1].attacker_composition, {"infantry": 3, "cavalry": 1})
        with self.assertRaises(StrategyError):
            set_pending_battle_composition(pending, battle_id=battle.battle_id, composition={})
        with self.assertRaisesRegex(StrategyError, "配兵超过"):
            set_pending_battle_composition(
                pending,
                battle_id=battle.battle_id,
                composition={"infantry": 16, "archer": 16, "cavalry": 16},
            )
        remaining_after_declare = pending.cities[0].resources.troops
        retreated = retreat_pending_battle(pending, battle_id=battle.battle_id, faction_id="faction_1")
        returned = (battle.attacker_troops * 7) // 10
        self.assertEqual(retreated.pending_battles[-1].status, "resolved")
        self.assertEqual(retreated.pending_battles[-1].resolution_mode, "retreat")
        self.assertEqual(retreated.cities[0].resources.troops, remaining_after_declare + returned)
        sieged = convert_pending_battle_to_siege(pending, battle_id=battle.battle_id, faction_id="faction_1")
        self.assertEqual(sieged.pending_battles[-1].resolution_mode, "siege")
        self.assertTrue(sieged.sieges)

    def test_city_attack_auto_composes_defender_for_real_battle(self) -> None:
        world = generate_random_world(seed=79, city_count=4, faction_count=2)
        _ensure_city_road(world, "city_1", "city_2")
        world.cities[0].resources.troops = 1200
        world.cities[1].resources.troops = 300
        pending = declare_city_attack(
            world,
            faction_id="faction_1",
            source_city_id="city_1",
            target_city_id="city_2",
            resolution_mode="manual",
        )
        battle = pending.pending_battles[-1]
        expected = auto_battle_composition(300, city=pending.cities[1])
        rosters = strategy_battle_rosters(pending, battle)
        defender_soldiers = [code for code in rosters.defender.roster if str(code).startswith("strategy_")]

        self.assertEqual(battle.defender_composition, expected)
        self.assertGreaterEqual(sum(battle.defender_composition.values()), 15)
        self.assertGreater(len(defender_soldiers), 12)
        self.assertEqual(len(defender_soldiers), sum(expected.values()))

    def test_city_attack_rejects_heroes_not_stationed_at_source(self) -> None:
        world = generate_random_world(seed=58, city_count=4, faction_count=2)
        _ensure_city_road(world, "city_1", "city_2")
        world.cities[0].resources.troops = 400
        world.cities[1].resources.troops = 80
        own_cities = [city for city in world.cities if city.owner_faction_id == "faction_1"]
        if len(own_cities) < 2:
            self.skipTest("need two owned cities to station a remote hero")
        remote = next(city for city in own_cities if city.city_id != "city_1")
        hero = next(
            item
            for item in world.strategic_heroes
            if item.faction_id == "faction_1" and item.status == "serving"
        )
        lord = next(item for item in world.offices if item.faction_id == "faction_1" and item.office_type == "lord")
        stationed = assign_strategic_hero_duty(
            world,
            faction_id="faction_1",
            issuer_office_id=lord.office_id,
            hero_code=hero.hero_code,
            assignment_type="garrison",
            target_id=remote.city_id,
        )
        with self.assertRaisesRegex(StrategyError, "出发城"):
            declare_city_attack(
                stationed,
                faction_id="faction_1",
                source_city_id="city_1",
                target_city_id="city_2",
                resolution_mode="pending_choice",
                attacker_hero_codes=[hero.hero_code],
            )
        gathered = assign_strategic_hero_duty(
            stationed,
            faction_id="faction_1",
            issuer_office_id=lord.office_id,
            hero_code=hero.hero_code,
            assignment_type="garrison",
            target_id="city_1",
        )
        pending = declare_city_attack(
            gathered,
            faction_id="faction_1",
            source_city_id="city_1",
            target_city_id="city_2",
            resolution_mode="pending_choice",
            attacker_hero_codes=[hero.hero_code],
        )
        self.assertEqual(pending.pending_battles[-1].attacker_hero_codes, [hero.hero_code])

    def test_city_attack_uses_explicit_committed_troops_and_rejects_busy_heroes(self) -> None:
        world = generate_random_world(seed=58, city_count=4, faction_count=2)
        world.cities[0].resources.troops = 400
        world.cities[1].resources.troops = 80
        hero = next(
            item
            for item in world.strategic_heroes
            if item.faction_id == "faction_1" and item.status == "serving"
        )
        lord = next(item for item in world.offices if item.faction_id == "faction_1" and item.office_type == "lord")
        gathered = assign_strategic_hero_duty(
            world,
            faction_id="faction_1",
            issuer_office_id=lord.office_id,
            hero_code=hero.hero_code,
            assignment_type="garrison",
            target_id="city_1",
        )
        first = declare_city_attack(
            gathered,
            faction_id="faction_1",
            source_city_id="city_1",
            target_city_id="city_2",
            resolution_mode="pending_choice",
            attacker_hero_codes=[hero.hero_code],
            committed_troops=80,
        )
        self.assertEqual(first.pending_battles[-1].attacker_troops, 80)
        self.assertEqual(first.cities[0].resources.troops, 320)
        with self.assertRaisesRegex(StrategyError, "其他出征"):
            declare_city_attack(
                first,
                faction_id="faction_1",
                source_city_id="city_1",
                target_city_id="city_2",
                resolution_mode="pending_choice",
                attacker_hero_codes=[hero.hero_code],
                committed_troops=80,
            )
        with self.assertRaisesRegex(StrategyError, "可带走兵力不足"):
            declare_city_attack(
                first,
                faction_id="faction_1",
                source_city_id="city_1",
                target_city_id="city_2",
                resolution_mode="pending_choice",
                committed_troops=400,
            )
        second = declare_city_attack(
            first,
            faction_id="faction_1",
            source_city_id="city_1",
            target_city_id="city_2",
            resolution_mode="pending_choice",
            committed_troops=80,
        )
        self.assertEqual(second.cities[0].resources.troops, 240)
        self.assertEqual(len([item for item in second.pending_battles if item.status == "pending"]), 2)

    def test_strategy_battle_board_size_follows_settlement(self) -> None:
        from wujiang.bridge.battle_bridge import strategy_battle_board_size

        self.assertEqual(strategy_battle_board_size("village"), (20, 16))
        self.assertEqual(strategy_battle_board_size("town"), (26, 18))
        self.assertEqual(strategy_battle_board_size("city"), (32, 22))
        self.assertEqual(strategy_battle_board_size("fortress"), (32, 22))
        self.assertEqual(strategy_battle_board_size(""), (32, 22))

    def test_strategy_battle_board_fits_fifty_vs_fifty_units(self) -> None:
        from wujiang.bridge.battle_bridge import (
            STRATEGY_BATTLE_BOARD_HEIGHT,
            STRATEGY_BATTLE_BOARD_WIDTH,
            siege_wall_cells,
            strategy_battle_board_size,
        )
        from wujiang.tactical.heroes.registry import RoomBattleEntry, create_room_battle, spawn_cells_for_anchor

        left = [RoomBattleEntry("strategy_infantry", 1, 1) for _ in range(50)]
        right = [RoomBattleEntry("strategy_infantry", 2, 2) for _ in range(50)]
        battle = create_room_battle(
            left,
            right,
            board_width=STRATEGY_BATTLE_BOARD_WIDTH,
            board_height=STRATEGY_BATTLE_BOARD_HEIGHT,
        )
        self.assertEqual(len(battle.units), 100)
        walls = siege_wall_cells(battle.width, battle.height)
        for unit in battle.units.values():
            self.assertFalse(spawn_cells_for_anchor(unit, unit.position) & walls)

        village_width, village_height = strategy_battle_board_size("village")
        village = create_room_battle(
            [RoomBattleEntry("strategy_infantry", 1, 1) for _ in range(20)],
            [RoomBattleEntry("strategy_infantry", 2, 2) for _ in range(20)],
            board_width=village_width,
            board_height=village_height,
        )
        self.assertEqual((village.width, village.height), (20, 16))
        village_walls = siege_wall_cells(village.width, village.height)
        for unit in village.units.values():
            self.assertFalse(spawn_cells_for_anchor(unit, unit.position) & village_walls)

    def test_city_defense_deploys_gate_towers_and_starting_cannons(self) -> None:
        from types import SimpleNamespace

        from wujiang.bridge.battle_bridge import (
            STRATEGY_BATTLE_BOARD_HEIGHT,
            STRATEGY_BATTLE_BOARD_WIDTH,
            deploy_city_defense_structures,
            siege_wall_cells,
            siege_wall_layout,
            strategy_room_survivors_by_team,
        )
        from wujiang.tactical.heroes.registry import RoomBattleEntry, create_room_battle

        layout = siege_wall_layout(STRATEGY_BATTLE_BOARD_WIDTH, STRATEGY_BATTLE_BOARD_HEIGHT)
        blocked = siege_wall_cells(STRATEGY_BATTLE_BOARD_WIDTH, STRATEGY_BATTLE_BOARD_HEIGHT)
        self.assertEqual(len(layout["tower_cells"]), 2)
        for cell in layout["tower_cells"]:
            self.assertNotIn(cell, blocked)
            self.assertEqual(cell[0], layout["wall_x"])
        left = [RoomBattleEntry("strategy_infantry", 1, 1)]
        right = [RoomBattleEntry("strategy_infantry", 2, 2)]
        battle = create_room_battle(
            left,
            right,
            board_width=STRATEGY_BATTLE_BOARD_WIDTH,
            board_height=STRATEGY_BATTLE_BOARD_HEIGHT,
        )
        battle.blocked_cells = set(blocked)
        placed = deploy_city_defense_structures(battle, defender_has_towers=True, deploy_cannons=True)
        self.assertEqual(placed["towers"], 2)
        self.assertEqual(placed["cannons"], 2)
        towers = [unit for unit in battle.all_units() if unit.hero_code == "strategy_arrow_tower"]
        cannons = [unit for unit in battle.all_units() if unit.hero_code == "strategy_cannon"]
        self.assertEqual({(unit.position.x, unit.position.y) for unit in towers}, set(layout["tower_cells"]))
        self.assertEqual(len(cannons), 2)
        self.assertTrue(all(unit.footprint_width == 2 and unit.footprint_height == 2 for unit in cannons))
        survivors = strategy_room_survivors_by_team(SimpleNamespace(battle=battle))
        self.assertEqual(survivors[1], 1)
        self.assertEqual(survivors[2], 1)

    def test_random_world_roads_stay_connected_and_sparse(self) -> None:
        world = generate_random_world(seed=42, city_count=16, faction_count=4)
        graph = {node.node_id: set(node.connected_node_ids) for node in world.nodes}
        seen: set[str] = set()
        stack = [world.nodes[0].node_id]
        while stack:
            node_id = stack.pop()
            if node_id in seen:
                continue
            seen.add(node_id)
            stack.extend(sorted(graph[node_id] - seen))
        self.assertEqual(seen, set(graph))
        degrees = [len(neighbors) for neighbors in graph.values()]
        self.assertLessEqual(max(degrees), 4)
        self.assertLessEqual(sum(degrees) / 2, 16 + 4)

    def test_random_world_places_remote_frontier_cities(self) -> None:
        world = generate_random_world(seed=42, city_count=16, faction_count=4)
        remote = [
            node
            for node in world.nodes
            if node.x <= 12 or node.x >= 88 or node.y <= 12 or node.y >= 88
        ]
        self.assertTrue(remote)

    def test_siege_tech_tree_exposes_requirements_and_foundry_gate(self) -> None:
        world = generate_random_world(seed=45, city_count=4, faction_count=2)
        faction = world.factions[0]
        faction.tactic_techs.extend(["civic_architecture_2", "fortified_garrison"])
        tree = {item["id"]: item for item in tactic_tech_tree_public(faction, world)}
        foundry = tree["cannon_foundry"]
        self.assertEqual(foundry["category"], "siege")
        self.assertEqual(foundry["required_building"], "academy")
        self.assertEqual(foundry["required_building_level"], 2)
        self.assertFalse(foundry["available"])
        capital = next(city for city in world.cities if city.city_id == faction.capital_city_id)
        capital.building_levels["academy"] = 2
        if "academy" not in capital.buildings:
            capital.buildings.append("academy")
        opened = {item["id"]: item for item in tactic_tech_tree_public(faction, world)}
        self.assertTrue(opened["cannon_foundry"]["available"])

    def test_city_work_forges_cannon_and_converts_city_fortress(self) -> None:
        world = generate_random_world(seed=746, city_count=4, faction_count=2)
        faction = world.factions[0]
        city = next(item for item in world.cities if item.owner_faction_id == faction.faction_id)
        city.settlement = "city"
        city.economy_class = "city"
        city.resources.money = 800
        city.resources.food = 800
        city.building_levels["academy"] = 2
        faction.tactic_techs.append("cannon_foundry")
        lord = next(office for office in world.offices if office.faction_id == faction.faction_id and office.office_type == "lord")
        forged = start_city_work(
            world,
            faction_id=faction.faction_id,
            city_id=city.city_id,
            work_id="forge_cannon",
            issuer_office_id=lord.office_id,
        )
        forged_city = next(item for item in forged.cities if item.city_id == city.city_id)
        self.assertEqual(forged_city.cannon_stock, int(city.cannon_stock) + 1)
        converted = start_city_work(
            forged,
            faction_id=faction.faction_id,
            city_id=city.city_id,
            work_id="convert_to_fortress",
            issuer_office_id=lord.office_id,
        )
        fortress = next(item for item in converted.cities if item.city_id == city.city_id)
        self.assertEqual(fortress.settlement, "fortress")
        self.assertEqual(fortress.economy_class, "city")
        back = start_city_work(
            converted,
            faction_id=faction.faction_id,
            city_id=city.city_id,
            work_id="convert_to_city",
            issuer_office_id=lord.office_id,
        )
        restored = next(item for item in back.cities if item.city_id == city.city_id)
        self.assertEqual(restored.settlement, "city")
        self.assertEqual(restored.economy_class, "city")

    def test_city_economy_growth_follows_economy_class_not_fortress(self) -> None:
        from wujiang.strategic.administration import city_economy_growth
        from wujiang.strategic.simulation import _apply_policy

        world = generate_random_world(seed=747, city_count=4, faction_count=2)
        city = world.cities[0]
        city.settlement = "fortress"
        city.economy_class = "town"
        city.policy = "稳定优先"
        city.resources.money = 100
        city.resources.food = 100
        city.resources.population = 1000
        town_growth = city_economy_growth(city)
        self.assertAlmostEqual(town_growth, 1.12)
        events = []
        _apply_policy(city, events, 1)
        self.assertTrue(any("人口 +" in event.message for event in events))
        city.economy_class = "city"
        self.assertAlmostEqual(city_economy_growth(city), 1.35)

    def test_deploy_uses_city_cannon_stock_and_tech_bonuses(self) -> None:
        from wujiang.bridge.battle_bridge import (
            STRATEGY_BATTLE_BOARD_HEIGHT,
            STRATEGY_BATTLE_BOARD_WIDTH,
            deploy_city_defense_structures,
            siege_wall_cells,
        )
        from wujiang.tactical.heroes.registry import RoomBattleEntry, create_room_battle

        battle = create_room_battle(
            [RoomBattleEntry("strategy_infantry", 1, 1)],
            [RoomBattleEntry("strategy_infantry", 2, 2)],
            board_width=STRATEGY_BATTLE_BOARD_WIDTH,
            board_height=STRATEGY_BATTLE_BOARD_HEIGHT,
        )
        battle.blocked_cells = siege_wall_cells(STRATEGY_BATTLE_BOARD_WIDTH, STRATEGY_BATTLE_BOARD_HEIGHT)
        placed = deploy_city_defense_structures(
            battle,
            defender_has_towers=True,
            attacker_cannon_count=2,
            defender_cannon_count=0,
            attacker_bonuses={"cannon_attack": 1, "cannon_range": 1},
            defender_bonuses={"tower_defense": 2},
        )
        self.assertEqual(placed["cannons"], 2)
        cannons = [unit for unit in battle.all_units() if unit.hero_code == "strategy_cannon"]
        towers = [unit for unit in battle.all_units() if unit.hero_code == "strategy_arrow_tower"]
        self.assertEqual(len(cannons), 2)
        self.assertTrue(all(int(unit.stat("attack")) == 4 for unit in cannons))
        self.assertTrue(all(int(unit.stat("attack_range")) == 9 for unit in cannons))
        self.assertTrue(all(int(unit.stat("defense")) == 7 for unit in towers))


class StrategyWorldCrisisTests(unittest.TestCase):
    def _campaign_world(self, *, seed: int = 260) -> WorldState:
        return generate_random_world(
            seed=seed,
            city_count=8,
            faction_count=2,
            neutral_city_states=True,
            campaign_contract=first_campaign_contract(),
        )

    def test_fixed_campaign_starts_with_deterministic_public_crisis_clock(self) -> None:
        world = self._campaign_world()
        self.assertEqual(len(world.world_crises), 1)
        crisis = world.world_crises[0]
        expected_origin = min(world.nodes, key=lambda node: (node.y, node.x, node.node_id))
        expected_frontier = sorted(
            {expected_origin.node_id, *expected_origin.connected_node_ids},
            key=lambda node_id: (
                next(node.y for node in world.nodes if node.node_id == node_id),
                next(node.x for node in world.nodes if node.node_id == node_id),
                node_id,
            ),
        )
        self.assertEqual(crisis.stage, "dormant")
        self.assertEqual(crisis.next_stage_month, 3)
        self.assertEqual(crisis.origin_node_id, expected_origin.node_id)
        self.assertEqual(crisis.frontier_node_ids, expected_frontier)

        public = world_crises_public(world)[0]
        self.assertEqual(public["stage_label"], "潜伏")
        self.assertEqual(public["origin_name"], expected_origin.name)
        self.assertIn("没有路线封锁", public["effect_summary"])
        self.assertTrue(all(item["city_id"] for item in public["frontier"]))

    def test_month_three_triggers_omen_once_without_map_or_army_effects(self) -> None:
        world = self._campaign_world()
        original_connections = [list(node.connected_node_ids) for node in world.nodes]
        original_owners = [city.owner_faction_id for city in world.cities]
        original_armies = [army.to_dict() for army in world.armies]

        month_two = advance_month(world)
        self.assertEqual(month_two.world_crises[0].stage, "dormant")
        month_three = advance_month(month_two)
        crisis = month_three.world_crises[0]
        self.assertEqual(crisis.status, "active")
        self.assertEqual(crisis.stage, "omen")
        self.assertEqual(crisis.pressure, 10)
        self.assertEqual(crisis.started_month, 3)
        self.assertEqual(crisis.next_stage_month, 5)
        self.assertEqual(
            sum(event.category == "world_crisis_omen" for event in month_three.event_log),
            1,
        )
        self.assertEqual([list(node.connected_node_ids) for node in month_three.nodes], original_connections)
        self.assertEqual([city.owner_faction_id for city in month_three.cities], original_owners)
        self.assertEqual([army.to_dict() for army in month_three.armies], original_armies)

        month_four = advance_month(month_three)
        self.assertEqual(month_four.world_crises[0].stage, "omen")
        self.assertEqual(
            sum(event.category == "world_crisis_omen" for event in month_four.event_log),
            1,
        )
        self.assertEqual(
            sum(item.get("event") == "northern_omen_confirmed" for item in month_four.world_crises[0].history),
            1,
        )

    def test_crisis_roundtrip_legacy_backfill_and_sandbox_compatibility(self) -> None:
        world = self._campaign_world()
        restored = WorldState.from_dict(world.to_dict())
        self.assertEqual(restored.world_crises[0].to_dict(), world.world_crises[0].to_dict())

        legacy_payload = world.to_dict()
        legacy_payload.pop("world_crises")
        legacy = WorldState.from_dict(legacy_payload)
        self.assertFalse(legacy.world_crises)
        backfilled = ensure_world_crises(legacy)
        self.assertEqual(backfilled.world_crises[0].origin_node_id, world.world_crises[0].origin_node_id)
        self.assertEqual(legacy.to_public_dict()["world_crises"][0]["stage"], "dormant")

        month_four_payload = advance_month(advance_month(world)).to_dict()
        month_four_payload.pop("world_crises")
        month_four_legacy = WorldState.from_dict(month_four_payload)
        self.assertEqual(month_four_legacy.to_public_dict()["world_crises"][0]["stage"], "omen")

        sandbox = generate_random_world(seed=260, city_count=8, faction_count=2)
        self.assertFalse(sandbox.world_crises)
        self.assertFalse(ensure_world_crises(sandbox).world_crises)

    def _formed_army_at(
        self,
        world: WorldState,
        *,
        node_id: str,
        supply: int,
    ) -> tuple[WorldState, str]:
        general = next(
            item for item in world.offices
            if item.faction_id == "faction_1" and item.office_type == "general"
        )
        hero = next(item for item in world.strategic_heroes if item.office_id == general.office_id)
        city = next(item for item in world.cities if item.city_id == hero.city_id)
        general.unit_inventory = {"infantry": 1}
        city.resources.food = max(city.resources.food, supply + 100)
        formed = form_or_reinforce_army(
            world,
            faction_id="faction_1",
            city_id=city.city_id,
            unit_inventory={"infantry": 1},
            supply=supply,
            issuer_office_id=general.office_id,
        )
        army = formed.armies[0]
        army.location_node_id = node_id
        army.status = "deployed"
        army.current_order = "hold"
        army.march_origin_node_id = node_id
        army.destination_node_id = node_id
        army.route_node_ids = [node_id]
        army.route_progress_index = 0
        army.departure_month = formed.current_month
        army.estimated_arrival_month = formed.current_month
        army.supply_source_city_id = None
        army.supply_line_node_ids = []
        army.supply_line_status = "unassessed"
        army.supply_distance = None
        formed.validate()
        return formed, general.office_id

    def test_month_five_creates_stable_cold_routes_once_without_spawning_enemies(self) -> None:
        world = self._campaign_world(seed=1)
        original_connections = [list(node.connected_node_ids) for node in world.nodes]
        original_owners = [city.owner_faction_id for city in world.cities]
        for _ in range(4):
            world = advance_month(world)

        crisis = world.world_crises[0]
        expected_routes = {
            strategic_route_key(crisis.origin_node_id, node_id)
            for node_id in crisis.frontier_node_ids
            if node_id != crisis.origin_node_id
        }
        self.assertEqual((crisis.stage, crisis.pressure, crisis.next_stage_month), ("border_pressure", 30, 7))
        self.assertEqual(set(crisis.affected_route_keys), expected_routes)
        self.assertEqual(snow_ghost_cold_route_keys(world), expected_routes)
        self.assertEqual(sum(event.category == "world_crisis_border_pressure" for event in world.event_log), 1)
        self.assertEqual([list(node.connected_node_ids) for node in world.nodes], original_connections)
        self.assertEqual([city.owner_faction_id for city in world.cities], original_owners)
        self.assertFalse(world.armies)

        month_six = advance_month(world)
        self.assertEqual(month_six.world_crises[0].stage, "border_pressure")
        self.assertEqual(sum(event.category == "world_crisis_border_pressure" for event in month_six.event_log), 1)
        public = world_crises_public(month_six)[0]
        self.assertEqual(len(public["route_effects"]), len(expected_routes))
        self.assertTrue(all(item["minimum_supply"] == SNOW_GHOST_COLD_ROUTE_MIN_SUPPLY for item in public["route_effects"]))
        self.assertIn("低补给军队必须安全改道", public["effect_summary"])

    def test_low_supply_detours_or_rejects_and_supplied_army_pays_cold_cost(self) -> None:
        world = self._campaign_world(seed=1)
        world.current_month = 5
        world = ensure_world_crises(world)
        origin = world.world_crises[0].origin_node_id
        neighbors = sorted(node_id for node_id in world.world_crises[0].frontier_node_ids if node_id != origin)
        self.assertEqual((origin, neighbors), ("node_2", ["node_1", "node_3"]))

        low_supply, general_id = self._formed_army_at(world, node_id="node_1", supply=50)
        army = low_supply.armies[0]
        detoured = order_army_march(
            low_supply,
            faction_id="faction_1",
            army_id=army.army_id,
            destination_node_id="node_3",
            issuer_office_id=general_id,
        )
        self.assertEqual(detoured.armies[0].route_node_ids, ["node_1", "node_7", "node_6", "node_5", "node_4", "node_3"])
        self.assertIn("自动避开严寒路线", detoured.event_log[-1].message)

        with self.assertRaisesRegex(StrategyError, "至少需要 80 粮草"):
            order_army_march(
                low_supply,
                faction_id="faction_1",
                army_id=army.army_id,
                destination_node_id=origin,
                issuer_office_id=general_id,
            )

        supplied, general_id = self._formed_army_at(world, node_id="node_1", supply=100)
        supplied_army = supplied.armies[0]
        ordered = order_army_march(
            supplied,
            faction_id="faction_1",
            army_id=supplied_army.army_id,
            destination_node_id=origin,
            issuer_office_id=general_id,
        )
        crossed = advance_army_movements(ordered)
        crossed_army = crossed.armies[0]
        self.assertEqual((crossed_army.location_node_id, crossed_army.supply, crossed_army.morale), (origin, 80, 65))
        self.assertEqual(
            (crossed_army.last_cold_exposure_month, crossed_army.last_cold_supply_loss, crossed_army.last_cold_morale_loss),
            (5, 20, 5),
        )
        self.assertEqual(crossed_army.last_cold_route_key, strategic_route_key("node_1", origin))
        self.assertTrue(any(event.category == "strategy_army_cold_route" for event in crossed.event_log))

    def test_existing_march_crosses_new_cold_route_with_shortage_penalty(self) -> None:
        world = self._campaign_world(seed=1)
        world.current_month = 4
        world = ensure_world_crises(world)
        origin = world.world_crises[0].origin_node_id
        formed, general_id = self._formed_army_at(world, node_id="node_1", supply=50)
        army = formed.armies[0]
        ordered = order_army_march(
            formed,
            faction_id="faction_1",
            army_id=army.army_id,
            destination_node_id=origin,
            issuer_office_id=general_id,
        )
        ordered.armies[0].supply = 10
        ordered.current_month = 5
        pressured = ensure_world_crises(ordered)

        crossed = advance_army_movements(pressured)
        crossed_army = crossed.armies[0]
        self.assertEqual(
            (crossed_army.location_node_id, crossed_army.supply, crossed_army.morale),
            (origin, 0, 60),
        )
        self.assertEqual(
            (crossed_army.last_cold_supply_loss, crossed_army.last_cold_morale_loss),
            (10, 10),
        )

    def test_month_seven_spreads_routes_and_spawns_one_persistent_vanguard(self) -> None:
        world = self._campaign_world(seed=1)
        original_owners = [city.owner_faction_id for city in world.cities]
        world.current_month = 7
        spread = ensure_world_crises(world)
        crisis = spread.world_crises[0]
        frontier_ids = set(crisis.frontier_node_ids)
        expected_routes = {
            strategic_route_key(node.node_id, target_id)
            for node in spread.nodes
            for target_id in node.connected_node_ids
            if node.node_id in frontier_ids or target_id in frontier_ids
        }

        self.assertEqual((crisis.stage, crisis.pressure, crisis.next_stage_month), ("spread", 60, 9))
        self.assertEqual(set(crisis.affected_route_keys), expected_routes)
        self.assertEqual(
            set(crisis.threatened_city_ids),
            {city.city_id for city in spread.cities if city.node_id in frontier_ids},
        )
        self.assertEqual(crisis.spawned_army_ids, [SNOW_GHOST_VANGUARD_ARMY_ID])
        snow_faction = next(item for item in spread.factions if item.faction_id == SNOW_GHOST_FACTION_ID)
        self.assertTrue(snow_faction.is_world_crisis)
        army = next(item for item in spread.armies if item.army_id == SNOW_GHOST_VANGUARD_ARMY_ID)
        self.assertEqual(
            (
                army.name,
                army.army_kind,
                army.location_node_id,
                army.unit_inventory,
                army.manpower,
                army.supply,
                army.morale,
            ),
            ("北境雪鬼先锋", "snow_ghost", crisis.origin_node_id, {"snow_ghost": 6}, 600, 600, 90),
        )
        self.assertEqual([city.owner_faction_id for city in spread.cities], original_owners)
        self.assertEqual(roster_for_registered_units({"snow_ghost": 6}).roster, ["strategy_snow_ghost"] * 6)

        repeated = ensure_world_crises(WorldState.from_dict(spread.to_dict()))
        self.assertEqual(sum(item.faction_id == SNOW_GHOST_FACTION_ID for item in repeated.factions), 1)
        self.assertEqual(sum(item.army_id == SNOW_GHOST_VANGUARD_ARMY_ID for item in repeated.armies), 1)
        self.assertEqual(
            sum(event.category == "world_crisis_snow_ghost_spread" for event in repeated.event_log),
            1,
        )
        public = world_crises_public(repeated)[0]
        self.assertEqual(len(public["threatened_cities"]), len(crisis.threatened_city_ids))
        self.assertEqual(public["crisis_armies"][0]["name"], "北境雪鬼先锋")

    def test_month_seven_vanguard_uses_existing_siege_or_encounter_chain(self) -> None:
        undefended = self._campaign_world(seed=1)
        undefended.current_month = 6
        undefended = ensure_world_crises(undefended)
        original_owners = [city.owner_faction_id for city in undefended.cities]
        besieged = advance_month(undefended)
        crisis = besieged.world_crises[0]
        origin_city = next(city for city in besieged.cities if city.node_id == crisis.origin_node_id)
        siege = next(item for item in besieged.sieges if item.city_id == origin_city.city_id)
        self.assertEqual(siege.attacker_faction_id, SNOW_GHOST_FACTION_ID)
        self.assertEqual(siege.attacker_army_ids, [SNOW_GHOST_VANGUARD_ARMY_ID])
        self.assertEqual(siege.status, "active")
        self.assertEqual([city.owner_faction_id for city in besieged.cities], original_owners)

        defended = self._campaign_world(seed=1)
        defended.current_month = 6
        defended = ensure_world_crises(defended)
        defended, _ = self._formed_army_at(
            defended,
            node_id=defended.world_crises[0].origin_node_id,
            supply=100,
        )
        encountered = advance_month(defended)
        active = next(item for item in encountered.encounters if item.status == "active")
        self.assertEqual(active.node_id, encountered.world_crises[0].origin_node_id)
        self.assertIn(SNOW_GHOST_FACTION_ID, active.faction_army_ids)
        self.assertFalse(any(item.status == "active" for item in encountered.sieges))

        pending = declare_strategic_battle(
            encountered,
            faction_id="faction_1",
            source_kind="encounter",
            source_entity_id=active.encounter_id,
            resolution_mode="manual",
            auto_resolve=False,
        )
        battle = pending.pending_battles[-1]
        rosters = strategy_battle_rosters(pending, battle)
        snow_ghost_side = (
            rosters.attacker
            if battle.attacker_faction_id == SNOW_GHOST_FACTION_ID
            else rosters.defender
        )
        self.assertEqual(snow_ghost_side.roster, ["strategy_snow_ghost"] * 6)
        self.assertTrue(
            any(
                item["source"] == "registered_unit"
                and item["unit_id"] == "snow_ghost"
                and item["grid_units"] == 6
                for item in snow_ghost_side.manifest
            )
        )

    def test_month_nine_opens_persistent_mobilization_and_public_choices(self) -> None:
        world = self._campaign_world(seed=1)
        world.current_month = SNOW_GHOST_MOBILIZATION_MONTH
        mobilized = ensure_world_crises(world)
        crisis = mobilized.world_crises[0]

        self.assertEqual((crisis.stage, crisis.pressure, crisis.next_stage_month), ("mobilization", 80, 11))
        self.assertEqual(crisis.contributions_by_faction, {"faction_1": 0, "faction_2": 0})
        self.assertEqual(
            sum(event.category == "world_crisis_mobilization" for event in mobilized.event_log),
            1,
        )
        public = world_crises_public(mobilized)[0]
        self.assertEqual(public["stage_label"], "联军动员")
        self.assertEqual(len(public["contribution_rows"]), 2)
        self.assertEqual(
            {item["id"] for item in public["choice_options_by_faction"]["faction_1"]},
            {"contribute", "cooperate", "betray"},
        )

        repeated = ensure_world_crises(WorldState.from_dict(mobilized.to_dict()))
        self.assertEqual(
            sum(event.category == "world_crisis_mobilization" for event in repeated.event_log),
            1,
        )
        self.assertEqual(repeated.world_crises[0].contributions_by_faction, crisis.contributions_by_faction)

    def test_mobilization_contribution_cooperation_and_betrayal_use_real_values(self) -> None:
        world = self._campaign_world(seed=1)
        world.current_month = 9
        world = ensure_world_crises(world)
        lords = {
            office.faction_id: office.office_id
            for office in world.offices
            if office.office_type == "lord" and office.faction_id in {"faction_1", "faction_2"}
        }
        lord_heroes = {
            office.faction_id: office.holder_id
            for office in world.offices
            if office.office_type == "lord" and office.faction_id in {"faction_1", "faction_2"}
        }
        first_subordinate = next(
            hero
            for hero in world.strategic_heroes
            if hero.faction_id == "faction_1"
            and hero.status == "serving"
            and hero.hero_code != lord_heroes["faction_1"]
        )
        subordinate_before = (
            first_subordinate.loyalty,
            first_subordinate.relationships.get(lord_heroes["faction_1"], 0),
        )
        first_before = next(item for item in world.factions if item.faction_id == "faction_1")
        initial_resources = (first_before.resources.food, first_before.resources.money)

        contributed = resolve_world_crisis_choice(
            WorldState.from_dict(world.to_dict()),
            faction_id="faction_1",
            choice_id="contribute",
            issuer_office_id=lords["faction_1"],
        )
        contributor = next(item for item in contributed.factions if item.faction_id == "faction_1")
        self.assertEqual(
            (contributor.resources.food, contributor.resources.money),
            (initial_resources[0] - 100, initial_resources[1] - 50),
        )
        self.assertEqual(contributed.world_crises[0].contributions_by_faction["faction_1"], 35)
        contributed_subordinate = next(
            hero for hero in contributed.strategic_heroes if hero.hero_code == first_subordinate.hero_code
        )
        self.assertEqual(
            (
                contributed_subordinate.loyalty,
                contributed_subordinate.relationships[lord_heroes["faction_1"]],
            ),
            (min(100, subordinate_before[0] + 3), subordinate_before[1] + 2),
        )

        pledged = resolve_world_crisis_choice(
            world,
            faction_id="faction_1",
            choice_id="cooperate",
            target_faction_id="faction_2",
            issuer_office_id=lords["faction_1"],
        )
        first = next(item for item in pledged.factions if item.faction_id == "faction_1")
        self.assertEqual(
            (first.resources.food, first.resources.money),
            (initial_resources[0] - 80, initial_resources[1] - 40),
        )
        self.assertEqual(pledged.world_crises[0].contributions_by_faction["faction_1"], 25)
        with self.assertRaisesRegex(StrategyError, "本月已经"):
            validate_world_crisis_choice(
                pledged,
                faction_id="faction_1",
                choice_id="contribute",
            )

        allied = resolve_world_crisis_choice(
            pledged,
            faction_id="faction_2",
            choice_id="cooperate",
            target_faction_id="faction_1",
            issuer_office_id=lords["faction_2"],
        )
        crisis = allied.world_crises[0]
        self.assertEqual(crisis.contributions_by_faction, {"faction_1": 40, "faction_2": 40})
        self.assertEqual(crisis.cooperation_pairs, ["faction_1::faction_2"])
        first = next(item for item in allied.factions if item.faction_id == "faction_1")
        second = next(item for item in allied.factions if item.faction_id == "faction_2")
        self.assertEqual((first.diplomatic_reputation, second.diplomatic_reputation), (55, 55))
        self.assertEqual((first.relations["faction_2"], second.relations["faction_1"]), (10, 10))

        allied_subordinate = next(
            hero for hero in allied.strategic_heroes if hero.hero_code == first_subordinate.hero_code
        )
        allied_personal = (
            allied_subordinate.loyalty,
            allied_subordinate.relationships[lord_heroes["faction_1"]],
        )
        allied.current_month = 10
        betrayed = resolve_world_crisis_choice(
            allied,
            faction_id="faction_1",
            choice_id="betray",
            target_faction_id="faction_2",
            issuer_office_id=lords["faction_1"],
        )
        crisis = betrayed.world_crises[0]
        first = next(item for item in betrayed.factions if item.faction_id == "faction_1")
        second = next(item for item in betrayed.factions if item.faction_id == "faction_2")
        self.assertEqual(crisis.contributions_by_faction, {"faction_1": 60, "faction_2": 20})
        self.assertEqual(crisis.broken_cooperation_pairs, ["faction_1::faction_2"])
        self.assertEqual(first.diplomatic_reputation, 35)
        self.assertEqual((first.relations["faction_2"], second.relations["faction_1"]), (-20, -20))
        betrayed_subordinate = next(
            hero for hero in betrayed.strategic_heroes if hero.hero_code == first_subordinate.hero_code
        )
        self.assertEqual(
            (
                betrayed_subordinate.loyalty,
                betrayed_subordinate.relationships[lord_heroes["faction_1"]],
            ),
            (max(0, allied_personal[0] - 6), allied_personal[1] - 5),
        )
        self.assertTrue(any(event.category == "world_crisis_cooperation_betrayed" for event in betrayed.event_log))
        self.assertEqual(
            WorldState.from_dict(betrayed.to_dict()).world_crises[0].decisions,
            crisis.decisions,
        )

    def test_month_eleven_freezes_all_three_showdown_branches_into_one_battle(self) -> None:
        cases = (
            ("united_counteroffensive", {"faction_1": 40, "faction_2": 40}, ["faction_1::faction_2"], []),
            ("rival_vanguards", {"faction_1": 35, "faction_2": 35}, [], []),
            ("shattered_line", {"faction_1": 60, "faction_2": 20}, ["faction_1::faction_2"], ["faction_1::faction_2"]),
        )
        for expected_branch, contributions, pairs, broken_pairs in cases:
            with self.subTest(branch=expected_branch):
                world = self._campaign_world(seed=261)
                world.current_month = 9
                world = ensure_world_crises(world)
                crisis = world.world_crises[0]
                crisis.contributions_by_faction = dict(contributions)
                crisis.cooperation_pairs = list(pairs)
                crisis.broken_cooperation_pairs = list(broken_pairs)
                world.current_month = SNOW_GHOST_SHOWDOWN_MONTH

                showdown = ensure_world_crises(world)
                crisis = showdown.world_crises[0]
                battles = [
                    battle
                    for battle in showdown.pending_battles
                    if battle.source_kind == "world_crisis"
                ]

                self.assertEqual(crisis.stage, "showdown")
                self.assertEqual(crisis.showdown_branch, expected_branch)
                self.assertEqual(len(battles), 1)
                self.assertEqual(battles[0].battle_id, crisis.showdown_battle_id)
                self.assertEqual(battles[0].source_city_id, battles[0].target_city_id)
                self.assertEqual(battles[0].resolution_mode, "unselected")
                self.assertEqual(len([
                    item for item in ensure_world_crises(showdown).pending_battles
                    if item.source_kind == "world_crisis"
                ]), 1)

    def test_united_showdown_victory_shares_mainline_without_capturing_city(self) -> None:
        world = self._campaign_world(seed=262)
        world.current_month = 9
        world = ensure_world_crises(world)
        crisis = world.world_crises[0]
        crisis.contributions_by_faction = {"faction_1": 40, "faction_2": 40}
        crisis.cooperation_pairs = ["faction_1::faction_2"]
        world.current_month = 11
        world = ensure_world_crises(world)
        owner_before = {city.city_id: city.owner_faction_id for city in world.cities}
        battle = next(
            item for item in world.pending_battles
            if item.battle_id == world.world_crises[0].showdown_battle_id
        )
        battle.resolution_mode = "quick"

        resolved = resolve_pending_battle(world, battle_id=battle.battle_id)
        crisis = resolved.world_crises[0]
        mainline = next(
            item for item in evaluate_strategic_status(resolved)["victory_conditions"]
            if item["id"] == "world_mainline"
        )

        self.assertEqual((crisis.stage, crisis.showdown_outcome, crisis.pressure), ("resolved", "victory", 0))
        self.assertEqual(crisis.mainline_winner_faction_ids, ["faction_1", "faction_2"])
        self.assertEqual({city.city_id: city.owner_faction_id for city in resolved.cities}, owner_before)
        self.assertFalse(crisis.affected_route_keys)
        self.assertTrue(mainline["achieved"])
        self.assertEqual(mainline["winner_faction_ids"], ["faction_1", "faction_2"])
        self.assertTrue(all(
            army.status == "destroyed"
            for army in resolved.armies
            if army.faction_id == SNOW_GHOST_FACTION_ID
        ))
        scores = {
            row["faction_id"]: row["mainline_score"]
            for row in campaign_assessment_rankings(resolved)
        }
        self.assertEqual(scores, {"faction_1": 40, "faction_2": 40})

    def test_showdown_defeat_applies_aftermath_and_blocks_month_twelve_until_resolved(self) -> None:
        world = self._campaign_world(seed=263)
        world.current_month = 11
        world = ensure_world_crises(world)
        crisis = world.world_crises[0]
        threatened_before = {
            city.city_id: (
                city.resources.food,
                city.support_by_faction.get(city.owner_faction_id, 50),
            )
            for city in world.cities
            if city.city_id in crisis.threatened_city_ids
        }
        with self.assertRaisesRegex(StrategyError, "北境决战尚未完成"):
            advance_month(world)
        lord = next(
            office for office in world.offices
            if office.faction_id == "faction_1" and office.office_type == "lord"
        )

        resolved = set_world_crisis_showdown_resolution(
            world,
            faction_id="faction_1",
            issuer_office_id=lord.office_id,
            resolution_mode="quick",
        )
        crisis = resolved.world_crises[0]

        self.assertEqual((crisis.stage, crisis.showdown_outcome), ("aftermath", "defeat"))
        self.assertFalse(crisis.mainline_winner_faction_ids)
        for city_id, (food, support) in threatened_before.items():
            city = next(item for item in resolved.cities if item.city_id == city_id)
            self.assertEqual(city.resources.food, max(0, food - 80))
            self.assertEqual(
                city.support_by_faction.get(city.owner_faction_id, 50),
                max(0, support - 10),
            )
        month_twelve = advance_month(resolved)
        self.assertEqual(month_twelve.current_month, 12)

    def test_crisis_ai_publicly_balances_survival_mainline_and_expansion(self) -> None:
        survival = self._campaign_world(seed=264)
        survival.current_month = 9
        survival = ensure_world_crises(survival)
        ai_city = next(city for city in survival.cities if city.owner_faction_id == "faction_2")
        survival.world_crises[0].threatened_city_ids = [ai_city.city_id]
        ai_faction = next(item for item in survival.factions if item.faction_id == "faction_2")
        ai_faction.resources.food = 1000
        ai_faction.resources.money = 1000

        survival = apply_strategy_ai_monthly_actions(
            survival,
            controlled_faction_ids={"faction_1"},
            enable_attacks=False,
        )
        survival_decision = next(
            item for item in survival.world_crises[0].decisions
            if item["faction_id"] == "faction_2"
        )
        self.assertEqual(
            (
                survival_decision["decision_origin"],
                survival_decision["ai_priority"],
                survival_decision["choice_id"],
            ),
            ("ai", "survival", "cooperate"),
        )
        self.assertIn(ai_city.name, survival_decision["ai_rationale"])
        public_intent = world_crises_public(survival)[0]["ai_intent_rows"][0]
        self.assertEqual(public_intent["faction_id"], "faction_2")
        self.assertEqual(public_intent["ai_priority"], "survival")

        mainline = self._campaign_world(seed=265)
        mainline.current_month = 9
        mainline = ensure_world_crises(mainline)
        mainline.world_crises[0].threatened_city_ids = [
            city.city_id for city in mainline.cities if city.owner_faction_id == "faction_1"
        ]
        ai_faction = next(item for item in mainline.factions if item.faction_id == "faction_2")
        ai_faction.resources.food = 1000
        ai_faction.resources.money = 1000
        mainline.ai_strategic_goals["faction_2"] = {
            "current": {
                "id": "ai_goal:faction_2:raise_army:9",
                "faction_id": "faction_2",
                "goal_type": "raise_army",
                "title": "积蓄兵力",
                "status": "active",
                "start_month": 9,
                "end_month": 10,
                "target_city_id": next(
                    city.city_id for city in mainline.cities if city.owner_faction_id == "faction_2"
                ),
            },
            "history": [],
        }
        mainline = apply_strategy_ai_monthly_actions(
            mainline,
            controlled_faction_ids={"faction_1"},
            enable_attacks=False,
        )
        mainline_decision = next(
            item for item in mainline.world_crises[0].decisions
            if item["faction_id"] == "faction_2"
        )
        self.assertEqual((mainline_decision["ai_priority"], mainline_decision["choice_id"]), ("mainline", "contribute"))

        expansion = self._campaign_world(seed=266)
        expansion.current_month = 9
        expansion = ensure_world_crises(expansion)
        expansion.world_crises[0].threatened_city_ids = [
            city.city_id for city in expansion.cities if city.owner_faction_id == "faction_1"
        ]
        target_city = next(city for city in expansion.cities if city.owner_faction_id != "faction_2")
        source_city = next(city for city in expansion.cities if city.owner_faction_id == "faction_2")
        expansion.ai_strategic_goals["faction_2"] = {
            "current": {
                "id": "ai_goal:faction_2:capture_city:9",
                "faction_id": "faction_2",
                "goal_type": "capture_city",
                "title": f"夺取{target_city.name}",
                "status": "active",
                "start_month": 9,
                "end_month": 11,
                "target_city_id": target_city.city_id,
                "source_city_id": source_city.city_id,
            },
            "history": [],
        }
        expansion = apply_strategy_ai_monthly_actions(
            expansion,
            controlled_faction_ids={"faction_1"},
            enable_attacks=False,
        )
        expansion_decision = next(
            item for item in expansion.world_crises[0].decisions
            if item["faction_id"] == "faction_2"
        )
        self.assertEqual((expansion_decision["ai_priority"], expansion_decision["choice_id"]), ("expansion", "avoid"))
        self.assertTrue(any(
            event.category == "world_crisis_ai_avoided"
            for event in expansion.event_log
        ))

    def test_expansion_ai_only_exploits_an_existing_crisis_cooperation(self) -> None:
        world = self._campaign_world(seed=267)
        world.current_month = 9
        world = ensure_world_crises(world)
        crisis = world.world_crises[0]
        crisis.threatened_city_ids = [
            city.city_id for city in world.cities if city.owner_faction_id == "faction_1"
        ]
        crisis.cooperation_pairs = ["faction_1::faction_2"]
        crisis.contributions_by_faction = {"faction_1": 40, "faction_2": 40}
        world.current_month = 10
        target_city = next(city for city in world.cities if city.owner_faction_id != "faction_2")
        source_city = next(city for city in world.cities if city.owner_faction_id == "faction_2")
        world.ai_strategic_goals["faction_2"] = {
            "current": {
                "id": "ai_goal:faction_2:capture_city:9",
                "faction_id": "faction_2",
                "goal_type": "capture_city",
                "title": f"夺取{target_city.name}",
                "status": "active",
                "start_month": 9,
                "end_month": 11,
                "target_city_id": target_city.city_id,
                "source_city_id": source_city.city_id,
            },
            "history": [],
        }

        updated = apply_strategy_ai_monthly_actions(
            world,
            controlled_faction_ids={"faction_1"},
            enable_attacks=False,
        )
        crisis = updated.world_crises[0]
        decision = next(item for item in crisis.decisions if item["faction_id"] == "faction_2")
        self.assertEqual((decision["ai_priority"], decision["choice_id"]), ("expansion", "betray"))
        self.assertEqual(crisis.broken_cooperation_pairs, ["faction_1::faction_2"])
        self.assertEqual(crisis.contributions_by_faction, {"faction_1": 20, "faction_2": 60})

    def test_ai_led_showdown_quick_resolves_and_full_campaign_survives_reload_to_month_twelve(self) -> None:
        world = self._campaign_world(seed=268)
        controlled = {"faction_1"}
        while world.current_month < 12:
            world = apply_strategy_ai_monthly_actions(
                world,
                controlled_faction_ids=controlled,
                enable_attacks=False,
            )
            world = advance_month(world)
            world = apply_strategy_ai_showdown_action(
                world,
                controlled_faction_ids=controlled,
            )
            world = WorldState.from_dict(world.to_dict())

        crisis = world.world_crises[0]
        battle = next(
            item for item in world.pending_battles
            if item.battle_id == crisis.showdown_battle_id
        )
        self.assertEqual(world.current_month, 12)
        self.assertEqual(battle.status, "resolved")
        self.assertIn(crisis.stage, {"resolved", "aftermath"})
        self.assertTrue(any(
            event.category == "world_crisis_ai_showdown_selected"
            for event in world.event_log
        ))
        self.assertTrue(any(
            item.get("decision_origin") == "ai"
            for item in crisis.decisions
        ))
        self.assertTrue(any(
            item.get("decision_origin") == "ai"
            and item.get("choice_id") in {"contribute", "cooperate"}
            for item in crisis.decisions
        ))
        self.assertTrue(any(
            item.get("decision_origin") == "ai"
            and item.get("ai_priority") in {"survival", "mainline", "expansion"}
            for item in WorldState.from_dict(world.to_dict()).world_crises[0].decisions
        ))

    def test_calendar_and_probabilistic_crisis_wait_until_configured_year(self) -> None:
        from wujiang.strategic.world_crisis import (
            apply_campaign_play_settings,
            campaign_calendar_label,
            campaign_year,
            monthly_crisis_chance,
            relative_crisis_clock,
        )

        self.assertEqual(campaign_year(1), 1)
        self.assertEqual(campaign_year(12), 1)
        self.assertEqual(campaign_year(13), 2)
        self.assertEqual(campaign_calendar_label(1), "第1年1月")
        self.assertEqual(campaign_calendar_label(13), "第2年1月")
        self.assertEqual(relative_crisis_clock(109, 2)["showdown"], 117)

        config = {
            "earliest_year": 10,
            "base_monthly_chance": 0.08,
            "chance_increase_per_year": 0.06,
            "max_monthly_chance": 0.90,
            "guarantee_after_years": 20,
            "stage_gap_months": 2,
        }
        self.assertEqual(monthly_crisis_chance(9, config), 0.0)
        self.assertAlmostEqual(monthly_crisis_chance(10, config), 0.08)
        self.assertAlmostEqual(monthly_crisis_chance(11, config), 0.14)
        self.assertEqual(monthly_crisis_chance(30, config), 1.0)

        contract = apply_campaign_play_settings(
            first_campaign_contract(),
            crisis_earliest_year=10,
            year_limit=0,
        )
        world = generate_random_world(
            seed=11,
            city_count=8,
            faction_count=2,
            campaign_contract=contract,
        )
        self.assertEqual(world.world_crises[0].stage, "dormant")
        self.assertEqual(world_crises_public(world), [])

        world.current_month = 12
        world = ensure_world_crises(world)
        self.assertEqual(world.world_crises[0].stage, "dormant")
        self.assertEqual(world_crises_public(world), [])

        world.current_month = 109
        guaranteed = apply_campaign_play_settings(
            first_campaign_contract(),
            crisis_earliest_year=10,
            year_limit=0,
        )
        guaranteed["crisis_config"]["base_monthly_chance"] = 1.0
        world.campaign_contract = guaranteed
        world = ensure_world_crises(world)
        crisis = world.world_crises[0]
        self.assertEqual(crisis.stage, "omen")
        self.assertEqual(crisis.started_month, 109)
        self.assertEqual(crisis.next_stage_month, 111)
        self.assertEqual(world_crises_public(world)[0]["stage"], "omen")

        world.current_month = 111
        world = ensure_world_crises(world)
        self.assertEqual(world.world_crises[0].stage, "border_pressure")


class StrategyObjectiveTests(unittest.TestCase):
    def test_first_campaign_tutorial_tracks_acknowledgement_actions_and_skip_without_rewards(self) -> None:
        world = generate_random_world(
            seed=146,
            city_count=8,
            faction_count=2,
            neutral_city_states=True,
            campaign_contract=first_campaign_contract(),
        )
        faction_id = "faction_1"
        initial_resources = next(item for item in world.factions if item.faction_id == faction_id).resources.to_dict()
        initial = campaign_tutorial_public(world, [])
        self.assertEqual(initial[faction_id]["completed_count"], 0)
        self.assertEqual([step["month"] for step in initial[faction_id]["steps"]], [1, 1, 1, 2, 3])

        surveyed = update_campaign_tutorial(world, faction_id=faction_id, action="survey_border")
        queued_actions = [
            {"faction_id": faction_id, "action_type": "issue_office_order", "payload": {"objective": "[引导:set_policy] 委托城主设置方针"}},
            {"faction_id": faction_id, "action_type": "resolve_story_event", "payload": {}},
            {"faction_id": faction_id, "action_type": "send_office_request", "payload": {"objective": "[引导:ritual_or_appoint] 请求祭祀"}},
            {"faction_id": faction_id, "action_type": "request_registered_units", "payload": {}},
        ]
        completed = campaign_tutorial_public(surveyed, queued_actions)[faction_id]
        self.assertTrue(completed["completed"])
        self.assertEqual(completed["completed_count"], 5)

        skipped = update_campaign_tutorial(surveyed, faction_id=faction_id, action="skip")
        restored = WorldState.from_dict(skipped.to_dict())
        public = campaign_tutorial_public(restored, [])[faction_id]
        self.assertTrue(public["skipped"])
        self.assertIn("不会获得或失去资源", public["skip_explanation"])
        self.assertEqual(next(item for item in restored.factions if item.faction_id == faction_id).resources.to_dict(), initial_resources)
        self.assertTrue(any(event.category == "campaign_tutorial_skipped" for event in restored.event_log))

    def test_legacy_sandbox_has_no_first_campaign_tutorial(self) -> None:
        world = generate_random_world(seed=147, city_count=4, faction_count=2)
        self.assertEqual(campaign_tutorial_public(world, []), {})
        with self.assertRaisesRegex(StrategyError, "没有前三个月引导"):
            update_campaign_tutorial(world, faction_id="faction_1", action="skip")

    def test_evaluate_strategic_status_marks_unification_and_exile(self) -> None:
        world = generate_random_world(seed=61, city_count=4, faction_count=2)
        for city in world.cities:
            city.owner_faction_id = "faction_1"

        status = evaluate_strategic_status(world)
        conditions = {condition["id"]: condition for condition in status["victory_conditions"]}

        self.assertEqual(status["city_counts_by_faction"], {"faction_1": 4, "faction_2": 0})
        self.assertEqual(status["active_faction_ids"], ["faction_1"])
        self.assertEqual(status["exiled_faction_ids"], ["faction_2"])
        self.assertTrue(conditions["unify_cities"]["achieved"])
        self.assertEqual(conditions["unify_cities"]["winner_faction_id"], "faction_1")
        self.assertTrue(conditions["eliminate_enemy_factions"]["achieved"])
        self.assertEqual(conditions["eliminate_enemy_factions"]["winner_faction_id"], "faction_1")
        self.assertTrue(conditions["world_mainline"]["implemented"])
        self.assertNotIn("relic_altar", conditions)
        self.assertTrue(status["campaign_complete"])
        self.assertEqual(status["winner_faction_ids"], ["faction_1"])

    def test_early_victory_waits_until_occupation_policy_is_chosen(self) -> None:
        world = generate_random_world(
            seed=91,
            city_count=4,
            faction_count=2,
            campaign_contract=first_campaign_contract(),
        )
        last_city = next(city for city in world.cities if city.owner_faction_id != "faction_1")
        previous_owner = last_city.owner_faction_id
        for city in world.cities:
            city.owner_faction_id = "faction_1"
        mark_city_captured(
            world,
            city_id=last_city.city_id,
            previous_owner_faction_id=previous_owner,
            occupier_faction_id="faction_1",
        )

        pending_status = evaluate_strategic_status(world)
        self.assertTrue(pending_status["awaiting_occupation_policy"])
        self.assertFalse(pending_status["awaiting_conclusion_choice"])
        self.assertFalse(pending_status["campaign_complete"])
        self.assertFalse(pending_status["conclusion"])

        occupied = apply_occupation_policy(
            world, faction_id="faction_1", city_id=last_city.city_id, policy_id="autonomy"
        )
        settled = evaluate_strategic_status(occupied)
        self.assertFalse(settled["awaiting_occupation_policy"])
        self.assertTrue(settled["awaiting_conclusion_choice"])
        self.assertEqual(settled["conclusion"]["reason"], "early_victory")
        self.assertTrue(occupied.campaign_conclusion)

    def test_record_strategic_status_events_is_idempotent(self) -> None:
        world = generate_random_world(seed=62, city_count=4, faction_count=2)
        for city in world.cities:
            city.owner_faction_id = "faction_1"

        recorded = record_strategic_status_events(world)
        recorded_again = record_strategic_status_events(recorded)

        self.assertIn("exile:faction_2", recorded.memory_tags)
        self.assertIn("victory:unify_cities:faction_1", recorded.memory_tags)
        self.assertIn("victory:eliminate_enemy_factions:faction_1", recorded.memory_tags)
        self.assertEqual(
            sum(1 for event in recorded_again.event_log if event.category == "faction_exiled"),
            1,
        )
        self.assertEqual(
            sum(1 for event in recorded_again.event_log if event.category == "victory_achieved"),
            2,
        )
        self.assertEqual(recorded_again.memory_tags.count("exile:faction_2"), 1)

    def test_bounded_campaign_settles_at_month_twelve_and_can_continue_as_sandbox(self) -> None:
        world = generate_random_world(
            seed=44,
            city_count=8,
            faction_count=2,
            neutral_city_states=True,
            campaign_contract=first_campaign_contract(),
        )
        world.current_month = 12

        status = evaluate_strategic_status(world)

        self.assertEqual(status["campaign_state"], "settled")
        self.assertEqual(status["months_remaining"], 0)
        self.assertTrue(status["awaiting_conclusion_choice"])
        self.assertEqual(status["conclusion"]["reason"], "time_limit")
        self.assertEqual(len(status["conclusion"]["rankings"]), 2)
        self.assertEqual(status["conclusion"]["rankings"][0]["city_score"], 100)

        settled = record_strategic_status_events(world)
        settled_again = record_strategic_status_events(settled)
        self.assertEqual(settled.campaign_conclusion["state"], "settled")
        self.assertEqual(
            sum(1 for event in settled_again.event_log if event.category == "campaign_concluded"),
            1,
        )
        with self.assertRaises(StrategyError):
            require_campaign_orders_open(settled)

        continued = continue_campaign_as_sandbox(settled)
        self.assertEqual(continued.campaign_conclusion["state"], "sandbox")
        self.assertTrue(evaluate_strategic_status(continued)["can_advance_month"])
        require_campaign_orders_open(continued)
        restored = type(continued).from_dict(continued.to_dict())
        self.assertEqual(restored.campaign_contract["month_limit"], 12)
        self.assertEqual(restored.campaign_conclusion["state"], "sandbox")

    def test_campaign_conclusion_freezes_retrospective_and_archive_blocks_orders(self) -> None:
        world = generate_random_world(
            seed=244,
            city_count=8,
            faction_count=2,
            neutral_city_states=True,
            campaign_contract=first_campaign_contract(),
        )
        source, target = world.cities[:2]
        old_owner = target.owner_faction_id
        target.owner_faction_id = source.owner_faction_id
        world.current_month = 12
        world.monthly_reports.append(
            {
                "month": 6,
                "city_changes": [
                    {
                        "city_id": target.city_id,
                        "city_name": target.name,
                        "owner_before": old_owner,
                        "owner_after": source.owner_faction_id,
                        "owner_changed": True,
                    }
                ],
            }
        )
        world.pending_battles.append(
            PendingBattle(
                battle_id="battle_recap",
                month=6,
                attacker_faction_id=source.owner_faction_id,
                defender_faction_id=old_owner,
                source_city_id=source.city_id,
                target_city_id=target.city_id,
                resolution_mode="manual",
                attacker_troops=500,
                defender_troops=400,
                status="resolved",
                winner_faction_id=source.owner_faction_id,
                attacker_hero_codes=[world.strategic_heroes[0].hero_code],
                battle_result={"summary": "进攻方赢得格子战。"},
            )
        )

        settled = record_strategic_status_events(world)
        recap = settled.campaign_conclusion["retrospective"]

        self.assertEqual(recap["concluded_month"], 12)
        self.assertEqual(recap["city_changes"][0]["city_id"], target.city_id)
        self.assertTrue(recap["battles"][0]["grid_battle"])
        self.assertEqual(recap["summary"]["resolved_battles"], 1)
        self.assertIn(recap["faction_outcomes"][0]["outcome_label"], {"胜利", "存续"})

        archived = archive_campaign(settled)
        self.assertEqual(archived.campaign_conclusion["state"], "archived")
        self.assertFalse(evaluate_strategic_status(archived)["can_advance_month"])
        with self.assertRaisesRegex(StrategyError, "已经归档"):
            require_campaign_orders_open(archived)
        with self.assertRaisesRegex(StrategyError, "已经归档"):
            continue_campaign_as_sandbox(archived)

    def test_public_world_includes_strategic_status(self) -> None:
        world = generate_random_world(seed=63, city_count=4, faction_count=2)
        for city in world.cities:
            city.owner_faction_id = "faction_1"

        public = world.to_public_dict()
        status = public["strategic_status"]

        self.assertEqual(status["exiled_factions"][0]["id"], "faction_2")
        self.assertTrue(
            any(condition["id"] == "unify_cities" and condition["achieved"] for condition in status["victory_conditions"])
        )


class StrategyExileTests(unittest.TestCase):
    def _exiled_world(self) -> WorldState:
        world = generate_random_world(seed=64, city_count=4, faction_count=2)
        for city in world.cities:
            city.owner_faction_id = "faction_1"
        faction = next(item for item in world.factions if item.faction_id == "faction_2")
        faction.resources.food = 0
        faction.resources.money = 0
        faction.resources.ether = 0
        faction.resources.troops = 0
        return world

    def test_exile_actions_gain_resources_troops_and_city_support(self) -> None:
        world = self._exiled_world()

        aided = apply_exile_action(world, faction_id="faction_2", action_id="seek_aid")
        aided_faction = next(item for item in aided.factions if item.faction_id == "faction_2")
        self.assertEqual(aided_faction.resources.food, 140)
        self.assertEqual(aided_faction.resources.money, 100)
        self.assertEqual(aided_faction.resources.ether, 10)

        rallied = apply_exile_action(aided, faction_id="faction_2", action_id="rally_followers")
        rallied_faction = next(item for item in rallied.factions if item.faction_id == "faction_2")
        self.assertEqual(rallied_faction.resources.troops, 180)
        self.assertTrue(any(event.category == "exile_action" for event in rallied.event_log))

        target_city = rallied.cities[0]
        before_support = target_city.support_by_faction.get("faction_2", 0)
        networked = apply_exile_action(
            rallied,
            faction_id="faction_2",
            action_id="build_network",
            target_city_id=target_city.city_id,
        )
        networked_city = next(item for item in networked.cities if item.city_id == target_city.city_id)
        self.assertEqual(networked_city.support_by_faction["faction_2"], min(100, before_support + 12))

    def test_exile_rebuild_base_requires_resources_and_restores_city_control(self) -> None:
        world = self._exiled_world()
        faction = next(item for item in world.factions if item.faction_id == "faction_2")
        faction.resources.money = 120
        faction.resources.troops = 300
        target = world.cities[0]
        target.resources.troops = 260
        target.support_by_faction["faction_2"] = 20

        rebuilt = apply_exile_action(
            world,
            faction_id="faction_2",
            action_id="rebuild_base",
            target_city_id=target.city_id,
        )
        rebuilt_faction = next(item for item in rebuilt.factions if item.faction_id == "faction_2")
        rebuilt_city = next(item for item in rebuilt.cities if item.city_id == target.city_id)

        self.assertEqual(rebuilt_faction.resources.money, 0)
        self.assertEqual(rebuilt_faction.resources.troops, 0)
        self.assertEqual(rebuilt_city.owner_faction_id, "faction_2")
        self.assertEqual(rebuilt_city.resources.troops, 300)
        self.assertFalse(evaluate_strategic_status(rebuilt)["campaign_complete"])

    def test_exile_rebuild_base_recovers_local_relics(self) -> None:
        world = generate_random_world(
            seed=616,
            city_count=8,
            faction_count=2,
            neutral_city_states=True,
            campaign_contract=first_campaign_contract(),
        )
        faction = next(item for item in world.factions if item.faction_id == "faction_2")
        target = next(item for item in world.cities if item.city_id == faction.capital_city_id)
        for city in world.cities:
            if city.owner_faction_id == faction.faction_id:
                city.owner_faction_id = "faction_1"
        faction.resources.money = 120
        faction.resources.troops = 300
        target.resources.troops = 260
        target.support_by_faction[faction.faction_id] = 20
        relic = next(item for item in world.relics if item.state == "scattered")
        relic.state = "stored"
        relic.condition = "damaged"
        relic.owner_faction_id = "faction_1"
        relic.location_node_id = target.node_id
        relic.location_city_id = target.city_id
        relic.altar_id = None
        target.relics_stored.append(relic.relic_id)

        rebuilt = apply_exile_action(
            world,
            faction_id=faction.faction_id,
            action_id="rebuild_base",
            target_city_id=target.city_id,
        )
        recovered = next(item for item in rebuilt.relics if item.relic_id == relic.relic_id)
        self.assertEqual(recovered.owner_faction_id, faction.faction_id)
        self.assertEqual(recovered.condition, "damaged")
        self.assertTrue(
            any(event.category == "relics_captured_on_city_control_change" for event in rebuilt.event_log)
        )

    def test_exile_action_validation_rejects_non_exiled_faction_and_unready_rebuild(self) -> None:
        world = generate_random_world(seed=65, city_count=4, faction_count=2)
        with self.assertRaises(StrategyError):
            validate_exile_action(world, faction_id="faction_1", action_id="seek_aid")

        exiled = self._exiled_world()
        target = exiled.cities[0]
        target.resources.troops = 500
        target.support_by_faction["faction_2"] = 20
        with self.assertRaises(StrategyError):
            apply_exile_action(
                exiled,
                faction_id="faction_2",
                action_id="rebuild_base",
                target_city_id=target.city_id,
            )

    def test_public_world_includes_exile_action_choices(self) -> None:
        public = self._exiled_world().to_public_dict()
        choices = {choice["id"]: choice for choice in public["exile_action_choices"]}

        self.assertIn("seek_aid", choices)
        self.assertFalse(choices["seek_aid"]["requires_target_city"])
        self.assertTrue(choices["rebuild_base"]["requires_target_city"])


class StrategyHeroTests(unittest.TestCase):
    def test_hero_personal_state_is_deterministic_persistent_and_visible(self) -> None:
        first = generate_random_world(
            seed=611,
            city_count=8,
            faction_count=2,
            neutral_city_states=True,
            campaign_contract=first_campaign_contract(),
        )
        second = generate_random_world(
            seed=611,
            city_count=8,
            faction_count=2,
            neutral_city_states=True,
            campaign_contract=first_campaign_contract(),
        )
        faction_id = "faction_1"
        lord_code = next(
            item.holder_id
            for item in first.offices
            if item.faction_id == faction_id and item.office_type == "lord"
        )
        hero = next(
            item
            for item in first.strategic_heroes
            if item.faction_id == faction_id and item.status == "serving" and item.hero_code != lord_code
        )
        counterpart = next(item for item in second.strategic_heroes if item.hero_code == hero.hero_code)
        self.assertIn(hero.strategic_specialty, {"vanguard", "guardian", "trainer", "aether_scholar"})
        self.assertEqual(hero.strategic_specialty, counterpart.strategic_specialty)
        self.assertEqual(hero.personal_mission_status, "active")
        self.assertEqual(hero.personal_mission_due_month, first.current_month + 3)
        self.assertEqual(hero.relationships[lord_code], 0)
        restored = WorldState.from_dict(first.to_dict())
        self.assertEqual(
            next(item for item in restored.strategic_heroes if item.hero_code == hero.hero_code).to_dict(),
            hero.to_dict(),
        )
        public = next(item for item in strategic_hero_pool_public(first) if item["code"] == hero.hero_code)
        self.assertEqual(public["specialty"]["id"], hero.strategic_specialty)
        self.assertEqual(public["personal_mission"]["status"], "active")
        self.assertEqual(public["lord_relationship"], 0)
        self.assertEqual(len(hero.strategic_skills), 2)
        self.assertEqual(hero.strategic_skills, hero_skills_for_code(hero.hero_code))
        self.assertEqual([item["id"] for item in public["strategic_skills"]], hero.strategic_skills)

    def test_heroes_share_a_standard_strategic_skill_pool(self) -> None:
        world = generate_random_world(
            seed=611,
            city_count=8,
            faction_count=2,
            neutral_city_states=True,
            campaign_contract=first_campaign_contract(),
        )
        kits = {hero.hero_code: tuple(hero.strategic_skills) for hero in world.strategic_heroes}
        used_skills = [skill_id for kit in kits.values() for skill_id in kit]
        self.assertTrue(all(len(kit) == 2 for kit in kits.values()))
        self.assertTrue(all(skill_id in STRATEGIC_SKILLS for skill_id in used_skills))
        self.assertLess(len(set(used_skills)), len(used_skills))
        self.assertGreater(len(set(kits.values())), 1)
        self.assertEqual(hero_skills_for_code("ellie"), hero_skills_for_code("ellie"))

    def test_matching_duty_applies_specialty_and_completes_personal_mission_once(self) -> None:
        world = generate_random_world(
            seed=611,
            city_count=8,
            faction_count=2,
            neutral_city_states=True,
            campaign_contract=first_campaign_contract(),
        )
        faction_id = "faction_1"
        city = next(item for item in world.cities if item.owner_faction_id == faction_id)
        lord = next(item for item in world.offices if item.faction_id == faction_id and item.office_type == "lord")
        hero = next(
            item
            for item in world.strategic_heroes
            if item.faction_id == faction_id and item.status == "serving" and item.hero_code != lord.holder_id
        )
        hero.strategic_specialty = "trainer"
        hero.personal_mission_assignment_type = "training"
        hero.loyalty = 60
        assigned = assign_strategic_hero_duty(
            world,
            faction_id=faction_id,
            issuer_office_id=lord.office_id,
            hero_code=hero.hero_code,
            assignment_type="training",
            target_id=city.city_id,
        )
        troops_before = next(item for item in assigned.cities if item.city_id == city.city_id).resources.troops
        assigned.current_month += 1
        first = advance_hero_personal_states(assigned)
        first_hero = next(item for item in first.strategic_heroes if item.hero_code == hero.hero_code)
        self.assertEqual(first_hero.personal_mission_progress, 1)
        self.assertEqual(
            next(item for item in first.cities if item.city_id == city.city_id).resources.troops,
            troops_before + 35,
        )
        idempotent = advance_hero_personal_states(first)
        self.assertEqual(
            next(item for item in idempotent.cities if item.city_id == city.city_id).resources.troops,
            troops_before + 35,
        )
        first.current_month += 1
        completed = advance_hero_personal_states(first)
        completed_hero = next(item for item in completed.strategic_heroes if item.hero_code == hero.hero_code)
        self.assertEqual(completed_hero.personal_mission_status, "completed")
        self.assertEqual(completed_hero.personal_mission_progress, 2)
        self.assertEqual(completed_hero.loyalty, 70)
        self.assertEqual(completed_hero.relationships[lord.holder_id], 8)
        self.assertEqual(
            next(item for item in completed.cities if item.city_id == city.city_id).resources.troops,
            troops_before + 70,
        )

    def test_unmatched_city_duty_still_applies_generic_bonus(self) -> None:
        world = generate_random_world(
            seed=613,
            city_count=8,
            faction_count=2,
            neutral_city_states=True,
            campaign_contract=first_campaign_contract(),
        )
        faction_id = "faction_1"
        city = next(item for item in world.cities if item.owner_faction_id == faction_id)
        lord = next(item for item in world.offices if item.faction_id == faction_id and item.office_type == "lord")
        hero = next(
            item
            for item in world.strategic_heroes
            if item.faction_id == faction_id and item.status == "serving" and item.hero_code != lord.holder_id
        )
        hero.strategic_specialty = "vanguard"
        assigned = assign_strategic_hero_duty(
            world,
            faction_id=faction_id,
            issuer_office_id=lord.office_id,
            hero_code=hero.hero_code,
            assignment_type="training",
            target_id=city.city_id,
        )
        troops_before = next(item for item in assigned.cities if item.city_id == city.city_id).resources.troops
        assigned.current_month += 1
        settled = advance_hero_personal_states(assigned)
        self.assertEqual(
            next(item for item in settled.cities if item.city_id == city.city_id).resources.troops,
            troops_before + 15,
        )

    def test_personal_mission_deadline_and_low_loyalty_refusal_are_real(self) -> None:
        world = generate_random_world(
            seed=612,
            city_count=8,
            faction_count=2,
            neutral_city_states=True,
            campaign_contract=first_campaign_contract(),
        )
        faction_id = "faction_1"
        lord = next(item for item in world.offices if item.faction_id == faction_id and item.office_type == "lord")
        hero = next(
            item
            for item in world.strategic_heroes
            if item.faction_id == faction_id and item.status == "serving" and item.hero_code != lord.holder_id
        )
        loyalty_before = hero.loyalty
        hero.assignment_type = "reserve"
        world.current_month = int(hero.personal_mission_due_month or 4)
        failed = advance_hero_personal_states(world)
        failed_hero = next(item for item in failed.strategic_heroes if item.hero_code == hero.hero_code)
        self.assertEqual(failed_hero.personal_mission_status, "failed")
        self.assertEqual(failed_hero.loyalty, loyalty_before - 10)
        self.assertEqual(failed_hero.relationships[lord.holder_id], -8)

        failed_hero.loyalty = 10
        self.assertFalse(hero_command_accepts(failed, failed_hero, "assignment:campaign"))
        with self.assertRaises(StrategyError):
            normalize_strategic_hero_deployment(failed, faction_id, [failed_hero.hero_code])
        with self.assertRaises(StrategyError):
            assign_strategic_hero_duty(
                failed,
                faction_id=faction_id,
                issuer_office_id=lord.office_id,
                hero_code=failed_hero.hero_code,
                assignment_type="campaign",
            )
        reserved = assign_strategic_hero_duty(
            failed,
            faction_id=faction_id,
            issuer_office_id=lord.office_id,
            hero_code=failed_hero.hero_code,
            assignment_type="reserve",
        )
        self.assertEqual(
            next(item for item in reserved.strategic_heroes if item.hero_code == hero.hero_code).assignment_type,
            "reserve",
        )

    def _summon_faction_hero(self, world: WorldState, faction_id: str = "faction_1") -> tuple[WorldState, dict[str, object]]:
        hero = next(
            item
            for item in strategic_hero_pool_public(world)
            if item["faction_id"] == faction_id and item["status"] == "serving"
        )
        return world, hero

    def test_strategic_hero_pool_is_dynamic_from_public_hero_registry(self) -> None:
        world = generate_random_world(seed=71, city_count=4, faction_count=2)
        public_codes = {hero["code"] for hero in list_heroes()}
        pool = strategic_hero_pool_public(world)
        pool_codes = {hero["code"] for hero in pool}

        self.assertEqual(pool_codes, public_codes)
        self.assertNotIn("strategy_infantry", pool_codes)
        self.assertTrue(any(hero["status"] == "roaming" and not hero["faction_id"] for hero in pool))
        self.assertTrue(any(hero["status"] == "serving" and hero["faction_id"] for hero in pool))
        self.assertTrue(all(hero["city_id"] for hero in pool))

    def test_recruitment_only_draws_nearby_roaming_heroes_and_accepts_one(self) -> None:
        world = generate_random_world(seed=72, city_count=4, faction_count=2)
        faction_id = "faction_1"
        city = next(item for item in world.cities if item.owner_faction_id == faction_id)
        lord = next(item for item in world.offices if item.faction_id == faction_id and item.office_type == "lord")
        nearby = nearby_roaming_hero_codes(world, city.city_id)

        issued = issue_hero_recruitment(
            world,
            faction_id=faction_id,
            city_id=city.city_id,
            issuer_office_id=lord.office_id,
        )
        recruitment = issued.hero_recruitments[-1]
        self.assertTrue(set(recruitment.candidate_hero_codes).issubset(set(nearby)))
        self.assertNotEqual(recruitment.status, "open")
        if recruitment.candidate_hero_codes:
            code = recruitment.candidate_hero_codes[0]
            accepted = accept_hero_recruitment(
                issued,
                faction_id=faction_id,
                recruitment_id=recruitment.recruitment_id,
                hero_code=code,
                issuer_office_id=lord.office_id,
            )
            hero = next(item for item in accepted.strategic_heroes if item.hero_code == code)
            self.assertEqual((hero.status, hero.faction_id, hero.city_id), ("serving", faction_id, city.city_id))
            self.assertTrue(any(event.category == "strategic_hero_recruited" for event in accepted.event_log))

    def test_direct_summon_is_forbidden(self) -> None:
        world = generate_random_world(seed=73, city_count=4, faction_count=2)
        hero = strategic_hero_pool_public(world)[0]
        with self.assertRaises(StrategyError):
            validate_summon_strategic_hero(world, faction_id="faction_1", hero_code=hero["code"])
        with self.assertRaises(StrategyError):
            summon_strategic_hero(world, faction_id="faction_1", hero_code=hero["code"])

    def test_player_can_found_new_faction_from_roaming_hero_city(self) -> None:
        world = generate_random_world(seed=731, city_count=6, faction_count=2)
        chosen = next(hero for hero in world.strategic_heroes if hero.status == "roaming")
        founding_city_id = chosen.city_id

        founded = choose_player_hero_path(
            world,
            user_id=7,
            hero_code=chosen.hero_code,
            path="found",
            assigned_faction_id="faction_1",
            allow_reselect=True,
        )
        controlled = next(hero for hero in founded.strategic_heroes if hero.controller_user_id == 7)
        faction = next(item for item in founded.factions if item.faction_id == controlled.faction_id)
        city = next(item for item in founded.cities if item.city_id == founding_city_id)
        lord = next(item for item in founded.offices if item.office_id == controlled.office_id)

        self.assertEqual(len(founded.factions), 3)
        self.assertEqual(city.owner_faction_id, faction.faction_id)
        self.assertEqual(faction.capital_city_id, city.city_id)
        self.assertEqual((controlled.status, lord.office_type, lord.holder_id), ("serving", "lord", chosen.hero_code))
        self.assertEqual(
            [hero.hero_code for hero in founded.strategic_heroes if hero.faction_id == faction.faction_id],
            [chosen.hero_code],
        )
        self.assertTrue(any(office.status == "vacant" for office in founded.offices if office.faction_id == faction.faction_id))
        self.assertTrue(any(event.category == "hero_founded_faction" for event in founded.event_log))
        expected_name = next(item["name"] for item in list_heroes() if item["code"] == chosen.hero_code)
        self.assertEqual(faction.name, expected_name)

    def test_recruited_hero_can_be_appointed_by_lord(self) -> None:
        world = generate_random_world(seed=735, city_count=6, faction_count=2)
        faction_id = "faction_1"
        lord = next(item for item in world.offices if item.faction_id == faction_id and item.office_type == "lord")
        recruit = next(hero for hero in world.strategic_heroes if hero.status == "roaming")
        city = next(item for item in world.cities if item.owner_faction_id == faction_id)
        for hero in world.strategic_heroes:
            if hero.status == "roaming":
                hero.city_id = None
        recruit.city_id = city.city_id
        recruit.loyalty = 100
        city.support_by_faction[faction_id] = 100
        issued = issue_hero_recruitment(
            world,
            faction_id=faction_id,
            city_id=city.city_id,
            issuer_office_id=lord.office_id,
        )
        request = issued.hero_recruitments[-1]
        self.assertIn(recruit.hero_code, request.candidate_hero_codes)
        accepted = accept_hero_recruitment(
            issued,
            faction_id=faction_id,
            recruitment_id=request.recruitment_id,
            hero_code=recruit.hero_code,
            issuer_office_id=lord.office_id,
        )
        target = next(
            office
            for office in accepted.offices
            if office.faction_id == faction_id and office.office_type == "grand_general"
        )
        appointed = appoint_strategic_hero_to_office(
            accepted,
            faction_id=faction_id,
            issuer_office_id=lord.office_id,
            target_office_id=target.office_id,
            hero_code=recruit.hero_code,
        )
        appointed_hero = next(hero for hero in appointed.strategic_heroes if hero.hero_code == recruit.hero_code)
        appointed_office = next(office for office in appointed.offices if office.office_id == target.office_id)

        self.assertEqual(appointed_hero.office_id, target.office_id)
        self.assertEqual((appointed_office.holder_type, appointed_office.holder_id), ("hero", recruit.hero_code))
        self.assertEqual(appointed_hero.relationships[lord.holder_id], 8)
        self.assertEqual(appointed_hero.personal_mission_status, "active")
        self.assertTrue(any(row.get("event") == "appointed" for row in appointed_hero.personal_history))
        self.assertTrue(any(event.category == "strategic_hero_appointed" for event in appointed.event_log))

    def test_appoint_ignores_placeholder_lord_holder(self) -> None:
        world = generate_random_world(seed=731, city_count=6, faction_count=2)
        faction_id = "faction_1"
        lord = next(item for item in world.offices if item.faction_id == faction_id and item.office_type == "lord")
        lord.holder_id = f"ai:{faction_id}"
        lord.holder_type = "officer"
        recruit = next(
            hero
            for hero in world.strategic_heroes
            if hero.status == "serving" and hero.faction_id == faction_id and hero.hero_code != lord.holder_id
        )
        target = next(
            office
            for office in world.offices
            if office.faction_id == faction_id and office.office_type != "lord"
        )
        appointed = appoint_strategic_hero_to_office(
            world,
            faction_id=faction_id,
            issuer_office_id=lord.office_id,
            target_office_id=target.office_id,
            hero_code=recruit.hero_code,
        )
        appointed_hero = next(hero for hero in appointed.strategic_heroes if hero.hero_code == recruit.hero_code)
        self.assertNotIn(f"ai:{faction_id}", appointed_hero.relationships)

    def test_roaming_player_join_request_requires_lord_acceptance(self) -> None:
        world = generate_random_world(seed=732, city_count=6, faction_count=2)
        chosen = next(hero for hero in world.strategic_heroes if hero.status == "roaming")
        requested = choose_player_hero_path(
            world,
            user_id=8,
            hero_code=chosen.hero_code,
            path="join",
            assigned_faction_id="faction_1",
            target_faction_id="faction_2",
            allow_reselect=True,
        )
        controlled = next(hero for hero in requested.strategic_heroes if hero.controller_user_id == 8)
        request = requested.hero_recruitments[-1]

        self.assertEqual((controlled.status, controlled.faction_id), ("roaming", None))
        self.assertEqual(request.candidate_hero_codes, [chosen.hero_code])
        accepted = accept_hero_recruitment(
            requested,
            faction_id="faction_2",
            recruitment_id=request.recruitment_id,
            hero_code=chosen.hero_code,
            issuer_office_id=next(
                office.office_id
                for office in requested.offices
                if office.faction_id == "faction_2" and office.office_type == "lord"
            ),
        )
        controlled = next(hero for hero in accepted.strategic_heroes if hero.controller_user_id == 8)
        self.assertEqual((controlled.status, controlled.faction_id), ("serving", "faction_2"))

    def test_active_player_cannot_switch_to_another_roaming_hero(self) -> None:
        world = generate_random_world(seed=733, city_count=6, faction_count=2)
        current = next(hero for hero in world.strategic_heroes if hero.status == "serving")
        current.controller_type = "player"
        current.controller_user_id = 9
        another = next(hero for hero in world.strategic_heroes if hero.status == "roaming")

        with self.assertRaises(StrategyError):
            choose_player_hero_path(
                world,
                user_id=9,
                hero_code=another.hero_code,
                path="roaming",
                assigned_faction_id="faction_1",
            )

    def test_ai_roaming_hero_can_spontaneously_request_allegiance(self) -> None:
        world = generate_random_world(seed=734, city_count=6, faction_count=2)

        requested = open_spontaneous_allegiance_request(world)
        request = requested.hero_recruitments[-1]
        hero = next(item for item in requested.strategic_heroes if item.hero_code == request.candidate_hero_codes[0])

        self.assertEqual(request.status, "responses")
        self.assertEqual((hero.status, hero.faction_id, hero.controller_type), ("roaming", None, "ai"))
        self.assertTrue(any(event.category == "hero_requested_allegiance" for event in requested.event_log))

    def test_public_world_includes_strategic_hero_pool_and_faction_slice(self) -> None:
        world = generate_random_world(seed=74, city_count=4, faction_count=2)
        public = world.to_public_dict()

        self.assertEqual(
            {hero["code"] for hero in public["strategic_hero_pool"]},
            {hero["code"] for hero in list_heroes()},
        )
        self.assertTrue(public["factions"][0]["strategic_heroes"])
        self.assertTrue(
            all(hero["faction_id"] == public["factions"][0]["id"] for hero in public["factions"][0]["strategic_heroes"])
        )

    def test_summoned_strategic_hero_joins_real_city_battle_roster(self) -> None:
        world = generate_random_world(seed=75, city_count=4, faction_count=2)
        _ensure_city_road(world, "city_1", "city_2")
        world.cities[0].resources.troops = 1200
        world.cities[1].resources.troops = 300
        summoned, hero = self._summon_faction_hero(world)
        pending = declare_city_attack(
            summoned,
            faction_id="faction_1",
            source_city_id="city_1",
            target_city_id="city_2",
            resolution_mode="manual",
            attacker_hero_codes=[str(hero["code"])],
        )

        rosters = strategy_battle_rosters(pending, pending.pending_battles[-1])

        self.assertIn(hero["code"], active_strategic_hero_codes_for_faction(pending, "faction_1"))
        self.assertEqual(pending.pending_battles[-1].attacker_hero_codes, [hero["code"]])
        self.assertIn(hero["code"], rosters.attacker.roster)
        self.assertEqual(rosters.attacker.roster.count(hero["code"]), 1)
        self.assertTrue(
            any(
                row["source"] == "strategic_hero" and row["hero_code"] == hero["code"] and row["grid_units"] == 1
                for row in rosters.attacker.manifest
            )
        )

    def test_defeated_strategic_hero_sleeps_after_real_battle_resolution(self) -> None:
        world = generate_random_world(seed=76, city_count=4, faction_count=2)
        _ensure_city_road(world, "city_1", "city_2")
        world.cities[0].resources.troops = 1200
        world.cities[1].resources.troops = 300
        summoned, hero = self._summon_faction_hero(world)
        pending = declare_city_attack(
            summoned,
            faction_id="faction_1",
            source_city_id="city_1",
            target_city_id="city_2",
            resolution_mode="manual",
            attacker_hero_codes=[str(hero["code"])],
        )
        attached = attach_battle_room(
            pending,
            battle_id=pending.pending_battles[-1].battle_id,
            room_id="hero_room",
            invite_path="/?room=HERO_ROOM",
        )
        committed = next(item for item in attached.strategic_heroes if item.hero_code == hero["code"])
        lord = next(item for item in attached.offices if item.faction_id == "faction_1" and item.office_type == "lord")
        committed.loyalty = 0

        resolved = resolve_battle_room_result(
            attached,
            battle_room_id="HERO_ROOM",
            winner_team_id=2,
            surviving_grid_units_by_team={1: 0, 2: 2},
            surviving_hero_codes_by_team={1: set(), 2: set()},
        )
        battle = resolved.pending_battles[-1]
        faction = next(item for item in resolved.factions if item.faction_id == "faction_1")
        public_hero = next(item for item in strategic_hero_pool_public(resolved) if item["code"] == hero["code"])

        self.assertIn(
            f"strategic_hero_sleeping:{hero['code']}:until:{resolved.current_month + STRATEGIC_HERO_BATTLE_SLEEP_MONTHS}",
            faction.memory_tags,
        )
        self.assertEqual(public_hero["status"], "sleeping")
        self.assertEqual(public_hero["sleeping_until_month"], resolved.current_month + STRATEGIC_HERO_BATTLE_SLEEP_MONTHS)
        self.assertEqual(public_hero["loyalty"], 0)
        self.assertEqual(public_hero["lord_relationship"], 0 if hero["code"] == lord.holder_id else -3)
        self.assertEqual(public_hero["lord_hero_code"], lord.holder_id)
        self.assertNotIn(hero["code"], active_strategic_hero_codes_for_faction(resolved, "faction_1"))
        self.assertEqual(battle.battle_result["strategic_heroes_by_side"]["attacker"]["sleeping"], [hero["code"]])
        self.assertTrue(any(event.category == "strategic_hero_sleeping" for event in resolved.event_log))

        woken = WorldState.from_dict(resolved.to_dict())
        woken.current_month = resolved.current_month + STRATEGIC_HERO_BATTLE_SLEEP_MONTHS
        woken_hero = next(item for item in strategic_hero_pool_public(woken) if item["code"] == hero["code"])
        self.assertEqual(woken_hero["status"], "serving")
        self.assertIn(hero["code"], active_strategic_hero_codes_for_faction(woken, "faction_1"))

    def test_strategic_hero_deployment_requires_explicit_attacker_selection(self) -> None:
        world = generate_random_world(seed=77, city_count=4, faction_count=2)
        _ensure_city_road(world, "city_1", "city_2")
        world.cities[0].resources.troops = 1200
        world.cities[1].resources.troops = 300
        summoned, hero = self._summon_faction_hero(world)
        pending = declare_city_attack(
            summoned,
            faction_id="faction_1",
            source_city_id="city_1",
            target_city_id="city_2",
            resolution_mode="manual",
        )

        rosters = strategy_battle_rosters(pending, pending.pending_battles[-1])

        self.assertEqual(pending.pending_battles[-1].attacker_hero_codes, [])
        self.assertNotIn(hero["code"], rosters.attacker.roster)

    def test_strategic_hero_deployment_validates_available_hero_and_limit(self) -> None:
        world = generate_random_world(seed=78, city_count=4, faction_count=2)
        summoned, hero = self._summon_faction_hero(world)
        other_hero = next(item for item in strategic_hero_pool_public(summoned) if item["faction_id"] != "faction_1")

        self.assertEqual(normalize_strategic_hero_deployment(summoned, "faction_1", [str(hero["code"])]), [hero["code"]])
        with self.assertRaises(StrategyError):
            normalize_strategic_hero_deployment(summoned, "faction_1", [str(other_hero["code"])])
        with self.assertRaises(StrategyError):
            normalize_strategic_hero_deployment(summoned, "faction_1", [str(hero["code"]), str(other_hero["code"])])

    def test_tactic_tech_expands_strategic_hero_deployment_limit(self) -> None:
        world = generate_random_world(seed=82, city_count=4, faction_count=2)
        hero_pool = strategic_hero_pool_public(world)
        faction_id = next(
            faction.faction_id
            for faction in world.factions
            if sum(1 for hero in hero_pool if hero["faction_id"] == faction.faction_id and hero["status"] == "serving") >= 2
        )
        enemy_faction_id = next(faction.faction_id for faction in world.factions if faction.faction_id != faction_id)
        heroes = [hero for hero in hero_pool if hero["faction_id"] == faction_id and hero["status"] == "serving"][:2]
        summoned = world

        self.assertEqual(strategic_hero_deployment_limit(summoned, faction_id), 3)
        extra = [hero for hero in hero_pool if hero["faction_id"] == faction_id and hero["status"] == "serving"][:4]
        if len(extra) > 3:
            with self.assertRaisesRegex(StrategyError, r"出征最多投入 \d+ 名武将"):
                normalize_strategic_hero_deployment(summoned, faction_id, [str(hero["code"]) for hero in extra])

        boosted = WorldState.from_dict(summoned.to_dict())
        boosted_faction = next(item for item in boosted.factions if item.faction_id == faction_id)
        boosted_faction.tactic_techs.append("hero_command")
        boosted.cities[0].owner_faction_id = faction_id
        boosted.cities[1].owner_faction_id = enemy_faction_id
        nodes_by_id = {node.node_id: node for node in boosted.nodes}
        source_node = nodes_by_id[boosted.cities[0].node_id]
        target_node = nodes_by_id[boosted.cities[1].node_id]
        source_node.connected_node_ids = list(set(source_node.connected_node_ids + [target_node.node_id]))
        target_node.connected_node_ids = list(set(target_node.connected_node_ids + [source_node.node_id]))
        boosted.cities[0].resources.troops = 1200
        boosted.cities[1].resources.troops = 300
        stationed = {str(hero["code"]) for hero in heroes}
        for hero in boosted.strategic_heroes:
            if hero.hero_code in stationed:
                hero.city_id = boosted.cities[0].city_id

        pending = declare_city_attack(
            boosted,
            faction_id=faction_id,
            source_city_id=boosted.cities[0].city_id,
            target_city_id=boosted.cities[1].city_id,
            resolution_mode="manual",
            attacker_hero_codes=[str(hero["code"]) for hero in heroes],
        )
        rosters = strategy_battle_rosters(pending, pending.pending_battles[-1])

        self.assertEqual(strategic_hero_deployment_limit(boosted, faction_id), 4)
        self.assertEqual(pending.pending_battles[-1].attacker_hero_codes, [hero["code"] for hero in heroes])
        self.assertTrue(all(hero["code"] in rosters.attacker.roster for hero in heroes))

    def test_configured_strategic_defender_hero_joins_defender_roster(self) -> None:
        world = generate_random_world(seed=79, city_count=4, faction_count=2)
        _ensure_city_road(world, "city_1", "city_2")
        world.cities[0].resources.troops = 1200
        world.cities[1].resources.troops = 300
        summoned, hero = self._summon_faction_hero(world, faction_id="faction_2")
        defended = set_strategic_defender_hero(summoned, faction_id="faction_2", hero_code=str(hero["code"]))
        pending = declare_city_attack(
            defended,
            faction_id="faction_1",
            source_city_id="city_1",
            target_city_id="city_2",
            resolution_mode="manual",
        )

        rosters = strategy_battle_rosters(pending, pending.pending_battles[-1])
        defender_hero = next(item for item in strategic_hero_pool_public(defended) if item["code"] == hero["code"])

        self.assertEqual(strategic_defender_hero_codes_for_faction(defended, "faction_2"), [hero["code"]])
        self.assertTrue(defender_hero["defender_assigned"])
        self.assertIn(hero["code"], rosters.defender.roster)

    def test_pending_battle_defender_override_uses_selected_hero(self) -> None:
        world = generate_random_world(seed=80, city_count=4, faction_count=2)
        _ensure_city_road(world, "city_1", "city_2")
        world.cities[0].resources.troops = 1200
        world.cities[1].resources.troops = 300
        summoned, hero = self._summon_faction_hero(world, faction_id="faction_2")
        pending = declare_city_attack(
            summoned,
            faction_id="faction_1",
            source_city_id="city_1",
            target_city_id="city_2",
            resolution_mode="manual",
        )
        battle_id = pending.pending_battles[-1].battle_id

        updated = set_battle_defender_hero(
            pending,
            faction_id="faction_2",
            battle_id=battle_id,
            hero_code=str(hero["code"]),
        )
        battle = updated.pending_battles[-1]
        rosters = strategy_battle_rosters(updated, battle)

        self.assertEqual(battle.defender_hero_codes, [hero["code"]])
        self.assertIn(hero["code"], rosters.defender.roster)
        self.assertTrue(any(event.category == "battle_defender_hero_set" for event in updated.event_log))

    def test_pending_battle_defender_override_validates_side_and_room_lock(self) -> None:
        world = generate_random_world(seed=81, city_count=4, faction_count=2)
        _ensure_city_road(world, "city_1", "city_2")
        world.cities[0].resources.troops = 1200
        world.cities[1].resources.troops = 300
        summoned, hero = self._summon_faction_hero(world, faction_id="faction_2")
        pending = declare_city_attack(
            summoned,
            faction_id="faction_1",
            source_city_id="city_1",
            target_city_id="city_2",
            resolution_mode="manual",
        )
        battle_id = pending.pending_battles[-1].battle_id

        with self.assertRaises(StrategyError):
            set_battle_defender_hero(
                pending,
                faction_id="faction_1",
                battle_id=battle_id,
                hero_code=str(hero["code"]),
            )

        attached = attach_battle_room(
            pending,
            battle_id=battle_id,
            room_id="locked_room",
            invite_path="/?room=LOCKED_ROOM",
        )
        with self.assertRaises(StrategyError):
            set_battle_defender_hero(
                attached,
                faction_id="faction_2",
                battle_id=battle_id,
                hero_code=str(hero["code"]),
            )


class StrategyRoleWorkspaceActionTests(unittest.TestCase):
    def _world_with_candidate(self) -> tuple[WorldState, str, City, object, object, object]:
        world = generate_random_world(seed=739, city_count=6, faction_count=2)
        faction_id = "faction_1"
        city = next(item for item in world.cities if item.owner_faction_id == faction_id)
        lord = next(item for item in world.offices if item.faction_id == faction_id and item.office_type == "lord")
        governor = next(
            item
            for item in world.offices
            if item.faction_id == faction_id and item.office_type == "governor" and city.city_id in item.managed_entity_ids
        )
        candidate = next(hero for hero in world.strategic_heroes if hero.status == "roaming")
        for hero in world.strategic_heroes:
            if hero.status == "roaming":
                hero.city_id = None
        candidate.city_id = city.city_id
        candidate.loyalty = 100
        city.support_by_faction[faction_id] = 100
        return world, faction_id, city, lord, governor, candidate

    def test_governor_recruits_recommends_and_lord_approves(self) -> None:
        world, faction_id, city, lord, governor, candidate = self._world_with_candidate()
        issued = issue_hero_recruitment(
            world,
            faction_id=faction_id,
            city_id=city.city_id,
            issuer_office_id=governor.office_id,
        )
        request = issued.hero_recruitments[-1]
        self.assertIn(candidate.hero_code, request.candidate_hero_codes)

        with self.assertRaises(StrategyError):
            accept_hero_recruitment(
                issued,
                faction_id=faction_id,
                recruitment_id=request.recruitment_id,
                hero_code=candidate.hero_code,
                issuer_office_id=governor.office_id,
            )
        with self.assertRaises(StrategyError):
            accept_hero_recruitment(
                issued,
                faction_id=faction_id,
                recruitment_id=request.recruitment_id,
                hero_code=candidate.hero_code,
                issuer_office_id=lord.office_id,
            )

        recommended = recommend_hero_recruitment(
            issued,
            faction_id=faction_id,
            recruitment_id=request.recruitment_id,
            hero_code=candidate.hero_code,
            issuer_office_id=governor.office_id,
        )
        self.assertEqual(recommended.hero_recruitments[-1].status, "recommended")
        approved = accept_hero_recruitment(
            recommended,
            faction_id=faction_id,
            recruitment_id=request.recruitment_id,
            hero_code=candidate.hero_code,
            issuer_office_id=lord.office_id,
        )
        hero = next(item for item in approved.strategic_heroes if item.hero_code == candidate.hero_code)
        self.assertEqual((hero.status, hero.faction_id), ("serving", faction_id))

    def test_governor_cannot_recruit_from_another_city(self) -> None:
        world, faction_id, _, _, governor, _ = self._world_with_candidate()
        other_city = next(
            city for city in world.cities if city.owner_faction_id == faction_id and city.city_id not in governor.managed_entity_ids
        )
        with self.assertRaises(StrategyError):
            issue_hero_recruitment(
                world,
                faction_id=faction_id,
                city_id=other_city.city_id,
                issuer_office_id=governor.office_id,
            )

    def test_lord_assigns_each_serving_hero_a_persistent_duty(self) -> None:
        world = generate_random_world(seed=740, city_count=6, faction_count=2)
        faction_id = "faction_1"
        lord = next(item for item in world.offices if item.faction_id == faction_id and item.office_type == "lord")
        city = next(item for item in world.cities if item.owner_faction_id == faction_id)
        hero = next(item for item in world.strategic_heroes if item.faction_id == faction_id)
        assigned = assign_strategic_hero_duty(
            world,
            faction_id=faction_id,
            issuer_office_id=lord.office_id,
            hero_code=hero.hero_code,
            assignment_type="garrison",
            target_id=city.city_id,
        )
        state = next(item for item in assigned.strategic_heroes if item.hero_code == hero.hero_code)
        self.assertEqual((state.assignment_type, state.assignment_target_id, state.city_id), ("garrison", city.city_id, city.city_id))
        self.assertEqual(WorldState.from_dict(assigned.to_dict()).to_dict(), assigned.to_dict())

    def test_grand_general_levies_field_troops(self) -> None:
        world = generate_random_world(seed=741, city_count=6, faction_count=2)
        faction_id = "faction_1"
        city = next(item for item in world.cities if item.owner_faction_id == faction_id)
        city.resources.population = city.resources.food = city.resources.money = 1000
        grand = next(item for item in world.offices if item.faction_id == faction_id and item.office_type == "grand_general")
        governor = next(item for item in world.offices if item.faction_id == faction_id and item.office_type == "governor")
        before = city.resources.troops
        levied = levy_field_troops(
            world,
            faction_id=faction_id,
            city_id=city.city_id,
            issuer_office_id=grand.office_id,
        )
        self.assertGreater(next(item for item in levied.cities if item.city_id == city.city_id).resources.troops, before)
        with self.assertRaises(StrategyError):
            levy_field_troops(
                world,
                faction_id=faction_id,
                city_id=city.city_id,
                issuer_office_id=governor.office_id,
            )

    def test_governor_levies_garrison_and_constructs_local_building(self) -> None:
        world = generate_random_world(seed=742, city_count=6, faction_count=2)
        faction_id = "faction_1"
        governor = next(item for item in world.offices if item.faction_id == faction_id and item.office_type == "governor")
        city = next(item for item in world.cities if item.city_id in governor.managed_entity_ids)
        city.settlement = "village"
        city.resources.population = city.resources.food = city.resources.money = 1000
        before_defense = city.defense
        levied = levy_city_garrison(
            world,
            faction_id=faction_id,
            city_id=city.city_id,
            issuer_office_id=governor.office_id,
        )
        levied_city = next(item for item in levied.cities if item.city_id == city.city_id)
        self.assertGreater(levied_city.defense, before_defense)
        built = construct_city_building(
            levied,
            faction_id=faction_id,
            city_id=city.city_id,
            building_id="market",
            issuer_office_id=governor.office_id,
        )
        built_city = next(item for item in built.cities if item.city_id == city.city_id)
        self.assertIn("market", built_city.buildings)
        with self.assertRaises(StrategyError):
            construct_city_building(
                built,
                faction_id=faction_id,
                city_id=city.city_id,
                building_id="market",
                issuer_office_id=governor.office_id,
            )

    def test_lord_can_construct_owned_city_building(self) -> None:
        world = generate_random_world(seed=747, city_count=6, faction_count=2)
        faction_id = "faction_1"
        lord = next(item for item in world.offices if item.faction_id == faction_id and item.office_type == "lord")
        city = next(item for item in world.cities if item.owner_faction_id == faction_id)
        city.settlement = "village"
        city.resources.food = city.resources.money = 1000
        built = construct_city_building(
            world,
            faction_id=faction_id,
            city_id=city.city_id,
            building_id="market",
            issuer_office_id=lord.office_id,
        )
        built_city = next(item for item in built.cities if item.city_id == city.city_id)
        self.assertIn("market", built_city.buildings)

    def test_governor_upgrades_village_to_town_and_town_to_city_or_fortress(self) -> None:
        world = generate_random_world(seed=746, city_count=6, faction_count=2)
        faction_id = "faction_1"
        governor = next(item for item in world.offices if item.faction_id == faction_id and item.office_type == "governor")
        city = next(item for item in world.cities if item.city_id in governor.managed_entity_ids)
        city.settlement = "village"
        city.resources.population = 1400
        city.resources.food = 900
        city.resources.money = 400
        with self.assertRaisesRegex(StrategyError, "只有城镇"):
            upgrade_city_settlement(
                world,
                faction_id=faction_id,
                city_id=city.city_id,
                settlement="city",
                issuer_office_id=governor.office_id,
            )
        city.resources.population = 1200
        with self.assertRaisesRegex(StrategyError, "人口不足"):
            upgrade_city_settlement(
                world,
                faction_id=faction_id,
                city_id=city.city_id,
                settlement="town",
                issuer_office_id=governor.office_id,
            )
        city.resources.population = 1400
        to_town = upgrade_city_settlement(
            world,
            faction_id=faction_id,
            city_id=city.city_id,
            settlement="town",
            issuer_office_id=governor.office_id,
        )
        town = next(item for item in to_town.cities if item.city_id == city.city_id)
        self.assertEqual(town.settlement, "town")
        self.assertGreaterEqual(town.level, 2)
        self.assertEqual(town.resources.food, 200)
        self.assertEqual(town.resources.money, 220)
        town.resources.population = 2600
        town.resources.food = 1300
        town.resources.money = 400
        to_city = upgrade_city_settlement(
            to_town,
            faction_id=faction_id,
            city_id=city.city_id,
            settlement="city",
            issuer_office_id=governor.office_id,
        )
        upgraded_city = next(item for item in to_city.cities if item.city_id == city.city_id)
        self.assertEqual(upgraded_city.settlement, "city")
        self.assertGreaterEqual(upgraded_city.level, 3)
        fortress_world = generate_random_world(seed=747, city_count=6, faction_count=2)
        fortress_governor = next(
            item for item in fortress_world.offices if item.faction_id == faction_id and item.office_type == "governor"
        )
        fortress_city = next(item for item in fortress_world.cities if item.city_id in fortress_governor.managed_entity_ids)
        fortress_city.settlement = "town"
        fortress_city.resources.population = 2000
        fortress_city.resources.food = 1000
        fortress_city.resources.money = 300
        before_defense = fortress_city.defense
        to_fortress = upgrade_city_settlement(
            fortress_world,
            faction_id=faction_id,
            city_id=fortress_city.city_id,
            settlement="fortress",
            issuer_office_id=fortress_governor.office_id,
        )
        fortress = next(item for item in to_fortress.cities if item.city_id == fortress_city.city_id)
        self.assertEqual(fortress.settlement, "fortress")
        self.assertEqual(fortress.defense, before_defense + 4)

    def test_staff_technology_expands_generals_per_grand_general(self) -> None:
        world = generate_random_world(seed=743, city_count=4, faction_count=2)
        faction = next(item for item in world.factions if item.faction_id == "faction_1")
        faction.tactic_techs.extend(["local_militia", "command_staff_1", "command_staff_2"])

        rebuilt = ensure_office_system(world)

        self.assertEqual(general_capacity_per_grand_general(rebuilt, faction.faction_id), 3)
        generals = [
            office
            for office in rebuilt.offices
            if office.faction_id == faction.faction_id and office.office_type == "general" and office.status != "disabled"
        ]
        self.assertEqual(len(generals), 3)
        self.assertEqual(sum(office.status == "vacant" for office in generals), 2)

    def test_ritual_requires_site_capacity_and_binds_random_hero(self) -> None:
        world = generate_random_world(seed=744, city_count=4, faction_count=2)
        faction_id = "faction_1"
        faction = next(item for item in world.factions if item.faction_id == faction_id)
        lord = next(item for item in world.offices if item.faction_id == faction_id and item.office_type == "lord")
        city = next(item for item in world.cities if item.owner_faction_id == faction_id)
        city.resources.ether = 100
        self.assertEqual(hero_ritual_capacity(world, faction_id)["remaining"], 0)
        with self.assertRaises(StrategyError):
            perform_hero_ritual(
                world,
                faction_id=faction_id,
                city_id=city.city_id,
                issuer_office_id=lord.office_id,
            )

        faction.tactic_techs.extend(["local_militia", "command_staff_1"])
        expanded = ensure_office_system(world)
        before = {hero.hero_code for hero in expanded.strategic_heroes if hero.faction_id == faction_id}
        summoned_world = perform_hero_ritual(
            expanded,
            faction_id=faction_id,
            city_id=city.city_id,
            issuer_office_id=lord.office_id,
        )
        summoned = next(
            hero
            for hero in summoned_world.strategic_heroes
            if hero.faction_id == faction_id and hero.hero_code not in before
        )
        summoned_city = next(item for item in summoned_world.cities if item.city_id == city.city_id)
        self.assertEqual(summoned.ritual_city_id, city.city_id)
        self.assertIsNone(summoned.office_id)
        self.assertEqual(summoned_city.resources.ether, 70)
        self.assertEqual(hero_ritual_capacity(summoned_world, faction_id)["remaining"], 0)

        unbound = unbind_strategic_hero(
            summoned_world,
            faction_id=faction_id,
            hero_code=summoned.hero_code,
            issuer_office_id=lord.office_id,
        )
        released = next(hero for hero in unbound.strategic_heroes if hero.hero_code == summoned.hero_code)
        self.assertEqual(released.status, "roaming")
        self.assertIsNone(released.faction_id)
        self.assertIsNone(released.ritual_city_id)
        self.assertEqual(hero_ritual_capacity(unbound, faction_id)["remaining"], 1)

    def test_ritual_rejects_city_without_ritual_site(self) -> None:
        world = generate_random_world(seed=745, city_count=4, faction_count=2)
        faction_id = "faction_1"
        faction = next(item for item in world.factions if item.faction_id == faction_id)
        faction.tactic_techs.extend(["local_militia", "command_staff_1"])
        world = ensure_office_system(world)
        lord = next(item for item in world.offices if item.faction_id == faction_id and item.office_type == "lord")
        city = next(item for item in world.cities if item.owner_faction_id == faction_id)
        city.buildings = [item for item in city.buildings if item != "ritual_site"]
        city.building_levels.pop("ritual_site", None)
        with self.assertRaises(StrategyError):
            perform_hero_ritual(
                world,
                faction_id=faction_id,
                city_id=city.city_id,
                issuer_office_id=lord.office_id,
            )

    def test_old_save_migration_restores_ritual_site_for_bound_heroes(self) -> None:
        world = generate_random_world(seed=207, city_count=6, faction_count=2)
        faction = next(item for item in world.factions if item.faction_id == "faction_1")
        capital = next(item for item in world.cities if item.city_id == faction.capital_city_id)
        capital.building_levels.pop("ritual_site", None)
        capital.buildings = [item for item in capital.buildings if item != "ritual_site"]

        migrated = ensure_strategic_hero_system(world)
        migrated_capital = next(item for item in migrated.cities if item.city_id == capital.city_id)

        self.assertEqual(migrated_capital.building_levels["ritual_site"], 1)

    def test_governor_registers_exact_units_and_building_level_is_settlement_gated(self) -> None:
        world = generate_random_world(seed=746, city_count=4, faction_count=2)
        faction_id = "faction_1"
        governor = next(item for item in world.offices if item.faction_id == faction_id and item.office_type == "governor")
        city = next(item for item in world.cities if item.city_id in governor.managed_entity_ids)
        city.settlement = "village"
        city.resources.troops = 500
        registered = register_city_soldiers(
            world,
            faction_id=faction_id,
            city_id=city.city_id,
            issuer_office_id=governor.office_id,
            unit_count=3,
        )
        registered_city = next(item for item in registered.cities if item.city_id == city.city_id)
        self.assertEqual(registered_city.registered_units, {"infantry": 3})
        self.assertEqual(registered_city.resources.troops, 200)
        with self.assertRaises(StrategyError):
            construct_city_building(
                registered,
                faction_id=faction_id,
                city_id=city.city_id,
                building_id="fields",
                issuer_office_id=governor.office_id,
            )
        with self.assertRaisesRegex(StrategyError, "只有要塞"):
            construct_city_building(
                registered,
                faction_id=faction_id,
                city_id=city.city_id,
                building_id="walls",
                issuer_office_id=governor.office_id,
            )

        registered_city.settlement = "town"
        registered_city.resources.food = registered_city.resources.money = 1000
        upgraded = construct_city_building(
            registered,
            faction_id=faction_id,
            city_id=city.city_id,
            building_id="fields",
            issuer_office_id=governor.office_id,
        )
        self.assertEqual(next(item for item in upgraded.cities if item.city_id == city.city_id).building_levels["fields"], 2)

    def test_city_buildings_follow_settlement_rank_and_pay_monthly_bonus(self) -> None:
        world = generate_random_world(seed=751, city_count=6, faction_count=2)
        faction_id = "faction_1"
        governor = next(item for item in world.offices if item.faction_id == faction_id and item.office_type == "governor")
        city = next(item for item in world.cities if item.city_id in governor.managed_entity_ids)
        city.settlement = "village"
        self.assertEqual(city_building_max_level(city, "academy"), 1)
        self.assertEqual(city_building_max_level(city, "walls"), 0)
        city.settlement = "city"
        self.assertEqual(city_building_max_level(city, "academy"), 3)
        self.assertEqual(city_building_max_level(city, "city_defense"), 1)
        city.settlement = "fortress"
        self.assertEqual(city_building_max_level(city, "walls"), 3)
        self.assertEqual(city_building_max_level(city, "castle"), 3)
        city.building_levels = {"market": 2, "industrial": 1, "barracks": 1}
        self.assertEqual(city_building_monthly_bonus(city), {"food": 0, "money": 135, "ether": 0, "troops": 23})
        city.resources.food = city.resources.money = 2000
        before_defense = city.defense
        built = construct_city_building(
            world,
            faction_id=faction_id,
            city_id=city.city_id,
            building_id="castle",
            issuer_office_id=governor.office_id,
        )
        built_city = next(item for item in built.cities if item.city_id == city.city_id)
        self.assertEqual(built_city.building_levels["castle"], 1)
        self.assertEqual(built_city.defense, before_defense + 1)

    def test_major_factions_can_gift_money_to_each_other(self) -> None:
        world = generate_random_world(seed=752, city_count=6, faction_count=2)
        actor = next(item for item in world.factions if item.faction_id == "faction_1")
        target = next(item for item in world.factions if item.faction_id == "faction_2")
        actor.resources.money = 200
        target.resources.money = 10
        gifted = apply_faction_diplomacy_action(
            world,
            actor_faction_id=actor.faction_id,
            target_faction_id=target.faction_id,
            action_id="gift_money",
        )
        next_actor = next(item for item in gifted.factions if item.faction_id == actor.faction_id)
        next_target = next(item for item in gifted.factions if item.faction_id == target.faction_id)
        self.assertEqual(next_actor.resources.money, 120)
        self.assertEqual(next_target.resources.money, 90)
        self.assertEqual(next_actor.relations[target.faction_id], 8)
        with self.assertRaises(StrategyError):
            apply_faction_diplomacy_action(
                gifted,
                actor_faction_id=actor.faction_id,
                target_faction_id=target.faction_id,
                action_id="gift_money",
            )

    def test_general_requests_units_and_grand_general_approves_or_transfers(self) -> None:
        world = generate_random_world(seed=747, city_count=4, faction_count=2)
        faction_id = "faction_1"
        city = next(item for item in world.cities if item.owner_faction_id == faction_id)
        city.registered_units = {"infantry": 3}
        grand = next(item for item in world.offices if item.faction_id == faction_id and item.office_type == "grand_general")
        general = next(item for item in world.offices if item.parent_office_id == grand.office_id and item.office_type == "general")
        transferred = transfer_registered_units(
            world,
            faction_id=faction_id,
            city_id=city.city_id,
            general_office_id=general.office_id,
            unit_type="infantry",
            count=1,
            issuer_office_id=grand.office_id,
        )
        transferred_city = next(item for item in transferred.cities if item.city_id == city.city_id)
        transferred_general = next(item for item in transferred.offices if item.office_id == general.office_id)
        self.assertEqual(transferred_city.registered_units, {"infantry": 2})
        self.assertEqual(transferred_general.unit_inventory, {"infantry": 1})

        requested = request_registered_units(
            transferred,
            faction_id=faction_id,
            city_id=city.city_id,
            unit_type="infantry",
            count=2,
            issuer_office_id=general.office_id,
        )
        request = requested.office_orders[-1]
        self.assertEqual(request.order_type, "unit_request")
        self.assertEqual(request.details["count"], 2)
        approved = approve_registered_unit_request(
            requested,
            faction_id=faction_id,
            request_id=request.order_id,
            issuer_office_id=grand.office_id,
        )
        approved_general = next(item for item in approved.offices if item.office_id == general.office_id)
        self.assertEqual(approved_general.unit_inventory, {"infantry": 3})
        self.assertEqual(approved.office_orders[-1].status, "completed")
        with self.assertRaises(StrategyError):
            approve_registered_unit_request(
                approved,
                faction_id=faction_id,
                request_id=request.order_id,
                issuer_office_id=grand.office_id,
            )

    def test_city_administration_and_registered_unit_permissions_reject_invalid_requests(self) -> None:
        world = generate_random_world(seed=749, city_count=6, faction_count=2)
        faction_id = "faction_1"
        governor = next(item for item in world.offices if item.faction_id == faction_id and item.office_type == "governor")
        own_city = next(item for item in world.cities if item.city_id in governor.managed_entity_ids)
        other_own_city = next(
            item for item in world.cities if item.owner_faction_id == faction_id and item.city_id != own_city.city_id
        )

        for action in (
            lambda: increase_city_troops(
                world,
                faction_id=faction_id,
                city_id=other_own_city.city_id,
                issuer_office_id=governor.office_id,
            ),
            lambda: register_city_soldiers(
                world,
                faction_id=faction_id,
                city_id=other_own_city.city_id,
                issuer_office_id=governor.office_id,
            ),
            lambda: construct_city_building(
                world,
                faction_id=faction_id,
                city_id=other_own_city.city_id,
                building_id="fields",
                issuer_office_id=governor.office_id,
            ),
        ):
            with self.assertRaises(StrategyError):
                action()

        own_city.resources.population = own_city.resources.food = own_city.resources.money = 0
        with self.assertRaisesRegex(StrategyError, "资源不足"):
            increase_city_troops(
                world,
                faction_id=faction_id,
                city_id=own_city.city_id,
                issuer_office_id=governor.office_id,
            )
        own_city.resources.population = own_city.resources.food = own_city.resources.money = 1000
        own_city.resources.troops = 500
        own_city.building_levels.clear()
        own_city.buildings.clear()
        with self.assertRaisesRegex(StrategyError, "没有可用的训练建筑"):
            register_city_soldiers(
                world,
                faction_id=faction_id,
                city_id=own_city.city_id,
                issuer_office_id=governor.office_id,
            )
        own_city.building_levels["barracks"] = 1
        own_city.resources.troops = 99
        with self.assertRaisesRegex(StrategyError, "兵力不足"):
            register_city_soldiers(
                world,
                faction_id=faction_id,
                city_id=own_city.city_id,
                issuer_office_id=governor.office_id,
            )
        with self.assertRaisesRegex(StrategyError, "建筑项目不存在"):
            construct_city_building(
                world,
                faction_id=faction_id,
                city_id=own_city.city_id,
                building_id="unknown",
                issuer_office_id=governor.office_id,
            )

        own_city.registered_units = {"infantry": 1}
        grand = next(item for item in world.offices if item.faction_id == faction_id and item.office_type == "grand_general")
        general = next(item for item in world.offices if item.parent_office_id == grand.office_id and item.office_type == "general")
        with self.assertRaisesRegex(StrategyError, "兵种不存在"):
            transfer_registered_units(
                world,
                faction_id=faction_id,
                city_id=own_city.city_id,
                general_office_id=general.office_id,
                unit_type="siege",
                count=1,
                issuer_office_id=grand.office_id,
            )
        with self.assertRaisesRegex(StrategyError, "没有足够"):
            transfer_registered_units(
                world,
                faction_id=faction_id,
                city_id=own_city.city_id,
                general_office_id=general.office_id,
                unit_type="infantry",
                count=2,
                issuer_office_id=grand.office_id,
            )
        with self.assertRaisesRegex(StrategyError, "兵种不存在"):
            request_registered_units(
                world,
                faction_id=faction_id,
                city_id=own_city.city_id,
                unit_type="siege",
                count=1,
                issuer_office_id=general.office_id,
            )
        general.parent_office_id = None
        with self.assertRaisesRegex(StrategyError, "没有直属大将军"):
            request_registered_units(
                world,
                faction_id=faction_id,
                city_id=own_city.city_id,
                unit_type="infantry",
                count=1,
                issuer_office_id=general.office_id,
            )

    def test_ritual_and_unbind_permissions_reject_invalid_requests(self) -> None:
        world = generate_random_world(seed=750, city_count=6, faction_count=2)
        faction_id = "faction_1"
        faction = next(item for item in world.factions if item.faction_id == faction_id)
        faction.tactic_techs.extend(["local_militia", "command_staff_1"])
        world = ensure_office_system(world)
        lord = next(item for item in world.offices if item.faction_id == faction_id and item.office_type == "lord")
        governor = next(item for item in world.offices if item.faction_id == faction_id and item.office_type == "governor")
        general = next(item for item in world.offices if item.faction_id == faction_id and item.office_type == "general")
        local_city = next(item for item in world.cities if item.city_id in governor.managed_entity_ids)
        other_own_city = next(
            item for item in world.cities if item.owner_faction_id == faction_id and item.city_id != local_city.city_id
        )
        enemy_city = next(item for item in world.cities if item.owner_faction_id != faction_id)

        with self.assertRaisesRegex(StrategyError, "只能在己方城市"):
            perform_hero_ritual(
                world,
                faction_id=faction_id,
                city_id=enemy_city.city_id,
                issuer_office_id=lord.office_id,
            )
        with self.assertRaisesRegex(StrategyError, "只有主公或城主"):
            perform_hero_ritual(
                world,
                faction_id=faction_id,
                city_id=local_city.city_id,
                issuer_office_id=general.office_id,
            )
        with self.assertRaisesRegex(StrategyError, "自己所辖城市"):
            perform_hero_ritual(
                world,
                faction_id=faction_id,
                city_id=other_own_city.city_id,
                issuer_office_id=governor.office_id,
            )
        local_city.resources.ether = 29
        with self.assertRaisesRegex(StrategyError, "需要 30 以太"):
            perform_hero_ritual(
                world,
                faction_id=faction_id,
                city_id=local_city.city_id,
                issuer_office_id=governor.office_id,
            )
        local_city.resources.ether = 100
        for hero in world.strategic_heroes:
            if hero.status == "roaming" and hero.faction_id is None:
                hero.controller_type = "player"
        with self.assertRaisesRegex(StrategyError, "没有可被召唤"):
            perform_hero_ritual(
                world,
                faction_id=faction_id,
                city_id=local_city.city_id,
                issuer_office_id=governor.office_id,
            )

        subordinate = next(
            hero
            for hero in world.strategic_heroes
            if hero.faction_id == faction_id and hero.office_id and hero.office_id != lord.office_id
        )
        with self.assertRaisesRegex(StrategyError, "只有本势力主公"):
            unbind_strategic_hero(
                world,
                faction_id=faction_id,
                hero_code=subordinate.hero_code,
                issuer_office_id=general.office_id,
            )
        with self.assertRaisesRegex(StrategyError, "没有绑定本势力祭祀场"):
            unbind_strategic_hero(
                world,
                faction_id=faction_id,
                hero_code="missing",
                issuer_office_id=lord.office_id,
            )
        lord_hero = next(hero for hero in world.strategic_heroes if hero.office_id == lord.office_id)
        with self.assertRaisesRegex(StrategyError, "主公不能解除自己"):
            unbind_strategic_hero(
                world,
                faction_id=faction_id,
                hero_code=lord_hero.hero_code,
                issuer_office_id=lord.office_id,
            )

    def test_general_registered_units_enter_battle_and_capture_unbinds_ritual_heroes(self) -> None:
        world = generate_random_world(seed=748, city_count=4, faction_count=2)
        pair = next(
            (source, target)
            for source in world.cities
            for target in world.cities
            if source.owner_faction_id != target.owner_faction_id
            and target.node_id in next(node for node in world.nodes if node.node_id == source.node_id).connected_node_ids
        )
        source, target = pair
        faction_id = source.owner_faction_id
        grand = next(item for item in world.offices if item.faction_id == faction_id and item.office_type == "grand_general")
        general = next(item for item in world.offices if item.parent_office_id == grand.office_id and item.office_type == "general")
        general.unit_inventory = {"cavalry": 3}
        source.resources.troops = 0
        target.resources.troops = 0
        target.defense = 0
        target.support_by_faction[target.owner_faction_id] = 0
        bound = next(
            hero
            for hero in world.strategic_heroes
            if hero.faction_id == target.owner_faction_id and hero.office_id and "lord" not in hero.office_id
        )
        bound.ritual_city_id = target.city_id
        target_lord = next(
            item
            for item in world.offices
            if item.faction_id == target.owner_faction_id and item.office_type == "lord"
        )
        loyalty_before = bound.loyalty
        resolved = declare_city_attack(
            world,
            faction_id=faction_id,
            source_city_id=source.city_id,
            target_city_id=target.city_id,
            resolution_mode="quick",
            attacker_office_id=general.office_id,
        )
        battle = resolved.pending_battles[-1]
        updated_general = next(item for item in resolved.offices if item.office_id == general.office_id)
        released = next(item for item in resolved.strategic_heroes if item.hero_code == bound.hero_code)
        self.assertEqual(battle.attacker_registered_units, {"cavalry": 3})
        self.assertEqual(updated_general.unit_inventory, {"cavalry": 2})
        self.assertEqual(released.status, "roaming")
        self.assertIsNone(released.ritual_city_id)
        self.assertEqual(released.loyalty, max(0, loyalty_before - 10))
        self.assertEqual(released.relationships[target_lord.holder_id], -10)
        self.assertTrue(any(row.get("event") == "ritual_city_lost" for row in released.personal_history))
        self.assertTrue(any(event.category == "hero_ritual_unbound_on_capture" for event in resolved.event_log))


class StrategySimulationTests(unittest.TestCase):
    def test_city_month_forecast_matches_deterministic_economy_settlement(self) -> None:
        world = generate_random_world(seed=82, city_count=4, faction_count=2)
        world.story_events = []
        world.scheduled_consequences = []
        city = world.cities[0]
        before = city.resources.to_dict()

        forecast = forecast_city_month(city)
        advanced = advance_month(world)
        actual = next(item for item in advanced.cities if item.city_id == city.city_id)

        self.assertEqual(forecast["resources_after"], actual.resources.to_dict())
        self.assertEqual(forecast["resource_delta"], {
            key: actual.resources.to_dict()[key] - before[key]
            for key in before
        })
        self.assertEqual(forecast["support_after"], actual.support_by_faction[actual.owner_faction_id])

    def test_monthly_report_persists_changes_and_public_cycle_filters_by_faction(self) -> None:
        world = generate_random_world(seed=83, city_count=4, faction_count=2)
        advanced = advance_month(world)
        report_world = record_monthly_report(
            world,
            advanced,
            resolved_actions=[{
                "action_type": "set_city_policy",
                "action_key": world.cities[0].city_id,
                "faction_id": world.cities[0].owner_faction_id,
                "payload": {"city_id": world.cities[0].city_id, "policy": "粮食优先"},
            }],
        )
        restored = WorldState.from_dict(report_world.to_dict())
        faction_id = world.cities[0].owner_faction_id
        cycle = monthly_cycle_public(restored, [])

        self.assertEqual(restored.monthly_reports[-1]["month"], advanced.current_month)
        self.assertTrue(cycle[faction_id]["previous_month"]["city_changes"])
        self.assertEqual(cycle[faction_id]["previous_month"]["resolved_actions"][0]["action_type"], "set_city_policy")
        self.assertTrue(cycle[faction_id]["advance_forecast"]["cities"])
        self.assertIn("战争", cycle[faction_id]["advance_forecast"]["disclaimer"])

    def test_neutral_city_state_never_attacks_without_incitement_and_attacks_once_after_it(self) -> None:
        world = generate_random_world(seed=7, city_count=8, faction_count=2, neutral_city_states=True)
        owners_before = {city.city_id: city.owner_faction_id for city in world.cities}

        passive = apply_strategy_ai_monthly_actions(
            world,
            controlled_faction_ids={"faction_1", "faction_2"},
        )
        self.assertEqual(owners_before, {city.city_id: city.owner_faction_id for city in passive.cities})
        self.assertFalse(any(
            battle.attacker_faction_id.startswith("neutral_city_state_")
            for battle in passive.pending_battles
        ))

        neutral_id = _neutral_bordering_faction(passive, "faction_2")
        incited = incite_neutral_city_state(
            passive,
            instigator_faction_id="faction_1",
            neutral_faction_id=neutral_id,
            target_faction_id="faction_2",
        )
        self.assertEqual(
            next(faction for faction in incited.factions if faction.faction_id == "faction_1").resources.money,
            next(faction for faction in passive.factions if faction.faction_id == "faction_1").resources.money - 60,
        )
        self.assertEqual(
            next(faction for faction in incited.factions if faction.faction_id == neutral_id).relations["faction_1"],
            -20,
        )
        acted = apply_strategy_ai_monthly_actions(
            incited,
            controlled_faction_ids={"faction_1", "faction_2"},
        )
        neutral = next(faction for faction in acted.factions if faction.faction_id == neutral_id)
        self.assertIsNone(neutral.incited_against_faction_id)
        self.assertIsNone(neutral.incited_by_faction_id)
        self.assertTrue(any(event.category == "neutral_city_state_incitement_spent" for event in acted.event_log))

    def test_major_ai_uses_shared_diplomacy_rules_for_acceptance_and_refusal(self) -> None:
        world = generate_random_world(seed=7, city_count=8, faction_count=2, neutral_city_states=True)
        accepted = apply_strategy_ai_monthly_actions(
            world,
            controlled_faction_ids={"faction_1"},
            enable_attacks=False,
        )
        self.assertTrue(any(event.category == "neutral_diplomacy_accepted" for event in accepted.event_log))
        self.assertTrue(any(
            event.category == "strategy_ai_political_decision"
            and any(item.startswith("neutral_city_state_") for item in event.related_ids)
            for event in accepted.event_log
        ))

        hostile = generate_random_world(seed=7, city_count=8, faction_count=2, neutral_city_states=True)
        for neutral in hostile.factions:
            if neutral.is_neutral_city_state:
                neutral.relations["faction_2"] = -30
        refused, remaining, actions, _ = apply_major_political_ai_actions(
            hostile,
            faction_id="faction_2",
            command_remaining=4,
            attack_reserve=0,
            strategic_goal={"goal_type": "border_defense"},
        )
        self.assertEqual(remaining, 3)
        self.assertTrue(any(action.endswith(":non_aggression") for action in actions))
        self.assertTrue(any(event.category == "neutral_diplomacy_refused" for event in refused.event_log))

    def test_major_ai_prioritizes_pending_occupation_and_formal_rebellion(self) -> None:
        world = generate_random_world(seed=7, city_count=8, faction_count=2, neutral_city_states=True)
        captured = next(city for city in world.cities if city.owner_faction_id == "neutral_city_state_4")
        previous_owner = captured.owner_faction_id
        captured.owner_faction_id = "faction_2"
        captured.support_by_faction["faction_2"] = 30
        mark_city_captured(
            world,
            city_id=captured.city_id,
            previous_owner_faction_id=previous_owner,
            occupier_faction_id="faction_2",
        )
        crisis = next(city for city in world.cities if city.owner_faction_id == "faction_2" and city.city_id != captured.city_id)
        crisis.support_by_faction["faction_2"] = 15
        crisis.resources.troops = 100
        set_rebellion_force_troops(crisis, 300, month=world.current_month)

        updated = apply_strategy_ai_monthly_actions(
            world,
            controlled_faction_ids={"faction_1"},
            enable_attacks=False,
        )
        captured_after = next(city for city in updated.cities if city.city_id == captured.city_id)
        crisis_after = next(city for city in updated.cities if city.city_id == crisis.city_id)
        self.assertEqual((captured_after.occupation["status"], captured_after.occupation["policy_id"]), ("active", "autonomy"))
        self.assertEqual(rebellion_force_troops(crisis_after), 180)
        self.assertTrue(any(event.category == "occupation_policy_selected" for event in updated.event_log))
        self.assertTrue(any(
            event.category == "strategy_ai_plan"
            and any(item.startswith("occupation:") for item in event.related_ids)
            and any(item.endswith(":negotiate") for item in event.related_ids)
            for event in updated.event_log
        ))

    def test_major_ai_completes_legal_peaceful_integration_before_routine_spending(self) -> None:
        world = generate_random_world(seed=7, city_count=8, faction_count=2, neutral_city_states=True)
        actor = next(item for item in world.factions if item.faction_id == "faction_2")
        actor.resources.money = 500
        actor.resources.food = 500
        neutral = next(
            item for item in world.factions
            if item.is_neutral_city_state
            and peaceful_integration_option(
                world,
                actor_faction_id=actor.faction_id,
                neutral_faction_id=item.faction_id,
            )["requirements"][1]["met"]
        )
        city = next(item for item in world.cities if item.owner_faction_id == neutral.faction_id)
        neutral.relations[actor.faction_id] = 65
        neutral.influence_by_faction[actor.faction_id] = 65
        city.support_by_faction[actor.faction_id] = 65
        world.diplomatic_agreements.append(DiplomaticAgreement(
            agreement_id="ai-fulfilled-for-integration",
            agreement_type="non_aggression",
            major_faction_id=actor.faction_id,
            neutral_faction_id=neutral.faction_id,
            started_month=1,
            expires_month=4,
            ended_month=4,
            status="ended",
            end_reason="fulfilled",
        ))

        updated = apply_strategy_ai_monthly_actions(
            world,
            controlled_faction_ids={"faction_1"},
            enable_attacks=False,
        )
        city_after = next(item for item in updated.cities if item.city_id == city.city_id)
        self.assertEqual(city_after.owner_faction_id, actor.faction_id)
        self.assertTrue(any(event.category == "neutral_city_state_peacefully_integrated" for event in updated.event_log))
        self.assertTrue(any(
            event.category == "strategy_ai_plan"
            and f"peaceful_integration:{neutral.faction_id}" in event.related_ids
            for event in updated.event_log
        ))

    def test_major_ai_can_fund_resistance_in_an_enemy_occupation(self) -> None:
        world = generate_random_world(seed=7, city_count=8, faction_count=2, neutral_city_states=True)
        city = next(item for item in world.cities if item.owner_faction_id == "neutral_city_state_3")
        previous_owner = city.owner_faction_id
        city.owner_faction_id = "faction_1"
        city.support_by_faction["faction_1"] = 25
        city.support_by_faction["faction_2"] = 45
        mark_city_captured(
            world,
            city_id=city.city_id,
            previous_owner_faction_id=previous_owner,
            occupier_faction_id="faction_1",
        )
        sponsor = next(item for item in world.factions if item.faction_id == "faction_2")
        sponsor.resources.money = 300

        updated, remaining, actions, _ = apply_major_political_ai_actions(
            world,
            faction_id=sponsor.faction_id,
            command_remaining=4,
            attack_reserve=0,
            strategic_goal={"goal_type": "capture_city"},
        )
        city_after = next(item for item in updated.cities if item.city_id == city.city_id)
        self.assertLessEqual(remaining, 3)
        self.assertIn(f"fund_rebellion:{city.city_id}", actions)
        self.assertEqual(rebellion_force_troops(city_after), 120)
        self.assertTrue(any(event.category == "rebellion_external_funding" for event in updated.event_log))
    def test_strategy_ai_monthly_actions_skip_player_factions_and_unlock_affordable_tech(self) -> None:
        world = generate_random_world(seed=36, city_count=4, faction_count=2)
        player_faction = next(faction for faction in world.factions if faction.faction_id == "faction_1")
        ai_faction = next(faction for faction in world.factions if faction.faction_id == "faction_2")
        player_faction.resources.money = 1000
        ai_faction.resources.money = 1000
        ai_faction.resources.ether = 100
        ai_city = next(city for city in world.cities if city.owner_faction_id == "faction_2")
        ai_city.resources.food = 0

        updated = apply_strategy_ai_monthly_actions(
            world,
            controlled_faction_ids={"faction_1"},
            enable_attacks=False,
        )
        updated_player = next(faction for faction in updated.factions if faction.faction_id == "faction_1")
        updated_ai = next(faction for faction in updated.factions if faction.faction_id == "faction_2")
        updated_ai_city = next(city for city in updated.cities if city.city_id == ai_city.city_id)

        self.assertEqual(updated_player.tactic_techs, [])
        self.assertEqual(updated_ai.tactic_techs, ["local_militia"])
        self.assertNotEqual(updated_ai_city.policy, ai_city.policy)
        self.assertTrue(any(event.category == "strategy_ai_plan" for event in updated.event_log))
        self.assertFalse(
            any(
                event.category == "strategy_ai_plan" and "faction_1" in event.related_ids
                for event in updated.event_log
            )
        )

    def test_bounded_campaign_ai_keeps_visible_food_goal_for_two_months_then_reassesses(self) -> None:
        world = generate_random_world(
            seed=246,
            city_count=8,
            faction_count=2,
            neutral_city_states=True,
            campaign_contract=first_campaign_contract(),
        )
        ai_city = next(city for city in world.cities if city.owner_faction_id == "faction_2")
        ai_city.resources.food = 0

        month_one = apply_strategy_ai_monthly_actions(world, controlled_faction_ids={"faction_1"}, enable_attacks=False)
        public_one = ai_strategic_goals_public(month_one)
        ai_goal = next(row for row in public_one if row["faction_id"] == "faction_2")

        self.assertEqual(ai_goal["goal_type"], "stabilize_food")
        self.assertEqual(ai_goal["duration_months"], 2)
        self.assertEqual(ai_goal["target_city_id"], ai_city.city_id)
        self.assertIn("粮", ai_goal["title"])
        self.assertTrue(any(event.category == "strategy_ai_goal_selected" for event in month_one.event_log))

        month_one.current_month = 2
        month_two = apply_strategy_ai_monthly_actions(month_one, controlled_faction_ids={"faction_1"}, enable_attacks=False)
        same_goal = next(row for row in ai_strategic_goals_public(month_two) if row["faction_id"] == "faction_2")
        self.assertEqual(same_goal["id"], ai_goal["id"])
        self.assertEqual(same_goal["months_remaining"], 1)

        month_two.current_month = 3
        month_three = apply_strategy_ai_monthly_actions(month_two, controlled_faction_ids={"faction_1"}, enable_attacks=False)
        replacement = next(row for row in ai_strategic_goals_public(month_three) if row["faction_id"] == "faction_2")
        self.assertNotEqual(replacement["id"], ai_goal["id"])
        self.assertIsNotNone(replacement["previous_goal"])
        self.assertIn("到期", replacement["change_reason"])

    def test_bounded_campaign_ai_capture_goal_drives_matching_legal_attack(self) -> None:
        world = generate_random_world(
            seed=247,
            city_count=8,
            faction_count=2,
            neutral_city_states=True,
            campaign_contract=first_campaign_contract(),
        )
        source = next(city for city in world.cities if city.owner_faction_id == "faction_2")
        source.resources.food = 10000
        source.resources.troops = 5000
        source.defense = 100
        source.building_levels["ritual_site"] = 0
        for city in world.cities:
            if city.owner_faction_id != "faction_2":
                city.resources.troops = 10
                city.defense = 0

        updated = apply_strategy_ai_monthly_actions(world, controlled_faction_ids={"faction_1"})
        goal = next(row for row in ai_strategic_goals_public(updated) if row["faction_id"] == "faction_2")
        battle = next(battle for battle in updated.pending_battles if battle.attacker_faction_id == "faction_2")

        self.assertEqual(goal["goal_type"], "capture_city")
        self.assertEqual(battle.target_city_id, goal["target_city_id"])
        self.assertEqual(goal["status"], "completed")
        self.assertEqual(goal["progress"], 100)
        self.assertIn("进攻", goal["last_action_summary"])
        self.assertTrue(any(event.category == "strategy_ai_goal_completed" for event in updated.event_log))

    def test_strategy_ai_performs_ritual_when_capacity_exists_and_sets_default_defender(self) -> None:
        world = generate_random_world(seed=39, city_count=4, faction_count=2)
        ai_faction = next(faction for faction in world.factions if faction.faction_id == "faction_2")
        ai_faction.resources.money = 0
        ai_faction.tactic_techs.extend(["local_militia", "command_staff_1"])
        world = ensure_office_system(world)
        ai_city = next(city for city in world.cities if city.owner_faction_id == "faction_2")
        ai_city.resources.ether = 100
        serving = next(
            hero for hero in strategic_hero_pool_public(world)
            if hero["faction_id"] == "faction_2" and hero["status"] == "serving"
        )

        updated = apply_strategy_ai_monthly_actions(
            world,
            controlled_faction_ids={"faction_1"},
            enable_attacks=False,
        )
        self.assertTrue(any(event.category == "hero_ritual_summoned" for event in updated.event_log))
        self.assertEqual(strategic_defender_hero_codes_for_faction(updated, "faction_2"), [serving["code"]])
        self.assertTrue(any(event.category == "strategic_hero_defender_set" for event in updated.event_log))
        self.assertTrue(
            any(
                event.category == "strategy_ai_plan"
                and any(item.startswith("ritual:") for item in event.related_ids)
                and f"defender:{serving['code']}" in event.related_ids
                for event in updated.event_log
            )
        )

    def test_strategy_ai_uses_same_rule_relic_binding_and_records_reason(self) -> None:
        world = generate_random_world(
            seed=622,
            city_count=8,
            faction_count=2,
            neutral_city_states=True,
            campaign_contract=first_campaign_contract(),
        )
        faction = next(item for item in world.factions if item.faction_id == "faction_2")
        city = next(item for item in world.cities if item.city_id == faction.capital_city_id)
        altar = next(item for item in world.relic_altars if item.city_id == city.city_id)
        relic = next(item for item in world.relics if item.state == "scattered")
        relic.state = "stored"
        relic.condition = "intact"
        relic.location_node_id = city.node_id
        relic.location_city_id = city.city_id
        relic.owner_faction_id = faction.faction_id
        city.relics_stored.append(relic.relic_id)
        city.resources.ether = 100

        updated = apply_strategy_ai_monthly_actions(
            world,
            controlled_faction_ids={"faction_1"},
            enable_attacks=False,
        )
        updated_relic = next(item for item in updated.relics if item.relic_id == relic.relic_id)
        updated_altar = next(item for item in updated.relic_altars if item.altar_id == altar.altar_id)

        self.assertEqual(updated_relic.state, "bound_to_altar")
        self.assertEqual(updated_altar.bound_relic_ids, [relic.relic_id])
        self.assertEqual(updated_altar.actions_used, 1)
        self.assertTrue(any(event.category == "strategy_ai_relic_decision" for event in updated.event_log))
        self.assertTrue(
            any(
                event.category == "strategy_ai_plan"
                and any(item.startswith("relic:bind:") for item in event.related_ids)
                for event in updated.event_log
            )
        )

    def test_strategy_ai_delays_proactive_relic_search_but_uses_public_clue_in_month_eight(self) -> None:
        world = generate_random_world(
            seed=622,
            city_count=8,
            faction_count=2,
            neutral_city_states=True,
            campaign_contract=first_campaign_contract(),
        )
        world.current_month = 7
        before = apply_strategy_ai_monthly_actions(
            world,
            controlled_faction_ids={"faction_1"},
            enable_attacks=False,
        )
        self.assertFalse(
            any(
                event.category == "strategy_ai_relic_decision"
                and event.related_ids[0] == "faction_2"
                for event in before.event_log
            )
        )

        world.current_month = 8
        after = apply_strategy_ai_monthly_actions(
            world,
            controlled_faction_ids={"faction_1"},
            enable_attacks=False,
        )
        self.assertTrue(
            any(
                event.category == "strategy_ai_relic_decision"
                and event.related_ids[0] == "faction_2"
                and any(item.startswith("relic:search:") for item in event.related_ids)
                for event in after.event_log
            )
        )

    def test_strategy_ai_reserves_bound_altar_ether_before_ritual(self) -> None:
        world = generate_random_world(
            seed=623,
            city_count=8,
            faction_count=2,
            neutral_city_states=True,
            campaign_contract=first_campaign_contract(),
        )
        faction = next(item for item in world.factions if item.faction_id == "faction_2")
        city = next(item for item in world.cities if item.city_id == faction.capital_city_id)
        altar = next(item for item in world.relic_altars if item.city_id == city.city_id)
        relic = next(item for item in world.relics if item.state == "scattered")
        relic.state = "bound_to_altar"
        relic.condition = "intact"
        relic.location_node_id = city.node_id
        relic.location_city_id = city.city_id
        relic.owner_faction_id = faction.faction_id
        relic.altar_id = altar.altar_id
        altar.bound_relic_ids = [relic.relic_id]
        altar.state = "active"
        city.resources.ether = 35
        for other in world.cities:
            if other.owner_faction_id != faction.faction_id:
                other.resources.troops = 0

        updated = apply_strategy_ai_monthly_actions(
            world,
            controlled_faction_ids={"faction_1"},
            enable_attacks=False,
        )

        self.assertFalse(any(event.category == "hero_ritual_summoned" for event in updated.event_log))
        self.assertGreaterEqual(
            next(item for item in updated.cities if item.city_id == city.city_id).resources.ether,
            relic.maintenance_ether_cost,
        )

    def test_strategy_ai_prioritizes_legal_attack_on_enemy_relic_progress(self) -> None:
        world = generate_random_world(
            seed=624,
            city_count=8,
            faction_count=2,
            neutral_city_states=True,
            campaign_contract=first_campaign_contract(),
        )
        target_faction = next(item for item in world.factions if item.faction_id == "faction_1")
        target = next(item for item in world.cities if item.city_id == target_faction.capital_city_id)
        altar = next(item for item in world.relic_altars if item.city_id == target.city_id)
        relic = next(item for item in world.relics if item.state == "scattered")
        relic.state = "bound_to_altar"
        relic.condition = "intact"
        relic.location_node_id = target.node_id
        relic.location_city_id = target.city_id
        relic.owner_faction_id = target_faction.faction_id
        relic.altar_id = altar.altar_id
        altar.bound_relic_ids = [relic.relic_id]
        altar.state = "active"
        neighbor_node_ids = set(next(node for node in world.nodes if node.node_id == target.node_id).connected_node_ids)
        source = next(city for city in world.cities if city.node_id in neighbor_node_ids)
        source.owner_faction_id = "faction_2"
        source.resources.troops = 10000
        source.resources.food = 10000
        target.resources.troops = 10
        target.defense = 0
        target.support_by_faction[target.owner_faction_id] = 0

        updated = apply_strategy_ai_monthly_actions(
            world,
            controlled_faction_ids={"faction_1"},
        )

        self.assertTrue(
            any(
                battle.attacker_faction_id == "faction_2"
                and battle.target_city_id == target.city_id
                for battle in updated.pending_battles
            )
        )

    def test_strategy_ai_prioritizes_high_rebellion_risk_city_policy(self) -> None:
        world = generate_random_world(seed=40, city_count=4, faction_count=2)
        ai_city = next(city for city in world.cities if city.city_id == "city_4")
        ai_city.support_by_faction["faction_2"] = 5
        ai_city.resources.food = 10000
        ai_city.resources.population = 1200
        ai_city.resources.troops = 1000
        ai_city.policy = next(policy for policy in world.to_public_dict()["policy_choices"] if "稳定" in policy)

        updated = apply_strategy_ai_monthly_actions(
            world,
            controlled_faction_ids={"faction_1"},
            enable_attacks=False,
        )
        updated_city = next(city for city in updated.cities if city.city_id == ai_city.city_id)

        self.assertIn("镇压", updated_city.policy)
        self.assertTrue(
            any(
                event.category == "strategy_ai_plan"
                and any(f"policy:{ai_city.city_id}:" in related_id for related_id in event.related_ids)
                for event in updated.event_log
            )
        )

    def test_strategy_ai_uses_autonomy_when_rebellion_risk_is_high_but_troops_are_low(self) -> None:
        world = generate_random_world(seed=401, city_count=4, faction_count=2)
        ai_city = next(city for city in world.cities if city.city_id == "city_4")
        ai_city.support_by_faction["faction_2"] = 5
        ai_city.resources.food = 10000
        ai_city.resources.population = 1200
        ai_city.resources.troops = 10
        ai_city.policy = next(policy for policy in world.to_public_dict()["policy_choices"] if "稳定" in policy)

        updated = apply_strategy_ai_monthly_actions(
            world,
            controlled_faction_ids={"faction_1"},
            enable_attacks=False,
        )
        updated_city = next(city for city in updated.cities if city.city_id == ai_city.city_id)

        self.assertIn("自治", updated_city.policy)

    def test_strategy_ai_can_launch_resolved_quick_attack(self) -> None:
        world = generate_random_world(seed=37, city_count=4, faction_count=2)
        ai_faction = next(faction for faction in world.factions if faction.faction_id == "faction_2")
        hero = next(
            hero
            for hero in strategic_hero_pool_public(world)
            if hero["faction_id"] == "faction_2" and hero["status"] == "serving"
        )
        source = next(city for city in world.cities if city.city_id == "city_2")
        target = next(city for city in world.cities if city.city_id == "city_1")
        source.resources.troops = 3000
        target.resources.troops = 20
        target.defense = 0

        updated = apply_strategy_ai_monthly_actions(world, controlled_faction_ids={"faction_1"})
        battle = updated.pending_battles[-1]
        updated_target = next(city for city in updated.cities if city.city_id == target.city_id)

        self.assertEqual(battle.attacker_faction_id, "faction_2")
        self.assertEqual(battle.resolution_mode, "quick")
        self.assertEqual(battle.status, "resolved")
        self.assertIn(hero["code"], battle.attacker_hero_codes)
        self.assertEqual(updated_target.owner_faction_id, "faction_2")
        plan = next(
            event
            for event in updated.event_log
            if event.category == "strategy_ai_plan" and event.related_ids[0] == "faction_2"
        )
        command_used = sum(
            2 if action.startswith("attack:") else 0 if action.startswith(("defender:", "duty:")) else 1
            for action in plan.related_ids[1:]
        )
        self.assertLessEqual(command_used, FACTION_MONTHLY_COMMAND_POINTS)
        self.assertTrue(any(action.startswith("attack:") for action in plan.related_ids))

    def test_strategy_ai_stations_heroes_and_can_levy_or_build(self) -> None:
        world = generate_random_world(seed=402, city_count=4, faction_count=2)
        for city in world.cities:
            if city.owner_faction_id != "faction_2":
                continue
            city.resources.troops = 40
            city.resources.population = 2400
            city.resources.food = 500
            city.resources.money = 300
        updated = apply_strategy_ai_monthly_actions(
            world,
            controlled_faction_ids={"faction_1"},
            enable_attacks=False,
        )
        plan = next(
            event
            for event in updated.event_log
            if event.category == "strategy_ai_plan" and event.related_ids[0] == "faction_2"
        )
        serving = [
            hero
            for hero in updated.strategic_heroes
            if hero.faction_id == "faction_2" and hero.status == "serving"
        ]
        stationed = [hero for hero in serving if hero.assignment_type in {"garrison", "campaign"} and hero.city_id]
        self.assertTrue(stationed or any(action.startswith("duty:") for action in plan.related_ids))
        self.assertTrue(
            any(action.startswith(("duty:", "levy:", "build:")) for action in plan.related_ids)
            or any(event.category in {"strategic_hero_assignment", "city_garrison_levied", "city_building_constructed"} for event in updated.event_log)
        )

    def test_strategy_ai_skips_exiled_factions(self) -> None:
        world = generate_random_world(seed=38, city_count=4, faction_count=2)
        for city in world.cities:
            city.owner_faction_id = "faction_1"
        exiled = next(faction for faction in world.factions if faction.faction_id == "faction_2")
        exiled.resources.money = 1000
        exiled.resources.ether = 100

        updated = apply_strategy_ai_monthly_actions(world, controlled_faction_ids={"faction_1"})
        updated_exiled = next(faction for faction in updated.factions if faction.faction_id == "faction_2")

        self.assertEqual(updated_exiled.tactic_techs, [])
        self.assertFalse(any(event.category == "strategy_ai_plan" for event in updated.event_log))

    def test_advance_month_applies_policy_income_upkeep_and_event_log(self) -> None:
        world = generate_random_world(seed=31, city_count=4, faction_count=2)
        city = world.cities[0]
        city.policy = "粮食优先"
        before_food = city.resources.food
        before_money = city.resources.money

        advanced = advance_month(world)
        advanced_city = advanced.cities[0]

        self.assertEqual(world.current_month, 1)
        self.assertEqual(advanced.current_month, 2)
        self.assertGreater(advanced_city.resources.food, before_food)
        self.assertGreater(advanced_city.resources.money, before_money)
        self.assertIn("month_2_resolved", advanced.memory_tags)
        self.assertTrue(any(event.category == "city_income" for event in advanced.event_log))

    def test_advance_month_records_strategic_status_events(self) -> None:
        world = generate_random_world(seed=35, city_count=4, faction_count=2)
        for city in world.cities:
            city.owner_faction_id = "faction_1"

        advanced = advance_month(world)

        self.assertIn("exile:faction_2", advanced.memory_tags)
        self.assertIn("victory:unify_cities:faction_1", advanced.memory_tags)
        self.assertTrue(any(event.category == "faction_exiled" for event in advanced.event_log))
        self.assertTrue(any(event.category == "victory_achieved" for event in advanced.event_log))

    def test_advance_month_records_food_shortage_and_rebellion_risk(self) -> None:
        world = generate_random_world(seed=32, city_count=4, faction_count=2)
        city = world.cities[0]
        city.policy = "征兵优先"
        city.resources.food = 0
        city.resources.population = 5000
        city.resources.troops = 100000
        city.support_by_faction[city.owner_faction_id] = 25

        advanced = advance_month(world)
        advanced_city = advanced.cities[0]

        self.assertEqual(advanced_city.resources.food, 0)
        self.assertTrue(any(state.startswith("rebellion_risk:") for state in advanced_city.event_states))
        self.assertTrue(any(event.category == "city_crisis" for event in advanced.event_log))
        self.assertTrue(any(event.category == "rebellion" for event in advanced.event_log))

    def test_advance_month_formal_rebellion_creates_rebel_force_and_losses(self) -> None:
        world = generate_random_world(seed=333, city_count=4, faction_count=2)
        city = world.cities[0]
        owner_id = city.owner_faction_id
        city.policy = "征兵优先"
        city.resources.food = 0
        city.resources.money = 300
        city.resources.population = 8000
        city.resources.troops = 10
        city.support_by_faction[owner_id] = 5
        before_troops = city.resources.troops

        advanced = advance_month(world)
        advanced_city = advanced.cities[0]

        self.assertGreater(rebellion_force_troops(advanced_city), 0)
        self.assertLess(advanced_city.resources.troops, before_troops + 100)
        self.assertLess(advanced_city.support_by_faction[owner_id], 5)
        self.assertGreater(advanced_city.support_by_faction["local_autonomy"], 45)
        self.assertTrue(any(state.startswith("rebellion_crisis:") for state in advanced_city.event_states))
        self.assertTrue(any(state.startswith("rebellion_force:") for state in advanced_city.event_states))
        self.assertTrue(any(event.category == "rebellion_uprising" for event in advanced.event_log))

    def test_formal_rebellion_force_persists_and_grows_across_months(self) -> None:
        world = generate_random_world(seed=334, city_count=4, faction_count=2)
        city = world.cities[0]
        city.policy = "征兵优先"
        city.resources.food = 0
        city.resources.population = 8000
        city.resources.troops = 10
        city.support_by_faction[city.owner_faction_id] = 5

        first = advance_month(world)
        first_force = rebellion_force_troops(first.cities[0])
        first.cities[0].support_by_faction[first.cities[0].owner_faction_id] = 0
        first.cities[0].resources.food = 0
        first.cities[0].resources.troops = 10
        second = advance_month(first)

        self.assertGreater(first_force, 0)
        self.assertGreater(rebellion_force_troops(second.cities[0]), first_force)

    def test_rebellion_risk_reflects_policy_and_shortage(self) -> None:
        world = generate_random_world(seed=33, city_count=4, faction_count=2)
        city = world.cities[0]
        city.support_by_faction[city.owner_faction_id] = 35
        city.resources.troops = 1

        city.policy = "稳定优先"
        stable_risk = rebellion_risk(city, food_shortage=False)
        shortage_risk = rebellion_risk(city, food_shortage=True)
        city.policy = "镇压优先"
        suppression_risk = rebellion_risk(city, food_shortage=True)

        self.assertGreater(shortage_risk, stable_risk)
        self.assertLess(suppression_risk, shortage_risk)

    def test_suppress_rebellion_action_reduces_and_can_clear_rebel_force(self) -> None:
        world = generate_random_world(seed=335, city_count=4, faction_count=2)
        city = world.cities[0]
        city.resources.troops = 500
        city.event_states.append("rebellion_force:100:month:1")

        suppressed = apply_rebellion_action(
            world,
            faction_id=city.owner_faction_id,
            action_id="suppress",
            city_id=city.city_id,
        )
        suppressed_city = suppressed.cities[0]

        self.assertEqual(suppressed_city.resources.troops, 380)
        self.assertEqual(rebellion_force_troops(suppressed_city), 0)
        self.assertTrue(any(event.category == "rebellion_suppressed" for event in suppressed.event_log))

    def test_suppress_rebellion_action_reduces_larger_rebel_force(self) -> None:
        world = generate_random_world(seed=336, city_count=4, faction_count=2)
        city = world.cities[0]
        city.resources.troops = 500
        city.event_states.append("rebellion_force:400:month:1")

        suppressed = apply_rebellion_action(
            world,
            faction_id=city.owner_faction_id,
            action_id="suppress",
            city_id=city.city_id,
        )

        self.assertEqual(rebellion_force_troops(suppressed.cities[0]), 160)

    def test_rebellion_battle_can_clear_rebel_force(self) -> None:
        world = generate_random_world(seed=337, city_count=4, faction_count=2)
        city = world.cities[0]
        city.resources.troops = 500
        city.defense = 4
        city.support_by_faction[city.owner_faction_id] = 50
        city.support_by_faction["local_autonomy"] = 35
        city.event_states.append("rebellion_force:120:month:1")

        resolved = apply_rebellion_battle(
            world,
            faction_id=city.owner_faction_id,
            city_id=city.city_id,
            troops=160,
        )
        resolved_city = resolved.cities[0]

        self.assertEqual(rebellion_force_troops(resolved_city), 0)
        self.assertLess(resolved_city.resources.troops, 500)
        self.assertGreater(resolved_city.support_by_faction[city.owner_faction_id], 50)
        self.assertTrue(any(event.category == "rebellion_battle" for event in resolved.event_log))
        self.assertTrue(any(event.category == "rebellion_suppressed" for event in resolved.event_log))

    def test_rebellion_battle_failure_reduces_but_keeps_rebel_force(self) -> None:
        world = generate_random_world(seed=338, city_count=4, faction_count=2)
        city = world.cities[0]
        city.resources.troops = 90
        city.defense = 0
        city.support_by_faction[city.owner_faction_id] = 50
        city.support_by_faction["local_autonomy"] = 90
        city.event_states.append("rebellion_force:300:month:1")

        resolved = apply_rebellion_battle(
            world,
            faction_id=city.owner_faction_id,
            city_id=city.city_id,
            troops=50,
        )
        resolved_city = resolved.cities[0]

        self.assertEqual(rebellion_force_troops(resolved_city), 275)
        self.assertEqual(resolved_city.resources.troops, 57)
        self.assertLess(resolved_city.support_by_faction[city.owner_faction_id], 50)
        self.assertFalse(any(event.category == "rebellion_suppressed" for event in resolved.event_log))

    def test_rebellion_battle_validation_rejects_wrong_city_or_troops(self) -> None:
        world = generate_random_world(seed=339, city_count=4, faction_count=2)
        city = world.cities[0]
        city.resources.troops = 500

        with self.assertRaises(StrategyError):
            validate_rebellion_battle(world, faction_id=city.owner_faction_id, city_id=city.city_id)

        city.event_states.append("rebellion_force:100:month:1")
        with self.assertRaises(StrategyError):
            validate_rebellion_battle(world, faction_id="faction_2", city_id=city.city_id)
        with self.assertRaises(StrategyError):
            validate_rebellion_battle(world, faction_id=city.owner_faction_id, city_id=city.city_id, troops=20)
        with self.assertRaises(StrategyError):
            validate_rebellion_battle(world, faction_id=city.owner_faction_id, city_id=city.city_id, troops=600)

    def test_rebellion_actions_cost_resources_and_reduce_risk(self) -> None:
        world = generate_random_world(seed=331, city_count=4, faction_count=2)
        city = world.cities[0]
        faction = world.factions[0]
        city.support_by_faction[city.owner_faction_id] = 25
        city.support_by_faction["local_autonomy"] = 50
        city.resources.troops = 500
        faction.resources.money = 200
        before_risk = rebellion_risk(city, food_shortage=False)

        appeased = apply_rebellion_action(
            world,
            faction_id=city.owner_faction_id,
            action_id="appease",
            city_id=city.city_id,
        )
        appeased_city = appeased.cities[0]
        appeased_faction = appeased.factions[0]

        self.assertEqual(appeased_faction.resources.money, 120)
        self.assertEqual(appeased_city.support_by_faction[city.owner_faction_id], 33)
        self.assertLess(rebellion_risk(appeased_city, food_shortage=False), before_risk)
        self.assertTrue(any(state.startswith("rebellion_action:appease") for state in appeased_city.event_states))
        self.assertTrue(any(event.category == "rebellion_action" for event in appeased.event_log))

        suppressed = apply_rebellion_action(
            appeased,
            faction_id=city.owner_faction_id,
            action_id="suppress",
            city_id=city.city_id,
        )
        suppressed_city = suppressed.cities[0]
        self.assertEqual(suppressed_city.resources.troops, 380)
        self.assertEqual(suppressed_city.support_by_faction["local_autonomy"], 41)

    def test_rebellion_action_validation_rejects_wrong_owner_and_costs(self) -> None:
        world = generate_random_world(seed=332, city_count=4, faction_count=2)
        city = world.cities[0]
        world.factions[0].resources.money = 0

        with self.assertRaises(StrategyError):
            validate_rebellion_action(
                world,
                faction_id="faction_2",
                action_id="appease",
                city_id=city.city_id,
            )
        with self.assertRaises(StrategyError):
            apply_rebellion_action(
                world,
                faction_id=city.owner_faction_id,
                action_id="appease",
                city_id=city.city_id,
            )

    def test_public_rebellion_action_choices_are_structured(self) -> None:
        choices = rebellion_action_choices_public()

        self.assertEqual({choice["id"] for choice in choices}, {"appease", "relief_grain", "suppress", "negotiate", "grant_autonomy"})
        self.assertTrue(all(choice["requires_target_city"] for choice in choices))

    def test_occupation_policies_have_distinct_costs_rewards_and_three_month_lifecycle(self) -> None:
        world = generate_random_world(seed=51, city_count=4, faction_count=2)
        _ensure_city_road(world, "city_1", "city_2")
        world.cities[0].resources.troops = 2400
        world.cities[1].resources.troops = 20
        world.cities[1].defense = 0
        captured = declare_city_attack(
            world,
            faction_id="faction_1",
            source_city_id="city_1",
            target_city_id="city_2",
            resolution_mode="quick",
        )
        city = captured.cities[1]
        actor = captured.factions[0]
        actor.resources.money = max(actor.resources.money, 300)
        actor.resources.food = max(actor.resources.food, 300)
        city.resources.troops = max(city.resources.troops, 200)
        pending = occupation_status_public(captured, city.city_id)
        self.assertEqual((pending["status"], pending["income_percent"], pending["rebellion_modifier"]), ("pending", 50, 30))
        self.assertEqual({item["id"] for item in pending["policy_choices"]}, {"autonomy", "integration", "garrison", "plunder"})

        integration = apply_occupation_policy(
            captured, faction_id="faction_1", city_id=city.city_id, policy_id="integration",
        )
        integration_city = integration.cities[1]
        integration_actor = integration.factions[0]
        self.assertEqual((integration_actor.resources.money, integration_actor.resources.food), (actor.resources.money - 100, actor.resources.food - 80))
        self.assertEqual((integration_city.occupation["status"], occupation_status_public(integration, city.city_id)["income_percent"]), ("active", 90))
        with self.assertRaises(StrategyError):
            apply_occupation_policy(integration, faction_id="faction_1", city_id=city.city_id, policy_id="plunder")
        settled = advance_month(advance_month(advance_month(integration)))
        settled_city = settled.cities[1]
        self.assertEqual((settled_city.occupation["status"], settled_city.occupation["settlements_completed"]), ("settled", 3))
        self.assertTrue(any(event.category == "occupation_settled" for event in settled.event_log))

        plunder_source = WorldState.from_dict(captured.to_dict())
        plunder_city = plunder_source.cities[1]
        plunder_actor = plunder_source.factions[0]
        city_money, city_food = plunder_city.resources.money, plunder_city.resources.food
        actor_money, actor_food = plunder_actor.resources.money, plunder_actor.resources.food
        plundered = apply_occupation_policy(
            plunder_source, faction_id="faction_1", city_id=plunder_city.city_id, policy_id="plunder",
        )
        self.assertEqual(plundered.factions[0].resources.money, actor_money + city_money * 40 // 100)
        self.assertEqual(plundered.factions[0].resources.food, actor_food + city_food * 25 // 100)
        self.assertEqual(occupation_status_public(plundered, city.city_id)["rebellion_modifier"], 30)

    def test_external_funding_can_trigger_defection_or_restore_neutral_autonomy(self) -> None:
        world = generate_random_world(seed=7, city_count=8, faction_count=2, neutral_city_states=True)
        city = next(item for item in world.cities if item.owner_faction_id.startswith("neutral_city_state_"))
        neutral_id = city.owner_faction_id
        city.owner_faction_id = "faction_1"
        city.occupation = {
            "status": "pending", "captured_month": 1, "previous_owner_faction_id": neutral_id,
            "occupier_faction_id": "faction_1", "policy_id": "", "settlements_completed": 0,
        }
        city.resources.troops = 50
        city.support_by_faction["faction_1"] = 15
        city.support_by_faction["faction_2"] = 50
        world.factions[1].resources.money = 200

        funded = apply_rebellion_funding(world, sponsor_faction_id="faction_2", city_id=city.city_id)
        funded_city = next(item for item in funded.cities if item.city_id == city.city_id)
        funded_city.support_by_faction["faction_2"] = 60
        defected = advance_month(funded)
        defected_city = next(item for item in defected.cities if item.city_id == city.city_id)
        self.assertEqual(defected_city.owner_faction_id, "faction_2")
        self.assertEqual(defected_city.occupation["outcome"], "rebellion_defection")
        self.assertTrue(any(event.category == "rebellion_defection" for event in defected.event_log))

        autonomy_world = WorldState.from_dict(world.to_dict())
        autonomy_city = next(item for item in autonomy_world.cities if item.city_id == city.city_id)
        autonomy_city.support_by_faction["local_autonomy"] = 80
        from wujiang.strategic.rebellion import set_rebellion_force_troops
        set_rebellion_force_troops(autonomy_city, 200, month=1)
        restored = advance_month(autonomy_world)
        restored_city = next(item for item in restored.cities if item.city_id == city.city_id)
        self.assertEqual(restored_city.owner_faction_id, neutral_id)
        self.assertEqual(restored_city.occupation["outcome"], "autonomy_restored")
        self.assertTrue(any(event.category == "rebellion_autonomy_restored" for event in restored.event_log))

    def test_advance_month_rejects_unknown_policy(self) -> None:
        world = generate_random_world(seed=34, city_count=4, faction_count=2)
        world.cities[0].policy = "不存在的方针"

        with self.assertRaises(StrategyError):
            advance_month(world)


class StrategyVisionTests(unittest.TestCase):
    def test_new_world_starts_with_owned_and_adjacent_vision(self) -> None:
        world = generate_random_world(seed=902, city_count=12, faction_count=3)
        faction_id = world.factions[0].faction_id
        visible = visible_city_ids(world, faction_id)
        owned = {city.city_id for city in world.cities if city.owner_faction_id == faction_id}
        self.assertTrue(owned)
        self.assertTrue(owned <= visible)
        self.assertLess(len(visible), len(world.cities))
        self.assertTrue(explore_options(world, faction_id))

    def test_explore_reveals_one_frontier_city(self) -> None:
        world = generate_random_world(seed=903, city_count=12, faction_count=3)
        faction_id = world.factions[0].faction_id
        option = explore_options(world, faction_id)[0]
        self.assertFalse(city_is_visible(world, faction_id, option["target_city_id"]))
        explored = apply_explore_city(
            world,
            faction_id=faction_id,
            target_city_id=option["target_city_id"],
            from_city_id=option["from_city_id"],
        )
        self.assertTrue(city_is_visible(explored, faction_id, option["target_city_id"]))
        self.assertTrue(any(event.category == "explore" for event in explored.event_log))
        from wujiang.strategic.vision import world_map_bounds

        before = mask_world_public_for_faction(world.to_public_dict(), world, faction_id)
        after = mask_world_public_for_faction(explored.to_public_dict(), explored, faction_id)
        self.assertEqual(before["map_bounds"], after["map_bounds"])
        self.assertEqual(after["map_bounds"], world_map_bounds(world))
        with self.assertRaises(StrategyError):
            apply_explore_city(
                explored,
                faction_id=faction_id,
                target_city_id=option["target_city_id"],
                from_city_id=option["from_city_id"],
            )

    def test_public_mask_hides_unknown_cities_and_other_vision(self) -> None:
        world = generate_random_world(seed=904, city_count=12, faction_count=3)
        left = world.factions[0].faction_id
        right = world.factions[1].faction_id
        left_public = mask_world_public_for_faction(world.to_public_dict(), world, left)
        right_public = mask_world_public_for_faction(world.to_public_dict(), world, right)
        left_known = {city["id"] for city in left_public["cities"] if city.get("visibility") != "hidden"}
        right_known = {city["id"] for city in right_public["cities"] if city.get("visibility") != "hidden"}
        self.assertNotEqual(left_known, right_known)
        self.assertEqual(set(left_public["known_city_ids_by_faction"]), {left})
        hidden = [city for city in left_public["cities"] if city.get("visibility") == "hidden"]
        self.assertTrue(hidden)
        self.assertTrue(all(not city.get("name") for city in hidden))
        self.assertTrue(all(not city.get("owner_faction_id") for city in hidden))
        self.assertLessEqual(len(left_public["cities"]), len(world.cities))
        omitted = {city.city_id for city in world.cities} - {city["id"] for city in left_public["cities"]}
        self.assertTrue(all(city_id not in visible_city_ids(world, left) for city_id in omitted))
        from wujiang.strategic.vision import world_map_bounds

        bounds = world_map_bounds(world)
        self.assertEqual(left_public["map_bounds"], bounds)
        self.assertEqual(world.to_public_dict()["map_bounds"], bounds)

    def test_diplomacy_and_trade_reveal_capitals(self) -> None:
        world = generate_random_world(seed=905, city_count=12, faction_count=3)
        left = world.factions[0]
        right = world.factions[1]
        left.resources.money = max(left.resources.money, 200)
        left.resources.food = max(left.resources.food, 200)
        right.resources.money = max(right.resources.money, 200)
        right.resources.food = max(right.resources.food, 200)
        before = visible_city_ids(world, left.faction_id)
        contacted = apply_faction_diplomacy_action(
            world,
            actor_faction_id=left.faction_id,
            target_faction_id=right.faction_id,
            action_id="gift_money",
        )
        after = visible_city_ids(contacted, left.faction_id)
        capital = right.capital_city_id
        self.assertTrue(capital)
        self.assertIn(capital, after)
        self.assertGreaterEqual(len(after), len(before))

    def test_legacy_save_without_vision_field_keeps_full_map(self) -> None:
        world = generate_random_world(seed=906, city_count=8, faction_count=2)
        raw = world.to_dict()
        raw.pop("known_city_ids_by_faction", None)
        restored = WorldState.from_dict(raw)
        faction_id = restored.factions[0].faction_id
        self.assertEqual(len(visible_city_ids(restored, faction_id)), len(restored.cities))


if __name__ == "__main__":
    unittest.main()
