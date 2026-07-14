from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wujiang.strategy import (  # noqa: E402
    FACTION_MONTHLY_COMMAND_POINTS,
    STRATEGIC_HERO_BATTLE_SLEEP_MONTHS,
    StrategyError,
    StrategyStore,
    active_strategic_hero_codes_for_faction,
    advance_month,
    advance_story_events,
    apply_rebellion_action,
    apply_rebellion_battle,
    apply_strategy_ai_monthly_actions,
    apply_exile_action,
    apply_office_order,
    appoint_strategic_hero_to_office,
    assign_strategic_hero_duty,
    hero_ritual_capacity,
    perform_hero_ritual,
    unbind_strategic_hero,
    accept_hero_recruitment,
    recommend_hero_recruitment,
    attach_battle_room,
    city_troop_conversion,
    choose_player_hero_path,
    declare_city_attack,
    evaluate_strategic_status,
    ensure_office_system,
    ensure_strategic_hero_system,
    faction_command_points,
    generate_random_world,
    monthly_briefings_public,
    issue_hero_recruitment,
    levy_field_troops,
    levy_city_garrison,
    increase_city_troops,
    register_city_soldiers,
    transfer_registered_units,
    request_registered_units,
    approve_registered_unit_request,
    construct_city_building,
    nearby_roaming_hero_codes,
    grand_general_capacity,
    general_capacity_per_grand_general,
    open_monthly_story_events,
    open_spontaneous_allegiance_request,
    rebellion_action_choices_public,
    rebellion_force_troops,
    rebellion_risk,
    record_strategic_status_events,
    resolve_battle_room_result,
    resolve_action_office,
    resolve_story_event,
    roster_for_city_troops,
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
    validate_rebellion_action,
    validate_rebellion_battle,
    validate_summon_strategic_hero,
    validate_story_event_choice,
    validate_exile_action,
)
from wujiang.strategy.models import City, Faction, MapNode, ResourceBundle, StoryEvent, WorldState  # noqa: E402
from wujiang.web.auth import AuthUser  # noqa: E402
from wujiang.heroes.registry import create_battle, list_heroes  # noqa: E402


class StrategyGenerationTests(unittest.TestCase):
    def test_random_world_is_deterministic_and_connected(self) -> None:
        first = generate_random_world(seed=42, city_count=7, faction_count=3)
        second = generate_random_world(seed=42, city_count=7, faction_count=3)

        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(len(first.cities), 7)
        self.assertEqual(len(first.factions), 3)
        self.assertTrue(all(city.troop_features for city in first.cities))

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

    def test_random_world_rejects_invalid_sizes(self) -> None:
        with self.assertRaises(StrategyError):
            generate_random_world(seed=1, city_count=1)
        with self.assertRaises(StrategyError):
            generate_random_world(seed=1, city_count=2, faction_count=0)
        with self.assertRaises(StrategyError):
            generate_random_world(seed=1, city_count=2, faction_count=3)


class StrategyOfficeTests(unittest.TestCase):
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

        rotated = self.store.rotate_join_code(campaign.campaign_id, self.alice.user_id)

        self.assertEqual(len(rotated.join_code), 6)
        self.assertNotEqual(rotated.join_code, old_code)
        with self.assertRaises(StrategyError):
            self.store.join_campaign_by_code(old_code, self.bob)
        joined = self.store.join_campaign_by_code(rotated.join_code, self.bob)
        self.assertEqual(joined.campaign_id, campaign.campaign_id)

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

        with self.assertRaises(StrategyError):
            self.store.join_campaign_by_code(campaign.join_code, self.carol)
        with self.assertRaises(StrategyError):
            self.store.lock_initial_players(campaign.campaign_id, self.bob.user_id)

        locked = self.store.lock_initial_players(campaign.campaign_id, self.alice.user_id)
        self.assertEqual(locked.status, "active")
        self.assertEqual(self.store.join_campaign_by_code(campaign.join_code, self.bob).campaign_id, campaign.campaign_id)
        with self.assertRaises(StrategyError):
            self.store.join_campaign_by_code(campaign.join_code, self.carol)

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

    def test_resume_requires_all_initial_players_online(self) -> None:
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
        self.assertFalse(after_leave.can_resume)
        self.assertEqual(after_leave.missing_initial_user_ids, (1,))

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

    def test_watch_ai_city_attack_waits_for_real_battle_room(self) -> None:
        for mode in ("watch_ai", "ai_auto"):
            with self.subTest(mode=mode):
                world = generate_random_world(seed=55, city_count=4, faction_count=2)
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
        self.assertEqual(resolved.cities[0].resources.troops, 2000)
        self.assertEqual(resolved.cities[1].resources.troops, 60)
        self.assertEqual(battle.battle_result["winner_side"], "defender")
        self.assertEqual(battle.battle_result["loser_side"], "attacker")
        self.assertEqual(battle.battle_result["resolution_source"], "real_grid")
        self.assertFalse(battle.battle_result["city_captured"])
        self.assertEqual(battle.battle_result["lost_troops_by_side"]["attacker"], 400)
        self.assertEqual(battle.battle_result["lost_troops_by_side"]["defender"], 60)
        self.assertEqual(battle.battle_result["remaining_troops_by_side"]["attacker"], 400)
        self.assertEqual(battle.battle_result["remaining_troops_by_side"]["defender"], 60)
        self.assertEqual(battle.battle_result["initial_grid_units_by_side"]["attacker"], 8)
        self.assertEqual(battle.battle_result["initial_grid_units_by_side"]["defender"], 2)
        self.assertEqual(battle.battle_result["surviving_grid_units_by_side"]["attacker"], 4)
        self.assertEqual(battle.battle_result["surviving_grid_units_by_side"]["defender"], 1)
        self.assertIn("Real grid", battle.battle_result["battle_log_summary"])
        self.assertTrue(any("attacker 4/8" in row for row in battle.report))
        self.assertTrue(any("defender 1/2" in row for row in battle.report))
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
        self.assertEqual(public["pending_battles"][-1]["status"], "resolved")


class StrategyObjectiveTests(unittest.TestCase):
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
        self.assertFalse(conditions["world_mainline"]["implemented"])
        self.assertFalse(conditions["relic_altar"]["implemented"])
        self.assertTrue(status["campaign_complete"])
        self.assertEqual(status["winner_faction_ids"], ["faction_1"])

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
        self.assertTrue(any(event.category == "strategic_hero_appointed" for event in appointed.event_log))

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

        self.assertEqual(strategic_hero_deployment_limit(summoned, faction_id), 1)
        with self.assertRaises(StrategyError):
            normalize_strategic_hero_deployment(summoned, faction_id, [str(hero["code"]) for hero in heroes])

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

        pending = declare_city_attack(
            boosted,
            faction_id=faction_id,
            source_city_id=boosted.cities[0].city_id,
            target_city_id=boosted.cities[1].city_id,
            resolution_mode="manual",
            attacker_hero_codes=[str(hero["code"]) for hero in heroes],
        )
        rosters = strategy_battle_rosters(pending, pending.pending_battles[-1])

        self.assertEqual(strategic_hero_deployment_limit(boosted, faction_id), 2)
        self.assertEqual(pending.pending_battles[-1].attacker_hero_codes, [hero["code"] for hero in heroes])
        self.assertTrue(all(hero["code"] in rosters.attacker.roster for hero in heroes))

    def test_configured_strategic_defender_hero_joins_defender_roster(self) -> None:
        world = generate_random_world(seed=79, city_count=4, faction_count=2)
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
        self.assertEqual((state.assignment_type, state.assignment_target_id), ("garrison", city.city_id))
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
            building_id="walls",
            issuer_office_id=governor.office_id,
        )
        built_city = next(item for item in built.cities if item.city_id == city.city_id)
        self.assertIn("walls", built_city.buildings)
        with self.assertRaises(StrategyError):
            construct_city_building(
                built,
                faction_id=faction_id,
                city_id=city.city_id,
                building_id="walls",
                issuer_office_id=governor.office_id,
            )

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

    def test_governor_registers_exact_units_and_building_level_is_tech_gated(self) -> None:
        world = generate_random_world(seed=746, city_count=4, faction_count=2)
        faction_id = "faction_1"
        governor = next(item for item in world.offices if item.faction_id == faction_id and item.office_type == "governor")
        city = next(item for item in world.cities if item.city_id in governor.managed_entity_ids)
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

        next(item for item in registered.factions if item.faction_id == faction_id).tactic_techs.append("civic_architecture_2")
        registered_city.resources.food = registered_city.resources.money = 1000
        upgraded = construct_city_building(
            registered,
            faction_id=faction_id,
            city_id=city.city_id,
            building_id="fields",
            issuer_office_id=governor.office_id,
        )
        self.assertEqual(next(item for item in upgraded.cities if item.city_id == city.city_id).building_levels["fields"], 2)

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
        self.assertTrue(any(event.category == "hero_ritual_unbound_on_capture" for event in resolved.event_log))


class StrategySimulationTests(unittest.TestCase):
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
        self.assertEqual(battle.attacker_hero_codes, [hero["code"]])
        self.assertEqual(updated_target.owner_faction_id, "faction_2")
        plan = next(
            event
            for event in updated.event_log
            if event.category == "strategy_ai_plan" and event.related_ids[0] == "faction_2"
        )
        command_used = sum(
            2 if action.startswith("attack:") else 0 if action.startswith("defender:") else 1
            for action in plan.related_ids[1:]
        )
        self.assertLessEqual(command_used, FACTION_MONTHLY_COMMAND_POINTS)
        self.assertTrue(any(action.startswith("attack:") for action in plan.related_ids))

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

        self.assertEqual({choice["id"] for choice in choices}, {"appease", "relief_grain", "suppress"})
        self.assertTrue(all(choice["requires_target_city"] for choice in choices))

    def test_advance_month_rejects_unknown_policy(self) -> None:
        world = generate_random_world(seed=34, city_count=4, faction_count=2)
        world.cities[0].policy = "不存在的方针"

        with self.assertRaises(StrategyError):
            advance_month(world)


if __name__ == "__main__":
    unittest.main()
