from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from dataclasses import replace
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    import quickjs
except ImportError:  # pragma: no cover - optional dependency guard
    quickjs = None


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wujiang.engine.core import ActionError, DamageContext, HealContext, Position, QueuedAction, ReactionWindow, StatusEffect  # noqa: E402
from wujiang.heroes.excel_roster import KingsInsightField, SkillDisabledStatus  # noqa: E402
from wujiang.heroes.first_five import MedusaSummon  # noqa: E402
from wujiang.heroes.next_five import BloodDanceLockStatus, ErasureCounterStatus  # noqa: E402
from wujiang.heroes.registry import create_battle, create_hero  # noqa: E402
from wujiang.strategy import StrategyStore, declare_city_attack, ensure_office_system, form_or_reinforce_army, shortest_army_route, strategic_hero_pool_public, summon_strategic_hero  # noqa: E402
from wujiang.strategy.models import DiplomaticAgreement  # noqa: E402
from wujiang.strategy.occupation import mark_city_captured  # noqa: E402
from wujiang.web.auth import UserStore  # noqa: E402
from wujiang.web.analytics import AnalyticsStore  # noqa: E402
from wujiang.web.match_history import MatchHistoryStore  # noqa: E402
from wujiang.web.ai import attack_payloads_for_action, build_attack_candidates, build_move_candidates, build_reaction_candidates, build_skill_candidates, choose_chain_reaction, choose_turn_action, choose_turn_bundle_action, difficulty_profile, payload_is_legal, reaction_payload_is_legal, reaction_payloads_for_option  # noqa: E402
from wujiang.web.multiplayer import GameRoom, ROOMS, battle_state_for_viewer  # noqa: E402
import wujiang.web.server as server_module  # noqa: E402
from wujiang.web.server import SESSION, WujiangHandler, configure_public_base_url  # noqa: E402


def primary_hero(battle, player_id: int):
    return next(unit for unit in battle.player_units(player_id) if not unit.is_summon)


def summon_by_code(battle, player_id: int, hero_code: str):
    return next(
        unit
        for unit in battle.player_units(player_id)
        if unit.is_summon and getattr(unit, "hero_code", "") == hero_code
    )


def skill_by_code(unit, skill_code: str):
    return unit.get_skill(skill_code)


def resolve_pending_chain(battle) -> None:
    while battle.pending_chain is not None:
        battle.perform_action({"type": "chain_skip"})


def end_turns(battle, count: int) -> None:
    for _ in range(count):
        battle.perform_action({"type": "end_turn"})


class AIDecisionBehaviorTests(unittest.TestCase):
    def test_ai_skips_damage_only_actions_that_cannot_affect_ellie_after_using_skill(self) -> None:
        # Given FireFuneral has already used an active skill this turn
        battle = create_battle("fire_funeral", "ellie")
        fire = battle.player_units(1)[0]
        ellie = battle.player_units(2)[0]
        fire.position = Position(4, 4)
        ellie.position = Position(5, 4)

        battle.perform_action({"type": "skill", "unit_id": fire.unit_id, "skill_code": "shensu"})

        # When the AI evaluates the rest of that turn
        profile = difficulty_profile("standard")
        attack_candidates = []
        skill_candidates = []
        for action in battle.action_snapshot_for(fire).get("actions", []):
            if action.get("kind") == "attack":
                attack_candidates.extend(build_attack_candidates(battle, fire, action, profile))
            if action.get("kind") == "skill":
                skill_candidates.extend(build_skill_candidates(battle, fire, action, profile, instant_only=False))
        chosen = choose_turn_action(battle, fire, "standard")

        # Then it does not waste an attack or pure damage skill on Ellie Ward
        self.assertEqual([], attack_candidates)
        self.assertNotIn("pierce", [candidate.payload.get("skill_code") for candidate in skill_candidates])
        self.assertNotEqual("attack", chosen.get("type"))
        self.assertNotEqual("pierce", chosen.get("skill_code"))

    def test_ai_keeps_multi_target_skill_when_at_least_one_enemy_can_be_affected(self) -> None:
        # Given a damage area covers Ellie and another enemy after the actor used a skill
        battle = create_battle("fire_funeral", ["ellie", "bard"])
        fire = battle.player_units(1)[0]
        ellie = next(unit for unit in battle.player_units(2) if unit.hero_code == "ellie")
        bard = next(unit for unit in battle.player_units(2) if unit.hero_code == "bard")
        fire.position = Position(4, 4)
        ellie.position = Position(5, 4)
        bard.position = Position(6, 4)

        battle.perform_action({"type": "skill", "unit_id": fire.unit_id, "skill_code": "shensu"})

        # When the AI evaluates Pierce
        profile = difficulty_profile("standard")
        pierce_action = next(action for action in battle.action_snapshot_for(fire)["actions"] if action.get("code") == "pierce")
        candidates = build_skill_candidates(battle, fire, pierce_action, profile, instant_only=False)

        # Then the line that can still hurt Bard remains a valid candidate
        self.assertTrue(any(candidate.payload.get("skill_code") == "pierce" for candidate in candidates))

    def test_ai_keeps_self_targeted_skill_that_actually_hits_enemies(self) -> None:
        # Given Whirlwind Attack targets self but resolves attacks around Li
        battle = create_battle("li", "bard")
        li = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        li.position = Position(4, 4)
        bard.position = Position(5, 4)

        # When the AI evaluates that action
        profile = difficulty_profile("standard")
        whirlwind_action = next(action for action in battle.action_snapshot_for(li)["actions"] if action.get("code") == "whirlwind_attack")
        candidates = build_skill_candidates(battle, li, whirlwind_action, profile, instant_only=False)

        # Then the offensive self-targeted skill is still available to the AI
        self.assertTrue(any(candidate.payload.get("skill_code") == "whirlwind_attack" for candidate in candidates))

    def test_ai_skips_basic_attack_that_cannot_affect_physical_immune_soul_wraith(self) -> None:
        # Given Soul Wraith is adjacent but immune to basic-attack damage and effects
        battle = create_battle("fire_funeral", "soul_wraith")
        fire = battle.player_units(1)[0]
        wraith = battle.player_units(2)[0]
        fire.position = Position(4, 4)
        wraith.position = Position(5, 4)

        # When the AI evaluates FireFuneral's basic attack
        profile = difficulty_profile("standard")
        attack_action = next(action for action in battle.action_snapshot_for(fire)["actions"] if action.get("code") == "attack")
        candidates = build_attack_candidates(battle, fire, attack_action, profile)

        # Then the candidate is filtered out because it has no effective enemy impact
        self.assertEqual([], candidates)

    def test_ai_keeps_basic_attack_that_only_consumes_temporary_shield(self) -> None:
        # Given the enemy has a leftover temporary shield from a protection effect
        battle = create_battle("fire_funeral", "bard")
        fire = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        fire.position = Position(4, 4)
        bard.position = Position(5, 4)
        bard.temporary_shields = 1

        # When the AI evaluates FireFuneral's basic attack assuming no chain
        profile = difficulty_profile("standard")
        attack_action = next(action for action in battle.action_snapshot_for(fire)["actions"] if action.get("code") == "attack")
        candidates = build_attack_candidates(battle, fire, attack_action, profile)

        # Then consuming that shield counts as an effective impact
        self.assertTrue(any(candidate.payload.get("target_unit_id") == bard.unit_id for candidate in candidates))

    def test_ai_keeps_damage_skill_that_only_consumes_temporary_shield(self) -> None:
        # Given the enemy has a leftover temporary shield from a protection effect
        battle = create_battle("fire_funeral", "bard")
        fire = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        fire.position = Position(4, 4)
        bard.position = Position(5, 4)
        bard.temporary_shields = 1

        # When the AI evaluates a hostile damage skill assuming no chain
        profile = difficulty_profile("standard")
        pierce_action = next(action for action in battle.action_snapshot_for(fire)["actions"] if action.get("code") == "pierce")
        candidates = build_skill_candidates(battle, fire, pierce_action, profile, instant_only=False)

        # Then consuming that shield counts as an effective impact
        self.assertTrue(any(candidate.payload.get("skill_code") == "pierce" for candidate in candidates))

    def test_ai_builds_area_attack_candidates_from_pattern_preview(self) -> None:
        # Given an area basic attack declares pattern cells instead of a single target
        battle = create_battle("soul_wraith", "bard")
        wraith = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        wraith.position = Position(4, 4)
        bard.position = Position(5, 4)

        # When the AI evaluates the area basic attack
        profile = difficulty_profile("standard")
        attack_action = next(action for action in battle.action_snapshot_for(wraith)["actions"] if action.get("kind") == "attack")
        candidates = build_attack_candidates(battle, wraith, attack_action, profile)

        # Then pattern-cell attacks produce legal candidates instead of falling back to end turn
        self.assertTrue(any(candidate.payload.get("choice_code") == "right" for candidate in candidates))

    def test_ai_bundle_lets_mount_or_summon_act_when_hero_has_no_action(self) -> None:
        # Given a rider's mounted dragon is controlled in the rider's turn bundle
        battle = create_battle("dragon_rider", "bard")
        rider = primary_hero(battle, 1)
        dragon = summon_by_code(battle, 1, "dragon_mount")
        bard = primary_hero(battle, 2)
        battle.configure_turn_order([rider.unit_id, bard.unit_id], starting_index=0)
        rider.position = Position(2, 2)
        dragon.position = Position(2, 2)
        bard.position = Position(5, 2)
        rider.cannot_attack = True
        rider.cannot_use_skills = True
        rider.cannot_move = True
        dragon.turn_ready = True

        # When the AI chooses for the whole turn bundle
        payload, actor = choose_turn_bundle_action(battle, battle.current_turn_bundle_units(include_banished=False), "standard")

        # Then it selects the mounted dragon instead of ending the hero turn
        self.assertIs(actor, dragon)
        self.assertNotEqual(payload.get("type"), "end_turn")
        self.assertEqual(payload.get("unit_id"), dragon.unit_id)

    def test_ai_does_not_dismount_knight_by_moving_rider_body(self) -> None:
        # Given a mounted knight and mount can both act in the same turn bundle
        battle = create_battle("dragon_rider", "bard")
        rider = primary_hero(battle, 1)
        dragon = summon_by_code(battle, 1, "dragon_mount")
        bard = primary_hero(battle, 2)
        battle.configure_turn_order([rider.unit_id, bard.unit_id], starting_index=0)
        rider.position = Position(1, 1)
        dragon.position = Position(1, 1)
        bard.position = Position(6, 1)
        rider.cannot_attack = True
        rider.cannot_use_skills = True
        dragon.cannot_attack = True
        dragon.cannot_use_skills = True
        dragon.turn_ready = True

        # When the AI chooses movement for the whole mounted turn bundle
        payload, actor = choose_turn_bundle_action(battle, battle.current_turn_bundle_units(include_banished=False), "standard")

        # Then it moves the mount, not the rider body, so riding is preserved
        self.assertIs(actor, dragon)
        self.assertEqual(payload.get("type"), "move")
        battle.perform_action(payload)
        self.assertIs(battle.mounted_unit_for(rider), dragon)
        self.assertEqual(rider.position, dragon.position)

    def test_ai_skips_movement_skills_when_actor_cannot_move(self) -> None:
        # Given Dark Human is under a movement-skill lock such as Blood Dance
        battle = create_battle("dark_human", "bard")
        dark = primary_hero(battle, 1)
        enemy = primary_hero(battle, 2)
        dark.position = Position(4, 4)
        enemy.position = Position(7, 7)
        dark.add_status(BloodDanceLockStatus("test"))

        # When the AI evaluates Fate Kick
        profile = difficulty_profile("standard")
        fate_kick_action = next(action for action in battle.action_snapshot_for(dark)["actions"] if action.get("code") == "fate_kick")
        candidates = build_skill_candidates(battle, dark, fate_kick_action, profile, instant_only=False)
        chosen = choose_turn_action(battle, dark, "standard")

        # Then the movement skill is not considered a legal candidate
        self.assertFalse(fate_kick_action["available"])
        self.assertEqual([], candidates)
        self.assertNotEqual("fate_kick", chosen.get("skill_code"))

    def test_dash_move_skill_is_unavailable_when_actor_cannot_move(self) -> None:
        battle = create_battle("dark_human", "bard")
        dark = primary_hero(battle, 1)
        dark.cannot_move = True

        action = next(action for action in battle.action_snapshot_for(dark)["actions"] if action["code"] == "fly_leap")

        self.assertFalse(action["available"])
        self.assertEqual(action["preview"]["cells"], [])

    def test_mounted_leap_moves_masamune_and_dismounts(self) -> None:
        # Given Masamune is mounted and uses his mounted-only free leap
        battle = create_battle("masamune", "bard")
        masamune = primary_hero(battle, 1)
        mount = summon_by_code(battle, 1, "motor_horse")
        mount.position = Position(2, 2)
        masamune.position = Position(2, 2)

        # When the mounted leap resolves
        battle.perform_action({"type": "skill", "unit_id": masamune.unit_id, "skill_code": "mounted_leap", "x": 5, "y": 2})

        # Then Masamune moves himself and leaves the mount behind
        self.assertEqual(mount.position, Position(2, 2))
        self.assertEqual(masamune.position, Position(5, 2))
        self.assertIsNone(battle.mounted_unit_for(masamune))

    def test_ai_oberon_uses_judgment_stone_and_can_plan_world_seed(self) -> None:
        # Given Oberon can summon Judgment Stone near an enemy
        battle = create_battle("excel_r020", "bard")
        oberon = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        oberon.position = Position(1, 1)
        bard.position = Position(3, 1)
        oberon.current_mana = 5

        # When the AI chooses an action
        payload = choose_turn_action(battle, oberon, "standard")

        # Then Judgment Stone is generated at the nearest legal surrounding cell
        self.assertEqual(payload.get("skill_code"), "judgment_stone")
        self.assertIn((payload.get("x"), payload.get("y")), {(2, 0), (2, 1), (2, 2)})

        profile = difficulty_profile("standard")
        heaven_lock = next(action for action in battle.action_snapshot_for(oberon)["actions"] if action.get("code") == "heaven_lock")
        heaven_candidates = build_skill_candidates(battle, oberon, heaven_lock, profile, instant_only=False)
        self.assertTrue(any(candidate.payload.get("target_unit_id") == bard.unit_id for candidate in heaven_candidates))

        # And on a board large enough for the 5x5 seed plus roots, World Seed has legal AI candidates
        battle = create_battle("excel_r020", ["bard", "ellie"])
        battle.width = 12
        battle.height = 12
        oberon = primary_hero(battle, 1)
        oberon.position = Position(0, 0)
        oberon.current_mana = 5
        world_seed = next(action for action in battle.action_snapshot_for(oberon)["actions"] if action.get("code") == "world_seed")
        world_candidates = build_skill_candidates(battle, oberon, world_seed, profile, instant_only=False)
        self.assertTrue(any(candidate.payload.get("skill_code") == "world_seed" for candidate in world_candidates))

    def test_ai_fei_wang_opens_stance_then_scores_gale_and_large_pierce(self) -> None:
        # Given Fei Wang has an enemy inside Gale and Large Pierce range
        battle = create_battle("excel_r028", "bard")
        fei_wang = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        fei_wang.position = Position(1, 1)
        bard.position = Position(3, 1)
        fei_wang.current_mana = 5

        # Then the AI opens the start-phase stance before spending its main actions
        payload = choose_turn_action(battle, fei_wang, "standard")
        self.assertEqual(payload.get("skill_code"), "inner_dimension_sword")
        battle.perform_action(payload)

        followup = choose_turn_action(battle, fei_wang, "standard")
        self.assertEqual(followup.get("skill_code"), "gale")
        self.assertEqual(followup.get("direction"), "east")

        profile = difficulty_profile("standard")
        actions = {action.get("code"): action for action in battle.action_snapshot_for(fei_wang)["actions"]}
        pierce_candidates = build_skill_candidates(battle, fei_wang, actions["large_pierce_plus"], profile, instant_only=False)
        insight_candidates = build_skill_candidates(battle, fei_wang, actions["kings_insight"], profile, instant_only=False)

        self.assertTrue(pierce_candidates)
        self.assertTrue(any(candidate.score >= profile.action_threshold for candidate in insight_candidates))

    def test_ai_wuchang_scores_mist_and_migratory_bird_mark_without_wasting_mist_control(self) -> None:
        battle = create_battle("excel_r027", "bard")
        wuchang = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        wuchang.position = Position(1, 1)
        bard.position = Position(3, 1)
        bard.max_health = 10
        bard.current_hp = 10

        profile = difficulty_profile("standard")
        actions = {action.get("code"): action for action in battle.action_snapshot_for(wuchang)["actions"]}
        mist_candidates = build_skill_candidates(battle, wuchang, actions["wuchang_mist"], profile, instant_only=False)
        mark_candidates = build_skill_candidates(battle, wuchang, actions["migratory_bird_mark"], profile, instant_only=False)

        self.assertEqual(choose_turn_action(battle, wuchang, "standard").get("skill_code"), "wuchang_mist")
        self.assertTrue(any(candidate.score >= profile.action_threshold for candidate in mist_candidates))
        self.assertTrue(any(candidate.payload.get("skill_code") == "migratory_bird_mark" for candidate in mark_candidates))

        battle.perform_action({"type": "skill", "unit_id": wuchang.unit_id, "skill_code": "wuchang_mist"})
        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})
        actions = {action.get("code"): action for action in battle.action_snapshot_for(wuchang)["actions"]}
        mark_after_mist = build_skill_candidates(battle, wuchang, actions["migratory_bird_mark"], profile, instant_only=False)

        self.assertFalse(any(candidate.score >= profile.action_threshold for candidate in mark_after_mist))

    def test_ai_basic_attack_payload_uses_legal_cell_for_large_targets(self) -> None:
        battle = create_battle("excel_r026", "doomlight_dragon")
        guardian = primary_hero(battle, 1)
        dragon = primary_hero(battle, 2)
        guardian.position = Position(6, 3)
        dragon.position = Position(4, 2)
        dragon.max_health = 10
        dragon.current_hp = 10

        attack_action = next(action for action in battle.action_snapshot_for(guardian)["actions"] if action.get("kind") == "attack")
        payloads = attack_payloads_for_action(battle, guardian, attack_action)

        self.assertTrue(payloads)
        self.assertTrue(all(payload_is_legal(battle, payload) for payload in payloads))
        self.assertTrue(any((payload.get("x"), payload.get("y")) in {(5, 2), (5, 3)} for payload in payloads))

    def test_ai_agency_contract_cancel_payload_has_no_self_target(self) -> None:
        battle = create_battle(["excel_r025", "excel_r026"], "bard")
        mubie = next(unit for unit in battle.hero_units(1) if unit.hero_code == "excel_r025")
        guardian = next(unit for unit in battle.hero_units(1) if unit.hero_code == "excel_r026")
        bard = primary_hero(battle, 2)
        mubie.position = Position(1, 1)
        guardian.position = Position(2, 1)
        bard.position = Position(4, 1)
        battle.configure_turn_order([mubie.unit_id, bard.unit_id], starting_index=0)

        action = next(action for action in battle.action_snapshot_for(mubie)["actions"] if action.get("code") == "agency_contract")
        attach_payload = next(
            candidate
            for candidate in build_skill_candidates(battle, mubie, action, difficulty_profile("standard"), instant_only=False)
            if candidate.payload.get("target_unit_id") == guardian.unit_id
        ).payload
        battle.perform_action(attach_payload)
        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})

        action = next(action for action in battle.action_snapshot_for(mubie)["actions"] if action.get("code") == "agency_contract")
        candidates = build_skill_candidates(battle, mubie, action, difficulty_profile("standard"), instant_only=False)

        self.assertTrue(candidates)
        self.assertTrue(all("target_unit_id" not in candidate.payload for candidate in candidates))
        self.assertTrue(all(payload_is_legal(battle, candidate.payload) for candidate in candidates))

    def test_ai_red_uses_deadly_bow_and_generates_weapon_copy_targets(self) -> None:
        battle = create_battle(["excel_r029", "bard"], "ellie")
        red = next(unit for unit in battle.hero_units(1) if unit.hero_code == "excel_r029")
        ally = next(unit for unit in battle.hero_units(1) if unit.hero_code == "bard")
        enemy = primary_hero(battle, 2)
        red.position = Position(1, 1)
        ally.position = Position(2, 1)
        enemy.position = Position(4, 1)
        red.current_mana = 5
        red.mana_points = 5
        ally.base_stats.attack = 4
        enemy.max_health = 10
        enemy.current_hp = 10

        chosen = choose_turn_action(battle, red, "standard")
        self.assertEqual(chosen.get("skill_code"), "deadly_bow")
        self.assertEqual(chosen.get("direction"), "east")

        profile = difficulty_profile("standard")
        actions = {action.get("code"): action for action in battle.action_snapshot_for(red)["actions"]}
        copy_candidates = build_skill_candidates(battle, red, actions["weapon_copy"], profile, instant_only=False)

        self.assertTrue(any(candidate.payload.get("target_unit_id") == ally.unit_id for candidate in copy_candidates))
        self.assertTrue(all(payload_is_legal(battle, candidate.payload) for candidate in copy_candidates))

    def test_ai_fusion_opens_nuclear_rush_before_pierce(self) -> None:
        battle = create_battle("excel_r030", "bard")
        fusion = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        fusion.position = Position(1, 1)
        bard.position = Position(3, 1)
        bard.max_health = 10
        bard.current_hp = 10

        chosen = choose_turn_action(battle, fusion, "standard")

        self.assertEqual(chosen.get("skill_code"), "nuclear_rush")
        battle.perform_action(chosen)

        profile = difficulty_profile("standard")
        attack_action = next(action for action in battle.action_snapshot_for(fusion)["actions"] if action.get("kind") == "attack")
        payloads = attack_payloads_for_action(battle, fusion, attack_action)
        attack_candidates = build_attack_candidates(battle, fusion, attack_action, profile)

        self.assertTrue(any(payload.get("direction") == "east" for payload in payloads))
        self.assertTrue(any(candidate.payload.get("direction") == "east" for candidate in attack_candidates))
        self.assertTrue(any(candidate.payload.get("direction") == "east" and payload_is_legal(battle, candidate.payload) for candidate in attack_candidates))

    def test_ai_does_not_spend_wall_against_piercing_gale(self) -> None:
        # Given Gale is a shield-piercing forced movement skill aimed at Oberon
        battle = create_battle("excel_r028", "excel_r020")
        fei_wang = primary_hero(battle, 1)
        oberon = primary_hero(battle, 2)
        fei_wang.position = Position(6, 3)
        oberon.position = Position(3, 3)
        fei_wang.current_mana = 5
        oberon.current_mana = 5

        battle.perform_action({"type": "skill", "unit_id": fei_wang.unit_id, "skill_code": "gale", "direction": "west"})
        self.assertIsNotNone(battle.pending_chain)
        options = [option.to_public_dict() for option in battle.pending_chain.options_by_unit.get(oberon.unit_id, [])]

        # Then the reaction AI does not waste Light Wall on an action that pierces shields
        reaction = choose_chain_reaction(battle, oberon, options, "standard")
        self.assertIsNone(reaction)

    def test_reaction_actor_death_during_declaration_does_not_stall_chain(self) -> None:
        # Given King's Insight will defeat Oberon as soon as he declares a passive Light Wall
        battle = create_battle("excel_r028", "excel_r020")
        fei_wang = primary_hero(battle, 1)
        oberon = primary_hero(battle, 2)
        fei_wang.position = Position(1, 1)
        oberon.position = Position(3, 1)
        fei_wang.current_mana = 5
        oberon.current_mana = 5
        oberon.current_hp = 0.75
        battle.add_field_effect(KingsInsightField(fei_wang.player_id))

        # When Oberon chooses Light Wall against Fei Wang's large pierce
        battle.perform_action(
            {
                "type": "skill",
                "unit_id": fei_wang.unit_id,
                "skill_code": "large_pierce_plus",
                "cells": [{"x": 2, "y": 1}, {"x": 3, "y": 1}, {"x": 4, "y": 1}],
            }
        )
        self.assertIsNotNone(battle.pending_chain)
        battle.perform_action(
            {
                "type": "chain_react",
                "unit_id": oberon.unit_id,
                "action_code": "light_wall",
                "target_unit_id": oberon.unit_id,
            }
        )

        # Then the invalidated reaction is skipped and the chain finishes instead of looping on ActionError
        self.assertIsNone(battle.pending_chain)
        self.assertFalse(oberon.alive and oberon.unit_id in battle.units)
        self.assertTrue(any("未能结算" in message for message in battle.logs))

    def test_reaction_payload_builder_skips_stale_shield_preview_targets(self) -> None:
        # Given a shield preview still contains a unit id that was destroyed earlier in the chain
        battle = create_battle("excel_r028", "ellie")
        fei_wang = primary_hero(battle, 1)
        ellie = primary_hero(battle, 2)
        queued = QueuedAction(
            action_type="skill",
            actor_id=fei_wang.unit_id,
            display_name="测试技能",
            speed=1,
            payload={},
            target_unit_ids=[ellie.unit_id],
            target_cells=[],
            source_player_id=fei_wang.player_id,
            hostile=True,
        )
        option = {
            "action_code": "magic_wall",
            "preview": {
                "target_unit_ids": ["destroyed-unit-id", ellie.unit_id],
                "selection": {"mode": "multi_unit", "max_targets": 2},
            },
        }

        # Then AI payload generation keeps the live target and does not raise ActionError for the stale id
        payloads = reaction_payloads_for_option(battle, ellie, queued, option)

        self.assertEqual(payloads[0]["target_unit_ids"], [ellie.unit_id])

    def test_ai_evasion_avoids_cells_still_inside_declared_area(self) -> None:
        # Given an area skill still covers one adjacent evasion destination
        battle = create_battle("doomlight_dragon", "dark_human")
        dragon = primary_hero(battle, 1)
        dark = primary_hero(battle, 2)
        dragon.position = Position(2, 4)
        dark.position = Position(4, 4)
        dark.current_mana = 5
        queued = QueuedAction(
            action_type="skill",
            actor_id=dragon.unit_id,
            display_name="龙息",
            speed=1,
            payload={},
            target_unit_ids=[dark.unit_id],
            target_cells=[Position(4, 4), Position(4, 5)],
            source_player_id=dragon.player_id,
            hostile=True,
        )
        evasion = dark.get_skill("evasion")
        option = {
            "action_code": "evasion",
            "preview": evasion.reaction_preview(battle, dark, queued),
        }

        # When the AI scores evasion candidates
        candidates = build_reaction_candidates(battle, dark, queued, option, difficulty_profile("standard"))
        chosen = max(candidates, key=lambda candidate: candidate.score)

        # Then it does not choose the still-damaging cell inside the declared area
        self.assertNotEqual((chosen.payload.get("x"), chosen.payload.get("y")), (4, 5))

    def test_ai_evasion_avoids_destinations_reserved_by_prior_chain_reactions(self) -> None:
        battle = create_battle(["dark_human", "excel_r030"], "excel_r094")
        dark = next(unit for unit in battle.hero_units(1) if unit.hero_code == "dark_human")
        fusion = next(unit for unit in battle.hero_units(1) if unit.hero_code == "excel_r030")
        attacker = primary_hero(battle, 2)
        dark.position = Position(4, 4)
        fusion.position = Position(7, 4)
        attacker.position = Position(9, 4)
        fusion.current_mana = 5
        queued = QueuedAction(
            action_type="skill",
            actor_id=attacker.unit_id,
            display_name="穿刺",
            speed=1,
            payload={},
            target_unit_ids=[dark.unit_id, fusion.unit_id],
            target_cells=[Position(7, 4), Position(8, 4)],
            source_player_id=attacker.player_id,
            hostile=True,
        )
        battle.pending_chain = ReactionWindow(
            reactive_player_id=1,
            queued_action=queued,
            pending_reactor_ids=[fusion.unit_id],
            options_by_unit={},
            chosen_reactions=[
                QueuedAction(
                    action_type="reaction_skill",
                    actor_id=dark.unit_id,
                    display_name="回避",
                    speed=2,
                    payload={"action_code": "evasion", "x": 8, "y": 5},
                    target_unit_ids=[attacker.unit_id],
                    target_cells=[],
                    source_player_id=dark.player_id,
                    hostile=True,
                )
            ],
        )

        reserved_payload = {"type": "chain_react", "unit_id": fusion.unit_id, "action_code": "evasion", "x": 8, "y": 5}
        open_payload = {"type": "chain_react", "unit_id": fusion.unit_id, "action_code": "evasion", "x": 6, "y": 3}

        self.assertFalse(reaction_payload_is_legal(battle, fusion, queued, reserved_payload))
        self.assertTrue(reaction_payload_is_legal(battle, fusion, queued, open_payload))

    def test_ai_throttles_unlimited_nonhostile_skill_after_one_use(self) -> None:
        battle = create_battle("erasure_apostle", "bard")
        apostle = primary_hero(battle, 1)
        apostle.position = Position(1, 1)
        apostle.current_mana = 5
        actions = {action["code"]: action for action in battle.action_snapshot_for(apostle)["actions"]}
        stealth = apostle.get_skill("stealth")
        profile = difficulty_profile("standard")

        first_candidates = build_skill_candidates(battle, apostle, actions["stealth"], profile, instant_only=False)
        stealth.uses_this_turn = 1
        second_candidates = build_skill_candidates(battle, apostle, actions["stealth"], profile, instant_only=False)

        self.assertTrue(first_candidates)
        self.assertEqual(second_candidates, [])

    def test_ai_enables_sakura_floating_cannon_berserk_on_next_turn(self) -> None:
        battle = create_battle("excel_r034", "bard")
        sakura = primary_hero(battle, 1)
        sakura.position = Position(3, 3)

        battle.perform_action(
            {"type": "skill", "unit_id": sakura.unit_id, "skill_code": "floating_cannons", "x": 2, "y": 2}
        )
        battle.perform_action({"type": "end_turn"})
        while battle.current_turn_unit() is not sakura:
            battle.perform_action({"type": "end_turn"})

        payload, actor = choose_turn_bundle_action(
            battle,
            battle.current_turn_bundle_units(include_banished=False),
            "standard",
        )

        self.assertIs(actor, sakura)
        self.assertEqual(payload.get("skill_code"), "floating_cannon_berserk")
        cannons = [unit for unit in battle.current_turn_bundle_units() if unit.hero_code == "floating_cannon"]
        self.assertTrue(cannons)
        self.assertTrue(all(cannon.turn_ready for cannon in cannons))

    def test_ushioni_counter_requires_enemy_chain_and_ai_uses_awakening(self) -> None:
        from wujiang.heroes.excel_roster import MountainGodCounterStatus

        immune_battle = create_battle("excel_r035", "soul_wraith")
        oni = primary_hero(immune_battle, 1)
        wraith = primary_hero(immune_battle, 2)
        oni.position = Position(1, 1)
        wraith.position = Position(2, 1)

        immune_battle.perform_action(
            {"type": "attack", "unit_id": oni.unit_id, "target_unit_id": wraith.unit_id}
        )

        self.assertFalse(any(isinstance(status, MountainGodCounterStatus) for status in oni.statuses))

        chained_battle = create_battle("excel_r035", "bard")
        oni = primary_hero(chained_battle, 1)
        bard = primary_hero(chained_battle, 2)
        oni.position = Position(1, 1)
        bard.position = Position(2, 1)
        bard.current_mana = 5
        chained_battle.perform_action(
            {"type": "attack", "unit_id": oni.unit_id, "target_unit_id": bard.unit_id}
        )
        chained_battle.perform_action(
            {"type": "chain_react", "unit_id": bard.unit_id, "action_code": "protection"}
        )
        resolve_pending_chain(chained_battle)

        self.assertEqual(sum(isinstance(status, MountainGodCounterStatus) for status in oni.statuses), 1)

        for _ in range(7):
            oni.add_status(MountainGodCounterStatus())
        chosen = choose_turn_action(chained_battle, oni, "standard")
        self.assertEqual(chosen.get("skill_code"), "mountain_awakening")

    def test_batch_13_ai_builds_jirobo_and_remi_special_actions(self) -> None:
        profile = difficulty_profile("standard")
        battle = create_battle("excel_r047", "bard")
        jirobo = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        jirobo.position = Position(1, 1)
        bard.position = Position(4, 1)
        actions = {action["code"]: action for action in battle.action_snapshot_for(jirobo)["actions"]}

        burial = build_skill_candidates(battle, jirobo, actions["hundred_bird_burial"], profile, instant_only=False)

        self.assertTrue(burial)

        bard.position = Position(2, 1)
        battle.perform_action({"type": "attack", "unit_id": jirobo.unit_id, "target_unit_id": bard.unit_id})
        resolve_pending_chain(battle)
        actions = {action["code"]: action for action in battle.action_snapshot_for(jirobo)["actions"]}
        follow_steps = build_skill_candidates(battle, jirobo, actions["jirobo_follow_step"], profile, instant_only=False)

        self.assertTrue(follow_steps)

        battle = create_battle("excel_r056", "bard")
        remi = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        remi.position = Position(1, 1)
        bard.position = Position(1, 5)
        actions = {action["code"]: action for action in battle.action_snapshot_for(remi)["actions"]}
        chaos = build_skill_candidates(battle, remi, actions["remi_chaos"], profile, instant_only=False)
        bats = build_skill_candidates(battle, remi, actions["summon_remi_bat"], profile, instant_only=False)

        self.assertTrue(any((candidate.payload.get("x"), candidate.payload.get("y")) == (1, 4) for candidate in chaos))
        self.assertTrue(bats)

        battle = create_battle("excel_r056", "bard")
        remi = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        remi.position = Position(1, 1)
        bard.position = Position(3, 1)
        battle.perform_action({"type": "skill", "unit_id": remi.unit_id, "skill_code": "summon_remi_bat", "x": 2, "y": 1})
        bat = next(unit for unit in battle.current_turn_bundle_units() if unit.hero_code == "remi_bat")
        attack_action = next(action for action in battle.action_snapshot_for(bat)["actions"] if action["kind"] == "attack")

        self.assertTrue(build_attack_candidates(battle, bat, attack_action, profile))

    def test_batch_13_ai_builds_nian_special_actions_without_wasting_dragon_dance(self) -> None:
        battle = create_battle("excel_r059", "bard")
        nian = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        nian.position = Position(1, 1)
        bard.position = Position(2, 1)
        nian.current_mana = nian.max_mana()
        bard.max_health = 4
        bard.current_hp = 4
        profile = difficulty_profile("standard")
        actions = {action["code"]: action for action in battle.action_snapshot_for(nian)["actions"]}

        breath = build_skill_candidates(battle, nian, actions["nian_large_dragon_breath"], profile, instant_only=False)
        roar = build_skill_candidates(battle, nian, actions["nian_roar"], profile, instant_only=False)
        flash = build_skill_candidates(battle, nian, actions["nian_jade_flash"], profile, instant_only=False)
        dance = build_skill_candidates(battle, nian, actions["nian_dragon_dance"], profile, instant_only=False)

        self.assertTrue(breath)
        self.assertTrue(roar)
        self.assertTrue(flash)
        self.assertTrue(dance)
        self.assertLess(max(candidate.score for candidate in dance), 0)

        nian.current_mana -= 1
        dance = build_skill_candidates(battle, nian, actions["nian_dragon_dance"], profile, instant_only=False)

        self.assertLess(max(candidate.score for candidate in dance), 0)

    def test_batch_14_ai_uses_black_cat_form_and_selects_unsealed_heaven_punishment_skill(self) -> None:
        profile = difficulty_profile("standard")
        battle = create_battle("excel_r066", "fire_funeral")
        cat = primary_hero(battle, 1)
        enemy = primary_hero(battle, 2)
        cat.position = Position(1, 1)
        enemy.position = Position(7, 7)
        actions = {action["code"]: action for action in battle.action_snapshot_for(cat)["actions"]}
        form = build_skill_candidates(battle, cat, actions["black_cat_form"], profile, instant_only=False)

        self.assertGreater(max(candidate.score for candidate in form), 18)
        self.assertEqual(choose_turn_action(battle, cat, "standard").get("skill_code"), "black_cat_form")

        battle = create_battle("excel_r070", "bard")
        crab = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        crab.position = Position(1, 1)
        bard.position = Position(4, 1)
        action = next(action for action in battle.action_snapshot_for(crab)["actions"] if action["code"] == "heaven_punishment")
        candidates = build_skill_candidates(battle, crab, action, profile, instant_only=False)

        self.assertTrue(candidates)
        self.assertTrue(all(candidate.payload.get("target_unit_id") == bard.unit_id for candidate in candidates))
        self.assertTrue(all(candidate.payload.get("disabled_skill_code") for candidate in candidates))

        bard.add_status(SkillDisabledStatus("heal", "回血"))
        candidates = build_skill_candidates(battle, crab, action, profile, instant_only=False)

        self.assertNotIn("heal", {candidate.payload.get("disabled_skill_code") for candidate in candidates})

    def test_batch_14_ai_values_big_avalanche_before_weather_exists(self) -> None:
        battle = create_battle("excel_r071", "bard")
        giant = primary_hero(battle, 1)
        action = next(action for action in battle.action_snapshot_for(giant)["actions"] if action["code"] == "big_avalanche")
        candidates = build_skill_candidates(battle, giant, action, difficulty_profile("standard"), instant_only=False)

        self.assertGreater(max(candidate.score for candidate in candidates), 18)

    def test_batch_15_ai_builds_kaiser_damage_skill_candidates(self) -> None:
        battle = create_battle("excel_r093", "bard")
        kaiser = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        kaiser.position = Position(4, 4)
        bard.position = Position(5, 4)
        actions = {action["code"]: action for action in battle.action_snapshot_for(kaiser)["actions"]}
        profile = difficulty_profile("standard")

        large_pierce = build_skill_candidates(battle, kaiser, actions["large_pierce"], profile, instant_only=False)
        fist = build_skill_candidates(battle, kaiser, actions["kaiser_fist"], profile, instant_only=False)

        self.assertTrue(large_pierce)
        self.assertTrue(fist)
        self.assertTrue(all(candidate.payload.get("target_unit_id") == bard.unit_id for candidate in fist))

    def test_batch_15_ai_values_interference_and_noise_wave_without_fictional_damage(self) -> None:
        battle = create_battle("excel_r094", "bard")
        noise = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        noise.position = Position(1, 1)
        bard.position = Position(4, 1)
        noise.current_mana = 1
        medusa = MedusaSummon(2)
        battle.summon_unit(medusa, Position(5, 1), summoner=bard)
        actions = {action["code"]: action for action in battle.action_snapshot_for(noise)["actions"]}
        profile = difficulty_profile("standard")

        interference = build_skill_candidates(battle, noise, actions["interference"], profile, instant_only=False)
        wave = build_skill_candidates(battle, noise, actions["noise_wave"], profile, instant_only=False)

        self.assertTrue(interference)
        self.assertGreater(max(candidate.score for candidate in interference), profile.action_threshold)
        self.assertTrue(wave)
        self.assertGreater(max(candidate.score for candidate in wave), profile.action_threshold)

    def test_batch_15_ai_builds_purify_and_sacred_duel_direct_targets(self) -> None:
        battle = create_battle("excel_r113", "fire_funeral")
        ernest = primary_hero(battle, 1)
        fire = primary_hero(battle, 2)
        ernest.position = Position(4, 4)
        fire.position = Position(5, 4)
        actions = {action["code"]: action for action in battle.action_snapshot_for(ernest)["actions"]}
        profile = difficulty_profile("standard")

        purify = build_skill_candidates(battle, ernest, actions["purify_mana"], profile, instant_only=False)
        duel = build_skill_candidates(battle, ernest, actions["sacred_duel"], profile, instant_only=False)

        self.assertTrue(purify)
        self.assertTrue(duel)
        self.assertEqual({candidate.payload.get("target_unit_id") for candidate in purify}, {fire.unit_id})
        self.assertEqual({candidate.payload.get("target_unit_id") for candidate in duel}, {fire.unit_id})
        self.assertEqual(choose_turn_action(battle, ernest, "standard").get("skill_code"), "sacred_duel")

    def test_chain_shield_target_moving_out_of_range_does_not_abort_resolution(self) -> None:
        battle = create_battle(["fire_funeral", "bard"], ["ellie", "excel_r030"])
        fire = next(unit for unit in battle.player_units(1) if unit.hero_code == "fire_funeral")
        ellie = next(unit for unit in battle.player_units(2) if unit.hero_code == "ellie")
        fusion = next(unit for unit in battle.player_units(2) if unit.hero_code == "excel_r030")
        fire.position = Position(4, 4)
        fusion.position = Position(5, 4)
        ellie.position = Position(5, 5)
        queued = QueuedAction(
            action_type="attack",
            actor_id=fire.unit_id,
            display_name="普攻",
            speed=1,
            payload={"target_unit_id": fusion.unit_id},
            target_unit_ids=[fusion.unit_id],
            source_player_id=fire.player_id,
            hostile=True,
        )
        wall = skill_by_code(ellie, "magic_wall")
        self.assertTrue(wall.can_react_with_payload(battle, ellie, queued, {"target_unit_id": fusion.unit_id})[0])

        fusion.position = Position(7, 4)
        wall.react(battle, ellie, {"target_unit_id": fusion.unit_id}, queued)

        self.assertEqual(fusion.position, Position(7, 4))
        self.assertTrue(any("已离开保护范围" in entry for entry in battle.logs))

    def test_batch_16_ai_scores_zero_dash_through_enemy(self) -> None:
        battle = create_battle("excel_r118", "bard")
        battle.width = 10
        battle.height = 10
        zero = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        zero.position = Position(1, 1)
        bard.position = Position(4, 1)
        zero.current_mana = 0
        action = next(action for action in battle.action_snapshot_for(zero)["actions"] if action["code"] == "zero_dash")

        candidates = build_skill_candidates(battle, zero, action, difficulty_profile("standard"), instant_only=False)
        best = max(candidates, key=lambda candidate: candidate.score)

        self.assertEqual(best.payload.get("direction"), {"dx": 1, "dy": 0})
        self.assertGreater(best.score, difficulty_profile("standard").action_threshold)

    def test_ai_zero_normal_move_plans_repeated_crossings(self) -> None:
        battle = create_battle("excel_r118", "bard")
        zero = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        zero.position = Position(1, 1)
        bard.position = Position(3, 1)
        action = next(action for action in battle.action_snapshot_for(zero)["actions"] if action["code"] == "move")

        candidates = build_move_candidates(battle, zero, action, difficulty_profile("standard"))
        best = max(candidates, key=lambda candidate: candidate.score)
        path = [(cell["x"], cell["y"]) for cell in best.payload.get("path", [])]

        self.assertGreaterEqual(path.count((3, 1)), 2)
        self.assertTrue(payload_is_legal(battle, best.payload))

    def test_ai_judgment_stone_moves_onto_enemy_to_explode(self) -> None:
        battle = create_battle("excel_r020", "bard")
        oberon = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        oberon.position = Position(1, 1)
        bard.position = Position(4, 1)
        oberon.current_mana = 5
        battle.perform_action({"type": "skill", "unit_id": oberon.unit_id, "skill_code": "judgment_stone", "x": 2, "y": 1})
        resolve_pending_chain(battle)
        stone = summon_by_code(battle, 1, "judgment_stone")
        stone.turn_ready = True
        stone.can_act_on_entry_turn = True
        action = next(action for action in battle.action_snapshot_for(stone)["actions"] if action["code"] == "move")

        candidates = build_move_candidates(battle, stone, action, difficulty_profile("standard"))
        best = max(candidates, key=lambda candidate: candidate.score)

        self.assertEqual((best.payload.get("x"), best.payload.get("y")), (4, 1))

    def test_ai_kiku_values_sun_slash_damage_and_passive_lock(self) -> None:
        battle = create_battle("excel_r379", "bard")
        kiku = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        kiku.position = Position(2, 2)
        bard.position = Position(3, 2)
        bard.max_health = 4
        bard.current_hp = 4

        sun_slash = next(action for action in battle.action_snapshot_for(kiku)["actions"] if action["code"] == "sun_slash")
        payload = choose_turn_action(battle, kiku, "standard")

        self.assertEqual(sun_slash["preview"]["target_unit_ids"], [bard.unit_id])
        self.assertEqual(payload.get("skill_code"), "sun_slash")
        self.assertEqual(payload.get("target_unit_id"), bard.unit_id)

    def test_batch_16_ai_builds_fuma_trap_and_shuriken_candidates(self) -> None:
        battle = create_battle("excel_r123", "bard")
        fuma = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        fuma.position = Position(1, 1)
        bard.position = Position(3, 1)
        actions = {action["code"]: action for action in battle.action_snapshot_for(fuma)["actions"]}
        profile = difficulty_profile("standard")

        traps = build_skill_candidates(battle, fuma, actions["fuma_trap"], profile, instant_only=False)
        shuriken = build_skill_candidates(battle, fuma, actions["fuma_shuriken"], profile, instant_only=False)

        self.assertGreater(max(candidate.score for candidate in traps), profile.action_threshold)
        self.assertGreater(max(candidate.score for candidate in shuriken), profile.action_threshold)
        self.assertLess(len(shuriken), 10)

    def test_batch_16_ai_builds_fantasy_and_rainbow_mirror_compound_payloads(self) -> None:
        battle = create_battle("excel_r127", "bard")
        bird = primary_hero(battle, 1)
        enemy = primary_hero(battle, 2)
        bird.position = Position(1, 1)
        enemy.position = Position(3, 1)
        actions = {action["code"]: action for action in battle.action_snapshot_for(bird)["actions"]}
        profile = difficulty_profile("standard")

        fantasy = build_skill_candidates(battle, bird, actions["fantasy_move"], profile, instant_only=False)

        self.assertTrue(fantasy)
        self.assertTrue(all(candidate.payload.get("target_unit_id") == enemy.unit_id for candidate in fantasy))
        self.assertTrue(all(candidate.payload.get("x") is not None and candidate.payload.get("y") is not None for candidate in fantasy))

        ally = create_hero("bard", 1)
        battle.add_unit(ally, Position(7, 7))
        actions = {action["code"]: action for action in battle.action_snapshot_for(bird)["actions"]}
        mirrors = build_skill_candidates(battle, bird, actions["rainbow_mirror"], profile, instant_only=False)

        self.assertTrue(any(candidate.payload.get("target_unit_id") == ally.unit_id for candidate in mirrors))
        self.assertTrue(all(candidate.payload.get("x") is not None and candidate.payload.get("y") is not None for candidate in mirrors))

    def test_batch_16_ai_saves_friendly_mirror_for_strong_attackers(self) -> None:
        battle = create_battle("excel_r127", "bard")
        bird = primary_hero(battle, 1)
        enemy = primary_hero(battle, 2)
        enemy.base_stats.attack = 3
        action = next(action for action in battle.action_snapshot_for(bird)["actions"] if action["code"] == "friendly_mirror")
        profile = difficulty_profile("standard")

        candidates = build_skill_candidates(battle, bird, action, profile, instant_only=False)

        self.assertGreater(max(candidate.score for candidate in candidates), profile.once_per_battle_threshold)

    def test_batch_16_fantasy_does_not_offer_evasion_reaction(self) -> None:
        battle = create_battle("excel_r127", "dark_human")
        bird = primary_hero(battle, 1)
        target = primary_hero(battle, 2)
        bird.position = Position(1, 1)
        target.position = Position(3, 1)

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": bird.unit_id,
                "skill_code": "fantasy_move",
                "target_unit_id": target.unit_id,
                "x": 7,
                "y": 1,
            }
        )

        if battle.pending_chain is not None:
            options = battle.pending_chain.options_by_unit.get(target.unit_id, [])
            self.assertNotIn("evasion", {option.action_code for option in options})
        resolve_pending_chain(battle)
        self.assertEqual(target.position, Position(7, 1))

    def test_batch_17_ai_builds_true_blade_target_and_landing_payloads(self) -> None:
        battle = create_battle("excel_r136", "bard")
        oboro = primary_hero(battle, 1)
        target = primary_hero(battle, 2)
        oboro.position = Position(1, 1)
        target.position = Position(7, 1)
        action = next(action for action in battle.action_snapshot_for(oboro)["actions"] if action["code"] == "true_blade_air_slash")

        candidates = build_skill_candidates(battle, oboro, action, difficulty_profile("standard"), instant_only=False)

        self.assertTrue(candidates)
        self.assertTrue(all(candidate.payload.get("target_unit_id") == target.unit_id for candidate in candidates))
        self.assertTrue(all(candidate.payload.get("x") is not None and candidate.payload.get("y") is not None for candidate in candidates))
        self.assertGreater(max(candidate.score for candidate in candidates), difficulty_profile("standard").action_threshold)

    def test_batch_17_ai_builds_and_values_devour_targets(self) -> None:
        battle = create_battle("excel_r137", "bard")
        undead = primary_hero(battle, 1)
        target = primary_hero(battle, 2)
        undead.position = Position(1, 1)
        target.position = Position(2, 1)
        undead.current_hp = 0.5
        action = next(action for action in battle.action_snapshot_for(undead)["actions"] if action["code"] == "undead_boy_devour")

        candidates = build_skill_candidates(battle, undead, action, difficulty_profile("standard"), instant_only=False)

        self.assertEqual({candidate.payload.get("target_unit_id") for candidate in candidates}, {target.unit_id})
        self.assertGreater(max(candidate.score for candidate in candidates), difficulty_profile("standard").action_threshold)

    def test_batch_17_ai_values_illumination_and_skips_it_without_targets(self) -> None:
        battle = create_battle("excel_r139", "excel_r137")
        sola = primary_hero(battle, 1)
        target = primary_hero(battle, 2)
        sola.position = Position(1, 1)
        target.position = Position(4, 1)
        target.shields = 1
        profile = difficulty_profile("standard")
        action = next(action for action in battle.action_snapshot_for(sola)["actions"] if action["code"] == "illumination_light")

        candidates = build_skill_candidates(battle, sola, action, profile, instant_only=False)

        self.assertGreater(max(candidate.score for candidate in candidates), profile.action_threshold)
        target.position = Position(9, 9)
        action = next(action for action in battle.action_snapshot_for(sola)["actions"] if action["code"] == "illumination_light")
        self.assertEqual(build_skill_candidates(battle, sola, action, profile, instant_only=False), [])

    def test_batch_17_nuclear_rush_attack_is_not_candidate_while_immobile(self) -> None:
        battle = create_battle("excel_r030", "bard")
        fusion = primary_hero(battle, 1)
        target = primary_hero(battle, 2)
        fusion.position = Position(1, 1)
        target.position = Position(3, 1)
        battle.perform_action({"type": "skill", "unit_id": fusion.unit_id, "skill_code": "nuclear_rush"})
        fusion.cannot_move = True
        attack = next(action for action in battle.action_snapshot_for(fusion)["actions"] if action["kind"] == "attack")

        self.assertEqual(build_attack_candidates(battle, fusion, attack, difficulty_profile("standard")), [])

    def test_batch_18_ai_builds_damaging_hell_slash_candidates(self) -> None:
        battle = create_battle("excel_r158", "bard")
        warrior = primary_hero(battle, 1)
        target = primary_hero(battle, 2)
        warrior.position = Position(1, 1)
        target.position = Position(4, 1)
        action = next(action for action in battle.action_snapshot_for(warrior)["actions"] if action["code"] == "hell_slash")

        candidates = build_skill_candidates(battle, warrior, action, difficulty_profile("standard"), instant_only=False)

        self.assertTrue(candidates)
        self.assertGreater(max(candidate.score for candidate in candidates), difficulty_profile("standard").action_threshold)

    def test_batch_18_ai_electric_wind_avoids_friendly_control(self) -> None:
        battle = create_battle(["excel_r166", "bard"], ["excel_r137", "excel_r139"])
        electric = next(unit for unit in battle.hero_units(1) if unit.hero_code == "excel_r166")
        ally = next(unit for unit in battle.hero_units(1) if unit.hero_code == "bard")
        north_enemy = next(unit for unit in battle.hero_units(2) if unit.hero_code == "excel_r137")
        east_enemy = next(unit for unit in battle.hero_units(2) if unit.hero_code == "excel_r139")
        electric.position = Position(4, 4)
        ally.position = Position(5, 4)
        north_enemy.position = Position(4, 2)
        east_enemy.position = Position(6, 4)
        action = next(action for action in battle.action_snapshot_for(electric)["actions"] if action["code"] == "electric_wind")

        candidates = build_skill_candidates(battle, electric, action, difficulty_profile("standard"), instant_only=False)
        best = max(candidates, key=lambda candidate: candidate.score)
        hit_ids = {unit.unit_id for unit in battle.units_at_cells([Position(cell["x"], cell["y"]) for cell in best.payload["cells"]])}

        self.assertIn(north_enemy.unit_id, hit_ids)
        self.assertNotIn(ally.unit_id, hit_ids)

    def test_batch_18_ai_values_martial_seal_and_unset_pandemonium(self) -> None:
        profile = difficulty_profile("standard")
        warrior_battle = create_battle("excel_r158", "bard")
        warrior = primary_hero(warrior_battle, 1)
        seal = next(action for action in warrior_battle.action_snapshot_for(warrior)["actions"] if action["code"] == "martial_god_seal")
        seal_candidates = build_skill_candidates(warrior_battle, warrior, seal, profile, instant_only=False)
        self.assertGreater(max(candidate.score for candidate in seal_candidates), profile.action_threshold)

        demon_battle = create_battle("excel_r187", "bard")
        demon = primary_hero(demon_battle, 1)
        weather = next(action for action in demon_battle.action_snapshot_for(demon)["actions"] if action["code"] == "pandemonium")
        weather_candidates = build_skill_candidates(demon_battle, demon, weather, profile, instant_only=False)
        self.assertGreater(max(candidate.score for candidate in weather_candidates), profile.once_per_battle_threshold)

    def test_batch_18_pandemonium_immediately_syncs_all_demon_leaders(self) -> None:
        battle = create_battle(["excel_r187", "excel_r187"], "bard")
        leaders = [unit for unit in battle.hero_units(1) if unit.hero_code == "excel_r187"]

        battle.perform_action({"type": "skill", "unit_id": leaders[0].unit_id, "skill_code": "pandemonium"})

        self.assertTrue(all(leader.has_status("万魔殿加速") for leader in leaders))
        self.assertTrue(all(leader.stat("speed") == 6 for leader in leaders))

    def test_batch_19_ai_builds_vitality_blast_candidates_after_weather(self) -> None:
        battle = create_battle("excel_r188", "bard")
        guardian = primary_hero(battle, 1)
        target = primary_hero(battle, 2)
        guardian.position = Position(1, 1)
        target.position = Position(4, 1)
        battle.perform_action({"type": "skill", "unit_id": guardian.unit_id, "skill_code": "sky_sanctuary"})
        action = next(action for action in battle.action_snapshot_for(guardian)["actions"] if action["code"] == "vitality_blast")

        candidates = build_skill_candidates(battle, guardian, action, difficulty_profile("standard"), instant_only=False)

        self.assertTrue(candidates)
        self.assertGreater(max(candidate.score for candidate in candidates), difficulty_profile("standard").action_threshold)

    def test_batch_19_vain_giant_shadow_exposes_targets_and_cannot_be_evaded(self) -> None:
        battle = create_battle("excel_r326", "bard")
        florenza = primary_hero(battle, 1)
        target = primary_hero(battle, 2)
        florenza.position = Position(1, 1)
        target.position = Position(3, 1)
        action = next(action for action in battle.action_snapshot_for(florenza)["actions"] if action["code"] == "vain_giant_shadow")

        candidates = build_skill_candidates(battle, florenza, action, difficulty_profile("standard"), instant_only=False)

        self.assertIn(target.unit_id, action["preview"]["target_unit_ids"])
        self.assertTrue(any(candidate.payload.get("target_unit_id") == target.unit_id for candidate in candidates))
        battle.perform_action({"type": "skill", "unit_id": florenza.unit_id, "skill_code": "vain_giant_shadow", "target_unit_id": target.unit_id})
        self.assertIsNotNone(battle.pending_chain)
        self.assertTrue(battle.pending_chain.queued_action.payload.get("cannot_evade"))

    def test_batch_19_ai_values_both_weather_ultimates(self) -> None:
        profile = difficulty_profile("standard")
        guardian_battle = create_battle("excel_r188", "bard")
        guardian = primary_hero(guardian_battle, 1)
        sky = next(action for action in guardian_battle.action_snapshot_for(guardian)["actions"] if action["code"] == "sky_sanctuary")
        self.assertEqual(sky["name"], "天使的气息")
        self.assertGreater(max(candidate.score for candidate in build_skill_candidates(guardian_battle, guardian, sky, profile, instant_only=False)), profile.once_per_battle_threshold)

        tina_battle = create_battle("excel_r337", "bard")
        tina = primary_hero(tina_battle, 1)
        wetland = next(action for action in tina_battle.action_snapshot_for(tina)["actions"] if action["code"] == "wetland_grassland")
        self.assertGreater(max(candidate.score for candidate in build_skill_candidates(tina_battle, tina, wetland, profile, instant_only=False)), profile.once_per_battle_threshold)

    def test_ai_moves_to_line_up_great_fire_funeral(self) -> None:
        # Given Fire Funeral is not yet aligned with the enemy but can move into the same row
        battle = create_battle("fire_funeral", "bard")
        fire = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        fire.position = Position(1, 1)
        bard.position = Position(4, 4)

        # When the AI first prepares mobility and then chooses movement
        payload = choose_turn_action(battle, fire, "standard")
        self.assertEqual(payload.get("skill_code"), "shensu")
        battle.perform_action(payload)
        payload = choose_turn_action(battle, fire, "standard")

        # Then movement toward a 大火葬 line is a valid high-value plan
        self.assertEqual(payload.get("type"), "move")
        self.assertTrue(payload.get("x") == bard.position.x or payload.get("y") == bard.position.y)

    def test_soul_wraith_growth_is_visible_in_public_state(self) -> None:
        # Given Bard blocks Soul Wraith's arc attack with a passive skill
        battle = create_battle("soul_wraith", "bard")
        wraith = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        wraith.position = Position(4, 4)
        bard.position = Position(5, 4)
        bard.current_mana = 1

        battle.perform_action({"type": "attack", "unit_id": wraith.unit_id, "choice_code": "right"})
        while battle.pending_chain is not None and battle.pending_chain.current_unit_id() != bard.unit_id:
            battle.perform_action({"type": "chain_skip"})
        battle.perform_action({"type": "chain_react", "unit_id": bard.unit_id, "action_code": "protection"})
        resolve_pending_chain(battle)

        # Then the public unit snapshot exposes the changed stats and extra move count
        public_wraith = next(unit for unit in battle.to_public_dict()["units"] if unit["id"] == wraith.unit_id)
        self.assertEqual(public_wraith["stats"]["attack"], 5)
        self.assertEqual(public_wraith["stats"]["speed"], 6)
        self.assertEqual(public_wraith["normal_move_actions_per_turn"], 2)
        self.assertTrue(any(status["name"] == "销魂成长" and status["stacks"] == 1 for status in public_wraith["statuses"]))


class RoomBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        configure_public_base_url(None)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), WujiangHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)

    def setUp(self) -> None:
        ROOMS._rooms.clear()  # type: ignore[attr-defined]
        SESSION.battle = None
        self.auth_tmpdir = tempfile.TemporaryDirectory()
        server_module.AUTH_STORE = UserStore(Path(self.auth_tmpdir.name) / "auth.sqlite3")
        server_module.ANALYTICS_STORE = AnalyticsStore(Path(self.auth_tmpdir.name) / "analytics.sqlite3")
        server_module.MATCH_HISTORY_STORE = MatchHistoryStore(Path(self.auth_tmpdir.name) / "history.sqlite3")
        server_module.STRATEGY_STORE = StrategyStore(Path(self.auth_tmpdir.name) / "strategy.sqlite3")
        self._default_session_token: str | None = None

    def tearDown(self) -> None:
        self.auth_tmpdir.cleanup()

    def api_get(self, path: str, *, params: dict[str, str] | None = None) -> dict:
        query = f"?{urlencode(params)}" if params else ""
        with urlopen(f"http://127.0.0.1:{self.port}{path}{query}") as response:
            return json.loads(response.read().decode("utf-8"))

    def api_get_error(self, path: str, *, params: dict[str, str] | None = None) -> tuple[int, dict]:
        query = f"?{urlencode(params)}" if params else ""
        try:
            with urlopen(f"http://127.0.0.1:{self.port}{path}{query}") as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def default_session_token(self) -> str:
        if self._default_session_token is None:
            payload = self.api_post(
                "/api/auth/register",
                {"username": "TestUser", "password": "secret123"},
            )
            self._default_session_token = payload["session_token"]
        return self._default_session_token

    def _bind_test_action_office(self, path: str, payload: dict) -> None:
        campaign_id = payload.get("campaign_id")
        if campaign_id is None:
            return
        action_payload = payload.get("action_payload") if isinstance(payload.get("action_payload"), dict) else payload
        if action_payload.get("issuer_office_id"):
            return
        action_type = str(payload.get("action_type") or "")
        office_type = ""
        city_id = ""
        if path.endswith("/advance-month") or path.endswith("/unlock-tactic-tech"):
            office_type = "lord"
        elif path.endswith("/set-city-policy"):
            office_type, city_id = "governor", str(payload.get("city_id") or "")
        elif path.endswith("/set-defense-hero"):
            office_type = "grand_general"
        elif path.endswith("/set-battle-defense-hero"):
            office_type = "general"
        elif path.endswith("/declare-attack"):
            office_type, city_id = "general", str(payload.get("source_city_id") or "")
        elif path.endswith("/queue-action"):
            city_id = str(action_payload.get("city_id") or action_payload.get("source_city_id") or "")
            if action_type in {"set_city_policy", "rebellion_action", "rebellion_battle", "resolve_story_event"}:
                office_type = "governor"
            elif action_type in {"increase_city_troops", "register_city_soldiers", "construct_city_building", "perform_hero_ritual"}:
                office_type = "governor"
            elif action_type in {"transfer_registered_units", "approve_registered_unit_request"}:
                office_type = "grand_general"
            elif action_type == "request_registered_units":
                office_type = "general"
            elif action_type in {"form_army", "disband_army", "set_army_movement", "load_army_supply"}:
                office_type = "general"
            elif action_type == "declare_attack":
                office_type = "general"
            elif action_type in {
                "unlock_tactic_tech",
                "unbind_strategic_hero",
                "exile_action",
                "assign_strategic_hero_duty",
            }:
                office_type = "lord"
        if not office_type:
            return
        token = str(payload.get("session_token") or self.default_session_token())
        user = server_module.AUTH_STORE.user_for_session(token)
        campaign = server_module.STRATEGY_STORE.get_campaign_for_user(int(campaign_id), user.user_id)
        member = next(item for item in campaign.members if item.user_id == user.user_id)
        if action_type == "resolve_story_event" and not city_id:
            event_id = str(action_payload.get("event_id") or "")
            event = next((item for item in campaign.world.story_events if item.event_id == event_id), None)
            city_id = event.city_id if event is not None else ""
        candidates = [
            office
            for office in campaign.world.offices
            if office.faction_id == member.faction_id
            and office.office_type == office_type
            and (not city_id or city_id in office.managed_entity_ids or office_type in {"lord", "grand_general"})
        ]
        if not candidates:
            return
        office = sorted(candidates, key=lambda item: item.office_id)[0]
        for item in campaign.world.offices:
            if item.faction_id == member.faction_id and item.controller_user_id == user.user_id:
                item.controller_type = "ai"
                item.controller_user_id = None
        office.controller_type = "player"
        office.controller_user_id = user.user_id
        for hero in campaign.world.strategic_heroes:
            if hero.controller_user_id == user.user_id:
                hero.controller_type = "ai"
                hero.controller_user_id = None
            if hero.hero_code == office.holder_id:
                hero.controller_type = "player"
                hero.controller_user_id = user.user_id
        server_module.STRATEGY_STORE.update_world(int(campaign_id), user.user_id, campaign.world)
        action_payload["issuer_office_id"] = office.office_id

    def api_post(self, path: str, payload: dict) -> dict:
        request_payload = dict(payload)
        if path.startswith("/api/rooms/") or path.startswith("/api/strategy/") or path in {"/api/new-game", "/api/action"}:
            request_payload.setdefault("session_token", self.default_session_token())
        self._bind_test_action_office(path, request_payload)
        body = json.dumps(request_payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))

    def api_post_error(self, path: str, payload: dict) -> tuple[int, dict]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with urlopen(request) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def confirm_room_ready(self, room_id: str, *player_tokens: str) -> None:
        for player_token in player_tokens:
            self.api_post(
                "/api/rooms/set-ready",
                {"room_id": room_id, "player_token": player_token, "ready": True},
            )

    def test_scenario_user_can_register_login_query_and_logout(self) -> None:
        registered = self.api_post("/api/auth/register", {"username": "Alice", "password": "secret123"})
        token = registered["session_token"]

        self.assertEqual(registered["user"]["username"], "Alice")
        self.assertTrue(token)

        me = self.api_get("/api/auth/me", params={"session_token": token})
        self.assertEqual(me["user"]["username"], "Alice")

        self.api_post("/api/auth/logout", {"session_token": token})
        status, expired = self.api_get_error("/api/auth/me", params={"session_token": token})
        self.assertEqual(status, 401)
        self.assertIn("error", expired)

        logged_in = self.api_post("/api/auth/login", {"username": "alice", "password": "secret123"})
        self.assertEqual(logged_in["user"]["username"], "Alice")
        self.assertNotEqual(logged_in["session_token"], token)

        status, bad_login = self.api_post_error(
            "/api/auth/login",
            {"username": "Alice", "password": "wrong-password"},
        )
        self.assertEqual(status, 401)
        self.assertIn("error", bad_login)

    def test_scenario_local_analytics_records_only_allowlisted_product_events(self) -> None:
        # Given a new anonymous visitor opens the home screen and starts the tutorial
        session_id = "visitor-test-1"
        self.api_post(
            "/api/analytics/events",
            {
                "event_name": "home_view",
                "anonymous_session_id": session_id,
                "properties": {"entry_state": "anonymous", "session_token": "must-not-be-stored"},
            },
        )
        self.api_post(
            "/api/analytics/events",
            {
                "event_name": "first_effective_action",
                "anonymous_session_id": session_id,
                "properties": {"tutorial_id": "first_battle", "action_type": "move", "duration_ms": 90000},
            },
        )
        self.api_post(
            "/api/analytics/events",
            {
                "event_name": "action_succeeded",
                "anonymous_session_id": session_id,
                "properties": {"match_id": "TEST01", "mode": "tutorial", "action_type": "move"},
            },
        )
        self.api_post(
            "/api/analytics/events",
            {
                "event_name": "tutorial_start",
                "anonymous_session_id": session_id,
                "properties": {"tutorial_id": "first_battle"},
            },
        )
        self.api_post(
            "/api/analytics/events",
            {
                "event_name": "quick_ai_start",
                "anonymous_session_id": session_id,
                "properties": {"match_id": "QUICK1", "roster_code": "steady_front", "opponent_code": "ranged_pressure"},
            },
        )
        self.api_post(
            "/api/analytics/events",
            {
                "event_name": "match_start",
                "anonymous_session_id": session_id,
                "properties": {"match_id": "QUICK1", "mode": "quick_ai"},
            },
        )
        for event_name, properties in (
            ("strategy_campaign_create", {"campaign_id": "12", "scenario_id": "city_states_twelve_months_v1"}),
            ("strategy_campaign_lock", {"campaign_id": "12"}),
            ("strategy_campaign_enter", {"campaign_id": "12"}),
            ("strategy_campaign_milestone", {"campaign_id": "12", "month": "3"}),
            ("strategy_battle_trigger", {"campaign_id": "12", "month": "4", "resolution_mode": "manual"}),
            ("strategy_campaign_complete", {"campaign_id": "12", "month": "12", "reason": "time_limit"}),
            ("strategy_campaign_archive", {"campaign_id": "12", "month": "12"}),
        ):
            self.api_post(
                "/api/analytics/events",
                {
                    "event_name": event_name,
                    "anonymous_session_id": session_id,
                    "properties": {**properties, "session_token": "must-not-be-stored"},
                },
            )
        self.api_post(
            "/api/analytics/events",
            {
                "event_name": "match_end",
                "anonymous_session_id": session_id,
                "properties": {"match_id": "QUICK1", "mode": "quick_ai", "result": "win", "duration_ms": 600000},
            },
        )
        self.api_post(
            "/api/analytics/events",
            {
                "event_name": "rematch_start",
                "anonymous_session_id": session_id,
                "properties": {"match_id": "QUICK1", "mode": "quick_ai", "duration_ms": 120000},
            },
        )
        self.api_post(
            "/api/analytics/events",
            {
                "event_name": "match_start",
                "anonymous_session_id": session_id,
                "properties": {"match_id": "QUICK2", "mode": "quick_ai"},
            },
        )

        # When a developer opens the local funnel endpoint
        funnel = self.api_get("/api/analytics/funnel")

        # Then the funnel is countable without accepting credentials or arbitrary event names
        by_event = {step["event"]: step for step in funnel["steps"]}
        self.assertEqual(by_event["home_view"]["unique_sessions"], 1)
        self.assertEqual(by_event["tutorial_start"]["unique_sessions"], 1)
        self.assertEqual(by_event["tutorial_start"]["from_home_rate"], 1.0)
        self.assertEqual(by_event["quick_ai_start"]["unique_sessions"], 1)
        self.assertEqual(by_event["match_start"]["events"], 2)
        self.assertEqual(by_event["rematch_start"]["unique_sessions"], 1)
        self.assertEqual(by_event["strategy_campaign_create"]["events"], 1)
        self.assertEqual(by_event["strategy_campaign_milestone"]["events"], 1)
        self.assertEqual(by_event["strategy_battle_trigger"]["events"], 1)
        self.assertEqual(by_event["strategy_campaign_complete"]["events"], 1)
        self.assertEqual(by_event["strategy_campaign_archive"]["events"], 1)
        self.assertEqual(funnel["metrics"]["first_effective_action_median_ms"], 90000)
        self.assertEqual(funnel["metrics"]["invalid_action_rate"], 0.0)
        self.assertEqual(funnel["metrics"]["rematch_within_10m_rate"], 1.0)
        self.assertEqual(funnel["metrics"]["match_completion_rate"], 0.5)
        status, error = self.api_post_error(
            "/api/analytics/events",
            {"event_name": "password_seen", "anonymous_session_id": session_id},
        )
        self.assertEqual(status, 400)
        self.assertIn("不支持", error["error"])

        # And the local review page exposes only aggregate labels and the funnel endpoint
        with urlopen(f"http://127.0.0.1:{self.port}/analytics.html") as response:
            dashboard = response.read().decode("utf-8")
        self.assertIn("Phase 1 · 本地聚合数据", dashboard)
        self.assertIn('id="analytics-summary"', dashboard)
        self.assertNotIn("session token 的值", dashboard)

    def test_scenario_quick_start_creates_a_fixed_ai_tutorial_battle(self) -> None:
        # Given a logged-in player chooses the primary quick-start path
        payload = self.api_post("/api/rooms/tutorial-start", {"player_name": "NewPlayer"})

        # Then the server starts the fixed tutorial matchup without room configuration
        self.assertEqual(payload["room"]["experience_kind"], "tutorial")
        self.assertEqual(payload["room"]["status"], "battle")
        self.assertEqual(payload["room"]["human_seat_count"], 1)
        self.assertEqual(payload["room"]["ai_seat_count"], 1)
        self.assertEqual(payload["room"]["tutorial"]["step_id"], "select_unit")
        rosters = {
            seat["player_id"]: [hero["code"] for hero in seat["hero_roster"]]
            for seat in payload["room"]["seats"]
        }
        self.assertEqual(rosters, {1: ["fire_funeral"], 2: ["ellie"]})
        self.assertIsNotNone(payload["battle"])

    def test_scenario_quick_ai_start_skips_room_setup_with_beginner_two_hero_rosters(self) -> None:
        # Given a logged-in player chooses the Phase 1 quick AI route
        payload = self.api_post("/api/rooms/quick-ai-start", {"player_name": "ReturningPlayer"})

        # Then the server immediately starts a human-versus-easy-AI 2v2 with named beginner rosters
        self.assertEqual(payload["room"]["experience_kind"], "quick_ai")
        self.assertEqual(payload["room"]["status"], "battle")
        self.assertEqual(payload["room"]["human_seat_count"], 1)
        self.assertEqual(payload["room"]["ai_seat_count"], 1)
        self.assertEqual(payload["room"]["default_ai_difficulty"], "easy")
        rosters = {
            seat["player_id"]: [hero["code"] for hero in seat["hero_roster"]]
            for seat in payload["room"]["seats"]
        }
        self.assertEqual(rosters, {1: ["bard", "masamune"], 2: ["elite_soldier", "excel_r139"]})
        ai_seat = next(seat for seat in payload["room"]["seats"] if seat["is_ai"])
        self.assertEqual(ai_seat["ai_difficulty_override"], "easy")
        self.assertEqual(payload["quick_ai"]["player_roster_code"], "steady_front")
        self.assertEqual(payload["quick_ai"]["opponent_roster_code"], "ranged_pressure")
        self.assertIsNotNone(payload["battle"])
        self.assertEqual(payload["battle"]["input_player"], 1)

        # And after this QA match ends, the same endpoint can create the configured matchup again without draft
        finished = self.api_post(
            "/api/rooms/surrender",
            {"room_id": payload["room"]["room_id"], "player_token": payload["player_token"]},
        )
        self.assertEqual(finished["room"]["status"], "finished")
        replayed = self.api_post("/api/rooms/quick-ai-start", {"player_name": "ReturningPlayer"})
        self.assertNotEqual(replayed["room"]["room_id"], payload["room"]["room_id"])
        replayed_rosters = {
            seat["player_id"]: [hero["code"] for hero in seat["hero_roster"]]
            for seat in replayed["room"]["seats"]
        }
        self.assertEqual(replayed_rosters, rosters)
        self.assertEqual(replayed["battle"]["input_player"], 1)

    def test_scenario_tutorial_enforces_steps_and_restores_progress_after_refresh(self) -> None:
        # Given the fixed tutorial has started
        started = self.api_post("/api/rooms/tutorial-start", {"player_name": "NewPlayer"})
        room_id = started["room"]["room_id"]
        token = started["player_token"]
        fire = next(unit for unit in started["battle"]["units"] if unit["name"] == "火葬者")
        ellie = next(unit for unit in started["battle"]["units"] if unit["name"] == "艾莉")

        # When the player tries to act before selecting Fire Funeral
        status, blocked = self.api_post_error(
            "/api/rooms/action",
            {"room_id": room_id, "player_token": token, "session_token": self.default_session_token(), "action": {"type": "end_turn"}},
        )
        self.assertEqual(status, 400)
        self.assertIn("点击火葬者", blocked["error"])

        # And the player completes the guided selection and fixed move
        selected = self.api_post(
            "/api/rooms/tutorial-select-unit",
            {"room_id": room_id, "player_token": token, "unit_id": fire["id"]},
        )
        self.assertEqual(selected["room"]["tutorial"]["step_id"], "move")
        moved = self.api_post(
            "/api/rooms/action",
            {
                "room_id": room_id,
                "player_token": token,
                "action": {"type": "move", "unit_id": fire["id"], "x": 4, "y": 4, "path": [{"x": 4, "y": 4}]},
            },
        )
        self.assertEqual(moved["room"]["tutorial"]["step_id"], "basic_attack")
        self.assertIsNotNone(moved["room"]["tutorial"]["first_effective_action_at"])

        # Then a refresh restores the server-owned step, and the wrong action remains blocked
        restored = self.api_get("/api/rooms/state", params={"room_id": room_id, "player_token": token})
        self.assertEqual(restored["room"]["tutorial"]["step_id"], "basic_attack")
        status, wrong_target = self.api_post_error(
            "/api/rooms/action",
            {
                "room_id": room_id,
                "player_token": token,
                "session_token": self.default_session_token(),
                "action": {"type": "skill", "unit_id": fire["id"], "skill_code": "pierce", "cells": [{"x": 5, "y": 4}, {"x": 6, "y": 4}]},
            },
        )
        self.assertEqual(status, 400)
        self.assertIn("普通攻击", wrong_target["error"])

        # And the correct basic attack advances to the active-skill lesson
        attacked = self.api_post(
            "/api/rooms/action",
            {
                "room_id": room_id,
                "player_token": token,
                "action": {"type": "attack", "unit_id": fire["id"], "target_unit_id": ellie["id"]},
            },
        )
        self.assertEqual(attacked["room"]["tutorial"]["step_id"], "active_skill")

        skilled = self.api_post(
            "/api/rooms/action",
            {
                "room_id": room_id,
                "player_token": token,
                "action": {
                    "type": "skill",
                    "unit_id": fire["id"],
                    "skill_code": "pierce",
                    "cells": [{"x": 5, "y": 4}, {"x": 6, "y": 4}],
                },
            },
        )
        self.assertEqual(skilled["room"]["tutorial"]["step_id"], "end_turn")
        ended = self.api_post(
            "/api/rooms/action",
            {"room_id": room_id, "player_token": token, "action": {"type": "end_turn"}},
        )
        self.assertEqual(ended["room"]["tutorial"]["step_id"], "chain_response")
        self.assertIsNotNone(ended["battle"]["pending_chain"])

        chained = self.api_post(
            "/api/rooms/action",
            {"room_id": room_id, "player_token": token, "action": {"type": "chain_skip"}},
        )
        self.assertEqual(chained["room"]["tutorial"]["step_id"], "win_objective")
        self.assertTrue(chained["room"]["tutorial"]["can_retry_checkpoint"])

        retried = self.api_post(
            "/api/rooms/tutorial-retry",
            {"room_id": room_id, "player_token": token},
        )
        self.assertEqual(retried["room"]["tutorial"]["step_id"], "win_objective")
        self.assertEqual(retried["room"]["tutorial"]["retry_count"], 1)

    def test_scenario_beginner_pool_and_recommended_roster_avoid_full_catalog_first(self) -> None:
        # Given a player opens the first hero-selection path
        catalog = self.api_get("/api/heroes")
        onboarding = catalog["onboarding"]

        # Then a focused ten-hero pool and three explained rosters are available
        self.assertEqual(len(onboarding["beginner_heroes"]), 10)
        self.assertEqual(len(onboarding["recommended_rosters"]), 3)
        self.assertTrue(all(item["position"] and item["difficulty"] and item["summary"] for item in onboarding["beginner_heroes"]))
        discovery = {item["code"]: item for item in onboarding["hero_discovery"]}
        self.assertEqual(len(discovery), len(catalog["heroes"]))
        self.assertEqual({item["difficulty"] for item in discovery.values()} - {"简单", "标准", "进阶"}, set())
        self.assertEqual(discovery["fire_funeral"]["difficulty_source"], "curated")
        self.assertEqual(discovery["fire_funeral"]["position"], "近中程爆发")
        self.assertEqual(discovery["ellie"]["difficulty_source"], "estimated")
        self.assertEqual(discovery["ellie"]["role"], "法师")

        # And one click replaces the editable seat roster with the chosen recommendation
        created = self.api_post("/api/rooms/create", {"player_name": "NewPlayer"})
        applied = self.api_post(
            "/api/rooms/apply-recommended-roster",
            {
                "room_id": created["room"]["room_id"],
                "player_token": created["player_token"],
                "roster_code": "steady_front",
            },
        )
        viewer = next(seat for seat in applied["room"]["seats"] if seat["player_id"] == 1)
        self.assertEqual(viewer["hero_counts"], {"bard": 1, "masamune": 1})

    def test_scenario_room_entry_requires_login(self) -> None:
        status, create_error = self.api_post_error("/api/rooms/create", {"player_name": "Alice"})
        self.assertEqual(status, 401)
        self.assertIn("登录", create_error["error"])

        created = self.api_post("/api/rooms/create", {"player_name": "Alice"})
        room_id = created["room"]["room_id"]

        status, join_error = self.api_post_error(
            "/api/rooms/join",
            {"room_id": room_id, "player_name": "Bob"},
        )
        self.assertEqual(status, 401)
        self.assertIn("登录", join_error["error"])

    def test_scenario_strategy_campaign_create_list_enter_and_resume(self) -> None:
        status, create_error = self.api_post_error(
            "/api/strategy/campaigns/create",
            {"name": "未登录战役"},
        )
        self.assertEqual(status, 401)
        self.assertIn("登录", create_error["error"])

        created = self.api_post(
            "/api/strategy/campaigns/create",
            {"name": "英灵城邦", "seed": 90, "city_count": 6, "faction_count": 2},
        )
        campaign = created["campaign"]
        campaign_id = campaign["id"]

        self.assertEqual(campaign["name"], "英灵城邦")
        self.assertEqual(campaign["world"]["seed"], 90)
        self.assertEqual(len(campaign["world"]["cities"]), 8)
        self.assertEqual(campaign["world"]["strategic_status"]["month_limit"], 12)
        self.assertEqual(campaign["resume"]["can_resume"], False)
        self.assertEqual(campaign["resume"]["missing_initial_user_ids"], [])

        listed = self.api_get(
            "/api/strategy/campaigns",
            params={"session_token": self.default_session_token()},
        )
        self.assertEqual([item["id"] for item in listed["campaigns"]], [campaign_id])

        entered = self.api_post("/api/strategy/campaigns/enter", {"campaign_id": campaign_id})
        self.assertFalse(entered["campaign"]["resume"]["can_resume"])
        self.assertEqual(entered["campaign"]["resume"]["missing_initial_user_ids"], [])

        locked = self.api_post("/api/strategy/campaigns/lock", {"campaign_id": campaign_id})
        self.assertEqual(locked["campaign"]["status"], "active")
        self.assertTrue(locked["campaign"]["resume"]["can_resume"])

        resumed = self.api_post("/api/strategy/campaigns/resume", {"campaign_id": campaign_id})
        self.assertTrue(resumed["campaign"]["resume"]["can_resume"])
        self.assertEqual(resumed["campaign"]["id"], campaign_id)

        left = self.api_post("/api/strategy/campaigns/leave", {"campaign_id": campaign_id})
        self.assertFalse(left["resume"]["can_resume"])

    def test_scenario_first_campaign_settles_at_month_twelve_before_sandbox_continuation(self) -> None:
        # Given the public creation path receives custom sizes from an older client
        created = self.api_post(
            "/api/strategy/campaigns/create",
            {"name": "十二月争衡", "seed": 144, "city_count": 5, "faction_count": 4},
        )["campaign"]
        campaign_id = created["id"]
        contract = created["world"]["strategic_status"]["campaign_contract"]
        majors = [item for item in created["world"]["factions"] if item["faction_type"] == "major"]
        neutrals = [item for item in created["world"]["factions"] if item["faction_type"] == "neutral_city_state"]

        # Then the product scenario remains the declared 8-city / 12-month contract
        self.assertEqual((len(created["world"]["cities"]), len(majors), len(neutrals)), (8, 2, 6))
        self.assertEqual(contract["month_limit"], 12)
        self.assertEqual(contract["expected_duration_minutes"], [60, 90])

        self.api_post("/api/strategy/campaigns/lock", {"campaign_id": campaign_id})
        stored = server_module.STRATEGY_STORE.get_campaign_for_user(campaign_id, created["owner_user_id"])
        stored.world.current_month = 11
        server_module.STRATEGY_STORE.update_world(campaign_id, created["owner_user_id"], stored.world)

        # When the host resolves the twelfth month
        advanced = self.api_post("/api/strategy/campaigns/advance-month", {"campaign_id": campaign_id})["campaign"]
        status = advanced["world"]["strategic_status"]

        # Then a persisted assessment pauses further orders until the host chooses
        self.assertEqual(advanced["world"]["current_month"], 12)
        self.assertTrue(status["awaiting_conclusion_choice"])
        self.assertEqual(status["conclusion"]["state"], "settled")
        self.assertEqual(len(status["conclusion"]["rankings"]), 2)
        error_status, blocked = self.api_post_error(
            "/api/strategy/campaigns/advance-month",
            {"campaign_id": campaign_id, "session_token": self.default_session_token()},
        )
        self.assertEqual(error_status, 400)
        self.assertIn("结算", blocked["error"])
        error_status, blocked = self.api_post_error(
            "/api/strategy/campaigns/queue-action",
            {
                "campaign_id": campaign_id,
                "action_type": "set_city_policy",
                "payload": {},
                "session_token": self.default_session_token(),
            },
        )
        self.assertEqual(error_status, 400)
        self.assertIn("结算", blocked["error"])

        # And continuing preserves the result while reopening the monthly loop
        continued = self.api_post(
            "/api/strategy/campaigns/continue-sandbox",
            {"campaign_id": campaign_id},
        )["campaign"]
        self.assertEqual(continued["world"]["strategic_status"]["campaign_state"], "sandbox")
        month_thirteen = self.api_post(
            "/api/strategy/campaigns/advance-month",
            {"campaign_id": campaign_id},
        )["campaign"]
        self.assertEqual(month_thirteen["world"]["current_month"], 13)
        self.assertEqual(month_thirteen["world"]["strategic_status"]["conclusion"]["state"], "sandbox")

    def test_scenario_campaign_ends_with_readable_retrospective_and_frozen_archive(self) -> None:
        created = self.api_post(
            "/api/strategy/campaigns/create",
            {"name": "归档复盘战役", "seed": 245},
        )["campaign"]
        campaign_id = created["id"]
        self.api_post("/api/strategy/campaigns/lock", {"campaign_id": campaign_id})
        stored = server_module.STRATEGY_STORE.get_campaign_for_user(campaign_id, created["owner_user_id"])
        stored.world.current_month = 11
        server_module.STRATEGY_STORE.update_world(campaign_id, created["owner_user_id"], stored.world)

        settled = self.api_post(
            "/api/strategy/campaigns/advance-month",
            {"campaign_id": campaign_id},
        )["campaign"]
        retrospective = settled["world"]["campaign_retrospective"]

        self.assertEqual(retrospective["concluded_month"], 12)
        self.assertEqual(len(retrospective["faction_outcomes"]), 2)
        self.assertTrue(all(row["outcome_label"] in {"胜利", "存续", "败北·流亡"} for row in retrospective["faction_outcomes"]))
        self.assertTrue(any(row["month"] == 12 for row in retrospective["key_months"]))
        self.assertIn("resolved_battles", retrospective["summary"])

        archived = self.api_post(
            "/api/strategy/campaigns/archive",
            {"campaign_id": campaign_id},
        )["campaign"]
        status = archived["world"]["strategic_status"]
        self.assertEqual(status["campaign_state"], "archived")
        self.assertFalse(status["can_advance_month"])
        self.assertEqual(archived["world"]["campaign_retrospective"], retrospective)

        error_status, blocked = self.api_post_error(
            "/api/strategy/campaigns/advance-month",
            {"campaign_id": campaign_id, "session_token": self.default_session_token()},
        )
        self.assertEqual(error_status, 400)
        self.assertIn("归档", blocked["error"])

    def test_scenario_campaign_retrospective_ui_exposes_archive_and_all_history_sections(self) -> None:
        app_source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        self.assertIn('heading.textContent = "完整战役复盘"', app_source)
        self.assertIn('title: "势力结局"', app_source)
        self.assertIn('title: "关键月份"', app_source)
        self.assertIn('title: "城市变化"', app_source)
        self.assertIn('title: "战斗记录"', app_source)
        self.assertIn('title: "角色经历"', app_source)
        self.assertIn('archiveButton.textContent = "结束并归档战役"', app_source)
        self.assertIn(".strategy-retrospective-section", styles)

    def test_scenario_player_can_read_ai_two_month_goal_progress_and_last_action(self) -> None:
        created = self.api_post(
            "/api/strategy/campaigns/create",
            {"name": "AI 目标公示", "seed": 248},
        )["campaign"]
        campaign_id = created["id"]
        stored = server_module.STRATEGY_STORE.get_campaign_for_user(campaign_id, created["owner_user_id"])
        ai_city = next(city for city in stored.world.cities if city.owner_faction_id == "faction_2")
        ai_city.resources.food = 0
        server_module.STRATEGY_STORE.update_world(campaign_id, created["owner_user_id"], stored.world)
        locked = self.api_post("/api/strategy/campaigns/lock", {"campaign_id": campaign_id})["campaign"]
        visible_at_lock = next(row for row in locked["world"]["ai_strategic_goals"] if row["faction_id"] == "faction_2")
        self.assertEqual(visible_at_lock["goal_type"], "stabilize_food")

        month_two = self.api_post(
            "/api/strategy/campaigns/advance-month",
            {"campaign_id": campaign_id},
        )["campaign"]
        goal = next(row for row in month_two["world"]["ai_strategic_goals"] if row["faction_id"] == "faction_2")

        self.assertEqual(goal["goal_type"], "stabilize_food")
        self.assertEqual(goal["duration_months"], 2)
        self.assertEqual(goal["target_city_name"], ai_city.name)
        self.assertIn("粮", goal["title"])
        self.assertIn("方针", goal["last_action_summary"])
        self.assertGreaterEqual(goal["progress"], 0)
        self.assertLessEqual(goal["progress"], 100)

        month_three = self.api_post(
            "/api/strategy/campaigns/advance-month",
            {"campaign_id": campaign_id},
        )["campaign"]
        same_goal = next(row for row in month_three["world"]["ai_strategic_goals"] if row["faction_id"] == "faction_2")
        self.assertEqual(same_goal["id"], goal["id"])
        self.assertIn(same_goal["status"], {"active", "completed"})

    def test_scenario_ai_goal_ui_explains_duration_progress_reason_and_map_target(self) -> None:
        app_source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        self.assertIn('title.textContent = "AI 战略动向"', app_source)
        self.assertIn("持续 2～3 个月", app_source)
        self.assertIn('progress.setAttribute("role", "progressbar")', app_source)
        self.assertIn("选择原因：", app_source)
        self.assertIn("上次行动：", app_source)
        self.assertIn("定位 ${goal.target_city_name", app_source)
        self.assertIn(".strategy-ai-goal-progress", styles)

    def test_scenario_phase2_full_twelve_month_player_and_ai_campaign_walkthrough(self) -> None:
        created = self.api_post(
            "/api/strategy/campaigns/create",
            {"name": "Phase 2 十二月走查", "seed": 260},
        )["campaign"]
        campaign_id = created["id"]
        owner_id = created["owner_user_id"]
        faction_id = next(member["faction_id"] for member in created["members"] if member["user_id"] == owner_id)
        player_heroes = [
            hero
            for hero in created["world"]["strategic_hero_pool"]
            if hero["controller_type"] == "player" and hero["controller_user_id"] == owner_id
        ]
        self.assertEqual(len(player_heroes), 1)
        self.assertEqual(player_heroes[0]["faction_id"], faction_id)
        player_office = next(
            office for office in created["world"]["offices"] if office["id"] == player_heroes[0]["office_id"]
        )
        self.assertEqual(player_office["office_type"], "lord")

        self.api_post(
            "/api/strategy/campaigns/guide-action",
            {"campaign_id": campaign_id, "action": "survey_border"},
        )
        campaign = self.api_post("/api/strategy/campaigns/lock", {"campaign_id": campaign_id})["campaign"]
        lord = next(
            office
            for office in campaign["world"]["offices"]
            if office["faction_id"] == faction_id and office["office_type"] == "lord" and office["controller_type"] == "player"
        )
        governor = next(
            office
            for office in campaign["world"]["offices"]
            if office["faction_id"] == faction_id and office["office_type"] == "governor"
        )
        capital = next(city for city in campaign["world"]["cities"] if city["owner_faction_id"] == faction_id)
        self.assertTrue(campaign["world"]["ai_strategic_goals"])

        def queue(action_type: str, payload: dict) -> dict:
            return self.api_post(
                "/api/strategy/campaigns/queue-action",
                {
                    "campaign_id": campaign_id,
                    "action_type": action_type,
                    "action_payload": {"issuer_office_id": lord["id"], **payload},
                },
            )["campaign"]

        def order(objective: str, target_entity_id: str = "") -> dict:
            return queue(
                "issue_office_order",
                {
                    "receiver_office_id": governor["id"],
                    "objective": objective,
                    "target_entity_id": target_entity_id,
                    "office_order_type": "order",
                    "priority": 3,
                },
            )

        def advance() -> dict:
            advanced = self.api_post(
                "/api/strategy/campaigns/advance-month",
                {"campaign_id": campaign_id, "issuer_office_id": lord["id"]},
            )["campaign"]
            status = advanced["world"]["strategic_status"]
            self.assertEqual(status["months_remaining"], max(0, 12 - advanced["world"]["current_month"]))
            cycle = advanced["world"]["monthly_cycle"][faction_id]
            self.assertIn("must_handle", cycle)
            self.assertIn("advance_forecast", cycle)
            self.assertLessEqual(
                len(advanced["world"]["office_coordination"][faction_id]["high_consequence_decisions"]),
                3,
            )
            self.assertTrue(advanced["world"]["ai_strategic_goals"])
            return advanced

        # Month 1: survey, delegate governance and event handling, then establish militia doctrine.
        order("[引导:set_policy] 根据粮情与秩序设置首月方针", capital["id"])
        month_one_orders = order("[引导:resolve_event] 处理本月待决事件", capital["id"])
        queue("unlock_tactic_tech", {"tech_id": "local_militia"})
        self.assertEqual(
            len([action for action in month_one_orders["queued_actions"] if action["action_type"] == "issue_office_order"]),
            2,
        )
        campaign = advance()
        self.assertEqual(campaign["world"]["current_month"], 2)
        month_one_completed_orders = [
            office_order
            for office_order in campaign["world"]["office_orders"]
            if office_order["issued_month"] == 1 and office_order["status"] == "completed"
        ]
        self.assertEqual(len(month_one_completed_orders), 2)

        # Month 2: expand office capacity, then let the AI governor perform a legal ritual.
        queue("unlock_tactic_tech", {"tech_id": "command_staff_1"})
        order("[引导:ritual_or_appoint] 在本城举行祭祀并补充执行力量", capital["id"])
        campaign = advance()
        self.assertEqual(campaign["world"]["current_month"], 3)
        self.assertTrue(any(event["category"] == "hero_ritual_summoned" for event in campaign["world"]["event_log"]))

        # Month 3: choose the weakest adjacent non-owned city and resolve one real strategic conflict.
        cities = {city["id"]: city for city in campaign["world"]["cities"]}
        nodes = {node["id"]: node for node in campaign["world"]["nodes"]}
        sources = [city for city in cities.values() if city["owner_faction_id"] == faction_id]
        attack_pairs = []
        for source in sources:
            connected = set(nodes[source["node_id"]]["connected_node_ids"])
            for target in cities.values():
                if target["node_id"] in connected and target["owner_faction_id"] != faction_id:
                    attack_pairs.append((source, target))
        source, target = min(
            attack_pairs,
            key=lambda pair: (pair[1]["resources"]["troops"] + pair[1]["defense"] * 80, pair[1]["id"]),
        )
        queue(
            "declare_attack",
            {
                "source_city_id": source["id"],
                "target_city_id": target["id"],
                "resolution_mode": "quick",
            },
        )
        order("[引导:resolve_event] 处理冲突前的本月事件", capital["id"])
        campaign = advance()
        self.assertEqual(campaign["world"]["current_month"], 4)
        player_battles = [
            battle
            for battle in campaign["world"]["pending_battles"]
            if battle["attacker_faction_id"] == faction_id and battle["status"] == "resolved"
        ]
        self.assertTrue(player_battles)
        tutorial = campaign["world"]["campaign_tutorial"][faction_id]
        self.assertTrue(tutorial["completed"], tutorial)

        # Months 4–11: continue handling each visible event through the office chain and settle normally.
        while campaign["world"]["current_month"] < 12:
            if any(
                event["faction_id"] == faction_id and event["status"] == "pending"
                for event in campaign["world"]["story_events"]
            ):
                order("[引导:resolve_event] 处理本月持续事件", capital["id"])
            campaign = advance()
            if campaign["world"]["current_month"] < 12:
                self.assertFalse(campaign["world"]["strategic_status"]["campaign_complete"])

        status = campaign["world"]["strategic_status"]
        retrospective = campaign["world"]["campaign_retrospective"]
        self.assertTrue(status["awaiting_conclusion_choice"])
        self.assertEqual(status["conclusion"]["reason"], "time_limit")
        stored_final = server_module.STRATEGY_STORE.get_campaign_for_user(campaign_id, owner_id)
        self.assertEqual(len(stored_final.world.monthly_reports), 11)
        self.assertGreaterEqual(retrospective["summary"]["resolved_battles"], 1)
        self.assertGreaterEqual(retrospective["summary"]["story_choices"], 1)
        self.assertTrue(retrospective["hero_experiences"])
        self.assertTrue(any(row["month"] == 12 for row in retrospective["key_months"]))

        archived = self.api_post(
            "/api/strategy/campaigns/archive",
            {"campaign_id": campaign_id},
        )["campaign"]
        self.assertEqual(archived["world"]["strategic_status"]["campaign_state"], "archived")
        self.assertEqual(archived["world"]["campaign_retrospective"], retrospective)

    def test_scenario_phase3_full_campaign_compares_peaceful_integration_and_conquest(self) -> None:
        created = self.api_post(
            "/api/strategy/campaigns/create",
            {"name": "Phase 3 和平与征服走查", "seed": 3},
        )["campaign"]
        campaign_id = created["id"]
        owner_id = created["owner_user_id"]
        player_faction_id = next(member["faction_id"] for member in created["members"] if member["user_id"] == owner_id)
        campaign = self.api_post("/api/strategy/campaigns/lock", {"campaign_id": campaign_id})["campaign"]
        lord = next(
            office for office in campaign["world"]["offices"]
            if office["faction_id"] == player_faction_id and office["office_type"] == "lord" and office["controller_type"] == "player"
        )
        capital = next(city for city in campaign["world"]["cities"] if city["owner_faction_id"] == player_faction_id)
        governor = next(
            office for office in campaign["world"]["offices"]
            if office["faction_id"] == player_faction_id
            and office["office_type"] == "governor"
            and capital["id"] in office["managed_entity_ids"]
        )
        recruit_policy = next(policy for policy in campaign["world"]["policy_choices"] if "征兵" in policy)

        def queue(action_type: str, payload: dict) -> dict:
            return self.api_post(
                "/api/strategy/campaigns/queue-action",
                {
                    "campaign_id": campaign_id,
                    "action_type": action_type,
                    "action_payload": {"issuer_office_id": lord["id"], **payload},
                },
            )["campaign"]

        def advance() -> dict:
            return self.api_post(
                "/api/strategy/campaigns/advance-month",
                {"campaign_id": campaign_id, "issuer_office_id": lord["id"]},
            )["campaign"]

        # Month 1: the human lord gives an exact persistent recruitment policy through the office chain.
        queue(
            "issue_office_order",
            {
                "receiver_office_id": governor["id"],
                "objective": f"将{capital['name']}设为{recruit_policy}",
                "target_entity_id": capital["id"],
                "office_order_type": "set_policy",
                "city_policy": recruit_policy,
                "priority": 3,
            },
        )
        campaign = advance()
        capital_after_order = next(city for city in campaign["world"]["cities"] if city["id"] == capital["id"])
        self.assertEqual(capital_after_order["policy"], recruit_policy)

        # Months 2–4: the player builds troops while the rival AI develops one focused peaceful route.
        while campaign["world"]["current_month"] < 5:
            campaign = advance()
        self.assertTrue(any(
            event["category"] == "neutral_diplomacy_accepted"
            and event["related_ids"][0] == "faction_2"
            for event in campaign["world"]["event_log"]
        ))

        # Month 5: choose the weakest adjacent target and commit 75% of the accumulated garrison.
        cities = {city["id"]: city for city in campaign["world"]["cities"]}
        nodes = {node["id"]: node for node in campaign["world"]["nodes"]}
        source = cities[capital["id"]]
        connected = set(nodes[source["node_id"]]["connected_node_ids"])
        targets = [
            city for city in cities.values()
            if city["node_id"] in connected and city["owner_faction_id"] != player_faction_id
        ]
        target = min(
            targets,
            key=lambda city: (
                city["resources"]["troops"]
                + city["defense"] * 80
                + city["support_by_faction"].get(city["owner_faction_id"], 50) * 3,
                city["id"],
            ),
        )
        queue(
            "declare_attack",
            {
                "source_city_id": source["id"],
                "target_city_id": target["id"],
                "resolution_mode": "quick",
            },
        )
        campaign = advance()
        conquered = next(city for city in campaign["world"]["cities"] if city["id"] == target["id"])
        self.assertEqual(conquered["owner_faction_id"], player_faction_id)
        self.assertEqual(conquered["occupation_governance"]["status"], "pending")

        # Month 6: govern the conquest instead of receiving a free stable city.
        queue(
            "choose_occupation_policy",
            {"city_id": target["id"], "policy_id": "autonomy"},
        )
        campaign = advance()
        governed = next(city for city in campaign["world"]["cities"] if city["id"] == target["id"])
        self.assertEqual((governed["occupation_governance"]["status"], governed["occupation_governance"]["policy_id"]), ("active", "autonomy"))

        # Months 7–12: both routes continue through normal settlement and reach the same final assessment.
        while campaign["world"]["current_month"] < 12:
            campaign = advance()
        events = campaign["world"]["event_log"]
        self.assertTrue(any(event["category"] == "neutral_city_state_peacefully_integrated" for event in events))
        self.assertTrue(any(event["category"] == "occupation_settled" and target["id"] in event["related_ids"] for event in events))
        self.assertTrue(any(event["category"] == "strategy_ai_political_decision" for event in events))
        status = campaign["world"]["strategic_status"]
        self.assertTrue(status["awaiting_conclusion_choice"])
        rankings = status["conclusion"]["rankings"]
        player_row = next(row for row in rankings if row["faction_id"] == player_faction_id)
        ai_row = next(row for row in rankings if row["faction_id"] == "faction_2")
        self.assertGreaterEqual(player_row["city_score"], 200)
        self.assertGreaterEqual(ai_row["influence_score"], 25)
        self.assertEqual(len(server_module.STRATEGY_STORE.get_campaign_for_user(campaign_id, owner_id).world.monthly_reports), 11)
        app_source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('["set_policy", "设置城市方针"]', app_source)
        self.assertIn('city_policy: policyOrder ? cityPolicy.value : ""', app_source)

    def test_scenario_neutral_city_states_hold_until_a_lord_incites_them(self) -> None:
        # Given a new strategic campaign with two major factions
        campaign = self.api_post(
            "/api/strategy/campaigns/create",
            {"name": "中立诸邦", "seed": 7, "city_count": 6, "faction_count": 2},
        )["campaign"]
        factions = campaign["world"]["factions"]
        majors = [item for item in factions if item["faction_type"] == "major"]
        neutrals = [item for item in factions if item["faction_type"] == "neutral_city_state"]

        # Then neutral one-city factions outnumber major starting cities and expose mortal governors
        self.assertEqual(len(majors), 2)
        self.assertEqual(len(neutrals), 6)
        self.assertGreater(len(neutrals), len(majors))
        self.assertTrue(all(item["governor_name"] for item in neutrals))
        self.assertTrue(all(item["neutral_politics"]["posture"]["label"] for item in neutrals))
        self.assertTrue(all(item["neutral_politics"]["current_need"]["label"] for item in neutrals))
        self.assertTrue(all(item["neutral_politics"]["fear"]["label"] for item in neutrals))
        self.assertTrue(all(item["neutral_politics"]["governor_position"]["label"] for item in neutrals))
        self.assertTrue(all(
            len(item["neutral_politics"]["relationships"]) == 2
            and {relation["score"] for relation in item["neutral_politics"]["relationships"]} == {0}
            for item in neutrals
        ))
        app_source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        style_source = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
        self.assertIn('card.classList.add("is-neutral-city-state")', app_source)
        self.assertIn("strategyIsNeutralCityState(campaign, selectedCity?.owner_faction_id)", app_source)
        self.assertIn(".LordWorkspace .strategy-command-card.is-neutral-city-state", style_source)
        self.assertTrue(all(
            len([city for city in campaign["world"]["cities"] if city["owner_faction_id"] == item["id"]]) == 1
            for item in neutrals
        ))

        campaign_id = campaign["id"]
        locked = self.api_post("/api/strategy/campaigns/lock", {"campaign_id": campaign_id})["campaign"]
        neutral = next(item for item in locked["world"]["factions"] if item["id"] == "neutral_city_state_3")

        # When the player lord spends a command and money to incite a bordering city-state
        queued = self.api_post(
            "/api/strategy/campaigns/queue-action",
            {
                "campaign_id": campaign_id,
                "action_type": "incite_neutral_city_state",
                "payload": {
                    "neutral_faction_id": neutral["id"],
                    "target_faction_id": "faction_2",
                },
            },
        )["campaign"]
        self.assertEqual(queued["queued_actions"][-1]["command_cost"], 1)

        advanced = self.api_post(
            "/api/strategy/campaigns/advance-month",
            {"campaign_id": campaign_id},
        )["campaign"]
        neutral_after = next(item for item in advanced["world"]["factions"] if item["id"] == neutral["id"])

        # Then it attacks once through its governor and returns to a passive posture
        self.assertIsNone(neutral_after["incited_against_faction_id"])
        categories = [event["category"] for event in advanced["world"]["event_log"]]
        self.assertIn("neutral_city_state_incited", categories)
        self.assertIn("neutral_city_state_incitement_spent", categories)

    def test_scenario_lord_replaces_one_neutral_negotiation_and_signs_non_aggression(self) -> None:
        # Given a locked campaign and a neutral city-state bordering the player's faction
        campaign = self.api_post(
            "/api/strategy/campaigns/create",
            {"name": "边境交涉", "seed": 7, "city_count": 8, "faction_count": 2},
        )["campaign"]
        campaign_id = campaign["id"]
        locked = self.api_post("/api/strategy/campaigns/lock", {"campaign_id": campaign_id})["campaign"]
        neutral = next(
            item for item in locked["world"]["factions"]
            if item["faction_type"] == "neutral_city_state"
            and any(
                option["id"] == "trade" and option["can_propose"]
                for relation in item["neutral_politics"]["relationships"]
                if relation["faction_id"] == "faction_1"
                for option in relation["diplomacy_options"]
            )
        )

        # When the lord replaces an aid plan with trade for the same city-state
        aid = self.api_post(
            "/api/strategy/campaigns/queue-action",
            {
                "campaign_id": campaign_id,
                "action_type": "neutral_diplomacy",
                "payload": {"neutral_faction_id": neutral["id"], "diplomacy_action_id": "aid"},
            },
        )
        self.assertFalse(aid["submission"]["replaced"])
        trade = self.api_post(
            "/api/strategy/campaigns/queue-action",
            {
                "campaign_id": campaign_id,
                "action_type": "neutral_diplomacy",
                "payload": {"neutral_faction_id": neutral["id"], "diplomacy_action_id": "trade"},
            },
        )
        self.assertTrue(trade["submission"]["replaced"])
        self.assertEqual(trade["submission"]["command_points"]["used"], 1)
        queued = [item for item in trade["campaign"]["queued_actions"] if item["action_type"] == "neutral_diplomacy"]
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0]["payload"]["diplomacy_action_id"], "trade")

        settled = self.api_post(
            "/api/strategy/campaigns/advance-month",
            {"campaign_id": campaign_id},
        )["campaign"]
        neutral_after_trade = next(item for item in settled["world"]["factions"] if item["id"] == neutral["id"])
        relation_after_trade = next(
            item for item in neutral_after_trade["neutral_politics"]["relationships"]
            if item["faction_id"] == "faction_1"
        )
        self.assertEqual(relation_after_trade["score"], 8)
        self.assertIn("neutral_diplomacy_accepted", [item["category"] for item in settled["world"]["event_log"]])

        # And when the next month proposes non-aggression, the accepted agreement is visible
        self.api_post(
            "/api/strategy/campaigns/queue-action",
            {
                "campaign_id": campaign_id,
                "action_type": "neutral_diplomacy",
                "payload": {"neutral_faction_id": neutral["id"], "diplomacy_action_id": "non_aggression"},
            },
        )
        peaceful = self.api_post(
            "/api/strategy/campaigns/advance-month",
            {"campaign_id": campaign_id},
        )["campaign"]
        peaceful_neutral = next(item for item in peaceful["world"]["factions"] if item["id"] == neutral["id"])
        peaceful_relation = next(
            item for item in peaceful_neutral["neutral_politics"]["relationships"]
            if item["faction_id"] == "faction_1"
        )
        self.assertEqual(peaceful_relation["score"], 14)
        self.assertTrue(any(
            item["agreement_type"] == "non_aggression" and item["major_faction_id"] == "faction_1"
            for item in peaceful_neutral["neutral_politics"]["agreements"]
        ))
        agreement = next(
            item for item in peaceful_neutral["neutral_politics"]["agreements"]
            if item["agreement_type"] == "non_aggression" and item["major_faction_id"] == "faction_1"
        )
        self.assertEqual((agreement["status"], agreement["remaining_months"]), ("active", 2))
        self.assertEqual(next(item for item in peaceful["world"]["factions"] if item["id"] == "faction_1")["diplomatic_reputation"], 50)
        self.assertTrue(any(
            item["category"] == "negotiation_accepted"
            for item in peaceful_neutral["neutral_politics"]["diplomatic_memory"]
        ))

        # Then two peaceful month ends fulfill the promise and leave a visible reputation consequence
        for _ in range(2):
            peaceful = self.api_post(
                "/api/strategy/campaigns/advance-month",
                {"campaign_id": campaign_id},
            )["campaign"]
        fulfilled_neutral = next(item for item in peaceful["world"]["factions"] if item["id"] == neutral["id"])
        fulfilled = next(
            item for item in fulfilled_neutral["neutral_politics"]["agreements"]
            if item["agreement_type"] == "non_aggression" and item["major_faction_id"] == "faction_1"
        )
        self.assertEqual((fulfilled["status"], fulfilled["end_reason"]), ("ended", "fulfilled"))
        self.assertEqual(next(item for item in peaceful["world"]["factions"] if item["id"] == "faction_1")["diplomatic_reputation"], 54)
        self.assertTrue(any(
            item["category"] == "agreement_fulfilled"
            for item in fulfilled_neutral["neutral_politics"]["diplomatic_memory"]
        ))

    def test_scenario_lord_sees_every_gate_and_peacefully_integrates_a_trusted_city_state(self) -> None:
        # Given a locked campaign where one adjacent city-state has mature trust and local support
        created = self.api_post(
            "/api/strategy/campaigns/create",
            {"name": "和平归附", "seed": 7, "city_count": 8, "faction_count": 2},
        )["campaign"]
        campaign_id = created["id"]
        owner_id = created["owner_user_id"]
        locked = self.api_post("/api/strategy/campaigns/lock", {"campaign_id": campaign_id})["campaign"]
        neutral_public = next(
            item for item in locked["world"]["factions"]
            if item["faction_type"] == "neutral_city_state"
            and next(
                relation for relation in item["neutral_politics"]["relationships"]
                if relation["faction_id"] == "faction_1"
            )["peaceful_integration"]["requirements"][1]["met"]
        )
        stored = server_module.STRATEGY_STORE.get_campaign_for_user(campaign_id, owner_id)
        stored.world.current_month = 4
        actor = next(item for item in stored.world.factions if item.faction_id == "faction_1")
        neutral = next(item for item in stored.world.factions if item.faction_id == neutral_public["id"])
        city = next(item for item in stored.world.cities if item.owner_faction_id == neutral.faction_id)
        actor.resources.money = max(actor.resources.money, 200)
        actor.resources.food = max(actor.resources.food, 200)
        actor.diplomatic_reputation = 60
        neutral.relations[actor.faction_id] = 60
        neutral.influence_by_faction[actor.faction_id] = 60
        city.support_by_faction[actor.faction_id] = 60
        stored.world.diplomatic_agreements.append(DiplomaticAgreement(
            agreement_id="behavior-fulfilled-integration",
            agreement_type="non_aggression",
            major_faction_id=actor.faction_id,
            neutral_faction_id=neutral.faction_id,
            started_month=1,
            expires_month=4,
            ended_month=4,
            status="ended",
            end_reason="fulfilled",
        ))
        server_module.STRATEGY_STORE.update_world(campaign_id, owner_id, stored.world)

        # When the lord queues the publicly enabled two-command peaceful integration
        queued = self.api_post(
            "/api/strategy/campaigns/queue-action",
            {
                "campaign_id": campaign_id,
                "action_type": "peaceful_integration",
                "payload": {"neutral_faction_id": neutral.faction_id},
            },
        )
        action = queued["campaign"]["queued_actions"][-1]
        self.assertEqual((action["action_type"], action["command_cost"]), ("peaceful_integration", 2))
        queued_neutral = next(item for item in queued["campaign"]["world"]["factions"] if item["id"] == neutral.faction_id)
        relationship = next(item for item in queued_neutral["neutral_politics"]["relationships"] if item["faction_id"] == actor.faction_id)
        self.assertEqual((relationship["influence"], relationship["local_support"]), (60, 60))
        self.assertTrue(relationship["peaceful_integration"]["can_integrate"])

        # Then month settlement changes ownership without a battle and preserves the political record
        advanced = self.api_post(
            "/api/strategy/campaigns/advance-month",
            {"campaign_id": campaign_id},
        )["campaign"]
        integrated_city = next(item for item in advanced["world"]["cities"] if item["id"] == city.city_id)
        self.assertEqual(integrated_city["owner_faction_id"], actor.faction_id)
        self.assertGreaterEqual(integrated_city["support_by_faction"][actor.faction_id], 70)
        self.assertIn("和平整合", integrated_city["traits"])
        self.assertFalse(any(
            battle["target_city_id"] == city.city_id
            for battle in advanced["world"]["pending_battles"]
        ))
        self.assertIn("neutral_city_state_peacefully_integrated", [item["category"] for item in advanced["world"]["event_log"]])
        integrated_neutral = next(item for item in advanced["world"]["factions"] if item["id"] == neutral.faction_id)
        self.assertTrue(any(item["category"] == "peaceful_integration" for item in integrated_neutral["neutral_politics"]["diplomatic_memory"]))

    def test_scenario_lord_governs_one_occupation_and_funds_resistance_in_an_enemy_occupation(self) -> None:
        # Given two recently conquered neutral cities, one held by each major faction
        created = self.api_post(
            "/api/strategy/campaigns/create",
            {"name": "占领政治", "seed": 7, "city_count": 8, "faction_count": 2},
        )["campaign"]
        campaign_id = created["id"]
        owner_id = created["owner_user_id"]
        self.api_post("/api/strategy/campaigns/lock", {"campaign_id": campaign_id})
        stored = server_module.STRATEGY_STORE.get_campaign_for_user(campaign_id, owner_id)
        neutral_cities = [
            city for city in stored.world.cities
            if next(item for item in stored.world.factions if item.faction_id == city.owner_faction_id).is_neutral_city_state
        ]
        own_occupation, enemy_occupation = neutral_cities[:2]
        own_previous, enemy_previous = own_occupation.owner_faction_id, enemy_occupation.owner_faction_id
        own_occupation.owner_faction_id = "faction_1"
        enemy_occupation.owner_faction_id = "faction_2"
        mark_city_captured(
            stored.world,
            city_id=own_occupation.city_id,
            previous_owner_faction_id=own_previous,
            occupier_faction_id="faction_1",
        )
        mark_city_captured(
            stored.world,
            city_id=enemy_occupation.city_id,
            previous_owner_faction_id=enemy_previous,
            occupier_faction_id="faction_2",
        )
        own_occupation.support_by_faction["faction_1"] = 45
        enemy_occupation.support_by_faction["faction_2"] = 45
        next(item for item in stored.world.factions if item.faction_id == "faction_1").resources.money = 300
        server_module.STRATEGY_STORE.update_world(campaign_id, owner_id, stored.world)

        # When the lord chooses autonomy at home and funds resistance in the enemy city
        occupation_queued = self.api_post(
            "/api/strategy/campaigns/queue-action",
            {
                "campaign_id": campaign_id,
                "action_type": "choose_occupation_policy",
                "payload": {"city_id": own_occupation.city_id, "policy_id": "autonomy"},
            },
        )
        self.assertEqual(occupation_queued["campaign"]["queued_actions"][-1]["command_cost"], 1)
        own_public = next(item for item in occupation_queued["campaign"]["world"]["cities"] if item["id"] == own_occupation.city_id)
        self.assertEqual((own_public["occupation_governance"]["status"], own_public["occupation_governance"]["income_percent"]), ("pending", 50))
        self.assertEqual({item["id"] for item in own_public["occupation_governance"]["policy_choices"]}, {"autonomy", "integration", "garrison", "plunder"})

        funding_queued = self.api_post(
            "/api/strategy/campaigns/queue-action",
            {
                "campaign_id": campaign_id,
                "action_type": "fund_rebellion",
                "payload": {"city_id": enemy_occupation.city_id},
            },
        )
        self.assertEqual(funding_queued["campaign"]["queued_actions"][-1]["command_cost"], 1)
        enemy_public = next(item for item in funding_queued["campaign"]["world"]["cities"] if item["id"] == enemy_occupation.city_id)
        self.assertTrue(enemy_public["rebellion_funding_options"]["faction_1"]["can_fund"])
        app_source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("const occupationCrisis = Boolean(occupation.status", app_source)
        self.assertIn("|| occupationCrisis", app_source)
        self.assertIn("|| externalFundingTarget", app_source)
        self.assertIn('cityCard.classList.add("is-political-crisis")', app_source)
        style_source = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
        self.assertIn(".LordWorkspace .strategy-command-card.is-political-crisis", style_source)

        # Then month settlement applies both political choices and preserves their sources
        advanced = self.api_post(
            "/api/strategy/campaigns/advance-month",
            {"campaign_id": campaign_id},
        )["campaign"]
        own_after = next(item for item in advanced["world"]["cities"] if item["id"] == own_occupation.city_id)
        enemy_after = next(item for item in advanced["world"]["cities"] if item["id"] == enemy_occupation.city_id)
        self.assertEqual((own_after["occupation_governance"]["status"], own_after["occupation_governance"]["policy_id"]), ("active", "autonomy"))
        self.assertEqual(own_after["occupation_governance"]["remaining_settlements"], 2)
        # The rival AI may immediately answer the newly funded force through the same
        # rebellion-action rules, so the source marker/event is the durable assertion.
        self.assertTrue(any(
            state.startswith("rebellion_sponsor:faction_1:")
            for state in enemy_after["event_states"]
        ))
        categories = [item["category"] for item in advanced["world"]["event_log"]]
        self.assertIn("occupation_policy_selected", categories)
        self.assertIn("rebellion_external_funding", categories)
        self.assertIn("strategy_ai_political_decision", categories)

    def test_scenario_player_selects_hero_and_founds_persistent_faction(self) -> None:
        created = self.api_post(
            "/api/strategy/campaigns/create",
            {"name": "武将建国", "seed": 901, "city_count": 6, "faction_count": 2},
        )["campaign"]
        roaming = next(hero for hero in created["world"]["strategic_hero_pool"] if hero["status"] == "roaming")

        founded = self.api_post(
            "/api/strategy/campaigns/choose-hero-path",
            {
                "campaign_id": created["id"],
                "hero_code": roaming["code"],
                "path": "found",
            },
        )["campaign"]
        controlled = next(
            hero
            for hero in founded["world"]["strategic_hero_pool"]
            if hero["controller_type"] == "player"
        )
        member = next(item for item in founded["members"] if item["user_id"] == founded["owner_user_id"])
        capital = next(city for city in founded["world"]["cities"] if city["id"] == controlled["city_id"])

        self.assertEqual(len(founded["world"]["factions"]), 9)
        self.assertEqual((controlled["status"], controlled["faction_id"]), ("serving", member["faction_id"]))
        self.assertEqual(capital["owner_faction_id"], member["faction_id"])
        self.assertEqual(
            [hero["code"] for hero in founded["world"]["strategic_hero_pool"] if hero["faction_id"] == member["faction_id"]],
            [controlled["code"]],
        )
        self.assertTrue(
            any(
                office["status"] == "vacant"
                for office in founded["world"]["offices"]
                if office["faction_id"] == member["faction_id"] and office["office_type"] != "lord"
            )
        )

        self.api_post("/api/strategy/campaigns/lock", {"campaign_id": created["id"]})
        another = next(
            hero
            for hero in founded["world"]["strategic_hero_pool"]
            if hero["status"] == "roaming" and hero["code"] != controlled["code"]
        )
        status, error = self.api_post_error(
            "/api/strategy/campaigns/choose-hero-path",
            {
                "campaign_id": created["id"],
                "hero_code": another["code"],
                "path": "roaming",
                "session_token": self.default_session_token(),
            },
        )
        self.assertEqual(status, 400)
        self.assertIn("不能改为控制另一名武将", error["error"])

    def test_scenario_strategy_campaign_join_code_lock_and_multiplayer_resume_gate(self) -> None:
        created = self.api_post(
            "/api/strategy/campaigns/create",
            {"name": "åŒäººåŸŽé‚¦", "seed": 95, "city_count": 6, "faction_count": 2},
        )
        campaign_id = created["campaign"]["id"]
        join_code = created["campaign"]["join_code"]
        bob = self.api_post(
            "/api/auth/register",
            {"username": "BobUser", "password": "secret123"},
        )
        carol = self.api_post(
            "/api/auth/register",
            {"username": "CarolUser", "password": "secret123"},
        )

        status, forbidden_rotate = self.api_post_error(
            "/api/strategy/campaigns/rotate-join-code",
            {"campaign_id": campaign_id, "session_token": bob["session_token"]},
        )
        self.assertEqual(status, 403)
        self.assertIn("重新生成加入码", forbidden_rotate["error"])

        rotated = self.api_post(
            "/api/strategy/campaigns/rotate-join-code",
            {"campaign_id": campaign_id},
        )
        rotated_code = rotated["campaign"]["join_code"]
        self.assertNotEqual(rotated_code, join_code)

        status, old_code_join = self.api_post_error(
            "/api/strategy/campaigns/join",
            {"join_code": join_code, "session_token": bob["session_token"]},
        )
        self.assertEqual(status, 404)
        self.assertIn("加入码不存在", old_code_join["error"])
        join_code = rotated_code

        joined = self.api_post(
            "/api/strategy/campaigns/join",
            {"join_code": join_code.lower(), "session_token": bob["session_token"]},
        )
        self.assertEqual(joined["campaign"]["id"], campaign_id)
        self.assertEqual(joined["campaign"]["status"], "lobby")
        self.assertEqual([member["username"] for member in joined["campaign"]["members"]], ["TestUser", "BobUser"])
        self.assertEqual(joined["campaign"]["resume"]["missing_initial_user_ids"], [])

        status, forbidden_lock = self.api_post_error(
            "/api/strategy/campaigns/lock",
            {"campaign_id": campaign_id, "session_token": bob["session_token"]},
        )
        self.assertEqual(status, 403)
        self.assertIn("锁定初始玩家", forbidden_lock["error"])

        self.api_post("/api/strategy/campaigns/enter", {"campaign_id": campaign_id})
        bob_entered = self.api_post(
            "/api/strategy/campaigns/enter",
            {"campaign_id": campaign_id, "session_token": bob["session_token"]},
        )
        self.assertFalse(bob_entered["campaign"]["resume"]["can_resume"])

        locked = self.api_post("/api/strategy/campaigns/lock", {"campaign_id": campaign_id})
        self.assertTrue(locked["campaign"]["resume"]["can_resume"])

        status, locked_join = self.api_post_error(
            "/api/strategy/campaigns/join",
            {"join_code": join_code, "session_token": carol["session_token"]},
        )
        self.assertEqual(status, 409)
        self.assertIn("已经锁定", locked_join["error"])

        bob_left = self.api_post(
            "/api/strategy/campaigns/leave",
            {"campaign_id": campaign_id, "session_token": bob["session_token"]},
        )
        self.assertFalse(bob_left["resume"]["can_resume"])

        status, blocked_resume = self.api_post_error(
            "/api/strategy/campaigns/resume",
            {"campaign_id": campaign_id, "session_token": self.default_session_token()},
        )
        self.assertEqual(status, 409)
        self.assertIn("所有初始玩家在线", blocked_resume["error"])

        bob_reentered = self.api_post(
            "/api/strategy/campaigns/enter",
            {"campaign_id": campaign_id, "session_token": bob["session_token"]},
        )
        self.assertTrue(bob_reentered["campaign"]["resume"]["can_resume"])
        bob_faction_id = next(
            member["faction_id"]
            for member in bob_reentered["campaign"]["members"]
            if member["username"] == "BobUser"
        )
        bob_city_id = next(
            city["id"]
            for city in bob_reentered["campaign"]["world"]["cities"]
            if city["owner_faction_id"] == bob_faction_id
        )
        bob_policy = self.api_post(
            "/api/strategy/campaigns/set-city-policy",
            {
                "campaign_id": campaign_id,
                "city_id": bob_city_id,
                "policy": "征兵优先",
                "session_token": bob["session_token"],
            },
        )
        self.assertEqual(
            next(city for city in bob_policy["campaign"]["world"]["cities"] if city["id"] == bob_city_id)["policy"],
            "征兵优先",
        )

        status, bob_advance = self.api_post_error(
            "/api/strategy/campaigns/advance-month",
            {"campaign_id": campaign_id, "session_token": bob["session_token"]},
        )
        self.assertEqual(status, 403)
        self.assertIn("房主", bob_advance["error"])

    def test_scenario_strategy_campaign_resume_rejects_non_member(self) -> None:
        created = self.api_post(
            "/api/strategy/campaigns/create",
            {"name": "私有战役", "seed": 91},
        )
        campaign_id = created["campaign"]["id"]
        other = self.api_post(
            "/api/auth/register",
            {"username": "OtherUser", "password": "secret123"},
        )
        status, forbidden = self.api_post_error(
            "/api/strategy/campaigns/enter",
            {"campaign_id": campaign_id, "session_token": other["session_token"]},
        )

        self.assertEqual(status, 403)
        self.assertIn("不是这个战役的成员", forbidden["error"])

    def test_scenario_strategy_campaign_advances_one_month_after_resume_gate(self) -> None:
        created = self.api_post(
            "/api/strategy/campaigns/create",
            {"name": "月度结算战役", "seed": 92, "city_count": 6, "faction_count": 2},
        )
        campaign_id = created["campaign"]["id"]

        status, blocked = self.api_post_error(
            "/api/strategy/campaigns/advance-month",
            {"campaign_id": campaign_id, "session_token": self.default_session_token()},
        )
        self.assertEqual(status, 409)
        self.assertIn("锁定初始玩家", blocked["error"])

        self.api_post("/api/strategy/campaigns/enter", {"campaign_id": campaign_id})
        self.api_post("/api/strategy/campaigns/lock", {"campaign_id": campaign_id})
        advanced = self.api_post("/api/strategy/campaigns/advance-month", {"campaign_id": campaign_id})
        world = advanced["campaign"]["world"]

        self.assertTrue(advanced["campaign"]["resume"]["can_resume"])
        self.assertEqual(world["current_month"], 2)
        self.assertIn("month_2_resolved", world["memory_tags"])
        self.assertTrue(any(event["category"] == "city_income" for event in world["event_log"]))
        faction_id = next(member["faction_id"] for member in advanced["campaign"]["members"] if member["user_id"] == 1)
        cycle = world["monthly_cycle"][faction_id]
        self.assertNotIn("monthly_reports", world)
        self.assertEqual(cycle["previous_month"]["month"], 2)
        self.assertTrue(cycle["previous_month"]["city_changes"])
        self.assertTrue(cycle["advance_forecast"]["cities"])
        self.assertIn("战争", cycle["advance_forecast"]["disclaimer"])

    def test_scenario_first_campaign_guide_can_acknowledge_border_and_skip_without_state_rewards(self) -> None:
        created = self.api_post(
            "/api/strategy/campaigns/create",
            {"name": "前三月引导", "seed": 922},
        )["campaign"]
        campaign_id = created["id"]
        faction_id = next(member["faction_id"] for member in created["members"] if member["user_id"] == 1)
        guide = created["world"]["campaign_tutorial"][faction_id]
        resources_before = next(item["resources"] for item in created["world"]["factions"] if item["id"] == faction_id)

        self.assertEqual((guide["completed_count"], guide["total_count"]), (0, 5))
        self.assertEqual([step["chapter"] for step in guide["steps"][:3]], ["第一月 · 读局与治理"] * 3)
        surveyed = self.api_post(
            "/api/strategy/campaigns/guide-action",
            {"campaign_id": campaign_id, "action": "survey_border"},
        )["campaign"]
        surveyed_guide = surveyed["world"]["campaign_tutorial"][faction_id]
        self.assertTrue(next(step for step in surveyed_guide["steps"] if step["id"] == "survey_border")["completed"])

        skipped = self.api_post(
            "/api/strategy/campaigns/guide-action",
            {"campaign_id": campaign_id, "action": "skip"},
        )["campaign"]
        skipped_guide = skipped["world"]["campaign_tutorial"][faction_id]
        resources_after = next(item["resources"] for item in skipped["world"]["factions"] if item["id"] == faction_id)
        self.assertTrue(skipped_guide["skipped"])
        self.assertEqual(skipped_guide["skipped_month"], 1)
        self.assertEqual(resources_after, resources_before)
        self.assertNotIn("progress_by_faction", skipped["world"]["campaign_tutorial"])

    def test_scenario_ai_governor_executes_lord_order_and_returns_cost_eta_and_result(self) -> None:
        created = self.api_post(
            "/api/strategy/campaigns/create",
            {"name": "官职自动化", "seed": 923},
        )["campaign"]
        campaign_id = created["id"]
        owner_id = created["owner_user_id"]
        self.api_post("/api/strategy/campaigns/enter", {"campaign_id": campaign_id})
        self.api_post("/api/strategy/campaigns/lock", {"campaign_id": campaign_id})
        stored = server_module.STRATEGY_STORE.get_campaign_for_user(campaign_id, owner_id)
        city = next(item for item in stored.world.cities if item.owner_faction_id == "faction_1")
        city.resources.food = 0
        city.support_by_faction["faction_1"] = 20
        governor = next(item for item in stored.world.offices if item.faction_id == "faction_1" and item.office_type == "governor")
        server_module.STRATEGY_STORE.update_world(campaign_id, owner_id, stored.world)

        queued = self.api_post(
            "/api/strategy/campaigns/queue-action",
            {
                "campaign_id": campaign_id,
                "action_type": "issue_office_order",
                "action_payload": {
                    "receiver_office_id": governor.office_id,
                    "objective": "[引导:set_policy] 处理粮食危机",
                    "target_entity_id": city.city_id,
                    "office_order_type": "order",
                    "priority": 3,
                },
            },
        )
        execution = queued["submission"]["execution"]
        self.assertEqual(execution["executor_office_id"], governor.office_id)
        self.assertEqual(execution["command_cost"], 1)
        self.assertEqual(execution["expected_completion_month"], 2)

        advanced = self.api_post("/api/strategy/campaigns/advance-month", {"campaign_id": campaign_id})["campaign"]
        order = advanced["world"]["office_orders"][-1]
        feedback = advanced["world"]["office_coordination"]["faction_1"]["order_feedback"][-1]
        self.assertEqual(order["status"], "completed")
        self.assertIn("已由城主设为", order["details"]["result_summary"])
        self.assertEqual(feedback["status"], "completed")
        self.assertEqual(feedback["expected_completion_month"], 2)
        self.assertIn("已由城主设为", feedback["result_summary"])


    def test_scenario_strategy_queue_submission_reports_new_and_replaced_plan(self) -> None:
        created = self.api_post(
            "/api/strategy/campaigns/create",
            {"name": "军令反馈战役", "seed": 921, "city_count": 6, "faction_count": 2},
        )["campaign"]
        campaign_id = created["id"]
        self.api_post("/api/strategy/campaigns/enter", {"campaign_id": campaign_id})
        locked = self.api_post("/api/strategy/campaigns/lock", {"campaign_id": campaign_id})["campaign"]
        faction_id = next(member["faction_id"] for member in locked["members"] if member["user_id"] == 1)
        city = next(item for item in locked["world"]["cities"] if item["owner_faction_id"] == faction_id)
        policies = [item for item in locked["world"]["policy_choices"] if item != city["policy"]]

        first = self.api_post(
            "/api/strategy/campaigns/queue-action",
            {
                "campaign_id": campaign_id,
                "action_type": "set_city_policy",
                "action_payload": {"city_id": city["id"], "policy": policies[0]},
            },
        )
        second = self.api_post(
            "/api/strategy/campaigns/queue-action",
            {
                "campaign_id": campaign_id,
                "action_type": "set_city_policy",
                "action_payload": {"city_id": city["id"], "policy": policies[1]},
            },
        )

        self.assertFalse(first["submission"]["replaced"])
        self.assertTrue(second["submission"]["replaced"])
        self.assertEqual(second["submission"]["previous_action"]["payload"]["policy"], policies[0])
        self.assertEqual(second["submission"]["command_points"]["remaining"], 3)
        self.assertEqual(second["submission"]["resource_balance"], next(
            faction["resources"] for faction in second["campaign"]["world"]["factions"] if faction["id"] == faction_id
        ))
        self.assertEqual(second["submission"]["affected_months"], [1, 2])
        self.assertEqual(len(second["campaign"]["queued_actions"]), 1)
        self.assertEqual(second["campaign"]["world"]["monthly_cycle"][faction_id]["planned_actions"][0]["payload"]["policy"], policies[1])

    def test_scenario_strategy_month_advance_runs_unclaimed_faction_ai(self) -> None:
        created = self.api_post(
            "/api/strategy/campaigns/create",
            {"name": "strategy ai month", "seed": 925, "city_count": 7, "faction_count": 3},
        )
        campaign_id = created["campaign"]["id"]
        campaign = server_module.STRATEGY_STORE.get_campaign_for_user(campaign_id, 1)
        high_risk_ai_city_id = ""
        for faction in campaign.world.factions:
            if faction.faction_id == "faction_2":
                faction.resources.ether = 1000
        for city in campaign.world.cities:
            if city.owner_faction_id == "faction_2" and not high_risk_ai_city_id:
                high_risk_ai_city_id = city.city_id
                city.support_by_faction["faction_2"] = 5
                city.resources.food = 10000
                city.resources.population = 1200
                city.resources.troops = 1000
                city.policy = next(
                    policy for policy in campaign.world.to_public_dict()["policy_choices"] if "稳定" in policy
                )
        server_module.STRATEGY_STORE.update_world(campaign_id, 1, campaign.world)

        self.api_post("/api/strategy/campaigns/enter", {"campaign_id": campaign_id})
        locked = self.api_post("/api/strategy/campaigns/lock", {"campaign_id": campaign_id})
        locked_members_by_faction = {member["faction_id"]: member for member in locked["campaign"]["members"]}
        self.assertEqual(locked_members_by_faction["faction_2"]["role"], "ai")
        self.assertLess(locked_members_by_faction["faction_2"]["user_id"], 0)
        self.assertEqual(locked["campaign"]["resume"]["initial_user_ids"], [1])
        self.assertTrue(locked["campaign"]["resume"]["can_resume"])
        advanced = self.api_post("/api/strategy/campaigns/advance-month", {"campaign_id": campaign_id})
        world = advanced["campaign"]["world"]
        factions = {faction["id"]: faction for faction in world["factions"]}
        ai_events = [
            event
            for event in world["event_log"]
            if event["category"] == "strategy_ai_plan"
        ]

        self.assertEqual(world["current_month"], 2)
        self.assertEqual(factions["faction_1"]["tactic_techs"], [])
        # A live neutral-diplomacy route now takes precedence and reserves the legal
        # peaceful-integration finish instead of spending that treasury on routine tech.
        self.assertEqual(factions["faction_2"]["tactic_techs"], [])
        self.assertEqual(world["hero_recruitments"], [])
        self.assertTrue(any(tag.startswith("strategic_hero_defender:") for tag in factions["faction_2"]["memory_tags"]))
        high_risk_ai_city = next(city for city in world["cities"] if city["id"] == high_risk_ai_city_id)
        self.assertIn("镇压", high_risk_ai_city["policy"])
        planned_factions = {event["related_ids"][0] for event in ai_events}
        self.assertIn("faction_2", planned_factions)
        self.assertTrue(any(
            event["category"] == "strategy_ai_political_decision"
            and event["related_ids"][0] == "faction_2"
            for event in world["event_log"]
        ))
        self.assertEqual(
            {faction_id for faction_id in planned_factions if faction_id.startswith("neutral_city_state_")},
            {faction["id"] for faction in world["factions"] if faction["faction_type"] == "neutral_city_state"},
        )
        self.assertFalse(any(event["category"].startswith("hero_recruitment_") for event in world["event_log"]))

    @unittest.skip("Superseded: one hero cannot queue governor and general actions then become lord for settlement.")
    def test_scenario_strategy_queued_actions_resolve_on_host_month_advance(self) -> None:
        created = self.api_post(
            "/api/strategy/campaigns/create",
            {"name": "queued strategy actions", "seed": 94, "city_count": 6, "faction_count": 2},
        )
        campaign_id = created["campaign"]["id"]
        self.api_post("/api/strategy/campaigns/enter", {"campaign_id": campaign_id})
        self.api_post("/api/strategy/campaigns/lock", {"campaign_id": campaign_id})

        city = created["campaign"]["world"]["cities"][0]
        new_policy = next(
            policy
            for policy in created["campaign"]["world"]["policy_choices"]
            if policy != city["policy"]
        )
        queued_policy = self.api_post(
            "/api/strategy/campaigns/queue-action",
            {
                "campaign_id": campaign_id,
                "action_type": "set_city_policy",
                "action_payload": {"city_id": city["id"], "policy": new_policy},
            },
        )

        self.assertEqual(len(queued_policy["campaign"]["queued_actions"]), 1)
        self.assertEqual(queued_policy["campaign"]["queued_actions"][0]["action_type"], "set_city_policy")
        self.assertEqual(queued_policy["campaign"]["queued_actions"][0]["payload"]["policy"], new_policy)

        queued_attack = self.api_post(
            "/api/strategy/campaigns/queue-action",
            {
                "campaign_id": campaign_id,
                "action_type": "declare_attack",
                "action_payload": {
                    "source_city_id": "city_1",
                    "target_city_id": "city_2",
                    "resolution_mode": "quick",
                },
            },
        )
        self.assertEqual(len(queued_attack["campaign"]["queued_actions"]), 2)

        queued_manual = self.api_post(
            "/api/strategy/campaigns/queue-action",
            {
                "campaign_id": campaign_id,
                "action_type": "declare_attack",
                "action_payload": {
                    "source_city_id": "city_1",
                    "target_city_id": "city_2",
                    "resolution_mode": "manual",
                },
                "session_token": self.default_session_token(),
            },
        )
        self.assertEqual(len(queued_manual["campaign"]["queued_actions"]), 2)
        self.assertEqual(queued_manual["campaign"]["queued_actions"][-1]["payload"]["resolution_mode"], "manual")
        self.assertEqual(queued_manual["campaign"]["queued_actions"][-1]["payload"]["attacker_hero_codes"], [])

        advanced = self.api_post("/api/strategy/campaigns/advance-month", {"campaign_id": campaign_id})
        world = advanced["campaign"]["world"]
        changed_city = next(item for item in world["cities"] if item["id"] == city["id"])
        queued_battle = world["pending_battles"][-1]

        self.assertEqual(world["current_month"], 2)
        self.assertEqual(changed_city["policy"], new_policy)
        self.assertEqual(advanced["campaign"]["queued_actions"], [])
        self.assertEqual(queued_battle["resolution_mode"], "manual")
        self.assertEqual(queued_battle["attacker_hero_codes"], [])
        self.assertEqual(queued_battle["status"], "pending")
        self.assertTrue(queued_battle["battle_room_id"])
        self.assertEqual(advanced["battle_rooms"][0]["room_id"], queued_battle["battle_room_id"])
        self.assertTrue(advanced["battle_rooms"][0]["player_token"])
        self.assertTrue(any(event["category"] == "battle_declared" for event in world["event_log"]))

    @unittest.skip("Superseded by hero-bound office permission and command-chain scenarios.")
    def test_scenario_strategy_office_permissions_orders_and_requests(self) -> None:
        created = self.api_post(
            "/api/strategy/campaigns/create",
            {"name": "office permissions", "seed": 194, "city_count": 6, "faction_count": 2},
        )
        campaign_id = created["campaign"]["id"]
        self.api_post("/api/strategy/campaigns/enter", {"campaign_id": campaign_id})
        self.api_post("/api/strategy/campaigns/lock", {"campaign_id": campaign_id})
        entered = self.api_post("/api/strategy/campaigns/enter", {"campaign_id": campaign_id})["campaign"]
        offices = [office for office in entered["world"]["offices"] if office["faction_id"] == "faction_1"]
        lord = next(office for office in offices if office["office_type"] == "lord")
        governor = next(office for office in offices if office["office_type"] == "governor")
        city = next(city for city in entered["world"]["cities"] if city["id"] in governor["managed_entity_ids"])
        policy = next(choice for choice in entered["world"]["policy_choices"] if choice != city["policy"])

        status, denied = self.api_post_error(
            "/api/strategy/campaigns/queue-action",
            {
                "campaign_id": campaign_id,
                "action_type": "set_city_policy",
                "action_payload": {"city_id": city["id"], "policy": policy, "issuer_office_id": lord["id"]},
                "session_token": self.default_session_token(),
            },
        )
        self.assertEqual(status, 400)
        self.assertIn("无权", denied["error"])

        queued = self.api_post(
            "/api/strategy/campaigns/queue-action",
            {
                "campaign_id": campaign_id,
                "action_type": "set_city_policy",
                "action_payload": {"city_id": city["id"], "policy": policy, "issuer_office_id": governor["id"]},
            },
        )["campaign"]
        policy_action = queued["queued_actions"][-1]
        self.assertEqual(policy_action["issuer_office_id"], governor["id"])
        self.assertEqual(policy_action["payload"]["issuer_office_id"], governor["id"])

        ordered = self.api_post(
            "/api/strategy/campaigns/queue-action",
            {
                "campaign_id": campaign_id,
                "action_type": "issue_office_order",
                "action_payload": {
                    "issuer_office_id": lord["id"],
                    "receiver_office_id": governor["id"],
                    "objective": "稳住粮食与民心",
                },
            },
        )["campaign"]
        self.assertEqual(ordered["queued_actions"][-1]["command_cost"], 1)

        requested = self.api_post(
            "/api/strategy/campaigns/queue-action",
            {
                "campaign_id": campaign_id,
                "action_type": "send_office_request",
                "action_payload": {
                    "issuer_office_id": governor["id"],
                    "receiver_office_id": lord["id"],
                    "objective": "请求拨付守城粮草",
                },
            },
        )["campaign"]
        self.assertEqual(requested["queued_actions"][-1]["command_cost"], 0)

        advanced = self.api_post(
            "/api/strategy/campaigns/advance-month",
            {"campaign_id": campaign_id, "issuer_office_id": lord["id"]},
        )["campaign"]
        self.assertEqual(len(advanced["world"]["office_orders"]), 2)
        self.assertEqual({order["order_type"] for order in advanced["world"]["office_orders"]}, {"order", "request"})

    @unittest.skip("Superseded: this scenario mixes governor, lord, and general identities on one account.")
    def test_scenario_strategy_city_monthly_order_limit_blocks_extra_city_orders(self) -> None:
        created = self.api_post(
            "/api/strategy/campaigns/create",
            {"name": "city order limit", "seed": 94, "city_count": 6, "faction_count": 2},
        )
        campaign_id = created["campaign"]["id"]
        self.api_post("/api/strategy/campaigns/enter", {"campaign_id": campaign_id})
        self.api_post("/api/strategy/campaigns/lock", {"campaign_id": campaign_id})
        entered = self.api_post("/api/strategy/campaigns/enter", {"campaign_id": campaign_id})
        world = entered["campaign"]["world"]
        city = world["cities"][0]
        new_policy = next(policy for policy in world["policy_choices"] if policy != city["policy"])
        hero = next(item for item in world["strategic_hero_pool"] if item["home_faction_id"] == "faction_1")

        self.api_post(
            "/api/strategy/campaigns/queue-action",
            {
                "campaign_id": campaign_id,
                "action_type": "set_city_policy",
                "action_payload": {"city_id": city["id"], "policy": new_policy},
            },
        )
        queued_recruit = self.api_post(
            "/api/strategy/campaigns/queue-action",
            {
                "campaign_id": campaign_id,
                "action_type": "summon_strategic_hero",
                "action_payload": {"city_id": city["id"], "hero_code": hero["code"]},
            },
        )
        self.assertEqual(len(queued_recruit["campaign"]["queued_actions"]), 2)
        self.assertEqual(queued_recruit["campaign"]["queued_actions"][-1]["payload"]["city_id"], city["id"])

        status, payload = self.api_post_error(
            "/api/strategy/campaigns/queue-action",
            {
                "campaign_id": campaign_id,
                "action_type": "declare_attack",
                "action_payload": {
                    "source_city_id": "city_1",
                    "target_city_id": "city_2",
                    "resolution_mode": "quick",
                },
                "session_token": self.default_session_token(),
            },
        )
        self.assertEqual(status, 409)
        self.assertIn("每座城市每月最多 2 条军令", payload["error"])

        replaced = self.api_post(
            "/api/strategy/campaigns/queue-action",
            {
                "campaign_id": campaign_id,
                "action_type": "set_city_policy",
                "action_payload": {"city_id": city["id"], "policy": city["policy"]},
            },
        )
        self.assertEqual(len(replaced["campaign"]["queued_actions"]), 2)

    @unittest.skip("Superseded: command budgets are now exercised through one hero office at a time.")
    def test_scenario_strategy_faction_command_points_force_monthly_tradeoffs(self) -> None:
        created = self.api_post(
            "/api/strategy/campaigns/create",
            {"name": "faction command budget", "seed": 94, "city_count": 6, "faction_count": 2},
        )
        campaign_id = created["campaign"]["id"]
        self.api_post("/api/strategy/campaigns/enter", {"campaign_id": campaign_id})
        self.api_post("/api/strategy/campaigns/lock", {"campaign_id": campaign_id})
        campaign = self.api_post("/api/strategy/campaigns/enter", {"campaign_id": campaign_id})["campaign"]
        world = campaign["world"]
        own_cities = [city for city in world["cities"] if city["owner_faction_id"] == "faction_1"]
        source_city = next(
            city
            for city in own_cities
            if any(
                target["owner_faction_id"] != "faction_1"
                and target["node_id"] in next(node for node in world["nodes"] if node["id"] == city["node_id"])["connected_node_ids"]
                for target in world["cities"]
            )
        )
        source_node = next(node for node in world["nodes"] if node["id"] == source_city["node_id"])
        target_city = next(
            city
            for city in world["cities"]
            if city["node_id"] in source_node["connected_node_ids"] and city["owner_faction_id"] != "faction_1"
        )
        other_city = next(city for city in own_cities if city["id"] != source_city["id"])
        policy = next(choice for choice in world["policy_choices"] if choice != source_city["policy"])
        hero = next(item for item in world["strategic_hero_pool"] if item["home_faction_id"] == "faction_1")

        first = self.api_post(
            "/api/strategy/campaigns/queue-action",
            {
                "campaign_id": campaign_id,
                "action_type": "set_city_policy",
                "action_payload": {"city_id": source_city["id"], "policy": policy},
            },
        )
        second = self.api_post(
            "/api/strategy/campaigns/queue-action",
            {
                "campaign_id": campaign_id,
                "action_type": "summon_strategic_hero",
                "action_payload": {"city_id": other_city["id"], "hero_code": hero["code"]},
            },
        )
        self.assertEqual(first["campaign"]["command_points_by_faction"]["faction_1"]["remaining"], 3)
        self.assertEqual(second["campaign"]["command_points_by_faction"]["faction_1"]["remaining"], 2)
        self.assertEqual(second["campaign"]["queued_actions"][-1]["command_cost"], 1)

        allowed_attack = self.api_post(
            "/api/strategy/campaigns/queue-action",
            {
                "campaign_id": campaign_id,
                "action_type": "declare_attack",
                "action_payload": {
                    "source_city_id": source_city["id"],
                    "target_city_id": target_city["id"],
                    "resolution_mode": "quick",
                },
            },
        )
        self.assertEqual(allowed_attack["campaign"]["command_points_by_faction"]["faction_1"]["remaining"], 0)
        self.assertEqual(allowed_attack["campaign"]["queued_actions"][-1]["command_cost"], 2)

        status, payload = self.api_post_error(
            "/api/strategy/campaigns/queue-action",
            {
                "campaign_id": campaign_id,
                "action_type": "set_city_policy",
                "action_payload": {"city_id": other_city["id"], "policy": policy},
                "session_token": self.default_session_token(),
            },
        )
        self.assertEqual(status, 409)
        self.assertIn("本势力军令不足", payload["error"])

        replaced = self.api_post(
            "/api/strategy/campaigns/queue-action",
            {
                "campaign_id": campaign_id,
                "action_type": "set_city_policy",
                "action_payload": {"city_id": source_city["id"], "policy": source_city["policy"]},
            },
        )
        self.assertEqual(replaced["campaign"]["command_points_by_faction"]["faction_1"]["remaining"], 0)

    @unittest.skip("Superseded: city-event governor and month-settlement lord are distinct hero controllers.")
    def test_scenario_strategy_story_choice_uses_command_and_resolves_on_month_advance(self) -> None:
        created = self.api_post(
            "/api/strategy/campaigns/create",
            {"name": "story choice", "seed": 181, "city_count": 6, "faction_count": 2},
        )
        campaign_id = created["campaign"]["id"]
        self.api_post("/api/strategy/campaigns/enter", {"campaign_id": campaign_id})
        self.api_post("/api/strategy/campaigns/lock", {"campaign_id": campaign_id})
        campaign = self.api_post("/api/strategy/campaigns/enter", {"campaign_id": campaign_id})["campaign"]
        event = next(
            item
            for item in campaign["world"]["story_events"]
            if item["faction_id"] == "faction_1" and item["status"] == "pending"
        )
        choice = next(item for item in event["choices"] if item["enabled"])

        queued = self.api_post(
            "/api/strategy/campaigns/queue-action",
            {
                "campaign_id": campaign_id,
                "action_type": "resolve_story_event",
                "action_payload": {"event_id": event["id"], "choice_id": choice["id"]},
            },
        )["campaign"]

        self.assertEqual(queued["command_points_by_faction"]["faction_1"]["remaining"], 3)
        self.assertEqual(queued["queued_actions"][0]["action_type"], "resolve_story_event")
        self.assertEqual(queued["queued_actions"][0]["command_cost"], 1)

        advanced = self.api_post("/api/strategy/campaigns/advance-month", {"campaign_id": campaign_id})["campaign"]
        resolved_event = next(item for item in advanced["world"]["story_events"] if item["id"] == event["id"])
        self.assertEqual(resolved_event["status"], "resolved")
        self.assertEqual(resolved_event["choice_id"], choice["id"])
        self.assertIn(
            f"story_choice:{event['id']}:{choice['id']}",
            advanced["world"]["memory_tags"],
        )
        self.assertTrue(any(item["status"] == "pending" and item["opened_month"] == 2 for item in advanced["world"]["story_events"]))

    @unittest.skip("Superseded: governor and lord must be distinct hero controllers in multiplayer settlement.")
    def test_scenario_strategy_queued_rebellion_action_resolves_on_month_advance(self) -> None:
        created = self.api_post(
            "/api/strategy/campaigns/create",
            {"name": "rebellion action", "seed": 97, "city_count": 5, "faction_count": 2},
        )
        campaign_id = created["campaign"]["id"]
        owner_user_id = created["campaign"]["owner_user_id"]
        self.api_post("/api/strategy/campaigns/enter", {"campaign_id": campaign_id})
        self.api_post("/api/strategy/campaigns/lock", {"campaign_id": campaign_id})

        campaign = server_module.STRATEGY_STORE.get_campaign_for_user(campaign_id, owner_user_id)
        city = next(city for city in campaign.world.cities if city.owner_faction_id == "faction_1")
        city.support_by_faction["faction_1"] = 70
        city.resources.troops = 500
        city.event_states.append("rebellion_force:100:month:1")
        server_module.STRATEGY_STORE.update_world(campaign_id, owner_user_id, campaign.world)

        queued = self.api_post(
            "/api/strategy/campaigns/queue-action",
            {
                "campaign_id": campaign_id,
                "action_type": "rebellion_action",
                "action_payload": {"rebellion_action_id": "suppress", "city_id": city.city_id},
            },
        )

        self.assertEqual(queued["campaign"]["queued_actions"][0]["action_type"], "rebellion_action")
        self.assertIn("suppress", [choice["id"] for choice in queued["campaign"]["world"]["rebellion_action_choices"]])

        advanced = self.api_post("/api/strategy/campaigns/advance-month", {"campaign_id": campaign_id})
        world = advanced["campaign"]["world"]
        updated_city = next(item for item in world["cities"] if item["id"] == city.city_id)

        self.assertFalse(any(state.startswith("rebellion_force:") for state in updated_city["event_states"]))
        self.assertLess(updated_city["resources"]["troops"], 500)
        self.assertEqual(advanced["campaign"]["queued_actions"], [])
        self.assertTrue(any(event["category"] == "rebellion_action" for event in world["event_log"]))
        self.assertTrue(any(event["category"] == "rebellion_suppressed" for event in world["event_log"]))

    @unittest.skip("Superseded: governor and lord must be distinct hero controllers in multiplayer settlement.")
    def test_scenario_strategy_queued_rebellion_battle_resolves_on_month_advance(self) -> None:
        created = self.api_post(
            "/api/strategy/campaigns/create",
            {"name": "rebellion battle", "seed": 98, "city_count": 5, "faction_count": 2},
        )
        campaign_id = created["campaign"]["id"]
        owner_user_id = created["campaign"]["owner_user_id"]
        self.api_post("/api/strategy/campaigns/enter", {"campaign_id": campaign_id})
        self.api_post("/api/strategy/campaigns/lock", {"campaign_id": campaign_id})

        campaign = server_module.STRATEGY_STORE.get_campaign_for_user(campaign_id, owner_user_id)
        city = next(city for city in campaign.world.cities if city.owner_faction_id == "faction_1")
        city.resources.troops = 500
        city.defense = 4
        city.support_by_faction["faction_1"] = 50
        city.support_by_faction["local_autonomy"] = 35
        city.event_states.append("rebellion_force:120:month:1")
        server_module.STRATEGY_STORE.update_world(campaign_id, owner_user_id, campaign.world)

        queued = self.api_post(
            "/api/strategy/campaigns/queue-action",
            {
                "campaign_id": campaign_id,
                "action_type": "rebellion_battle",
                "action_payload": {"city_id": city.city_id, "troops": 160},
            },
        )

        self.assertEqual(queued["campaign"]["queued_actions"][0]["action_type"], "rebellion_battle")
        self.assertEqual(queued["campaign"]["queued_actions"][0]["payload"]["troops"], 160)

        advanced = self.api_post("/api/strategy/campaigns/advance-month", {"campaign_id": campaign_id})
        world = advanced["campaign"]["world"]
        updated_city = next(item for item in world["cities"] if item["id"] == city.city_id)

        self.assertFalse(any(state.startswith("rebellion_force:") for state in updated_city["event_states"]))
        self.assertLess(updated_city["resources"]["troops"], 500)
        self.assertEqual(advanced["campaign"]["queued_actions"], [])
        self.assertTrue(any(event["category"] == "rebellion_battle" for event in world["event_log"]))
        self.assertTrue(any(event["category"] == "rebellion_suppressed" for event in world["event_log"]))

    @unittest.skip("Superseded: general and lord must be distinct hero controllers in multiplayer settlement.")
    def test_scenario_strategy_queued_ai_auto_attack_runs_real_battle_on_month_advance(self) -> None:
        created = self.api_post(
            "/api/strategy/campaigns/create",
            {"name": "queued ai auto", "seed": 94, "city_count": 6, "faction_count": 2},
        )
        campaign_id = created["campaign"]["id"]
        self.api_post("/api/strategy/campaigns/enter", {"campaign_id": campaign_id})
        self.api_post("/api/strategy/campaigns/lock", {"campaign_id": campaign_id})
        queued = self.api_post(
            "/api/strategy/campaigns/queue-action",
            {
                "campaign_id": campaign_id,
                "action_type": "declare_attack",
                "action_payload": {
                    "source_city_id": "city_1",
                    "target_city_id": "city_2",
                    "resolution_mode": "ai_auto",
                },
            },
        )

        self.assertEqual(queued["campaign"]["queued_actions"][0]["payload"]["resolution_mode"], "ai_auto")

        advanced = self.api_post("/api/strategy/campaigns/advance-month", {"campaign_id": campaign_id})
        battle = advanced["campaign"]["world"]["pending_battles"][-1]
        battle_room = advanced["battle_rooms"][0]

        self.assertEqual(battle["resolution_mode"], "ai_auto")
        self.assertEqual(battle["status"], "resolved")
        self.assertEqual(battle["battle_room_id"], battle_room["room_id"])
        self.assertEqual(battle["battle_result"]["resolution_source"], "real_grid")
        self.assertIn(battle["battle_result"]["winner_side"], {"attacker", "defender"})
        self.assertEqual(battle_room["status"], "finished")
        self.assertIn(battle_room["winner"], {1, 2})
        self.assertGreater(battle_room["simulation_steps"], 0)

    def test_scenario_strategy_exiled_player_queues_exile_action_and_advances(self) -> None:
        created = self.api_post(
            "/api/strategy/campaigns/create",
            {"name": "exile action", "seed": 96, "city_count": 5, "faction_count": 2},
        )
        campaign_id = created["campaign"]["id"]
        owner_user_id = created["campaign"]["owner_user_id"]
        self.api_post("/api/strategy/campaigns/enter", {"campaign_id": campaign_id})
        self.api_post("/api/strategy/campaigns/lock", {"campaign_id": campaign_id})

        campaign = server_module.STRATEGY_STORE.get_campaign_for_user(campaign_id, owner_user_id)
        world = campaign.world
        for city in world.cities:
            city.owner_faction_id = "faction_2"
        faction = next(item for item in world.factions if item.faction_id == "faction_1")
        faction.resources.food = 0
        faction.resources.money = 0
        faction.resources.ether = 0
        faction.resources.troops = 0
        server_module.STRATEGY_STORE.update_world(campaign_id, owner_user_id, world)

        # Eliminating the last rival now opens a conclusion; the host keeps the exile story alive by continuing.
        self.api_post(
            "/api/strategy/campaigns/continue-sandbox",
            {"campaign_id": campaign_id},
        )

        queued = self.api_post(
            "/api/strategy/campaigns/queue-action",
            {
                "campaign_id": campaign_id,
                "action_type": "exile_action",
                "action_payload": {"exile_action_id": "seek_aid"},
            },
        )

        self.assertEqual(queued["campaign"]["queued_actions"][0]["action_type"], "exile_action")
        self.assertIn("faction_1", queued["campaign"]["world"]["strategic_status"]["exiled_faction_ids"])
        self.assertIn("seek_aid", [choice["id"] for choice in queued["campaign"]["world"]["exile_action_choices"]])

        advanced = self.api_post("/api/strategy/campaigns/advance-month", {"campaign_id": campaign_id})
        advanced_world = advanced["campaign"]["world"]
        advanced_faction = next(item for item in advanced_world["factions"] if item["id"] == "faction_1")

        self.assertEqual(advanced_world["current_month"], 2)
        self.assertEqual(advanced["campaign"]["queued_actions"], [])
        self.assertEqual(advanced_faction["resources"]["food"], 140)
        self.assertEqual(advanced_faction["resources"]["money"], 100)
        self.assertEqual(advanced_faction["resources"]["ether"], 10)
        self.assertTrue(any(event["category"] == "exile_action" for event in advanced_world["event_log"]))

    def test_scenario_strategy_player_performs_ritual_and_binds_summoned_hero(self) -> None:
        created = self.api_post(
            "/api/strategy/campaigns/create",
            {"name": "hero summon", "seed": 97, "city_count": 5, "faction_count": 2},
        )
        campaign_id = created["campaign"]["id"]
        self.api_post("/api/strategy/campaigns/enter", {"campaign_id": campaign_id})
        self.api_post("/api/strategy/campaigns/lock", {"campaign_id": campaign_id})

        campaign = server_module.STRATEGY_STORE.get_campaign_for_user(campaign_id, created["campaign"]["owner_user_id"])
        city = next(item for item in campaign.world.cities if item.owner_faction_id == "faction_1")
        faction = next(item for item in campaign.world.factions if item.faction_id == "faction_1")
        faction.tactic_techs.extend(["local_militia", "command_staff_1"])
        campaign = replace(campaign, world=ensure_office_system(campaign.world))
        city = next(item for item in campaign.world.cities if item.city_id == city.city_id)
        lord = next(
            item
            for item in campaign.world.offices
            if item.faction_id == "faction_1" and item.office_type == "lord"
        )
        city.resources.ether = 100
        before_codes = {item.hero_code for item in campaign.world.strategic_heroes if item.faction_id == "faction_1"}
        server_module.STRATEGY_STORE.update_world(campaign_id, created["campaign"]["owner_user_id"], campaign.world)

        queued = self.api_post(
            "/api/strategy/campaigns/queue-action",
            {
                "campaign_id": campaign_id,
                "action_type": "perform_hero_ritual",
                "action_payload": {"city_id": city.city_id, "issuer_office_id": lord.office_id},
            },
        )
        self.assertEqual(queued["campaign"]["queued_actions"][0]["action_type"], "perform_hero_ritual")

        advanced = self.api_post("/api/strategy/campaigns/advance-month", {"campaign_id": campaign_id})
        world = advanced["campaign"]["world"]
        ritual_heroes = [
            item
            for item in world["strategic_hero_pool"]
            if item["faction_id"] == "faction_1" and item["code"] not in before_codes
        ]
        self.assertTrue(
            ritual_heroes,
            [event for event in world["event_log"] if event["category"] in {"queued_action_failed", "hero_ritual_summoned"}],
        )
        advanced_hero = ritual_heroes[0]
        recruited_code = advanced_hero["code"]
        self.assertEqual(advanced_hero["status"], "serving")
        self.assertEqual(advanced_hero["faction_id"], "faction_1")
        self.assertEqual(advanced_hero["ritual_city_id"], city.city_id)
        self.assertTrue(any(event["category"] == "hero_ritual_summoned" for event in world["event_log"]))

        grand_general = next(
            office
            for office in world["offices"]
            if office["faction_id"] == "faction_1" and office["office_type"] == "general" and office["status"] == "vacant"
        )
        appointed = self.api_post(
            "/api/strategy/campaigns/queue-action",
            {
                "campaign_id": campaign_id,
                "action_type": "appoint_strategic_hero",
                "action_payload": {"target_office_id": grand_general["id"], "hero_code": recruited_code},
            },
        )["campaign"]
        self.assertEqual(appointed["queued_actions"][-1]["command_cost"], 1)
        appointed = self.api_post("/api/strategy/campaigns/advance-month", {"campaign_id": campaign_id})["campaign"]
        appointed_hero = next(item for item in appointed["world"]["strategic_hero_pool"] if item["code"] == recruited_code)
        appointed_office = next(item for item in appointed["world"]["offices"] if item["id"] == grand_general["id"])
        self.assertEqual(appointed_hero["office_id"], grand_general["id"])
        self.assertEqual(appointed_office["holder_id"], recruited_code)

        defended = self.api_post(
            "/api/strategy/campaigns/set-defense-hero",
            {"campaign_id": campaign_id, "hero_code": recruited_code},
        )
        defended_world = defended["campaign"]["world"]
        defended_hero = next(item for item in defended_world["strategic_hero_pool"] if item["code"] == recruited_code)
        defended_faction = next(item for item in defended_world["factions"] if item["id"] == "faction_1")

        self.assertTrue(defended_hero["defender_assigned"])
        self.assertIn(f"strategic_hero_defender:{recruited_code}", defended_faction["memory_tags"])
        self.assertTrue(any(event["category"] == "strategic_hero_defender_set" for event in defended_world["event_log"]))

    def test_scenario_governor_can_perform_local_ritual_without_lord_approval(self) -> None:
        created = self.api_post(
            "/api/strategy/campaigns/create",
            {"name": "governor recommendation", "seed": 214, "city_count": 6, "faction_count": 2},
        )
        campaign_id = created["campaign"]["id"]
        owner_id = created["campaign"]["owner_user_id"]
        self.api_post("/api/strategy/campaigns/enter", {"campaign_id": campaign_id})
        self.api_post("/api/strategy/campaigns/lock", {"campaign_id": campaign_id})
        campaign = server_module.STRATEGY_STORE.get_campaign_for_user(campaign_id, owner_id)
        city = next(item for item in campaign.world.cities if item.owner_faction_id == "faction_1")
        governor = next(
            item
            for item in campaign.world.offices
            if item.faction_id == "faction_1" and item.office_type == "governor" and city.city_id in item.managed_entity_ids
        )
        faction = next(item for item in campaign.world.factions if item.faction_id == "faction_1")
        faction.tactic_techs.extend(["local_militia", "command_staff_1"])
        campaign = replace(campaign, world=ensure_office_system(campaign.world))
        city = next(item for item in campaign.world.cities if item.city_id == city.city_id)
        governor = next(item for item in campaign.world.offices if item.office_id == governor.office_id)
        city.resources.ether = 100
        before_codes = {item.hero_code for item in campaign.world.strategic_heroes if item.faction_id == "faction_1"}
        server_module.STRATEGY_STORE.update_world(campaign_id, owner_id, campaign.world)

        switch_payload = {
            "campaign_id": campaign_id,
            "action_type": "construct_city_building",
            "action_payload": {"city_id": city.city_id},
            "session_token": self.default_session_token(),
        }
        self._bind_test_action_office("/api/strategy/campaigns/queue-action", switch_payload)
        governor_office_id = switch_payload["action_payload"]["issuer_office_id"]

        self.api_post(
            "/api/strategy/campaigns/queue-action",
            {
                "campaign_id": campaign_id,
                "action_type": "perform_hero_ritual",
                "action_payload": {"city_id": city.city_id, "issuer_office_id": governor_office_id},
            },
        )
        pending = server_module.STRATEGY_STORE.get_campaign_for_user(campaign_id, owner_id)
        issued_world, _ = server_module.apply_strategy_action_queue(pending)
        server_module.STRATEGY_STORE.update_world(campaign_id, owner_id, issued_world)
        server_module.STRATEGY_STORE.mark_queued_actions_resolved(campaign_id, owner_id, pending.world.current_month)
        issued = server_module.STRATEGY_STORE.get_campaign_for_user(campaign_id, owner_id).world.to_public_dict()
        hero = next(item for item in issued["strategic_hero_pool"] if item["faction_id"] == "faction_1" and item["code"] not in before_codes)
        self.assertEqual((hero["status"], hero["ritual_city_id"]), ("serving", city.city_id))
        self.assertTrue(any(event["category"] == "hero_ritual_summoned" for event in issued["event_log"]))

    def test_scenario_retired_recruitment_and_legacy_levy_actions_are_rejected(self) -> None:
        created = self.api_post(
            "/api/strategy/campaigns/create",
            {"name": "retired actions", "seed": 215, "city_count": 5, "faction_count": 2},
        )
        campaign_id = created["campaign"]["id"]
        self.api_post("/api/strategy/campaigns/enter", {"campaign_id": campaign_id})
        self.api_post("/api/strategy/campaigns/lock", {"campaign_id": campaign_id})

        for action_type in (
            "summon_strategic_hero",
            "issue_hero_recruitment",
            "accept_hero_recruitment",
            "recommend_hero_recruitment",
            "levy_field_troops",
            "levy_city_garrison",
        ):
            with self.subTest(action_type=action_type):
                status, payload = self.api_post_error(
                    "/api/strategy/campaigns/queue-action",
                    {
                        "campaign_id": campaign_id,
                        "action_type": action_type,
                        "action_payload": {},
                        "session_token": self.default_session_token(),
                    },
                )
                self.assertEqual(status, 400)
                self.assertEqual(payload["error"], "Unknown strategy action type.")

    def test_scenario_role_specific_management_actions_settle_together(self) -> None:
        created = self.api_post(
            "/api/strategy/campaigns/create",
            {"name": "role workspaces", "seed": 215, "city_count": 6, "faction_count": 2},
        )
        campaign_id = created["campaign"]["id"]
        owner_id = created["campaign"]["owner_user_id"]
        self.api_post("/api/strategy/campaigns/enter", {"campaign_id": campaign_id})
        self.api_post("/api/strategy/campaigns/lock", {"campaign_id": campaign_id})
        campaign = server_module.STRATEGY_STORE.get_campaign_for_user(campaign_id, owner_id)
        offices = [item for item in campaign.world.offices if item.faction_id == "faction_1"]
        lord = next(item for item in offices if item.office_type == "lord")
        grand = next(item for item in offices if item.office_type == "grand_general")
        governor = next(item for item in offices if item.office_type == "governor")
        city = next(item for item in campaign.world.cities if item.city_id in governor.managed_entity_ids)
        hero = next(item for item in campaign.world.strategic_heroes if item.faction_id == "faction_1")
        city.resources.population = city.resources.food = city.resources.money = 1000
        for office in (lord, grand, governor):
            office.controller_type = "player"
            office.controller_user_id = owner_id
        before_troops = city.resources.troops
        server_module.STRATEGY_STORE.update_world(campaign_id, owner_id, campaign.world)

        actions = [
            ("assign_strategic_hero_duty", {"hero_code": hero.hero_code, "assignment_type": "garrison", "target_id": city.city_id}),
            ("increase_city_troops", {"city_id": city.city_id}),
            ("register_city_soldiers", {"city_id": city.city_id, "unit_count": 1}),
        ]
        for action_type, action_payload in actions:
            self.api_post(
                "/api/strategy/campaigns/queue-action",
                {"campaign_id": campaign_id, "action_type": action_type, "action_payload": action_payload},
            )
        queued = server_module.STRATEGY_STORE.get_campaign_for_user(campaign_id, owner_id)
        self.assertEqual(
            [action.action_type for action in queued.queued_actions],
            ["assign_strategic_hero_duty", "increase_city_troops", "register_city_soldiers"],
        )
        offices_by_id = {office.office_id: office.office_type for office in queued.world.offices}
        self.assertEqual(
            [offices_by_id[action.payload["issuer_office_id"]] for action in queued.queued_actions],
            ["lord", "governor", "governor"],
        )
        self.assertEqual(server_module.strategy_action_command_cost(queued.queued_actions[0].action_type), 0)
        self.assertEqual(
            [server_module.strategy_action_command_cost(action.action_type, action.payload) for action in queued.queued_actions[1:]],
            [1, 1],
        )

    def test_scenario_general_forms_and_disbands_a_persistent_army_through_monthly_orders(self) -> None:
        created = self.api_post(
            "/api/strategy/campaigns/create",
            {"name": "persistent army", "seed": 401, "city_count": 8, "faction_count": 2},
        )["campaign"]
        campaign_id = created["id"]
        owner_id = created["owner_user_id"]
        self.api_post("/api/strategy/campaigns/lock", {"campaign_id": campaign_id})
        stored = server_module.STRATEGY_STORE.get_campaign_for_user(campaign_id, owner_id)
        general = next(item for item in stored.world.offices if item.faction_id == "faction_1" and item.office_type == "general")
        hero = next(item for item in stored.world.strategic_heroes if item.office_id == general.office_id)
        city = next(item for item in stored.world.cities if item.city_id == hero.city_id)
        general.unit_inventory = {"infantry": 2, "archer": 1}
        city.resources.food = 1000
        server_module.STRATEGY_STORE.update_world(campaign_id, owner_id, stored.world)

        queued = self.api_post(
            "/api/strategy/campaigns/queue-action",
            {
                "campaign_id": campaign_id,
                "action_type": "form_army",
                "action_payload": {
                    "city_id": city.city_id,
                    "unit_inventory": {"infantry": 1, "archer": 1},
                    "supply": 100,
                },
            },
        )["campaign"]
        self.assertEqual(queued["queued_actions"][-1]["command_cost"], 1)
        advance_status, advance_payload = self.api_post_error(
            "/api/strategy/campaigns/advance-month",
            {"campaign_id": campaign_id, "session_token": self.default_session_token()},
        )
        self.assertEqual(advance_status, 200, advance_payload)
        formed = advance_payload["campaign"]
        self.assertTrue(
            formed["world"]["armies"],
            [event for event in formed["world"]["event_log"] if event["category"] == "queued_action_failed"],
        )
        army = formed["world"]["armies"][0]
        self.assertEqual((army["manpower"], army["supply"], army["morale"], army["status"]), (240, 100, 72, "garrisoned"))
        self.assertEqual(army["unit_inventory"], {"infantry": 1, "archer": 1})
        formed_general = next(item for item in formed["world"]["offices"] if item["id"] == general.office_id)
        self.assertEqual(formed_general["unit_inventory"], {"infantry": 1})

        disbanded_queue = self.api_post(
            "/api/strategy/campaigns/queue-action",
            {
                "campaign_id": campaign_id,
                "action_type": "disband_army",
                "action_payload": {"army_id": army["id"]},
            },
        )["campaign"]
        self.assertEqual(disbanded_queue["queued_actions"][-1]["command_cost"], 1)
        disband_status, disband_payload = self.api_post_error(
            "/api/strategy/campaigns/advance-month",
            {"campaign_id": campaign_id, "session_token": self.default_session_token()},
        )
        self.assertEqual(disband_status, 200, disband_payload)
        disbanded = disband_payload["campaign"]
        self.assertEqual(disbanded["world"]["armies"][0]["status"], "disbanded")
        returned_general = next(item for item in disbanded["world"]["offices"] if item["id"] == general.office_id)
        self.assertEqual(returned_general["unit_inventory"], {"infantry": 2, "archer": 1})
        categories = [event["category"] for event in disbanded["world"]["event_log"]]
        self.assertIn("strategy_army_formed", categories)
        self.assertIn("strategy_army_disbanded", categories)
        app_source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('title.textContent = "持久军队"', app_source)
        self.assertIn('queueStrategyAction("form_army"', app_source)

    def test_scenario_general_orders_cross_month_route_movement_and_halts_through_http(self) -> None:
        created = self.api_post(
            "/api/strategy/campaigns/create",
            {"name": "army movement", "seed": 402, "city_count": 8, "faction_count": 2},
        )["campaign"]
        campaign_id = created["id"]
        owner_id = created["owner_user_id"]
        self.api_post("/api/strategy/campaigns/lock", {"campaign_id": campaign_id})
        stored = server_module.STRATEGY_STORE.get_campaign_for_user(campaign_id, owner_id)
        general = next(item for item in stored.world.offices if item.faction_id == "faction_1" and item.office_type == "general")
        hero = next(item for item in stored.world.strategic_heroes if item.office_id == general.office_id)
        city = next(item for item in stored.world.cities if item.city_id == hero.city_id)
        general.unit_inventory = {"infantry": 1}
        city.resources.food = 500
        formed = form_or_reinforce_army(
            stored.world,
            faction_id="faction_1",
            city_id=city.city_id,
            unit_inventory={"infantry": 1},
            supply=100,
            issuer_office_id=general.office_id,
        )
        server_module.STRATEGY_STORE.update_world(campaign_id, owner_id, formed)
        army_id = formed.armies[0].army_id
        routes = [
            shortest_army_route(formed, city.node_id, node.node_id)
            for node in formed.nodes
            if node.node_id != city.node_id
        ]
        route = max(routes, key=lambda item: (len(item), item[-1]))
        self.assertGreaterEqual(len(route), 3)

        queued = self.api_post(
            "/api/strategy/campaigns/queue-action",
            {
                "campaign_id": campaign_id,
                "action_type": "set_army_movement",
                "action_payload": {
                    "army_id": army_id,
                    "movement_order": "march",
                    "destination_node_id": route[-1],
                },
            },
        )["campaign"]
        movement = queued["queued_actions"][-1]
        self.assertEqual((movement["action_key"], movement["command_cost"]), (army_id, 1))
        advance_status, advance_payload = self.api_post_error(
            "/api/strategy/campaigns/advance-month",
            {"campaign_id": campaign_id, "session_token": self.default_session_token()},
        )
        self.assertEqual(advance_status, 200, advance_payload)
        marched = advance_payload["campaign"]
        army = marched["world"]["armies"][0]
        self.assertEqual((army["location_node_id"], army["route_progress_index"], army["status"]), (route[1], 1, "marching"))
        self.assertEqual(army["route_node_ids"], route)
        self.assertEqual(army["estimated_arrival_month"], formed.current_month + len(route) - 1)

        halted_queue = self.api_post(
            "/api/strategy/campaigns/queue-action",
            {
                "campaign_id": campaign_id,
                "action_type": "set_army_movement",
                "action_payload": {"army_id": army_id, "movement_order": "hold"},
            },
        )["campaign"]
        self.assertEqual(halted_queue["queued_actions"][-1]["action_key"], army_id)
        halt_status, halt_payload = self.api_post_error(
            "/api/strategy/campaigns/advance-month",
            {"campaign_id": campaign_id, "session_token": self.default_session_token()},
        )
        self.assertEqual(halt_status, 200, halt_payload)
        halted = halt_payload["campaign"]["world"]["armies"][0]
        self.assertEqual((halted["location_node_id"], halted["status"], halted["current_order"]), (route[1], "deployed", "hold"))
        categories = [event["category"] for event in halt_payload["campaign"]["world"]["event_log"]]
        self.assertIn("strategy_army_march_ordered", categories)
        self.assertIn("strategy_army_marched", categories)
        self.assertIn("strategy_army_march_halted", categories)
        app_source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('queueStrategyAction("set_army_movement"', app_source)
        self.assertIn("预计第 ${army.estimated_arrival_month} 月抵达", app_source)
        self.assertIn('line.setAttribute("class", `strategy-map-route-line', app_source)

    def test_scenario_general_loads_supply_and_monthly_logistics_are_public_through_http(self) -> None:
        created = self.api_post(
            "/api/strategy/campaigns/create",
            {"name": "army supply", "seed": 403, "city_count": 8, "faction_count": 2},
        )["campaign"]
        campaign_id = created["id"]
        owner_id = created["owner_user_id"]
        self.api_post("/api/strategy/campaigns/lock", {"campaign_id": campaign_id})
        stored = server_module.STRATEGY_STORE.get_campaign_for_user(campaign_id, owner_id)
        general = next(item for item in stored.world.offices if item.faction_id == "faction_1" and item.office_type == "general")
        hero = next(item for item in stored.world.strategic_heroes if item.office_id == general.office_id)
        city = next(item for item in stored.world.cities if item.city_id == hero.city_id)
        general.unit_inventory = {"infantry": 1}
        city.resources.food = 500
        formed = form_or_reinforce_army(
            stored.world,
            faction_id="faction_1",
            city_id=city.city_id,
            unit_inventory={"infantry": 1},
            supply=50,
            issuer_office_id=general.office_id,
        )
        server_module.STRATEGY_STORE.update_world(campaign_id, owner_id, formed)
        army_id = formed.armies[0].army_id

        queued = self.api_post(
            "/api/strategy/campaigns/queue-action",
            {
                "campaign_id": campaign_id,
                "action_type": "load_army_supply",
                "action_payload": {"army_id": army_id, "supply": 50},
            },
        )["campaign"]
        self.assertEqual((queued["queued_actions"][-1]["action_key"], queued["queued_actions"][-1]["command_cost"]), (army_id, 1))
        status, payload = self.api_post_error(
            "/api/strategy/campaigns/advance-month",
            {"campaign_id": campaign_id, "session_token": self.default_session_token()},
        )
        self.assertEqual(status, 200, payload)
        army = payload["campaign"]["world"]["armies"][0]
        self.assertEqual((army["supply_line_status"], army["supply_distance"], army["monthly_supply_need"]), ("local", 0, 10))
        self.assertEqual((army["supply"], army["last_supply_received"], army["last_supply_consumed"], army["morale"]), (100, 10, 10, 72))
        categories = [event["category"] for event in payload["campaign"]["world"]["event_log"]]
        self.assertIn("strategy_army_supply_loaded", categories)
        self.assertIn("strategy_army_supplied", categories)
        app_source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('queueStrategyAction("load_army_supply"', app_source)
        self.assertIn("strategyArmySupplyStatusLabel", app_source)
        self.assertIn("补给路径", app_source)

    def test_scenario_lord_hero_can_campaign_personally_and_order_grand_general(self) -> None:
        created = self.api_post(
            "/api/strategy/campaigns/create",
            {"name": "lord hero command", "seed": 211, "city_count": 6, "faction_count": 2},
        )
        campaign_id = created["campaign"]["id"]
        self.api_post("/api/strategy/campaigns/enter", {"campaign_id": campaign_id})
        self.api_post("/api/strategy/campaigns/lock", {"campaign_id": campaign_id})
        campaign = self.api_post("/api/strategy/campaigns/enter", {"campaign_id": campaign_id})["campaign"]
        world = campaign["world"]
        lord = next(office for office in world["offices"] if office["faction_id"] == "faction_1" and office["office_type"] == "lord")
        grand = next(office for office in world["offices"] if office["faction_id"] == "faction_1" and office["office_type"] == "grand_general")
        nodes = {node["id"]: node for node in world["nodes"]}
        cities_by_node = {city["node_id"]: city for city in world["cities"]}
        source = target = None
        for city in world["cities"]:
            if city["owner_faction_id"] != "faction_1":
                continue
            candidate = next(
                (
                    cities_by_node[node_id]
                    for node_id in nodes[city["node_id"]]["connected_node_ids"]
                    if cities_by_node[node_id]["owner_faction_id"] != "faction_1"
                ),
                None,
            )
            if candidate is not None:
                source, target = city, candidate
                break
        self.assertIsNotNone(source)
        self.api_post(
            "/api/strategy/campaigns/queue-action",
            {
                "campaign_id": campaign_id,
                "action_type": "issue_office_order",
                "action_payload": {
                    "issuer_office_id": lord["id"],
                    "receiver_office_id": grand["id"],
                    "office_order_type": "attack_city",
                    "target_entity_id": target["id"],
                    "objective": f"进攻{target['name']}",
                },
            },
        )
        queued = self.api_post(
            "/api/strategy/campaigns/queue-action",
            {
                "campaign_id": campaign_id,
                "action_type": "declare_attack",
                "action_payload": {
                    "issuer_office_id": lord["id"],
                    "source_city_id": source["id"],
                    "target_city_id": target["id"],
                    "resolution_mode": "quick",
                },
            },
        )["campaign"]
        attack = next(action for action in queued["queued_actions"] if action["action_type"] == "declare_attack")
        self.assertEqual(attack["payload"]["commander_hero_code"], lord["holder_id"])
        self.assertIn(lord["holder_id"], attack["payload"]["attacker_hero_codes"])
        advanced = self.api_post(
            "/api/strategy/campaigns/advance-month",
            {"campaign_id": campaign_id, "issuer_office_id": lord["id"]},
        )["campaign"]
        self.assertEqual(advanced["world"]["office_orders"][-1]["order_type"], "attack_city")
        self.assertIn(lord["holder_id"], advanced["world"]["pending_battles"][-1]["attacker_hero_codes"])

    def test_scenario_strategy_defender_sets_pending_battle_hero_response(self) -> None:
        created = self.api_post(
            "/api/strategy/campaigns/create",
            {"name": "battle defender response", "seed": 98, "city_count": 6, "faction_count": 2},
        )
        campaign_id = created["campaign"]["id"]
        owner_user_id = created["campaign"]["owner_user_id"]
        bob = self.api_post("/api/auth/register", {"username": "BattleBob", "password": "secret123"})
        self.api_post("/api/strategy/campaigns/join", {"join_code": created["campaign"]["join_code"], "session_token": bob["session_token"]})
        self.api_post("/api/strategy/campaigns/enter", {"campaign_id": campaign_id})
        self.api_post("/api/strategy/campaigns/enter", {"campaign_id": campaign_id, "session_token": bob["session_token"]})
        self.api_post("/api/strategy/campaigns/lock", {"campaign_id": campaign_id})

        campaign = server_module.STRATEGY_STORE.get_campaign_for_user(campaign_id, owner_user_id)
        bob_member = next(member for member in campaign.members if member.username == "BattleBob")
        world = campaign.world
        heroes = [item for item in strategic_hero_pool_public(world) if item["faction_id"] == bob_member.faction_id and item["status"] == "serving"][:2]
        self.assertEqual(len(heroes), 2)
        bob_faction = next(item for item in world.factions if item.faction_id == bob_member.faction_id)
        bob_faction.tactic_techs.append("hero_command")

        nodes = {node.node_id: node for node in world.nodes}
        source_city = target_city = None
        for city in world.cities:
            if city.owner_faction_id != "faction_1":
                continue
            connected = set(nodes[city.node_id].connected_node_ids)
            target_city = next(
                (
                    candidate
                    for candidate in world.cities
                    if candidate.owner_faction_id == bob_member.faction_id and candidate.node_id in connected
                ),
                None,
            )
            if target_city is not None:
                source_city = city
                break
        if source_city is None or target_city is None:
            source_city = next(city for city in world.cities if city.owner_faction_id == "faction_1")
            target_city = next(city for city in world.cities if city.owner_faction_id == bob_member.faction_id)
            source_node = nodes[source_city.node_id]
            target_node = nodes[target_city.node_id]
            if target_node.node_id not in source_node.connected_node_ids:
                source_node.connected_node_ids.append(target_node.node_id)
            if source_node.node_id not in target_node.connected_node_ids:
                target_node.connected_node_ids.append(source_node.node_id)
        self.assertIsNotNone(source_city)
        self.assertIsNotNone(target_city)
        assert source_city is not None and target_city is not None
        source_city.resources.troops = 1200
        target_city.resources.troops = 300
        world = declare_city_attack(
            world,
            faction_id="faction_1",
            source_city_id=source_city.city_id,
            target_city_id=target_city.city_id,
            resolution_mode="manual",
        )
        battle_id = world.pending_battles[-1].battle_id
        server_module.STRATEGY_STORE.update_world(campaign_id, owner_user_id, world)

        updated = self.api_post(
            "/api/strategy/campaigns/set-battle-defense-hero",
            {
                "campaign_id": campaign_id,
                "battle_id": battle_id,
                "hero_codes": [hero["code"] for hero in heroes],
                "session_token": bob["session_token"],
            },
        )
        battle = next(item for item in updated["campaign"]["world"]["pending_battles"] if item["id"] == battle_id)

        self.assertEqual(battle["defender_hero_codes"], [hero["code"] for hero in heroes])
        self.assertEqual(
            next(item for item in updated["campaign"]["world"]["factions"] if item["id"] == bob_member.faction_id)["strategic_hero_deployment_limit"],
            2,
        )
        self.assertTrue(any(event["category"] == "battle_defender_hero_set" for event in updated["campaign"]["world"]["event_log"]))

    def test_scenario_serving_strategic_hero_enters_manual_city_battle_room(self) -> None:
        created = self.api_post(
            "/api/strategy/campaigns/create",
            {"name": "hero city battle", "seed": 99, "city_count": 6, "faction_count": 2},
        )
        campaign_id = created["campaign"]["id"]
        self.api_post("/api/strategy/campaigns/enter", {"campaign_id": campaign_id})
        self.api_post("/api/strategy/campaigns/lock", {"campaign_id": campaign_id})
        entered = self.api_post("/api/strategy/campaigns/enter", {"campaign_id": campaign_id})
        hero = next(
            item for item in entered["campaign"]["world"]["strategic_hero_pool"]
            if item["faction_id"] == "faction_1" and item["status"] == "serving"
        )

        declared = self.api_post(
            "/api/strategy/campaigns/declare-attack",
            {
                "campaign_id": campaign_id,
                "source_city_id": "city_1",
                "target_city_id": "city_2",
                "resolution_mode": "manual",
                "attacker_hero_codes": [hero["code"]],
            },
        )
        battle_room = declared["battle_room"]

        self.assertIn(hero["code"], battle_room["attacker_roster"])
        self.assertEqual(declared["campaign"]["world"]["pending_battles"][-1]["attacker_hero_codes"], [hero["code"]])
        self.assertTrue(
            any(
                row["source"] == "strategic_hero" and row["hero_code"] == hero["code"] and row["grid_units"] == 1
                for row in battle_room["attacker_roster_manifest"]
            )
        )

    def test_strategy_room_survivor_helpers_split_troops_from_strategic_heroes(self) -> None:
        room, _player_id, _token = ROOMS.create_preconfigured_battle_room(
            host_name="attacker",
            opponent_name="defender",
            player1_roster=["strategy_infantry", "fire_funeral"],
            player2_roster=["strategy_garrison", "ellie"],
            start_immediately=True,
        )

        self.assertEqual(server_module.strategy_room_survivors_by_team(room), {1: 1, 2: 1})
        self.assertEqual(server_module.strategy_room_surviving_hero_codes_by_team(room), {1: {"fire_funeral"}, 2: {"ellie"}})

    def test_scenario_strategy_city_policy_and_tactic_tech_update_sandbox_state(self) -> None:
        created = self.api_post(
            "/api/strategy/campaigns/create",
            {"name": "战术沙盒", "seed": 93, "city_count": 6, "faction_count": 2},
        )
        campaign_id = created["campaign"]["id"]
        city_id = created["campaign"]["world"]["cities"][0]["id"]
        before_conversion = created["campaign"]["world"]["cities"][0]["troop_conversion"]

        status, blocked = self.api_post_error(
            "/api/strategy/campaigns/set-city-policy",
            {
                "campaign_id": campaign_id,
                "city_id": city_id,
                "policy": "征兵优先",
                "session_token": self.default_session_token(),
            },
        )
        self.assertEqual(status, 409)

        self.api_post("/api/strategy/campaigns/enter", {"campaign_id": campaign_id})
        self.api_post("/api/strategy/campaigns/lock", {"campaign_id": campaign_id})
        policy_updated = self.api_post(
            "/api/strategy/campaigns/set-city-policy",
            {"campaign_id": campaign_id, "city_id": city_id, "policy": "征兵优先"},
        )
        self.assertEqual(policy_updated["campaign"]["world"]["cities"][0]["policy"], "征兵优先")
        self.assertTrue(
            any(event["category"] == "city_policy" for event in policy_updated["campaign"]["world"]["event_log"])
        )

        tech_updated = self.api_post(
            "/api/strategy/campaigns/unlock-tactic-tech",
            {"campaign_id": campaign_id, "tech_id": "local_militia"},
        )
        after_conversion = tech_updated["campaign"]["world"]["cities"][0]["troop_conversion"]
        tech_tree = {
            item["id"]: item
            for item in tech_updated["campaign"]["world"]["factions"][0]["tactic_tech_tree"]
        }

        self.assertTrue(tech_tree["local_militia"]["unlocked"])
        self.assertTrue(tech_tree["city_doctrine"]["available"])
        self.assertGreater(after_conversion[0]["ratio"], before_conversion[0]["ratio"])
        self.assertTrue(any(event["category"] == "tactic_tech" for event in tech_updated["campaign"]["world"]["event_log"]))

    def test_scenario_strategy_campaign_declares_and_resolves_city_attack_choice(self) -> None:
        created = self.api_post(
            "/api/strategy/campaigns/create",
            {"name": "战斗沙盒", "seed": 94, "city_count": 6, "faction_count": 2},
        )
        campaign_id = created["campaign"]["id"]
        self.api_post("/api/strategy/campaigns/enter", {"campaign_id": campaign_id})
        self.api_post("/api/strategy/campaigns/lock", {"campaign_id": campaign_id})

        resolved = self.api_post(
            "/api/strategy/campaigns/declare-attack",
            {
                "campaign_id": campaign_id,
                "source_city_id": "city_1",
                "target_city_id": "city_2",
                "resolution_mode": "ai_auto",
            },
        )
        world = resolved["campaign"]["world"]
        battle = world["pending_battles"][-1]
        battle_room = resolved["battle_room"]

        self.assertEqual(battle["status"], "resolved")
        self.assertEqual(battle["resolution_mode"], "ai_auto")
        self.assertEqual(battle["battle_room_id"], battle_room["room_id"])
        self.assertEqual(battle["battle_result"]["resolution_source"], "real_grid")
        self.assertIn(battle["battle_result"]["winner_side"], {"attacker", "defender"})
        self.assertTrue(battle_room["room_id"])
        self.assertEqual(battle_room["player_token"], "")
        self.assertEqual(battle_room["status"], "finished")
        self.assertIn(battle_room["winner"], {1, 2})
        self.assertGreater(battle_room["simulation_steps"], 0)
        self.assertIn("manual", world["battle_resolution_modes"])
        self.assertTrue(any(event["category"] == "battle_declared" for event in world["event_log"]))
        self.assertTrue(any(event["category"] == "battle_resolved" for event in world["event_log"]))
        room_state = self.api_get("/api/rooms/state", params={"room_id": battle_room["room_id"]})
        self.assertEqual(room_state["room"]["status"], "finished")
        self.assertIn("strategy_campaign", room_state)
        self.assertEqual(
            room_state["strategy_campaign"]["world"]["pending_battles"][-1]["battle_result"]["resolution_source"],
            "real_grid",
        )

    def test_scenario_strategy_manual_city_attack_creates_real_battle_room(self) -> None:
        created = self.api_post(
            "/api/strategy/campaigns/create",
            {"name": "真实战斗入口", "seed": 96, "city_count": 6, "faction_count": 2},
        )
        campaign_id = created["campaign"]["id"]
        self.api_post("/api/strategy/campaigns/enter", {"campaign_id": campaign_id})
        self.api_post("/api/strategy/campaigns/lock", {"campaign_id": campaign_id})

        declared = self.api_post(
            "/api/strategy/campaigns/declare-attack",
            {
                "campaign_id": campaign_id,
                "source_city_id": "city_1",
                "target_city_id": "city_2",
                "resolution_mode": "manual",
            },
        )
        battle_room = declared["battle_room"]
        battle = declared["campaign"]["world"]["pending_battles"][-1]

        self.assertTrue(battle_room["room_id"])
        self.assertTrue(battle_room["player_token"])
        self.assertGreaterEqual(len(battle_room["attacker_roster"]), 2)
        self.assertGreaterEqual(len(battle_room["defender_roster"]), 2)
        self.assertEqual(
            sum(row["grid_units"] for row in battle_room["attacker_roster_manifest"]),
            len(battle_room["attacker_roster"]),
        )
        self.assertEqual(
            sum(row["grid_units"] for row in battle_room["defender_roster_manifest"]),
            len(battle_room["defender_roster"]),
        )
        self.assertTrue(any(row["source"] == "city_feature" for row in battle_room["attacker_roster_manifest"]))
        self.assertTrue(all(code.startswith("strategy_") for code in battle_room["attacker_roster"]))
        self.assertTrue(all(row["hero_code"].startswith("strategy_") for row in battle_room["attacker_roster_manifest"]))
        self.assertEqual(battle["status"], "pending")
        self.assertEqual(battle["battle_room_id"], battle_room["room_id"])
        self.assertEqual(battle["battle_room_invite_path"], battle_room["invite_path"])
        self.assertTrue(any(event["category"] == "battle_room_created" for event in declared["campaign"]["world"]["event_log"]))

        room_state = self.api_get(
            "/api/rooms/state",
            params={"room_id": battle_room["room_id"], "player_token": battle_room["player_token"]},
        )
        self.assertEqual(room_state["room"]["status"], "battle")
        self.assertIsNotNone(room_state["battle"])
        self.assertEqual(room_state["room"]["viewer_player_id"], 1)
        self.assertEqual(len(room_state["battle"]["units"]), len(battle_room["attacker_roster"]) + len(battle_room["defender_roster"]))

    def test_scenario_strategy_manual_city_attack_writes_finished_real_battle_back(self) -> None:
        created = self.api_post(
            "/api/strategy/campaigns/create",
            {"name": "strategy writeback", "seed": 98, "city_count": 6, "faction_count": 2},
        )
        campaign_id = created["campaign"]["id"]
        self.api_post("/api/strategy/campaigns/enter", {"campaign_id": campaign_id})
        self.api_post("/api/strategy/campaigns/lock", {"campaign_id": campaign_id})

        declared = self.api_post(
            "/api/strategy/campaigns/declare-attack",
            {
                "campaign_id": campaign_id,
                "source_city_id": "city_1",
                "target_city_id": "city_2",
                "resolution_mode": "manual",
            },
        )
        battle_room = declared["battle_room"]
        declared_world = declared["campaign"]["world"]
        declared_battle = declared_world["pending_battles"][-1]
        source_troops_after_declared = declared_world["cities"][0]["resources"]["troops"]
        target_owner_before = declared["campaign"]["world"]["cities"][1]["owner_faction_id"]

        surrendered = self.api_post(
            "/api/rooms/surrender",
            {
                "room_id": battle_room["room_id"],
                "player_token": battle_room["player_token"],
            },
        )

        self.assertEqual(surrendered["room"]["status"], "finished")
        self.assertEqual(surrendered["battle"]["winner"], 2)
        self.assertIn("strategy_campaign", surrendered)
        campaign = surrendered["strategy_campaign"]
        battle = campaign["world"]["pending_battles"][-1]
        self.assertEqual(campaign["id"], campaign_id)
        self.assertEqual(battle["status"], "resolved")
        self.assertEqual(battle["winner_faction_id"], target_owner_before)
        self.assertEqual(
            campaign["world"]["cities"][0]["resources"]["troops"],
            source_troops_after_declared + declared_battle["attacker_troops"],
        )
        self.assertEqual(campaign["world"]["cities"][1]["resources"]["troops"], declared_battle["defender_troops"])
        self.assertEqual(campaign["world"]["cities"][1]["owner_faction_id"], target_owner_before)
        self.assertTrue(any("Real grid survivors" in row for row in battle["report"]))
        battle_result = battle["battle_result"]
        self.assertEqual(battle_result["winner_side"], "defender")
        self.assertEqual(battle_result["resolution_source"], "real_grid")
        self.assertEqual(battle_result["winner_faction_id"], target_owner_before)
        self.assertFalse(battle_result["city_captured"])
        self.assertEqual(battle_result["lost_troops_by_side"]["attacker"], 0)
        self.assertEqual(battle_result["lost_troops_by_side"]["defender"], 0)
        self.assertEqual(battle_result["remaining_troops_by_side"]["attacker"], declared_battle["attacker_troops"])
        self.assertEqual(battle_result["remaining_troops_by_side"]["defender"], declared_battle["defender_troops"])
        self.assertGreaterEqual(battle_result["surviving_grid_units_by_side"]["attacker"], 1)
        self.assertGreaterEqual(battle_result["surviving_grid_units_by_side"]["defender"], 1)
        self.assertEqual(
            sum(1 for event in campaign["world"]["event_log"] if event["category"] == "battle_resolved"),
            1,
        )

        room_state = self.api_get(
            "/api/rooms/state",
            params={"room_id": battle_room["room_id"], "player_token": battle_room["player_token"]},
        )
        self.assertEqual(room_state["room"]["status"], "finished")
        self.assertIn("strategy_campaign", room_state)
        self.assertEqual(
            sum(
                1
                for event in room_state["strategy_campaign"]["world"]["event_log"]
                if event["category"] == "battle_resolved"
            ),
            1,
        )

    def test_scenario_strategy_watch_ai_city_attack_creates_spectator_battle_room(self) -> None:
        created = self.api_post(
            "/api/strategy/campaigns/create",
            {"name": "AI观战入口", "seed": 97, "city_count": 6, "faction_count": 2},
        )
        campaign_id = created["campaign"]["id"]
        self.api_post("/api/strategy/campaigns/enter", {"campaign_id": campaign_id})
        self.api_post("/api/strategy/campaigns/lock", {"campaign_id": campaign_id})

        declared = self.api_post(
            "/api/strategy/campaigns/declare-attack",
            {
                "campaign_id": campaign_id,
                "source_city_id": "city_1",
                "target_city_id": "city_2",
                "resolution_mode": "watch_ai",
            },
        )
        battle_room = declared["battle_room"]
        battle = declared["campaign"]["world"]["pending_battles"][-1]

        self.assertTrue(battle_room["room_id"])
        self.assertEqual(battle_room["player_token"], "")
        self.assertGreaterEqual(len(battle_room["attacker_roster"]), 2)
        self.assertTrue(any(row["source"] == "city_feature" for row in battle_room["defender_roster_manifest"]))
        self.assertTrue(any(code.startswith("strategy_") for code in battle_room["defender_roster"]))
        self.assertEqual(battle["status"], "pending")
        self.assertEqual(battle["resolution_mode"], "watch_ai")
        self.assertEqual(battle["battle_room_id"], battle_room["room_id"])

        room_state = self.api_get(
            "/api/rooms/state",
            params={"room_id": battle_room["room_id"]},
        )
        self.assertEqual(room_state["room"]["status"], "battle")
        self.assertIsNotNone(room_state["battle"])
        self.assertIsNone(room_state["room"]["viewer_player_id"])
        self.assertTrue(room_state["room"]["simulation"]["enabled"])

    def test_scenario_created_room_is_visible_to_public_lobby_queries(self) -> None:
        # Given an empty public lobby
        lobby_before = self.api_get("/api/rooms")
        self.assertEqual(lobby_before["rooms"], [])

        # When a host creates a room
        created = self.api_post("/api/rooms/create", {"player_name": "Alice"})
        room_id = created["room"]["room_id"]

        # Then another viewer can discover that room from the public room APIs
        lobby_after = self.api_get("/api/rooms")
        hero_index = self.api_get("/api/heroes")

        self.assertEqual(len(lobby_after["rooms"]), 1)
        self.assertEqual(lobby_after["rooms"][0]["room_id"], room_id)
        self.assertEqual(lobby_after["rooms"][0]["occupied_seat_count"], 1)
        self.assertEqual(lobby_after["rooms"][0]["status"], "lobby")
        self.assertEqual(hero_index["rooms"][0]["room_id"], room_id)
        hero_codes = {hero["code"] for hero in hero_index["heroes"]}
        self.assertIn("excel_r030", hero_codes)
        self.assertIn("excel_r031", hero_codes)
        self.assertIn("excel_r032", hero_codes)
        self.assertIn("excel_r033", hero_codes)
        self.assertIn("excel_r034", hero_codes)
        self.assertIn("excel_r035", hero_codes)
        self.assertNotIn("excel_r038", hero_codes)

    def test_scenario_anonymous_viewer_can_open_room_state_before_joining(self) -> None:
        # Given a host has created a room
        created = self.api_post("/api/rooms/create", {"player_name": "Alice"})
        room_id = created["room"]["room_id"]

        # When another viewer opens the room by room id without a player token
        room_state = self.api_get("/api/rooms/state", params={"room_id": room_id})

        # Then the viewer can still see the room and current seat state
        self.assertEqual(room_state["room"]["room_id"], room_id)
        self.assertIsNone(room_state["room"]["viewer_player_id"])
        self.assertEqual(room_state["room"]["status"], "lobby")
        self.assertEqual(room_state["room"]["start_blocker"], "仍有开放席位未被真人或 AI 占用。")
        self.assertTrue(room_state["room"]["seats"][0]["occupied"])
        self.assertFalse(room_state["room"]["seats"][1]["occupied"])

    def test_scenario_custom_room_confirms_readiness_times_out_and_restores_the_same_seat(self) -> None:
        # Given two human players have legal rosters but have not confirmed the final room configuration
        created = self.api_post("/api/rooms/create", {"player_name": "Alice"})
        room_id = created["room"]["room_id"]
        host_token = created["player_token"]
        joined = self.api_post("/api/rooms/join", {"room_id": room_id, "player_name": "Bob"})
        guest_token = joined["player_token"]
        self.api_post(
            "/api/rooms/select-hero",
            {"room_id": room_id, "player_token": host_token, "hero_code": "bard", "delta": 1},
        )
        configured = self.api_post(
            "/api/rooms/select-hero",
            {"room_id": room_id, "player_token": guest_token, "hero_code": "ellie", "delta": 1},
        )

        self.assertTrue(configured["room"]["configuration_ready"])
        self.assertFalse(configured["room"]["can_start"])
        self.assertIn("确认准备", configured["room"]["start_blocker"])

        # When both players confirm, only the host can start; a later roster edit revokes both confirmations
        self.confirm_room_ready(room_id, host_token, guest_token)
        ready_state = self.api_get("/api/rooms/state", params={"room_id": room_id, "player_token": host_token})
        self.assertTrue(ready_state["room"]["can_start"])
        self.assertEqual(ready_state["room"]["human_ready_count"], 2)
        status, error = self.api_post_error(
            "/api/rooms/start",
            {
                "room_id": room_id,
                "player_token": guest_token,
                "session_token": self.default_session_token(),
            },
        )
        self.assertEqual(status, 400)
        self.assertIn("房主", error["error"])
        changed = self.api_post(
            "/api/rooms/select-hero",
            {"room_id": room_id, "player_token": host_token, "hero_code": "bard", "delta": 1},
        )
        self.assertEqual(changed["room"]["human_ready_count"], 0)
        self.confirm_room_ready(room_id, host_token, guest_token)
        started = self.api_post("/api/rooms/start", {"room_id": room_id, "player_token": host_token})

        # Then the server owns the 90-second prompt timer and publishes both players' connection state
        self.assertEqual(started["room"]["status"], "battle")
        self.assertTrue(started["room"]["turn_timer"]["enabled"])
        self.assertEqual(started["room"]["turn_timer"]["duration_seconds"], 90)
        self.assertTrue(all(seat["connection_status"] == "online" for seat in started["room"]["seats"]))

        # And an expired deadline safely advances the prompt while a saved token restores the exact seat
        room = ROOMS.get_room(room_id)
        room.turn_deadline_at = 0
        timed_out = self.api_get("/api/rooms/state", params={"room_id": room_id, "player_token": host_token})
        self.assertIsNotNone(timed_out["room"]["turn_timer"]["last_timeout"])
        room.seats[2].last_seen_at = 0
        offline = self.api_get("/api/rooms/state", params={"room_id": room_id, "player_token": host_token})
        self.assertEqual(offline["room"]["seats"][1]["connection_status"], "offline")
        restored = self.api_get("/api/rooms/state", params={"room_id": room_id, "player_token": guest_token})
        self.assertEqual(restored["room"]["viewer_player_id"], 2)
        self.assertEqual(restored["room"]["seats"][1]["connection_status"], "online")

    def test_scenario_finished_room_explains_surrender_stats_key_turns_and_mvp(self) -> None:
        # Given a legal human match has recorded effective healing, shield pressure, and damage
        created = self.api_post("/api/rooms/create", {"player_name": "Alice"})
        room_id = created["room"]["room_id"]
        host_token = created["player_token"]
        joined = self.api_post("/api/rooms/join", {"room_id": room_id, "player_name": "Bob"})
        guest_token = joined["player_token"]
        self.api_post(
            "/api/rooms/select-hero",
            {"room_id": room_id, "player_token": host_token, "hero_code": "fire_funeral", "delta": 1},
        )
        self.api_post(
            "/api/rooms/select-hero",
            {"room_id": room_id, "player_token": guest_token, "hero_code": "ellie", "delta": 1},
        )
        self.confirm_room_ready(room_id, host_token, guest_token)
        self.api_post("/api/rooms/start", {"room_id": room_id, "player_token": host_token})
        room = ROOMS.get_room(room_id)
        fire = primary_hero(room.battle, 1)
        ellie = primary_hero(room.battle, 2)
        fire.current_hp = 0.5
        room.battle.heal(HealContext(source=fire, target=fire, amount=0.25, action_name="测试治疗"))
        ellie.shields = 1
        room.battle.resolve_damage(
            DamageContext(source=fire, target=ellie, attack_power=4, raw_damage=0.25, is_skill=True, action_name="测试破盾")
        )
        room.battle.resolve_damage(
            DamageContext(source=fire, target=ellie, attack_power=4, raw_damage=0.25, is_skill=True, action_name="测试伤害")
        )

        # When the guest surrenders
        finished = self.api_post("/api/rooms/surrender", {"room_id": room_id, "player_token": guest_token})
        summary = finished["room"]["postgame"]

        # Then the server returns transparent winner, contribution, MVP, and replay-linked key-turn evidence
        self.assertTrue(summary["available"])
        self.assertEqual(summary["winner_team_id"], 1)
        self.assertEqual(summary["reason_code"], "surrender")
        self.assertIn("Bob", summary["reason_text"])
        red = next(team for team in summary["team_stats"] if team["team_id"] == 1)
        self.assertEqual(red["damage_dealt"], 0.25)
        self.assertEqual(red["healing_done"], 0.25)
        self.assertEqual(red["shields_broken"], 1)
        self.assertEqual(summary["mvp"]["name"], "火葬者")
        self.assertEqual(summary["mvp"]["damage_dealt"], 0.25)
        self.assertEqual(summary["mvp"]["healing_done"], 0.25)
        self.assertEqual(summary["mvp"]["shields_broken"], 1)
        self.assertTrue(summary["key_turns"])
        self.assertTrue(all(item["replay_step_index"] is not None for item in summary["key_turns"]))

    def test_scenario_account_history_replay_and_same_configuration_rematch_form_a_closed_loop(self) -> None:
        # Given two logged-in accounts finish a configured room
        alice_token = self.default_session_token()
        bob = self.api_post("/api/auth/register", {"username": "HistoryBob", "password": "secret123"})
        charlie = self.api_post("/api/auth/register", {"username": "HistoryCharlie", "password": "secret123"})
        created = self.api_post(
            "/api/rooms/create",
            {"player_name": "Alice", "session_token": alice_token},
        )
        room_id = created["room"]["room_id"]
        host_token = created["player_token"]
        joined = self.api_post(
            "/api/rooms/join",
            {"room_id": room_id, "player_name": "Bob", "session_token": bob["session_token"]},
        )
        guest_token = joined["player_token"]
        self.api_post(
            "/api/rooms/select-hero",
            {"room_id": room_id, "player_token": host_token, "hero_code": "fire_funeral", "delta": 1},
        )
        self.api_post(
            "/api/rooms/select-hero",
            {"room_id": room_id, "player_token": guest_token, "hero_code": "ellie", "delta": 1, "session_token": bob["session_token"]},
        )
        self.api_post("/api/rooms/set-ready", {"room_id": room_id, "player_token": host_token, "ready": True})
        self.api_post(
            "/api/rooms/set-ready",
            {"room_id": room_id, "player_token": guest_token, "ready": True, "session_token": bob["session_token"]},
        )
        started = self.api_post("/api/rooms/start", {"room_id": room_id, "player_token": host_token})
        first_match_id = started["room"]["match_id"]
        self.api_post(
            "/api/rooms/surrender",
            {"room_id": room_id, "player_token": guest_token, "session_token": bob["session_token"]},
        )

        # Then each participant gets one private result and a persisted replay
        alice_history = self.api_get("/api/matches/recent", params={"session_token": alice_token})["matches"]
        bob_history = self.api_get("/api/matches/recent", params={"session_token": bob["session_token"]})["matches"]
        self.assertEqual([item["match_id"] for item in alice_history], [first_match_id])
        self.assertEqual(alice_history[0]["result"], "win")
        self.assertEqual(bob_history[0]["result"], "loss")
        serialized_history = json.dumps(alice_history, ensure_ascii=False)
        self.assertNotIn("session_token", serialized_history)
        self.assertNotIn("player_token", serialized_history)

        # And the same account-owned history produces non-stat mastery progress and a next goal
        alice_progression = self.api_get(
            "/api/progression/overview",
            params={"session_token": alice_token},
        )["progression"]
        bob_progression = self.api_get(
            "/api/progression/overview",
            params={"session_token": bob["session_token"]},
        )["progression"]
        fire_progress = next(item for item in alice_progression["hero_progress"] if item["hero_code"] == "fire_funeral")
        ellie_progress = next(item for item in bob_progression["hero_progress"] if item["hero_code"] == "ellie")
        self.assertEqual((fire_progress["matches"], fire_progress["wins"], fire_progress["mastery_points"]), (1, 1, 2))
        self.assertEqual((ellie_progress["matches"], ellie_progress["losses"], ellie_progress["mastery_points"]), (1, 1, 1))
        self.assertEqual(fire_progress["mastery_level"], "初识")
        self.assertEqual(fire_progress["next_mastery_level"], "熟练")
        self.assertFalse(alice_progression["grants_gameplay_power"])
        self.assertEqual(alice_progression["next_goal"]["hero_code"], "fire_funeral")
        self.assertNotEqual(alice_progression, bob_progression)
        anonymous_status, _ = self.api_get_error("/api/progression/overview")
        self.assertEqual(anonymous_status, 401)
        replay = self.api_get(
            "/api/matches/replay",
            params={"session_token": alice_token, "match_id": first_match_id, "step_index": "-1"},
        )
        self.assertTrue(replay["room"]["historical"])
        self.assertEqual(replay["replay"]["step_index"], replay["replay"]["last_step_index"])
        forbidden_status, _ = self.api_get_error(
            "/api/matches/replay",
            params={"session_token": charlie["session_token"], "match_id": first_match_id},
        )
        self.assertEqual(forbidden_status, 404)

        # And only the host can retain the full configuration for a newly confirmed second match
        guest_status, _ = self.api_post_error(
            "/api/rooms/rematch",
            {"room_id": room_id, "player_token": guest_token, "session_token": bob["session_token"]},
        )
        self.assertEqual(guest_status, 400)
        rematch = self.api_post("/api/rooms/rematch", {"room_id": room_id, "player_token": host_token})
        self.assertEqual(rematch["room"]["seats"][0]["hero_counts"], {"fire_funeral": 1})
        self.assertEqual(rematch["room"]["seats"][1]["hero_counts"], {"ellie": 1})
        self.assertFalse(rematch["room"]["seats"][0]["ready"])
        self.assertFalse(rematch["room"]["seats"][1]["ready"])
        self.api_post("/api/rooms/set-ready", {"room_id": room_id, "player_token": host_token, "ready": True})
        self.api_post(
            "/api/rooms/set-ready",
            {"room_id": room_id, "player_token": guest_token, "ready": True, "session_token": bob["session_token"]},
        )
        second = self.api_post("/api/rooms/start", {"room_id": room_id, "player_token": host_token})
        self.assertNotEqual(second["room"]["match_id"], first_match_id)

    def test_scenario_room_flows_from_lobby_to_battle_through_public_http_api(self) -> None:
        # Given two players create and join the same room
        created = self.api_post("/api/rooms/create", {"player_name": "Alice"})
        room_id = created["room"]["room_id"]
        host_token = created["player_token"]
        joined = self.api_post("/api/rooms/join", {"room_id": room_id, "player_name": "Bob"})
        guest_token = joined["player_token"]

        # When both sides choose heroes and the host starts the battle
        self.api_post(
            "/api/rooms/select-hero",
            {"room_id": room_id, "player_token": host_token, "hero_code": "dark_human", "delta": 1},
        )
        self.api_post(
            "/api/rooms/select-hero",
            {"room_id": room_id, "player_token": host_token, "hero_code": "fire_funeral", "delta": 1},
        )
        self.api_post(
            "/api/rooms/select-hero",
            {"room_id": room_id, "player_token": guest_token, "hero_code": "undead_king_lina", "delta": 1},
        )
        self.confirm_room_ready(room_id, host_token, guest_token)
        started = self.api_post("/api/rooms/start", {"room_id": room_id, "player_token": host_token})

        # Then the room enters battle and exposes the current acting unit bundle
        self.assertEqual(started["room"]["status"], "battle")
        self.assertEqual(started["battle"]["input_player"], 2)
        self.assertEqual(started["battle"]["active_turn_unit_name"], "不死王利娜")
        self.assertEqual(len(started["battle"]["active_units"]), 0)

        guest_view = self.api_get("/api/rooms/state", params={"room_id": room_id, "player_token": guest_token})
        self.assertEqual(len(guest_view["battle"]["active_units"]), 1)
        self.assertEqual(guest_view["battle"]["active_units"][0]["name"], "不死王利娜")


    def test_scenario_ai_room_seat_takes_the_opening_turn_before_returning_input_to_the_human(self) -> None:
        # Given a room with one human seat and one AI seat
        created = self.api_post("/api/rooms/create", {"player_name": "Alice"})
        room_id = created["room"]["room_id"]
        host_token = created["player_token"]
        self.api_post(
            "/api/rooms/set-seat-controller",
            {"room_id": room_id, "player_token": host_token, "seat_id": 2, "controller_type": "ai"},
        )
        self.api_post(
            "/api/rooms/select-hero",
            {"room_id": room_id, "player_token": host_token, "hero_code": "bard", "delta": 1},
        )
        self.api_post(
            "/api/rooms/select-hero",
            {"room_id": room_id, "player_token": host_token, "seat_id": 2, "hero_code": "elite_soldier", "delta": 1},
        )

        # When the host starts the battle
        self.confirm_room_ready(room_id, host_token)
        started = self.api_post("/api/rooms/start", {"room_id": room_id, "player_token": host_token})

        # Then the battle exposes a staged AI opening action instead of resolving it instantly
        self.assertEqual(started["room"]["status"], "battle")
        self.assertEqual(started["battle"]["input_player"], 2)
        self.assertIsNotNone(started["room"]["simulation"]["pending_action"])
        self.assertEqual(started["room"]["simulation"]["pending_action"]["actor_name"], "精兵")

        room = ROOMS.get_room(room_id)
        settled = started
        for _ in range(12):
            if room.pending_simulation_action is not None:
                room.pending_simulation_action["next_due_at"] = 0
            settled = self.api_get("/api/rooms/state", params={"room_id": room_id, "player_token": host_token})
            if settled["battle"]["input_player"] == 1:
                break

        self.assertEqual(settled["battle"]["input_player"], 1)
        self.assertIsNotNone(settled["battle"]["pending_chain"])

    def test_scenario_random_room_assigns_n_heroes_per_side_with_classic_turn_rules(self) -> None:
        # Given a random-mode room with both players joined
        created = self.api_post("/api/rooms/create", {"player_name": "Alice", "mode": "random"})
        room_id = created["room"]["room_id"]
        host_token = created["player_token"]
        joined = self.api_post("/api/rooms/join", {"room_id": room_id, "player_name": "Bob"})
        guest_token = joined["player_token"]

        # When the host sets n and starts with a deterministic random roster
        configured = self.api_post(
            "/api/rooms/set-random-roster-size",
            {"room_id": room_id, "player_token": host_token, "random_roster_size": 3},
        )
        roster1 = ["doomlight_dragon", "bard", "dark_human"]
        roster2 = ["rock_god", "elite_soldier", "ellie"]
        self.confirm_room_ready(room_id, host_token, guest_token)
        with mock.patch("wujiang.web.multiplayer.random_room_hero_codes", return_value=(roster1, roster2)):
            with mock.patch("wujiang.heroes.registry.random.choice", side_effect=lambda seq: seq[-1]):
                started = self.api_post("/api/rooms/start", {"room_id": room_id, "player_token": host_token})

        # Then both sides receive n heroes and use the classic multi-hero board and turn-order rules
        self.assertEqual(configured["room"]["random_roster_size"], 3)
        self.assertEqual(started["room"]["status"], "battle")
        self.assertEqual(started["room"]["random_roster_size"], 3)
        self.assertEqual(started["room"]["seats"][0]["hero_total_count"], 3)
        self.assertEqual(started["room"]["seats"][1]["hero_total_count"], 3)
        self.assertEqual(
            len([unit for unit in started["battle"]["units"] if unit["player_id"] == 1 and not unit.get("is_summon")]),
            3,
        )
        self.assertEqual(
            len([unit for unit in started["battle"]["units"] if unit["player_id"] == 2 and not unit.get("is_summon")]),
            3,
        )

        expected_battle = create_battle(roster1, roster2)
        self.assertEqual(started["battle"]["board"]["width"], expected_battle.width)
        self.assertEqual(started["battle"]["board"]["height"], expected_battle.height)
        room = ROOMS.get_room(room_id)
        self.assertEqual(
            [room.battle.get_unit(unit_id).hero_code for unit_id in room.battle.turn_order_unit_ids],
            [expected_battle.get_unit(unit_id).hero_code for unit_id in expected_battle.turn_order_unit_ids],
        )

    def test_scenario_random_room_never_starts_with_duplicate_heroes_on_field(self) -> None:
        created = self.api_post("/api/rooms/create", {"player_name": "Alice", "mode": "random"})
        room_id = created["room"]["room_id"]
        host_token = created["player_token"]
        joined = self.api_post("/api/rooms/join", {"room_id": room_id, "player_name": "Bob"})
        guest_token = joined["player_token"]
        self.api_post(
            "/api/rooms/set-random-roster-size",
            {"room_id": room_id, "player_token": host_token, "random_roster_size": 1},
        )

        self.confirm_room_ready(room_id, host_token, guest_token)
        started = self.api_post("/api/rooms/start", {"room_id": room_id, "player_token": host_token})
        hero_names = [
            unit["name"]
            for unit in started["battle"]["units"]
            if not unit.get("is_summon")
        ]

        self.assertEqual(len(hero_names), 2)
        self.assertEqual(len(set(hero_names)), 2)

    def test_scenario_multi_seat_room_can_start_with_team_merge_and_random_quotas(self) -> None:
        created = self.api_post("/api/rooms/create", {"player_name": "Alice", "mode": "random"})
        room_id = created["room"]["room_id"]
        host_token = created["player_token"]
        joined = self.api_post("/api/rooms/join", {"room_id": room_id, "player_name": "Bob"})
        guest_token = joined["player_token"]

        self.api_post("/api/rooms/set-seat-count", {"room_id": room_id, "player_token": host_token, "seat_count": 4})
        self.api_post(
            "/api/rooms/set-seat-controller",
            {"room_id": room_id, "player_token": host_token, "seat_id": 3, "controller_type": "ai"},
        )
        self.api_post(
            "/api/rooms/set-seat-controller",
            {"room_id": room_id, "player_token": host_token, "seat_id": 4, "controller_type": "ai"},
        )
        self.api_post("/api/rooms/set-seat-team", {"room_id": room_id, "player_token": host_token, "seat_id": 3, "team_id": 1})
        self.api_post("/api/rooms/set-seat-team", {"room_id": room_id, "player_token": host_token, "seat_id": 4, "team_id": 2})
        self.api_post(
            "/api/rooms/set-random-roster-size",
            {"room_id": room_id, "player_token": host_token, "random_roster_size": 2},
        )
        self.api_post("/api/rooms/set-seat-random-quota", {"room_id": room_id, "player_token": host_token, "seat_id": 1, "quota": 1})
        self.api_post("/api/rooms/set-seat-random-quota", {"room_id": room_id, "player_token": host_token, "seat_id": 2, "quota": 1})
        self.api_post("/api/rooms/set-seat-random-quota", {"room_id": room_id, "player_token": host_token, "seat_id": 3, "quota": 1})
        self.api_post("/api/rooms/set-seat-random-quota", {"room_id": room_id, "player_token": host_token, "seat_id": 4, "quota": 1})

        roster1 = ["bard", "dark_human"]
        roster2 = ["doomlight_dragon", "elite_soldier"]
        self.confirm_room_ready(room_id, host_token, guest_token)
        with mock.patch("wujiang.web.multiplayer.random_room_hero_codes", return_value=(roster1, roster2)):
            started = self.api_post("/api/rooms/start", {"room_id": room_id, "player_token": host_token})

        self.assertEqual(started["room"]["seat_count"], 4)
        self.assertEqual(started["room"]["ai_seat_count"], 2)
        self.assertEqual(started["room"]["seats"][0]["hero_total_count"], 1)
        self.assertEqual(started["room"]["seats"][2]["hero_total_count"], 1)
        self.assertEqual(started["room"]["seats"][1]["hero_total_count"], 1)
        self.assertEqual(started["room"]["seats"][3]["hero_total_count"], 1)
        self.assertEqual(started["room"]["status"], "battle")
        self.assertIsNotNone(started["battle"])


    def test_scenario_ai_only_room_can_run_with_pause_step_and_replay_http_api(self) -> None:
        created = self.api_post("/api/rooms/create", {"player_name": "Alice"})
        room_id = created["room"]["room_id"]
        host_token = created["player_token"]

        self.api_post("/api/rooms/set-seat-count", {"room_id": room_id, "player_token": host_token, "seat_count": 4})
        self.api_post(
            "/api/rooms/set-seat-controller",
            {"room_id": room_id, "player_token": host_token, "seat_id": 2, "controller_type": "ai"},
        )
        self.api_post(
            "/api/rooms/set-seat-controller",
            {"room_id": room_id, "player_token": host_token, "seat_id": 3, "controller_type": "ai"},
        )
        self.api_post(
            "/api/rooms/set-seat-controller",
            {"room_id": room_id, "player_token": host_token, "seat_id": 4, "controller_type": "ai"},
        )
        self.api_post("/api/rooms/set-seat-team", {"room_id": room_id, "player_token": host_token, "seat_id": 2, "team_id": 2})
        self.api_post("/api/rooms/set-seat-team", {"room_id": room_id, "player_token": host_token, "seat_id": 3, "team_id": 1})
        self.api_post("/api/rooms/set-seat-team", {"room_id": room_id, "player_token": host_token, "seat_id": 4, "team_id": 2})
        self.api_post(
            "/api/rooms/select-hero",
            {"room_id": room_id, "player_token": host_token, "seat_id": 3, "hero_code": "bard", "delta": 1},
        )
        self.api_post(
            "/api/rooms/select-hero",
            {"room_id": room_id, "player_token": host_token, "seat_id": 2, "hero_code": "elite_soldier", "delta": 1},
        )

        self.confirm_room_ready(room_id, host_token)
        started = self.api_post("/api/rooms/start", {"room_id": room_id, "player_token": host_token})
        self.assertTrue(started["room"]["simulation"]["enabled"])
        self.assertTrue(started["room"]["replay"]["available"])
        self.assertIsNotNone(started["room"]["simulation"]["pending_action"])
        self.assertEqual(started["room"]["simulation"]["pending_action"]["visible_count"], 0)

        room = ROOMS.get_room(room_id)
        running = started
        for _ in range(12):
            if room.pending_simulation_action is not None:
                room.pending_simulation_action["next_due_at"] = 0
            running = self.api_get("/api/rooms/state", params={"room_id": room_id, "player_token": host_token})
            if running["room"]["simulation"]["live_step_index"] > 0:
                break
        self.assertTrue(
            running["room"]["simulation"]["live_step_index"] > 0
            or running["room"]["simulation"]["pending_action"] is not None
        )
        self.assertGreater(running["room"]["simulation"]["live_step_index"], 0)

        paused = self.api_post(
            "/api/rooms/simulation-control",
            {"room_id": room_id, "player_token": host_token, "action": "pause"},
        )
        self.assertTrue(paused["room"]["simulation"]["paused"])
        paused_step = paused["room"]["simulation"]["live_step_index"]

        stepped = self.api_post(
            "/api/rooms/simulation-control",
            {"room_id": room_id, "player_token": host_token, "action": "step"},
        )
        self.assertGreaterEqual(stepped["room"]["simulation"]["live_step_index"], paused_step)

        replay = self.api_get(
            "/api/rooms/replay",
            params={"room_id": room_id, "player_token": host_token, "step_index": 0},
        )
        self.assertEqual(replay["replay"]["step_index"], 0)
        self.assertIn("board", replay["battle"])


class CombatBehaviorTests(unittest.TestCase):
    def test_scenario_classic_multihero_turn_order_stays_fixed_and_skips_destroyed_slots(self) -> None:
        # Given a classic multi-hero battle with both rosters sorted once at battle start
        battle = create_battle(
            ["dark_human", "fire_funeral", "bard"],
            ["undead_king_lina", "jade", "doomlight_dragon"],
        )
        bard = next(unit for unit in battle.hero_units(1) if unit.hero_code == "bard")

        # Then the round order alternates between the already-sorted side lists
        turn_order_codes = [battle.get_unit(unit_id).hero_code for unit_id in battle.turn_order_unit_ids]
        self.assertEqual(
            turn_order_codes,
            ["undead_king_lina", "dark_human", "jade", "fire_funeral", "doomlight_dragon", "bard"],
        )

        # When the bard slot comes later in the ring but the bard has already been destroyed
        end_turns(battle, 4)
        battle.remove_unit(bard)
        battle.perform_action({"type": "end_turn"})

        # Then the fixed slot is skipped without reordering the rest of the ring
        self.assertIn(bard.unit_id, battle.turn_order_unit_ids)
        self.assertEqual(battle.current_turn_unit().hero_code, "undead_king_lina")
        self.assertEqual(battle.round_number, 2)
        battle.perform_action({"type": "end_turn"})
        self.assertEqual(battle.current_turn_unit().hero_code, "dark_human")
        self.assertEqual(battle.active_player, 1)

    def test_scenario_timeout_rule_scales_with_opening_hero_count(self) -> None:
        # Given a four-hero battle with no damage or kills happening
        battle = create_battle(["bard", "ellie"], ["dark_human", "elite_soldier"])
        self.assertEqual(battle.initial_hero_count, 4)
        self.assertEqual(battle.turn_timeout_limit, 80)

        # When 79 hero turns have completed
        end_turns(battle, 79)

        # Then the battle is still unresolved
        self.assertIsNone(battle.winner)

        # And the 80th completed hero turn forces a random winner
        with mock.patch("wujiang.engine.core.random.choice", return_value=1):
            battle.perform_action({"type": "end_turn"})

        self.assertEqual(battle.winner, 1)
        self.assertIn("80 个武将回合上限", battle.logs[-1])

    def test_scenario_stealth_blocks_enemy_direct_targeting_but_friendly_support_still_works(self) -> None:
        # Given a stealthed Dark Human with a friendly Bard nearby
        battle = create_battle("dark_human", "elite_soldier")
        dark = primary_hero(battle, 1)
        soldier = primary_hero(battle, 2)
        bard = create_hero("bard", 1)
        battle.add_unit(bard, Position(4, 4))
        dark.position = Position(5, 4)
        soldier.position = Position(7, 7)
        dark.current_hp = 0.5

        # When Dark Human enters stealth and Bard uses a friendly targeted heal
        battle.perform_action({"type": "skill", "unit_id": dark.unit_id, "skill_code": "stealth"})
        battle.perform_action({"type": "skill", "unit_id": bard.unit_id, "skill_code": "heal", "target_unit_id": dark.unit_id})

        # Then the friendly support still lands on the stealthed ally
        self.assertGreater(dark.current_hp, 0.5)

        # And enemy direct targeting still fails on the following turn
        battle.perform_action({"type": "end_turn"})
        with self.assertRaises(ActionError):
            battle.perform_action({"type": "attack", "unit_id": soldier.unit_id, "target_unit_id": dark.unit_id})

    def test_scenario_direct_unit_skill_preview_requires_straight_line(self) -> None:
        # Given Bard has one ally in square range but not on any straight line
        battle = create_battle("bard", "fire_funeral")
        bard = primary_hero(battle, 1)
        enemy = primary_hero(battle, 2)
        ally = create_hero("elite_soldier", 1)
        bard.position = Position(3, 3)
        enemy.position = Position(7, 7)
        battle.add_unit(ally, Position(5, 4))

        # Then the player-facing Heal preview does not expose that ally as a selectable target
        actions = {action["code"]: action for action in battle.action_snapshot_for(bard)["actions"]}
        self.assertNotIn(ally.unit_id, actions["heal"]["preview"]["target_unit_ids"])

        # And the same ally becomes selectable when it is in Bard's range on a straight line
        ally.position = Position(5, 5)
        actions = {action["code"]: action for action in battle.action_snapshot_for(bard)["actions"]}
        self.assertIn(ally.unit_id, actions["heal"]["preview"]["target_unit_ids"])

    def test_scenario_bard_chant_adds_mana_points_without_refilling_mana(self) -> None:
        # Given a Bard with mana below cap
        battle = create_battle("bard", "dark_human")
        bard = primary_hero(battle, 1)
        enemy = primary_hero(battle, 2)
        bard.position = Position(4, 4)
        enemy.position = Position(7, 7)
        bard.current_mana = 3

        # When Bard uses Chant on self
        battle.perform_action({"type": "skill", "unit_id": bard.unit_id, "skill_code": "chant", "target_unit_id": bard.unit_id})

        # Then mana points increase but mana itself does not
        self.assertEqual(bard.mana_points, 2.0)
        self.assertEqual(bard.current_mana, 3)

    def test_scenario_blood_eater_blood_dance_locks_until_blood_eaters_next_turn_start(self) -> None:
        # Given BloodEater has an allied unit in range
        battle = create_battle("blood_eater", "fire_funeral")
        blood = primary_hero(battle, 1)
        ally = create_hero("bard", 1)
        enemy = primary_hero(battle, 2)
        battle.add_unit(ally, Position(4, 4))
        blood.position = Position(3, 4)
        enemy.position = Position(7, 7)
        blood.current_mana = 4
        ally.current_mana = 4
        blood.current_hp = 0.5
        ally.current_hp = 0.5

        # When BloodEater uses Blood Dance
        battle.perform_action({"type": "skill", "unit_id": blood.unit_id, "skill_code": "blood_dance", "target_unit_id": ally.unit_id})

        # Then both units are healed, gain mana, and cannot move yet
        self.assertAlmostEqual(blood.current_hp, 0.75)
        self.assertAlmostEqual(ally.current_hp, 0.75)
        self.assertEqual(blood.current_mana, 5)
        self.assertEqual(ally.current_mana, 5)
        self.assertTrue(blood.cannot_move)
        self.assertTrue(ally.cannot_move)

        # When only the enemy turn starts, the lock is still active
        battle.perform_action({"type": "end_turn"})
        self.assertTrue(blood.cannot_move)
        self.assertTrue(ally.cannot_move)

        # And it ends at BloodEater's next own turn start
        battle.perform_action({"type": "end_turn"})
        self.assertFalse(blood.cannot_move)
        self.assertFalse(ally.cannot_move)

    def test_scenario_li_start_phase_toggle_and_split_move_are_player_visible(self) -> None:
        # Given Li is at the beginning of his turn
        battle = create_battle("li", "bard")
        li = primary_hero(battle, 1)
        enemy = primary_hero(battle, 2)
        li.position = Position(3, 4)
        enemy.position = Position(8, 8)

        # Then Red Heat is available before actions and normal movement shows the full speed budget
        start_actions = {action["code"]: action for action in battle.action_snapshot_for(li)["actions"]}
        self.assertTrue(start_actions["red_heat"]["available"])
        self.assertEqual(start_actions["move"]["preview"]["selection"]["max_steps"], 3)

        # When Li splits his normal move
        battle.perform_action({"type": "move", "unit_id": li.unit_id, "path": [{"x": 4, "y": 4}, {"x": 5, "y": 4}]})

        # Then the frontend action snapshot still exposes the remaining normal move distance
        after_move = {action["code"]: action for action in battle.action_snapshot_for(li)["actions"]}
        self.assertTrue(after_move["move"]["available"])
        self.assertEqual(after_move["move"]["preview"]["selection"]["max_steps"], 1)
        self.assertFalse(after_move["red_heat"]["available"])

    def test_scenario_li_stillness_is_once_per_battle_ultimate_not_instant(self) -> None:
        # Given Li is at the beginning of his own turn
        battle = create_battle("li", "bard")
        li = primary_hero(battle, 1)
        enemy = primary_hero(battle, 2)
        li.position = Position(3, 4)
        enemy.position = Position(8, 8)

        # Then Stillness is exposed as an active once-per-battle skill
        own_actions = {action["code"]: action for action in battle.action_snapshot_for(li)["actions"]}
        stillness = own_actions["stillness"]
        self.assertTrue(stillness["available"])
        self.assertEqual(stillness["timing"], "active")
        self.assertEqual(stillness["max_uses_per_battle"], 1)

        # And it is not exposed as an instant action while the enemy is acting
        battle.perform_action({"type": "end_turn"})
        waiting_actions = {action["code"]: action for action in battle.action_snapshot_for(li)["actions"]}
        self.assertFalse(waiting_actions["stillness"]["available"])
        self.assertEqual(waiting_actions["stillness"]["timing"], "active")

    def test_scenario_chanter_card_marker_blocks_enemy_skill_from_field_state(self) -> None:
        # Given Chanter places a paralysis card on an enemy's cell
        battle = create_battle("chanter", "bard")
        chanter = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        chanter.position = Position(3, 4)
        bard.position = Position(5, 4)

        battle.perform_action({"type": "skill", "unit_id": chanter.unit_id, "skill_code": "paralysis_card", "x": 5, "y": 4})
        resolve_pending_chain(battle)

        # Then the field marker is visible and the enemy cannot use skills while inside its 3x3 area
        public_state = battle.to_public_dict()
        self.assertTrue(any(effect["name"] == "麻痹牌" for effect in public_state["field_effects"]))

        battle.perform_action({"type": "end_turn"})
        with self.assertRaises(ActionError):
            battle.perform_action({"type": "skill", "unit_id": bard.unit_id, "skill_code": "heal", "target_unit_id": bard.unit_id})

    def test_scenario_erasure_apostle_descent_preview_exposes_target_destinations(self) -> None:
        # Given an enemy has any erasure counter and there are legal adjacent landing cells
        battle = create_battle("erasure_apostle", "bard")
        apostle = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        apostle.position = Position(3, 4)
        bard.position = Position(5, 4)
        bard.add_status(ErasureCounterStatus("other-source"))

        # Then the player-facing action snapshot exposes both the selectable target and landing cells
        actions = {action["code"]: action for action in battle.action_snapshot_for(apostle)["actions"]}
        descent = actions["descent_moment"]
        preview = descent["preview"]

        self.assertTrue(descent["available"])
        self.assertIn(bard.unit_id, preview["target_unit_ids"])
        self.assertIn({"x": 4, "y": 4}, preview["destinations_by_target"][bard.unit_id])

    def test_scenario_ellie_experiment_kills_after_three_target_rounds_not_three_global_turns(self) -> None:
        # Given Ellie buffs a faster ally with Experiment
        battle = create_battle(["dark_human", "ellie"], ["bard"])
        dark = next(unit for unit in battle.hero_units(1) if unit.hero_code == "dark_human")
        ellie = next(unit for unit in battle.hero_units(1) if unit.hero_code == "ellie")
        bard = primary_hero(battle, 2)
        dark.position = Position(5, 4)
        ellie.position = Position(4, 4)
        bard.position = Position(7, 7)

        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})
        battle.perform_action(
            {"type": "skill", "unit_id": ellie.unit_id, "skill_code": "experiment", "target_unit_id": dark.unit_id}
        )

        # When three global turns pass after the cast
        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})

        # Then the buffed target is still alive because only one of its own rounds has ended
        self.assertTrue(dark.alive)

        # And it dies only after its third own turn ends
        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})
        self.assertTrue(dark.alive)
        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})
        self.assertFalse(dark.alive)

    def test_scenario_ellie_crystal_ball_lasts_four_own_rounds_not_four_global_turns(self) -> None:
        # Given Ellie reaches her turn in a multi-hero ring
        battle = create_battle(["dark_human", "ellie"], ["bard"])
        ellie = next(unit for unit in battle.hero_units(1) if unit.hero_code == "ellie")
        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})

        # When Ellie uses Crystal Ball
        battle.perform_action({"type": "skill", "unit_id": ellie.unit_id, "skill_code": "crystal_ball"})

        # Then two unrelated global turns do not consume the effect
        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})
        self.assertEqual(ellie.get_status("水晶球").duration, 4)

        # And it only drops when Ellie's own next round begins
        battle.perform_action({"type": "end_turn"})
        self.assertEqual(ellie.get_status("水晶球").duration, 3)

    def test_scenario_great_holy_light_damages_only_enemy_normal_movement(self) -> None:
        # Given Bard has created Great Holy Light
        battle = create_battle("bard", "dark_human")
        bard = primary_hero(battle, 1)
        dark = primary_hero(battle, 2)

        battle.perform_action({"type": "skill", "unit_id": bard.unit_id, "skill_code": "great_holy_light"})
        battle.perform_action({"type": "end_turn"})

        # When the enemy finishes a normal move inside the field
        hp_before = dark.current_hp
        battle.perform_action({"type": "move", "unit_id": dark.unit_id, "x": 5, "y": 4})

        # Then the field deals damage
        self.assertLess(dark.current_hp, hp_before)

    def test_scenario_great_holy_light_does_not_damage_skill_movement(self) -> None:
        # Given Bard has created Great Holy Light
        battle = create_battle("bard", "dark_human")
        bard = primary_hero(battle, 1)
        dark = primary_hero(battle, 2)
        bard.position = Position(4, 4)
        dark.position = Position(0, 4)

        battle.perform_action({"type": "skill", "unit_id": bard.unit_id, "skill_code": "great_holy_light"})
        battle.perform_action({"type": "end_turn"})

        # When the enemy enters the area using a movement skill instead of a normal move
        hp_before = dark.current_hp
        battle.perform_action({"type": "skill", "unit_id": dark.unit_id, "skill_code": "fly_leap", "x": 3, "y": 4})

        # Then the field does not damage that movement
        self.assertEqual(dark.current_hp, hp_before)

    def test_scenario_element_hunter_clone_can_act_but_cannot_attack_or_use_skills_and_expires(self) -> None:
        # Given Element Hunter uses Earth Walker
        battle = create_battle("element_hunter", "bard")
        hunter = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        original_position = hunter.position

        battle.perform_action({"type": "skill", "unit_id": hunter.unit_id, "skill_code": "earth_walker", "x": 2, "y": 4})

        # Then the clone keeps the old cell and can still take turn actions this turn
        clones = [unit for unit in battle.all_units() if unit.is_clone]
        self.assertEqual(len(clones), 1)
        clone = clones[0]
        self.assertEqual(hunter.position, Position(2, 4))
        self.assertEqual(clone.position, original_position)
        self.assertTrue(clone.turn_ready)
        self.assertTrue(clone.can_take_turn_actions(battle))

        # But it cannot attack or use skills
        self.assertTrue(clone.cannot_attack)
        self.assertTrue(clone.cannot_use_skills)
        self.assertEqual(clone.skills, [])
        bard.position = Position(1, 5)
        with self.assertRaises(ActionError):
            battle.perform_action({"type": "attack", "unit_id": clone.unit_id, "target_unit_id": bard.unit_id})

        # And it expires before Element Hunter's next own turn begins
        battle.perform_action({"type": "end_turn"})
        self.assertTrue(any(unit.unit_id == clone.unit_id for unit in battle.all_units()))
        battle.perform_action({"type": "end_turn"})
        self.assertFalse(any(unit.unit_id == clone.unit_id for unit in battle.all_units()))

    def test_scenario_rock_absorb_allows_stealthed_targets_to_chain_protection_per_target(self) -> None:
        # Given Rock God hits a stealthed Dark Human with Rock Absorb
        battle = create_battle("rock_god", "dark_human")
        rock = primary_hero(battle, 1)
        dark = primary_hero(battle, 2)
        rock.position = Position(3, 3)
        dark.position = Position(6, 3)
        dark.add_status(StatusEffect("隐身"))

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": rock.unit_id,
                "skill_code": "rock_absorb",
                "stat_name": "attack",
                "cells": [{"x": 2, "y": 3}],
            }
        )

        # Then the stealthed target still gets a protection reaction window
        self.assertIsNotNone(battle.pending_chain)
        options = battle.to_public_dict()["pending_chain"]["options_by_unit"].get(dark.unit_id, [])
        self.assertIn("protection", {option["action_code"] for option in options})

        # When Dark Human reacts with Protection
        battle.perform_action({"type": "chain_react", "unit_id": dark.unit_id, "action_code": "protection"})

        # Then that target's absorption is blocked without granting Rock God the extra body cell
        self.assertEqual(dark.stat("attack"), 3)
        self.assertEqual(rock.stat("attack"), 3)
        self.assertNotIn(Position(2, 3), battle.unit_cells(rock))
        self.assertEqual(dark.shields, 1)

    def test_scenario_doomlight_dragon_apocalypse_allows_n_one_above_one_hp(self) -> None:
        # Given Doomlight Dragon has slightly more than 1 hp
        battle = create_battle("doomlight_dragon", "bard")
        dragon = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        dragon.position = Position(0, 0)
        bard.position = Position(3, 3)
        dragon.current_hp = 1.25

        # When the player chooses Apocalypse with n = 1
        battle.perform_action(
            {
                "type": "skill",
                "unit_id": dragon.unit_id,
                "skill_code": "apocalypse",
                "n": 1,
                "cells": [{"x": 3, "y": 3}],
            }
        )
        resolve_pending_chain(battle)

        # Then the skill is legal, costs 1 hp, and still hits the target cell
        self.assertAlmostEqual(dragon.current_hp, 0.25)
        self.assertLess(bard.current_hp, 1.0)

    def test_scenario_masamune_redirects_threats_to_mount_and_only_gains_block_counter_after_dismount(self) -> None:
        # Given Masamune starts the battle mounted
        battle = create_battle("fire_funeral", "masamune")
        fire = primary_hero(battle, 1)
        masamune = primary_hero(battle, 2)
        mount = summon_by_code(battle, 2, "motor_horse")
        fire.position = Position(4, 4)
        mount.position = Position(5, 4)
        masamune.position = Position(5, 4)

        self.assertIs(battle.mounted_unit_for(masamune), mount)

        # When the enemy attacks Masamune while he is mounted
        battle.perform_action({"type": "attack", "unit_id": fire.unit_id, "target_unit_id": masamune.unit_id, "x": 5, "y": 4})

        # Then the mounted version does not offer block/counter and the mount takes the hit
        self.assertIsNotNone(battle.pending_chain)
        while battle.pending_chain is not None and battle.pending_chain.current_unit_id() != masamune.unit_id:
            battle.perform_action({"type": "chain_skip"})
        mounted_codes = {option.action_code for option in battle.pending_chain.options_by_unit.get(masamune.unit_id, [])}
        self.assertNotIn("block", mounted_codes)
        self.assertNotIn("counter", mounted_codes)
        battle.perform_action({"type": "chain_skip"})
        self.assertAlmostEqual(masamune.current_hp, 1.0)
        self.assertLess(mount.current_hp, 1.0)

        # When Masamune is no longer mounted
        battle = create_battle("fire_funeral", "masamune")
        fire = primary_hero(battle, 1)
        masamune = primary_hero(battle, 2)
        mount = summon_by_code(battle, 2, "motor_horse")
        fire.position = Position(4, 4)
        mount.position = Position(1, 1)
        masamune.position = Position(5, 4)
        battle.clear_mounted_state(masamune)

        battle.perform_action(
            {"type": "attack", "unit_id": fire.unit_id, "target_unit_id": masamune.unit_id, "x": 5, "y": 4}
        )

        # Then the unmounted version does offer block and counter
        while battle.pending_chain is not None and battle.pending_chain.current_unit_id() != masamune.unit_id:
            battle.perform_action({"type": "chain_skip"})
        unmounted_codes = {option.action_code for option in battle.pending_chain.options_by_unit.get(masamune.unit_id, [])}
        self.assertIn("block", unmounted_codes)
        self.assertIn("counter", unmounted_codes)

    def test_scenario_jade_stance_protects_until_jades_next_own_turn_start(self) -> None:
        # Given Jade prepares Stance around an ally
        battle = create_battle("jade", "fire_funeral")
        jade = primary_hero(battle, 1)
        ally = create_hero("bard", 1)
        enemy = primary_hero(battle, 2)
        battle.add_unit(ally, Position(4, 4))
        jade.position = Position(3, 4)
        enemy.position = Position(6, 4)

        battle.perform_action({"type": "skill", "unit_id": jade.unit_id, "skill_code": "stance"})
        battle.perform_action({"type": "end_turn"})

        # When the next enemy turn tries to damage the ally and Jade
        ally_ctx = battle.resolve_damage(
            DamageContext(source=enemy, target=ally, attack_power=5, is_skill=False, action_name="test attack")
        )
        jade_ctx = battle.resolve_damage(
            DamageContext(source=enemy, target=jade, attack_power=5, is_skill=False, action_name="test attack")
        )

        # Then Stance protects the ally, but not Jade herself
        self.assertTrue(ally_ctx.cancelled)
        self.assertAlmostEqual(ally.current_hp, 1.0)
        self.assertFalse(jade_ctx.cancelled)
        self.assertLess(jade.current_hp, 1.0)

        # And the field still exists during that enemy turn
        self.assertTrue(any(effect.name == "立场" for effect in battle.field_effects))

        # When the enemy turn ends and Jade's next own turn starts
        battle.perform_action({"type": "end_turn"})

        # Then Stance expires at Jade's next own turn start
        self.assertFalse(any(effect.name == "立场" for effect in battle.field_effects))

    def test_scenario_jade_quantum_shield_uses_three_casts_then_locks_the_next_own_cycle(self) -> None:
        # Given Jade has a threatened ally during a usable Quantum Shield cycle
        battle = create_battle("jade", "bard")
        jade = primary_hero(battle, 1)
        ally = create_hero("dark_human", 1)
        enemy = primary_hero(battle, 2)
        battle.add_unit(ally, Position(4, 4))
        jade.position = Position(4, 3)
        enemy.position = Position(6, 3)
        skill = skill_by_code(jade, "quantum_shield")
        threatened_cells = [Position(4, 3), Position(4, 4)]
        queued = battle.build_skill_effect_action(
            actor=enemy,
            display_name="test area damage",
            effect_code="area_damage",
            payload={"cells": [cell.to_dict() for cell in threatened_cells], "attack_power": 4, "tags": ["skill"]},
            target_cells=threatened_cells,
            speed=1,
        )
        reaction_payload = {"target_unit_ids": [jade.unit_id, ally.unit_id]}

        battle.perform_action({"type": "end_turn"})

        # When Jade spends all three casts in the current usable cycle
        for expected_uses in range(1, 4):
            ok, reason = skill.can_react_with_payload(battle, jade, queued, reaction_payload)
            self.assertTrue(ok, reason)
            skill.prepay_resources(battle, jade, reaction_payload)
            skill.react(battle, jade, reaction_payload, queued)
            self.assertEqual(skill.uses_this_turn, expected_uses)
            battle.expire_chain_temporary_statuses()

        # Then the next own cycle is unavailable
        ok, _ = skill.can_react_with_payload(battle, jade, queued, reaction_payload)
        self.assertFalse(ok)
        self.assertTrue(skill.cooldown_pending)

        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})
        ok, reason = skill.can_react_with_payload(battle, jade, queued, reaction_payload)
        self.assertFalse(ok)
        self.assertEqual(reason, "技能冷却中。")

        # And the following own cycle becomes usable again
        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})
        ok, reason = skill.can_react_with_payload(battle, jade, queued, reaction_payload)
        self.assertTrue(ok, reason)

    def test_scenario_public_battle_visual_events_cover_attacks_skills_and_shield_blocks(self) -> None:
        # Given Bard threatens a shielded Dark Human
        battle = create_battle("bard", "dark_human")
        bard = primary_hero(battle, 1)
        dark = primary_hero(battle, 2)
        bard.position = Position(4, 4)
        dark.position = Position(5, 4)
        dark.shields = 1

        # When Bard attacks into the shield
        battle.perform_action({"type": "attack", "unit_id": bard.unit_id, "target_unit_id": dark.unit_id})

        # Then the public state exposes both the outgoing attack and the defensive block event
        public_after_attack = battle.to_public_dict()
        attack_events = [event for event in public_after_attack["visual_events"] if event["kind"] == "attack"]
        defense_events = [event for event in public_after_attack["visual_events"] if event["kind"] == "defense"]
        self.assertTrue(attack_events)
        self.assertEqual(attack_events[-1]["actor_id"], bard.unit_id)
        self.assertIn(dark.unit_id, attack_events[-1]["target_unit_ids"])
        self.assertTrue(defense_events)
        self.assertEqual(defense_events[-1]["defense_reason"], "shield")
        self.assertIn(dark.unit_id, defense_events[-1]["target_unit_ids"])

        # And when Bard follows up with a skill cast
        battle.perform_action({"type": "skill", "unit_id": bard.unit_id, "skill_code": "great_holy_light"})

        # Then the skill cast is also present in the visual event feed
        public_after_skill = battle.to_public_dict()
        skill_events = [
            event
            for event in public_after_skill["visual_events"]
            if event["kind"] == "skill" and event["action_code"] == "great_holy_light"
        ]
        self.assertTrue(skill_events)

    def test_scenario_stealthed_enemy_visual_events_are_filtered_from_the_opponent_view(self) -> None:
        # Given Dark Human becomes stealthed on its own turn
        battle = create_battle("dark_human", "bard")
        dark = primary_hero(battle, 1)
        dark.position = Position(4, 4)
        battle.perform_action({"type": "skill", "unit_id": dark.unit_id, "skill_code": "stealth"})

        # When the opposing viewer reads the public battle state
        enemy_view = battle_state_for_viewer(battle, 2)

        # Then the stealthed unit's visual event does not leak through the event feed
        self.assertFalse(enemy_view["visual_events"])

    def test_scenario_n_instant_skill_is_exposed_to_the_waiting_player_and_room_allows_it(self) -> None:
        # Given a room where player 2 controls N during player 1's turn
        room = GameRoom("n-skill")
        _, host_token = room.create_host("p1")
        _, guest_token = room.join("p2")
        room.select_hero(host_token, "dark_human", 1)
        room.select_hero(guest_token, "n", 1)
        room.start_battle(host_token)
        battle = room.battle
        assert battle is not None
        attacker = primary_hero(battle, 1)
        caster = primary_hero(battle, 2)
        attacker.position = Position(5, 4)
        caster.position = Position(4, 4)
        caster.mana_points = 2

        # When player 2 reads the viewer state while waiting
        viewer_state = room.serialize_state(guest_token)["battle"]

        # Then that viewer still gets an interactable instant-skill unit bundle
        self.assertEqual(viewer_state["input_player"], 2)
        self.assertEqual([entry["unit_id"] for entry in viewer_state["active_units"]], [caster.unit_id])
        actions = viewer_state["active_units"][0]["actions"]["actions"]
        magnetic_wave = next(action for action in actions if action["code"] == "magnetic_wave")
        self.assertTrue(magnetic_wave["available"])
        self.assertEqual(magnetic_wave["timing"], "instant")

        # And when player 2 uses Magnetic Wave through the room permission layer
        room.perform_action(
            guest_token,
            {
                "type": "skill",
                "unit_id": caster.unit_id,
                "skill_code": "magnetic_wave",
                "cells": [
                    {"x": 4, "y": 3},
                    {"x": 4, "y": 4},
                    {"x": 4, "y": 5},
                    {"x": 5, "y": 3},
                    {"x": 5, "y": 4},
                    {"x": 5, "y": 5},
                    {"x": 6, "y": 3},
                    {"x": 6, "y": 4},
                    {"x": 6, "y": 5},
                ],
            },
        )

        # Then the current actor loses the rest of this turn
        self.assertFalse(attacker.turn_ready)
        self.assertAlmostEqual(caster.mana_points, 0.0)


@unittest.skipIf(quickjs is None, "quickjs is required for frontend behavior checks")
class FrontendBehaviorTests(unittest.TestCase):
    def test_scenario_phase2_mastery_overview_renders_next_goal_and_hero_progress(self) -> None:
        home_source = (ROOT / "static" / "home-ui.js").read_text(encoding="utf-8")
        ctx = quickjs.Context()
        ctx.eval(
            """
            function element(id) {
              return {
                id, children: [], textContent: "", className: "", value: 0, max: 0,
                append(...nodes) { this.children.push(...nodes); },
                setAttribute(name, value) { this[name] = String(value); },
                set innerHTML(_value) { this.children = []; },
              };
            }
            const elements = {
              "mastery-summary": element("mastery-summary"),
              "mastery-next-goal": element("mastery-next-goal"),
              "mastery-hero-list": element("mastery-hero-list"),
            };
            const document = {
              getElementById(id) { return elements[id] || null; },
              createElement() { return element(""); },
            };
            globalThis.document = document;
            """
        )
        ctx.eval(home_source)
        ctx.eval(
            """
            WujiangHomeUi.renderProgression({
              document,
              state: {
                progressionBusy: false,
                progressionError: "",
                progression: {
                  total_matches: 3, total_wins: 2, win_rate: 0.6667,
                  next_goal: {kind: "mastery_level", message: "再获得 1 点熟练度，火葬者即可达到熟练。"},
                  hero_progress: [{
                    hero_code: "fire_funeral", hero_name: "火葬者", matches: 3, wins: 2,
                    mastery_points: 5, mastery_level: "熟练", mastery_threshold: 3,
                    next_mastery_level: "精通", next_mastery_threshold: 8, points_to_next_level: 3,
                  }],
                },
              },
            });
            globalThis.masterySummary = elements["mastery-summary"].textContent;
            globalThis.goalText = elements["mastery-next-goal"].children[1].textContent;
            globalThis.heroCardCount = elements["mastery-hero-list"].children.length;
            globalThis.heroCardTitle = elements["mastery-hero-list"].children[0].children[0].children[0].textContent;
            """
        )
        self.assertEqual(ctx.eval("globalThis.masterySummary"), "3 场 · 2 胜 · 胜率 67%")
        self.assertIn("火葬者", ctx.eval("globalThis.goalText"))
        self.assertEqual(ctx.eval("globalThis.heroCardCount"), 1)
        self.assertEqual(ctx.eval("globalThis.heroCardTitle"), "火葬者")

    def test_scenario_p1_accessibility_feedback_and_frontend_modules_are_wired(self) -> None:
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        styles = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
        app_source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        home_source = (ROOT / "static" / "home-ui.js").read_text(encoding="utf-8")

        script_order = [
            html.index('src="/home-ui.js"'),
            html.index('src="/replay-ui.js"'),
            html.index('src="/battle-feedback.js"'),
            html.index('src="/app.js"'),
        ]
        self.assertEqual(script_order, sorted(script_order))
        for element_id in (
            "toggle-battle-sound",
            "toggle-colorblind-mode",
            "toggle-reduced-motion",
            "open-keyboard-help",
            "keyboard-help",
            "combat-feedback-feed",
            "battle-announcer",
        ):
            self.assertIn(f'id="{element_id}"', html)
        self.assertIn('aria-live="assertive"', html)
        self.assertIn("prefers-reduced-motion: reduce", styles)
        self.assertIn("body.colorblind-mode .cell.is-target", styles)
        self.assertIn(":focus-visible", styles)
        self.assertIn("min-height: 44px", styles)
        self.assertIn("handleBattleKeyboard", app_source)
        self.assertIn('event.key === "Escape"', app_source)
        self.assertIn('key === "e"', app_source)
        self.assertIn('event.key === "["', app_source)
        self.assertIn('event.key === "]"', app_source)
        self.assertIn("WujiangHomeUi?.renderRecentMatches", app_source)
        self.assertIn('id="mastery-overview"', html)
        self.assertIn('id="postgame-next-goal"', html)
        self.assertIn("renderProgression", home_source)
        self.assertIn('/api/progression/overview', app_source)
        self.assertIn("WujiangReplayUi?.renderToolbar", app_source)
        self.assertIn("WujiangBattleFeedback?.consume", app_source)

    def test_scenario_battle_feedback_announces_defense_death_chain_and_victory(self) -> None:
        feedback_source = (ROOT / "static" / "battle-feedback.js").read_text(encoding="utf-8")
        home_source = (ROOT / "static" / "home-ui.js").read_text(encoding="utf-8")
        replay_source = (ROOT / "static" / "replay-ui.js").read_text(encoding="utf-8")
        ctx = quickjs.Context()
        ctx.eval(
            """
            function classList() {
              return { values: {}, toggle(name, active) { this.values[name] = Boolean(active); } };
            }
            function element(id) {
              return {
                id,
                children: [],
                dataset: {},
                className: "",
                classList: classList(),
                textContent: "",
                setAttribute(name, value) { this[name] = String(value); },
                prepend(node) { node.parentNode = this; this.children.unshift(node); },
                get lastElementChild() { return this.children[this.children.length - 1] || null; },
                remove() {
                  if (!this.parentNode) return;
                  this.parentNode.children = this.parentNode.children.filter((child) => child !== this);
                },
              };
            }
            const elements = {
              "combat-feedback-feed": element("combat-feedback-feed"),
              "battle-announcer": element("battle-announcer"),
              "toggle-battle-sound": element("toggle-battle-sound"),
              "toggle-colorblind-mode": element("toggle-colorblind-mode"),
              "toggle-reduced-motion": element("toggle-reduced-motion"),
            };
            const document = {
              body: element("body"),
              getElementById(id) { return elements[id] || null; },
              createElement() { return element(""); },
            };
            const localStorage = {
              value: "",
              getItem() { return this.value || null; },
              setItem(_key, value) { this.value = String(value); },
            };
            function matchMedia() { return {matches: false, addEventListener() {}}; }
            globalThis.document = document;
            globalThis.localStorage = localStorage;
            globalThis.matchMedia = matchMedia;
            """
        )
        ctx.eval(home_source)
        ctx.eval(replay_source)
        ctx.eval(feedback_source)
        ctx.eval(
            """
            WujiangBattleFeedback.initialize();
            WujiangBattleFeedback.toggle("colorblind");
            WujiangBattleFeedback.toggle("motion");
            const previousBattle = {
              winner: null,
              pending_chain: null,
              units: [{id: "u1", name: "守卫", hp: 1, destroyed: false}],
            };
            const battle = {
              winner: 1,
              pending_chain: {current_unit_id: "u1"},
              units: [{id: "u1", name: "守卫", hp: 0, destroyed: true}],
            };
            WujiangBattleFeedback.consume({
              previousBattle,
              battle,
              viewerTeamId: 1,
              replayMode: false,
              events: [{kind: "defense", defense_reason: "shield", actor_id: "u1"}],
            });
            globalThis.feedbackAnnouncement = elements["battle-announcer"].textContent;
            globalThis.feedbackCount = elements["combat-feedback-feed"].children.length;
            globalThis.colorblindApplied = document.body.classList.values["colorblind-mode"];
            globalThis.reduceMotionApplied = document.body.classList.values["reduce-motion"];
            globalThis.modulesLoaded = typeof WujiangHomeUi.renderRecentMatches === "function"
              && typeof WujiangReplayUi.renderToolbar === "function";
            """
        )

        self.assertEqual(ctx.eval("globalThis.feedbackAnnouncement"), "战斗胜利")
        self.assertEqual(ctx.eval("globalThis.feedbackCount"), 3)
        self.assertTrue(ctx.eval("globalThis.colorblindApplied"))
        self.assertTrue(ctx.eval("globalThis.reduceMotionApplied"))
        self.assertTrue(ctx.eval("globalThis.modulesLoaded"))

    def test_scenario_home_entry_prioritizes_game_modes_and_hides_reference_roster(self) -> None:
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        styles = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
        app_source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('class="mode-gateway"', html)
        self.assertIn('id="quick-start-entry"', html)
        self.assertIn('id="focus-strategy-mode"', html)
        self.assertIn('id="focus-duel-mode"', html)
        self.assertIn("快速开始", html)
        self.assertIn("英灵城邦", html)
        self.assertIn("自定义对战", html)
        self.assertIn('"mode mode"', styles)
        self.assertIn("#home-hero-cards", styles)
        self.assertIn("display: none", styles)
        self.assertIn("openStrategyModeEntry", app_source)
        self.assertIn("openDuelModeEntry", app_source)
        self.assertIn("openQuickStartEntry", app_source)
        self.assertIn("renderHomeFlow", app_source)
        self.assertIn('id="toggle-full-roster"', html)
        self.assertIn('id="recommended-rosters"', html)
        self.assertIn('id="hero-search"', html)
        self.assertIn('id="hero-role-filter"', html)
        self.assertIn('id="hero-difficulty-filter"', html)
        self.assertIn('id="team-readiness"', html)
        self.assertIn("beginnerByCode", app_source)
        self.assertIn("applyRecommendedRoster", app_source)
        self.assertIn("heroMatchesFilters", app_source)
        self.assertIn("rosterExactlyMatches", app_source)
        self.assertIn("renderTeamReadiness", app_source)
        self.assertIn("估算难度", app_source)
        self.assertIn("state.room.start_blocker", app_source)
        self.assertIn("isRoomConfigControlActive", app_source)
        self.assertIn('id="toggle-ready"', html)
        self.assertIn('id="room-connection-summary"', html)
        self.assertIn('id="battle-turn-timer"', html)
        self.assertIn("toggleRoomReady", app_source)
        self.assertIn("renderConnectionAndTurnState", app_source)
        self.assertIn("turn_timer", app_source)
        self.assertIn('id="postgame-summary"', html)
        self.assertIn('id="postgame-team-stats"', html)
        self.assertIn('id="postgame-mvp"', html)
        self.assertIn('id="postgame-key-turns"', html)
        self.assertIn("renderPostgameSummary", app_source)
        self.assertIn("loadReplayStep(Number(item.replay_step_index))", app_source)
        self.assertIn('id="tutorial-guide"', html)
        self.assertIn("filterTutorialActions", app_source)
        self.assertIn("renderTutorialGuide", app_source)
        self.assertIn("completeTutorialUnitSelection", app_source)
        self.assertIn('id="resume-tutorial"', html)
        self.assertIn('id="start-quick-ai"', html)
        self.assertIn("LAST_TUTORIAL_ROOM_KEY", app_source)
        self.assertIn("refreshResumableTutorial", app_source)
        self.assertIn("resumeTutorialBattle", app_source)
        self.assertIn("继续未完成教学", app_source)
        self.assertIn("重新开始教学", app_source)
        self.assertIn("startQuickAiBattle", app_source)
        self.assertIn("同阵容再来一局", app_source)
        self.assertIn("同配置再来一局", app_source)
        self.assertIn('recordProductEvent("rematch_start"', app_source)
        self.assertIn('id="recent-matches-panel"', html)
        self.assertIn('id="refresh-recent-matches"', html)
        self.assertIn("refreshRecentMatches", app_source)
        self.assertIn("openRecentReplay", app_source)
        self.assertIn('state.historicalMatchId ? "/api/matches/replay"', app_source)
        self.assertIn(".recent-match-card", styles)
        self.assertIn('id="action-forecast"', html)
        self.assertIn("renderActionForecast", app_source)
        self.assertIn("estimatedSummaryDamage", app_source)
        self.assertIn("explainInvalidBoardChoice", app_source)
        self.assertIn("资源消耗", app_source)
        self.assertIn("预计效果", app_source)
        self.assertIn("最终站位", app_source)
        self.assertIn("影响单位", app_source)
        self.assertIn("focusAuthGateForMode(\"英灵城邦战役\")", app_source)
        self.assertIn("focusAuthGateForMode(\"武将对战房间\")", app_source)
        self.assertIn(".strategy-war-tabs", styles)
        self.assertIn(".strategy-war-state", styles)
        self.assertIn(".strategy-map-plan", styles)
        self.assertIn("isStrategyControlActive", app_source)
        self.assertIn('selectedCampaignId: state.strategyCampaign?.id || 0', app_source)
        self.assertNotIn('tagName === "TEXTAREA" || tagName === "BUTTON"', app_source)
        self.assertIn("strategyCanResume", app_source)
        self.assertIn("strategyCityOrderLimit", app_source)
        self.assertIn("filterStrategySelectOptions", app_source)
        self.assertIn("createStrategyHeroPathPanel", app_source)
        self.assertIn("createStrategyHeroAppointmentPanel", app_source)
        self.assertIn("createLordHeroDutyPanel", app_source)
        self.assertIn("createLordRitualPanel", app_source)
        self.assertIn("createLordTechnologyPanel", app_source)
        self.assertIn("createGrandGeneralMilitaryPanel", app_source)
        self.assertIn("createGeneralLogisticsPanel", app_source)
        self.assertIn("举行召唤祭祀", app_source)
        self.assertIn("增加兵力", app_source)
        self.assertIn("注册士兵", app_source)
        self.assertIn("调拨给直属将军", app_source)
        self.assertIn("请示直属大将军", app_source)
        self.assertNotIn("发布招募武将令", app_source)
        self.assertIn(".strategy-role-header.role-grand_general", styles)
        self.assertIn(".strategy-hero-duty-row", styles)
        self.assertIn("nextHomePollAt = now + 5000", app_source)
        self.assertIn("homeRenderSignature === lastHomeRenderSignature", app_source)
        self.assertIn(".strategy-map-routes-drawer", styles)
        self.assertIn("scroll-margin-top: 76px", styles)
        self.assertIn("transform: translate(-50%, -50%)", styles)

    def test_scenario_local_analytics_dashboard_has_empty_error_and_refresh_states(self) -> None:
        html = (ROOT / "static" / "analytics.html").read_text(encoding="utf-8")
        app_source = (ROOT / "static" / "analytics.js").read_text(encoding="utf-8")
        styles = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        self.assertIn('id="refresh-analytics"', html)
        self.assertIn('id="analytics-status"', html)
        self.assertIn('id="analytics-funnel"', html)
        self.assertIn('fetch("/api/analytics/funnel")', app_source)
        self.assertIn("目前还没有事件样本", app_source)
        self.assertIn("无法读取内测数据", app_source)
        self.assertIn("真实玩家样本仍需按内测清单判定有效性", app_source)
        self.assertIn(".analytics-summary", styles)

    def test_scenario_room_directory_renders_and_primary_join_button_starts_join_flow(self) -> None:
        app_source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        ctx = quickjs.Context()
        ctx.eval(
            """
            function createClassList() {
              return {
                add() {},
                remove() {},
                toggle() {},
                contains() { return false; },
              };
            }

            function createElement(tagName, id) {
              const element = {
                tagName: String(tagName || "div").toUpperCase(),
                id: id || "",
                children: [],
                listeners: {},
                className: "",
                value: "",
                disabled: false,
                dataset: {},
                style: {},
                _textContent: "",
                _innerHTML: "",
                classList: createClassList(),
                append(...nodes) {
                  this.children.push(...nodes);
                },
                appendChild(node) {
                  this.children.push(node);
                  return node;
                },
                addEventListener(type, handler) {
                  if (!this.listeners[type]) this.listeners[type] = [];
                  this.listeners[type].push(handler);
                },
                querySelector() {
                  return null;
                },
                querySelectorAll() {
                  return [];
                },
                closest(selector) {
                  if (!selector) return null;
                  if (selector === ".cell") {
                    let node = this;
                    while (node) {
                      if (String(node.className || "").split(" ").includes("cell")) return node;
                      node = node.parentNode;
                    }
                  }
                  if (selector === "input, select, textarea, label, .board-alert" || selector === "button") {
                    return null;
                  }
                  return null;
                },
                contains(node) {
                  if (node === this) return true;
                  return this.children.some((child) => child === node || (child.contains && child.contains(node)));
                },
                replaceWith() {},
                focus() {},
                set innerHTML(value) {
                  this._innerHTML = String(value);
                },
                get innerHTML() {
                  return this._innerHTML;
                },
                set textContent(value) {
                  this._textContent = String(value);
                },
                get textContent() {
                  return this._textContent;
                },
              };
              return element;
            }

            const document = {
              elements: {},
              listeners: {},
              body: createElement("body", "body"),
              getElementById(id) {
                if (!this.elements[id]) this.elements[id] = createElement("div", id);
                return this.elements[id];
              },
              createElement(tagName) {
                return createElement(tagName);
              },
              querySelector() {
                return null;
              },
              querySelectorAll() {
                return [];
              },
              addEventListener(type, handler) {
                if (!this.listeners[type]) this.listeners[type] = [];
                this.listeners[type].push(handler);
              },
            };
            document.body.classList = createClassList();

            const storageFactory = () => ({
              _store: {},
              getItem(key) {
                return Object.prototype.hasOwnProperty.call(this._store, key) ? this._store[key] : null;
              },
              setItem(key, value) {
                this._store[key] = String(value);
              },
              removeItem(key) {
                delete this._store[key];
              },
            });

            const window = {
              location: { href: "http://example.test/", search: "", hash: "" },
              listeners: {},
              addEventListener(type, handler) {
                if (!this.listeners[type]) this.listeners[type] = [];
                this.listeners[type].push(handler);
              },
              requestAnimationFrame(callback) { return callback(); },
              cancelAnimationFrame() {},
              setInterval() { return 1; },
              clearInterval() {},
            };

            const history = { replaceState() {} };
            const Element = Object;
            const localStorage = storageFactory();
            const sessionStorage = storageFactory();
            """
        )
        ctx.eval(app_source)
        ctx.eval(
            """
            canReclaimSeatByName = function () { return false; };
            loadStoredIdentity = function () { return { token: "", name: "" }; };
            joinListedRoom = function (roomId) { globalThis.joinRoomCalledWith = roomId; };
            globalThis.joinRoomCalledWith = "";
            state.profileReady = true;
            state.rooms = [
              {
                room_id: "AB12CD",
                can_join: true,
                status: "lobby",
                occupied_seat_count: 1,
                seat_count: 2,
                mode: "classic",
                mode_name: "标准选将",
                is_full: false,
                seats: [
                  { player_id: 1, occupied: true, name: "Alice", hero_total_count: 1, hero_summary: "吟游诗人 × 1" },
                  { player_id: 2, occupied: false, name: "", hero_total_count: 0, hero_summary: null },
                ],
              },
            ];
            renderRoomListActive();
            """
        )

        self.assertEqual(ctx.eval("document.elements['room-list'].children.length"), 1)

        ctx.eval("document.elements['room-list'].children[0].children[0].children[0].listeners.click[0]();")
        self.assertEqual(ctx.eval("globalThis.joinRoomCalledWith"), "AB12CD")

    def test_scenario_strategy_panel_renders_campaign_city_policy_and_tactic_tech(self) -> None:
        app_source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        ctx = quickjs.Context()
        ctx.eval(
            """
            function createClassList() {
              return { add() {}, remove() {}, toggle() {}, contains() { return false; } };
            }

            function createElement(tagName, id) {
              const element = {
                tagName: String(tagName || "div").toUpperCase(),
                id: id || "",
                children: [],
                listeners: {},
                className: "",
                value: "",
                disabled: false,
                selected: false,
                type: "",
                dataset: {},
                style: {},
                _textContent: "",
                _innerHTML: "",
                classList: createClassList(),
                append(...nodes) { this.children.push(...nodes); },
                appendChild(node) { this.children.push(node); return node; },
                prepend(...nodes) { this.children.unshift(...nodes); },
                addEventListener(type, handler) {
                  if (!this.listeners[type]) this.listeners[type] = [];
                  this.listeners[type].push(handler);
                },
                querySelector() { return null; },
                querySelectorAll() { return []; },
                contains(node) { return node === this || this.children.some((child) => child.contains && child.contains(node)); },
                replaceWith() {},
                focus() {},
                setAttribute(name, value) { this[name] = String(value); },
                removeAttribute(name) { delete this[name]; },
                set innerHTML(value) {
                  this._innerHTML = String(value);
                  if (value === "") this.children = [];
                },
                get innerHTML() { return this._innerHTML; },
                set textContent(value) { this._textContent = String(value); },
                get textContent() { return this._textContent; },
              };
              return element;
            }

            const document = {
              elements: {},
              listeners: {},
              body: createElement("body", "body"),
              getElementById(id) {
                if (!this.elements[id]) this.elements[id] = createElement("div", id);
                return this.elements[id];
              },
              createElement(tagName) { return createElement(tagName); },
              querySelector() { return null; },
              querySelectorAll() { return []; },
              addEventListener(type, handler) {
                if (!this.listeners[type]) this.listeners[type] = [];
                this.listeners[type].push(handler);
              },
            };
            document.body.classList = createClassList();

            const storageFactory = () => ({
              _store: {},
              getItem(key) { return Object.prototype.hasOwnProperty.call(this._store, key) ? this._store[key] : null; },
              setItem(key, value) { this._store[key] = String(value); },
              removeItem(key) { delete this._store[key]; },
            });
            const window = {
              location: { href: "http://example.test/", search: "", hash: "" },
              listeners: {},
              addEventListener(type, handler) {
                if (!this.listeners[type]) this.listeners[type] = [];
                this.listeners[type].push(handler);
              },
              requestAnimationFrame(callback) { return callback(); },
              cancelAnimationFrame() {},
              setInterval() { return 1; },
              clearInterval() {},
            };
            function URL(href) {
              this.href = String(href || "");
              this.hash = "";
              this.searchParams = {
                values: {},
                set(key, value) { this.values[key] = String(value); },
                delete(key) { delete this.values[key]; },
              };
            }
            const history = { replaceState() {} };
            const Element = Object;
            const localStorage = storageFactory();
            const sessionStorage = storageFactory();
            """
        )
        ctx.eval(app_source)
        ctx.eval(
            """
            globalThis.rotatedCampaignId = 0;
            rotateStrategyJoinCode = function (campaignId) { globalThis.rotatedCampaignId = campaignId; };
            setStrategyDefenseHero = function (heroCode) { globalThis.defenseHeroCode = heroCode; };
            setStrategyBattleDefenseHero = function (battleId, heroCode) {
              globalThis.battleDefensePayload = JSON.stringify({ battleId, heroCode });
            };
            state.authUser = { id: 1, username: "Alice" };
            state.strategyCampaigns = [
              {
                id: 7,
                name: "英灵城邦",
                join_code: "ABC234",
                owner_user_id: 1,
                status: "active",
                members: [
                  { user_id: 1, username: "Alice", role: "host", faction_id: "faction_1", is_initial_player: true },
                  { user_id: -2, username: "第二势力 AI", role: "ai", faction_id: "faction_2", is_initial_player: true },
                ],
                queued_actions: [
                  {
                    id: 1,
                    user_id: 1,
                    username: "Alice",
                    faction_id: "faction_1",
                    month: 2,
                    action_type: "set_city_policy",
                    action_key: "city_1",
                    payload: { city_id: "city_1", policy: "征兵优先" },
                    status: "pending",
                  },
                ],
                command_points_by_faction: {
                  faction_1: { maximum: 4, used: 1, remaining: 3 },
                  faction_2: { maximum: 4, used: 0, remaining: 4 },
                },
                resume: { can_resume: true, online_initial_user_ids: [1], missing_initial_user_ids: [], initial_user_ids: [1], campaign_status: "active" },
                world: {
                  current_month: 2,
                  story_events: [
                    {
                      id: "story_2_faction_1_guild",
                      template_id: "guild_dispute",
                      faction_id: "faction_1",
                      city_id: "city_1",
                      opened_month: 2,
                      status: "pending",
                      title: "行会争端",
                      category: "city",
                      description: "商人与工匠都要求政厅公开表态。",
                      choices: [
                        { id: "favor_merchants", label: "支持商人", preview: "金钱 +100，支持度下降。", enabled: true, disabled_reason: "", command_cost: 1 },
                        { id: "favor_workers", label: "支持工匠", preview: "消耗金钱，提高支持度。", enabled: true, disabled_reason: "", command_cost: 1 },
                        { id: "mediate_guilds", label: "出资调停", preview: "消耗势力金钱，支持度 +3。", enabled: true, disabled_reason: "", command_cost: 1 },
                      ],
                    },
                  ],
                  scheduled_consequences: [
                    { id: "thread_1", faction_id: "faction_1", city_id: "city_1", due_month: 3, description: "旧商路的粮队即将抵达。", status: "pending" },
                  ],
                  monthly_briefings: {
                    faction_1: {
                      month: 2,
                      faction_id: "faction_1",
                      entries: [
                        { kind: "threat", title: "晨星城叛军集结", detail: "叛军规模 180，若继续放任将损耗守军与民心。", city_id: "city_1", severity: "critical" },
                        { kind: "opportunity", title: "雾港城防线薄弱", detail: "从晨星城出征有兵力优势。", city_id: "city_1", severity: "positive" },
                        { kind: "rival_intent", title: "斥候推测：第二势力准备进攻", detail: "情报并非完全可靠。", city_id: "city_1", severity: "warning" },
                      ],
                    },
                  },
                  monthly_cycle: {
                    faction_1: {
                      previous_month: {
                        from_month: 1,
                        month: 2,
                        city_changes: [
                          { city_name: "晨星城", owner_before: "faction_1", owner_after: "faction_1", owner_changed: false, resource_delta: { food: 120, money: 70, ether: 12, troops: 30 }, support_delta: 2 },
                        ],
                        important_events: [{ message: "晨星城平稳度过上月。" }],
                      },
                      must_handle: ["晨星城叛乱风险 80（正式叛乱）。", "待决事件：行会争端；月末未处理将自动放任。"],
                      advance_forecast: {
                        target_month: 3,
                        cities: [
                          { city_name: "晨星城", policy: "征兵优先", food_upkeep: 17, resource_delta: { food: 95, money: 70, ether: 12, troops: 73 }, support_delta: -2, rebellion_risk: 80, rebellion_stage: "正式叛乱" },
                        ],
                        disclaimer: "经济、维护与叛乱按当前已知状态确定性预测；战争、事件结果和 AI 决策不在预测内。",
                      },
                      planned_actions: [
                        { action_type: "set_city_policy", payload: { city_id: "city_1", policy: "征兵优先" }, affected_months: [2, 3] },
                      ],
                    },
                  },
                  campaign_tutorial: {
                    faction_1: {
                      id: "first_three_months_v1",
                      enabled: true,
                      skipped: false,
                      completed: false,
                      completed_count: 1,
                      total_count: 5,
                      current_month: 2,
                      guide_period_ended: false,
                      skip_explanation: "跳过只会隐藏前三个月的情境目标；不会获得或失去资源，不会替你下令，也不会跳过战略月份。",
                      steps: [
                        { id: "survey_border", month: 1, chapter: "第一月 · 读局与治理", title: "查看边境", detail: "确认相邻城邦。", action_kind: "map", completed: false, timing: "overdue" },
                        { id: "set_policy", month: 1, chapter: "第一月 · 读局与治理", title: "设置城市方针", detail: "提交城市方针。", action_kind: "city_command", completed: true, timing: "completed" },
                        { id: "resolve_event", month: 1, chapter: "第一月 · 读局与治理", title: "处理待决事件", detail: "主动选择事件。", action_kind: "story", completed: false, timing: "overdue" },
                        { id: "ritual_or_appoint", month: 2, chapter: "第二月 · 建立执行力量", title: "祭祀或任命", detail: "举行祭祀或任命武将。", action_kind: "organization", completed: false, timing: "active" },
                        { id: "prepare_conflict", month: 3, chapter: "第三月 · 准备冲突", title: "准备一次边境冲突", detail: "按官职完成军事准备。", action_kind: "conflict", completed: false, timing: "upcoming" },
                      ],
                    },
                  },
                  office_coordination: {
                    faction_1: {
                      high_consequence_decisions: [
                        { kind: "story", title: "决定待决事件", detail: "月底未处理将自动采用放任结果。", city_id: "city_1", planned: false },
                        { kind: "threat", title: "晨星城边境承压", detail: "决定是否调整防务。", city_id: "city_1", planned: true },
                      ],
                      routine_maintenance: [
                        { city_id: "city_1", city_name: "晨星城", policy: "稳定优先", executor_office_id: "office:faction_1:governor:city_1", mode: "ai_emergency" },
                      ],
                      order_feedback: [
                        { status: "completed", issuer_office_id: "office:faction_1:lord", executor_office_id: "office:faction_1:governor:city_1", objective: "处理粮食危机", command_cost: 1, expected_completion_month: 2, result_summary: "晨星城已由城主设为粮食优先。" },
                      ],
                      automation_rule: "默认方针持续生效；AI 官职只在缺粮或叛乱风险下自动调整一座城，并且只能使用玩家计划后剩余的军令。",
                    },
                  },
                  policy_choices: ["稳定优先", "征兵优先", "金钱优先"],
                  rebellion_action_choices: [
                    { id: "appease", name: "安抚民心", description: "提升支持度", requires_target_city: true },
                    { id: "suppress", name: "派兵镇压", description: "消耗兵力镇压", requires_target_city: true },
                  ],
                  building_projects: [
                    { id: "academy", name: "学院", money: 80, food: 30 },
                    { id: "fields", name: "田地", money: 60, food: 20 },
                    { id: "barracks", name: "兵营", money: 90, food: 40 },
                    { id: "ritual_site", name: "祭祀场", money: 100, food: 30 },
                  ],
                  registered_unit_types: [
                    { id: "infantry", name: "步兵", troop_cost: 100 },
                    { id: "archer", name: "弓兵", troop_cost: 140 },
                    { id: "cavalry", name: "骑兵", troop_cost: 180 },
                  ],
                  nodes: [
                    { id: "node_1", name: "晨星城", type: "city", x: 0, y: 0, connected_node_ids: ["node_2"] },
                    { id: "node_2", name: "雾港城", type: "city", x: 1, y: 0, connected_node_ids: ["node_1"] },
                  ],
                  factions: [
                    {
                      id: "faction_1",
                      name: "第一势力",
                      resources: { food: 300, money: 250, population: 0, ether: 50, troops: 200 },
                      tactic_tech_tree: [
                        { id: "local_militia", name: "乡勇编练", branch: "兵种", description: "提高特色士兵比例", money_cost: 80, ether_cost: 0, unit_unlocks: [], unlocked: false, available: true },
                      ],
                      hero_ritual_capacity: { maximum: 4, used: 3, remaining: 1 },
                      strategic_heroes: [
                        { code: "ellie", name: "艾莉", role: "支援", attribute: "光", race: "人类", level: 1, faction_id: null, city_id: "city_1", status: "roaming" },
                        { code: "li", name: "李", role: "勇者", attribute: "土", race: "人类", level: 9, faction_id: "faction_1", city_id: "city_1", ritual_city_id: "city_1", status: "serving" },
                      ],
                    },
                    {
                      id: "faction_2",
                      name: "第二势力",
                      resources: { food: 260, money: 220, population: 0, ether: 30, troops: 180 },
                      tactic_tech_tree: [],
                      strategic_heroes: [
                        { code: "fire_funeral", name: "火葬", role: "法师", attribute: "火", race: "人类", level: 2, faction_id: null, city_id: "city_2", status: "roaming" },
                      ],
                    },
                  ],
                  strategic_hero_pool: [
                    { code: "ellie", name: "艾莉", role: "支援", attribute: "光", race: "人类", level: 1, faction_id: null, city_id: "city_1", status: "roaming" },
                    { code: "li", name: "李", role: "勇者", attribute: "土", race: "人类", level: 9, faction_id: "faction_1", city_id: "city_1", ritual_city_id: "city_1", status: "serving" },
                    { code: "fire_funeral", name: "火葬", role: "法师", attribute: "火", race: "人类", level: 2, faction_id: null, city_id: "city_2", status: "roaming" },
                  ],
                  cities: [
                    {
                      id: "city_1",
                      node_id: "node_1",
                      name: "晨星城",
                      owner_faction_id: "faction_1",
                      policy: "稳定优先",
                      resources: { food: 900, money: 700, population: 1200, ether: 40, troops: 300 },
                      building_levels: { fields: 1, barracks: 1, ritual_site: 1 },
                      building_limits: { fields: 1, barracks: 1, ritual_site: 1, academy: 1, stables: 1, archery_range: 1 },
                      registered_units: { infantry: 2, archer: 0, cavalry: 0 },
                      defense: 4,
                      event_states: ["rebellion_risk:80:正式叛乱", "rebellion_force:160:month:2"],
                      troop_conversion: [
                        { unit_type: "守备兵", ratio: 10, troops: 30 },
                        { unit_type: "普通步兵", ratio: 90, troops: 270 },
                      ],
                    },
                    {
                      id: "city_2",
                      node_id: "node_2",
                      name: "雾港城",
                      owner_faction_id: "faction_2",
                      policy: "征兵优先",
                      resources: { food: 760, money: 520, population: 900, ether: 35, troops: 220 },
                      defense: 3,
                      troop_conversion: [
                        { unit_type: "弓兵", ratio: 20, troops: 44 },
                        { unit_type: "普通步兵", ratio: 80, troops: 176 },
                      ],
                    },
                  ],
                  pending_battles: [
                    {
                      battle_id: "battle_2_1",
                      source_city_id: "city_1",
                      target_city_id: "city_2",
                      attacker_faction_id: "faction_1",
                      defender_faction_id: "faction_2",
                      declared_month: 2,
                      resolution_mode: "manual",
                      status: "pending",
                      battle_room_id: "AB12CD",
                      battle_room_invite_path: "/?room=AB12CD",
                    },
                    {
                      battle_id: "battle_2_2",
                      source_city_id: "city_2",
                      target_city_id: "city_1",
                      attacker_faction_id: "faction_2",
                      defender_faction_id: "faction_1",
                      declared_month: 2,
                      resolution_mode: "manual",
                      status: "resolved",
                      battle_room_id: "CD34EF",
                      battle_room_invite_path: "/?room=CD34EF",
                      battle_result: {
                        winner_faction_id: "faction_1",
                        loser_faction_id: "faction_2",
                        winner_side: "defender",
                        loser_side: "attacker",
                        city_captured: false,
                        resolution_source: "real_grid",
                        lost_troops_by_side: { attacker: 400, defender: 60 },
                        remaining_troops_by_side: { attacker: 400, defender: 240 },
                        initial_grid_units_by_side: { attacker: 8, defender: 3 },
                        surviving_grid_units_by_side: { attacker: 4, defender: 2 },
                        strategic_heroes_by_side: {
                          attacker: { committed: ["ellie"], surviving: [], sleeping: ["ellie"] },
                          defender: { committed: ["fire_funeral"], surviving: ["fire_funeral"], sleeping: [] },
                        },
                        battle_log_summary: "Real grid room CD34EF finished; winning side: defender.",
                      },
                    },
                    {
                      id: "battle_2_defense",
                      source_city_id: "city_2",
                      target_city_id: "city_1",
                      attacker_faction_id: "faction_2",
                      defender_faction_id: "faction_1",
                      declared_month: 2,
                      resolution_mode: "manual",
                      status: "pending",
                      defender_hero_codes: [],
                    },
                  ],
                  strategic_status: {
                    city_counts_by_faction: { faction_1: 2, faction_2: 0 },
                    active_faction_ids: ["faction_1"],
                    exiled_faction_ids: ["faction_2"],
                    active_factions: [{ id: "faction_1", name: "第一势力", city_count: 2 }],
                    exiled_factions: [{ id: "faction_2", name: "第二势力", city_count: 0 }],
                    victory_conditions: [
                      {
                        id: "unify_cities",
                        name: "统一城邦",
                        description: "同一势力控制地图上的全部城市。",
                        implemented: true,
                        achieved: true,
                        winner_faction_id: "faction_1",
                      },
                      {
                        id: "world_mainline",
                        name: "世界主线",
                        description: "完成世界主线目标。",
                        implemented: false,
                        achieved: false,
                        winner_faction_id: null,
                      },
                    ],
                    achieved_conditions: [
                      {
                        id: "unify_cities",
                        name: "统一城邦",
                        description: "同一势力控制地图上的全部城市。",
                        implemented: true,
                        achieved: true,
                        winner_faction_id: "faction_1",
                      },
                    ],
                    campaign_complete: true,
                    winner_faction_ids: ["faction_1"],
                  },
                  event_log: [{ month: 2, message: "晨星城执行稳定优先。" }],
                },
              },
            ];
            state.strategyCampaign = state.strategyCampaigns[0];
            state.strategyBattleRoom = {
              room_id: "AB12CD",
              attacker_roster_manifest: [
                { unit_type: "守备兵", source: "city_feature", grid_units: 1 },
                { unit_type: "普通步兵", source: "default", grid_units: 2 },
              ],
              defender_roster_manifest: [
                { unit_type: "弓兵", source: "city_feature", grid_units: 1 },
                { unit_type: "普通步兵", source: "default", grid_units: 3 },
              ],
            };
            renderStrategyPanel();

            function collectText(node) {
              if (!node) return "";
              let text = node.textContent || "";
              for (const child of node.children || []) text += " " + collectText(child);
              return text;
            }
            function findFirstTag(node, tagName) {
              if (!node) return null;
              if (node.tagName === tagName) return node;
              for (const child of node.children || []) {
                const found = findFirstTag(child, tagName);
                if (found) return found;
              }
              return null;
            }
            function findButtonByText(node, text) {
              if (!node) return null;
              if (node.tagName === "BUTTON" && node.textContent === text) return node;
              for (const child of node.children || []) {
                const found = findButtonByText(child, text);
                if (found) return found;
              }
              return null;
            }
            function findButtonContainingText(node, text) {
              if (!node) return null;
              if (node.tagName === "BUTTON" && collectText(node).includes(text)) return node;
              for (const child of node.children || []) {
                const found = findButtonContainingText(child, text);
                if (found) return found;
              }
              return null;
            }
            function findSelectWithOption(node, value) {
              if (!node) return null;
              if (node.tagName === "SELECT" && (node.children || []).some((child) => child.value === value)) return node;
              for (const child of node.children || []) {
                const found = findSelectWithOption(child, value);
                if (found) return found;
              }
              return null;
            }
            function findMapCityButton(node, cityId) {
              if (!node) return null;
              if (node.tagName === "BUTTON" && node.dataset && node.dataset.cityId === cityId) return node;
              for (const child of node.children || []) {
                const found = findMapCityButton(child, cityId);
                if (found) return found;
              }
              return null;
            }
            const rotateButton = findButtonByText(document.elements["strategy-current"], "重新生成加入码");
            function findByClass(node, className) {
              if (!node) return null;
              const classes = String(node.className || "").split(/\\s+/);
              if (classes.includes(className)) return node;
              for (const child of node.children || []) {
                const found = findByClass(child, className);
                if (found) return found;
              }
              return null;
            }
            function findSelectNearButton(node, buttonText, optionValue) {
              if (!node) return null;
              if ((node.children || []).some((child) => child.tagName === "BUTTON" && child.textContent === buttonText)) {
                return findSelectWithOption(node, optionValue);
              }
              for (const child of node.children || []) {
                const found = findSelectNearButton(child, buttonText, optionValue);
                if (found) return found;
              }
              return null;
            }
            globalThis.hasRotateJoinCodeButton = Boolean(rotateButton);
            if (rotateButton) rotateButton.listeners.click[0]();
            queueStrategyAction = function (actionType, payload) {
              globalThis.queuedStrategyActionType = actionType;
              globalThis.queuedStrategyActionPayload = JSON.stringify(payload);
              if (actionType === "declare_attack") globalThis.queuedAttackPayload = JSON.stringify(payload);
              if (actionType === "rebellion_action") globalThis.queuedRebellionPayload = JSON.stringify(payload);
              if (actionType === "rebellion_battle") globalThis.queuedRebellionBattlePayload = JSON.stringify(payload);
              if (actionType === "resolve_story_event") globalThis.queuedStoryPayload = JSON.stringify(payload);
            };
            globalThis.guideActions = [];
            updateStrategyCampaignGuide = async function (action) {
              globalThis.guideActions.push(action);
              return true;
            };
            const guideBorderButton = findButtonByText(document.elements["strategy-current"], "查看边境");
            globalThis.hasGuideBorderButton = Boolean(guideBorderButton);
            if (guideBorderButton) guideBorderButton.listeners.click[0]();
            const guideSkipButton = findButtonByText(document.elements["strategy-current"], "跳过情境引导");
            globalThis.hasGuideSkipButton = Boolean(guideSkipButton);
            if (guideSkipButton) guideSkipButton.listeners.click[0]();
            const storyChoiceButton = findButtonContainingText(document.elements["strategy-current"], "出资调停");
            globalThis.hasStoryChoiceButton = Boolean(storyChoiceButton);
            if (storyChoiceButton) storyChoiceButton.listeners.click[0]();
            const rebellionSelect = findSelectWithOption(document.elements["strategy-current"], "suppress");
            globalThis.hasRebellionSelect = Boolean(rebellionSelect);
            if (rebellionSelect) rebellionSelect.value = "suppress";
            const rebellionButton = findButtonByText(document.elements["strategy-current"], "计划处理 · 1 军令");
            globalThis.hasRebellionButton = Boolean(rebellionButton);
            if (rebellionButton) rebellionButton.listeners.click[0]();
            const rebellionBattleButton = findButtonByText(document.elements["strategy-current"], "计划清剿 · 2 军令");
            globalThis.hasRebellionBattleButton = Boolean(rebellionBattleButton);
            if (rebellionBattleButton) rebellionBattleButton.listeners.click[0]();
            const heroDeploySelect = findSelectWithOption(document.elements["strategy-current"], "li");
            globalThis.hasHeroDeploySelect = Boolean(heroDeploySelect);
            if (heroDeploySelect) heroDeploySelect.value = "li";
            const planAttackButton = findButtonByText(document.elements["strategy-current"], "计划进攻 · 2 军令");
            globalThis.hasPlanAttackButton = Boolean(planAttackButton);
            if (planAttackButton) planAttackButton.listeners.click[0]();
            const defenseButton = findButtonByText(document.elements["strategy-current"], "设为防守");
            globalThis.hasDefenseHeroButton = Boolean(defenseButton);
            if (defenseButton) defenseButton.listeners.click[0]();
            const battleDefenseButton = findButtonByText(document.elements["strategy-current"], "设置本场防守");
            globalThis.hasBattleDefenseButton = Boolean(battleDefenseButton);
            const battleDefenseSelect = findSelectNearButton(document.elements["strategy-current"], "设置本场防守", "li");
            globalThis.hasBattleDefenseSelect = Boolean(battleDefenseSelect);
            if (battleDefenseSelect) battleDefenseSelect.value = "li";
            if (battleDefenseButton) battleDefenseButton.listeners.click[0]();
            const ritualButton = findButtonByText(document.elements["strategy-current"], "举行祭祀 · 30 以太 · 1 军令");
            globalThis.hasCityRitualButton = Boolean(ritualButton);
            if (ritualButton) {
              ritualButton.listeners.click[0]();
              globalThis.cityRitualPayload = globalThis.queuedStrategyActionPayload;
            }
            const summonButton = findButtonByText(document.elements["strategy-current"], "加入召唤计划 · 1 军令");
            globalThis.hasSummonHeroButton = Boolean(summonButton);
            if (summonButton) summonButton.listeners.click[0]();
            globalThis.hasGuideLocateButton = Boolean(findButtonByText(document.elements["strategy-current"], "定位 晨星城"));
            globalThis.hasStrategyWarTabs = Boolean(findByClass(document.elements["strategy-current"], "strategy-war-tabs"));
            globalThis.hasStrategyMapPlan = Boolean(findByClass(document.elements["strategy-current"], "strategy-map-plan"));
            globalThis.hasStrategyCommandPlan = Boolean(findByClass(document.elements["strategy-current"], "strategy-command-plan"));
            globalThis.hasStrategyMapStage = Boolean(findByClass(document.elements["strategy-current"], "strategy-map-stage"));
            globalThis.hasStrategyCityCommandCard = Boolean(findByClass(document.elements["strategy-current"], "strategy-city-command-card"));
            globalThis.hasStrategyDossier = Boolean(findByClass(document.elements["strategy-current"], "strategy-dossier"));
            globalThis.hasStrategyRouteDrawer = Boolean(findByClass(document.elements["strategy-current"], "strategy-map-routes-drawer"));
            const routeDrawer = findByClass(document.elements["strategy-current"], "strategy-map-routes-drawer");
            globalThis.routeDrawerInitiallyOpen = Boolean(routeDrawer && routeDrawer.open);
            if (routeDrawer && routeDrawer.children[0]?.listeners?.click?.length) {
              routeDrawer.children[0].listeners.click[0]({ preventDefault() { globalThis.routePrevented = true; } });
            }
            globalThis.routeDrawerOpenAfterClick = Boolean(routeDrawer && routeDrawer.open);
            globalThis.routeDrawerStateAfterClick = state.strategyRouteIntelOpen;
            const dossier = findByClass(document.elements["strategy-current"], "strategy-dossier");
            globalThis.dossierInitiallyOpen = Boolean(dossier && dossier.open);
            if (dossier && dossier.children[0]?.listeners?.click?.length) {
              dossier.children[0].listeners.click[0]({ preventDefault() { globalThis.dossierPrevented = true; } });
            }
            globalThis.dossierOpenAfterClick = Boolean(dossier && dossier.open);
            globalThis.dossierStateAfterClick = state.strategyDossierOpen;
            const dossierTabs = findByClass(dossier, "strategy-dossier-tabs");
            globalThis.hasDossierTabs = Boolean(dossierTabs);
            const dossierTechTab = findButtonByText(dossier, "科技");
            globalThis.hasDossierTechTab = Boolean(dossierTechTab);
            if (dossierTechTab) dossierTechTab.listeners.click[0]();
            globalThis.dossierTabAfterTechClick = state.strategyDossierTab;
            globalThis.strategyText = collectText(document.elements["strategy-current"]);
            globalThis.advanceDisabled = document.elements["strategy-advance-month"].disabled;
            globalThis.firstSelectDisabled = findFirstTag(document.elements["strategy-current"], "SELECT").disabled;
            const fogMapButton = findMapCityButton(document.elements["strategy-current"], "city_2");
            globalThis.hasFogMapButton = Boolean(fogMapButton);
            if (fogMapButton) fogMapButton.listeners.click[0]();
            globalThis.selectedCityAfterMapClick = state.strategySelectedCityId;
            globalThis.strategyTextAfterMapClick = collectText(document.elements["strategy-current"]);
            globalThis.strategyCampaignCount = document.elements["strategy-campaign-list"].children.length;
            const originalStrategicStatus = state.strategyCampaign.world.strategic_status;
            state.strategyCampaign.world.strategic_status = {
              ...originalStrategicStatus,
              campaign_contract: {
                id: "city_states_twelve_months_v1",
                name: "十二月城邦争衡",
                city_count: 8,
                major_faction_count: 2,
                neutral_city_state_count: 6,
                month_limit: 12,
                expected_duration_minutes: [60, 90],
              },
              month_limit: 12,
              months_remaining: 0,
              campaign_state: "settled",
              awaiting_conclusion_choice: true,
              can_advance_month: false,
              conclusion: {
                state: "settled",
                reason: "time_limit",
                result_label: "十二月评议",
                concluded_month: 12,
                winner_faction_ids: ["faction_1"],
                rankings: [
                  { rank: 1, faction_id: "faction_1", faction_name: "第一势力", total_score: 400, city_score: 200, support_score: 75, survival_score: 100, battle_score: 25, mainline_score: 0 },
                  { rank: 2, faction_id: "faction_2", faction_name: "第二势力", total_score: 25, city_score: 0, support_score: 0, survival_score: 25, battle_score: 0, mainline_score: 0 },
                ],
              },
            };
            continueStrategySandbox = function () { globalThis.continueSandboxClicked = true; };
            renderStrategyPanel();
            globalThis.settledStrategyText = collectText(document.elements["strategy-current"]);
            globalThis.settledAdvanceDisabled = document.elements["strategy-advance-month"].disabled;
            const continueSandboxButton = findButtonByText(document.elements["strategy-current"], "保留结算并继续沙盒");
            globalThis.hasContinueSandboxButton = Boolean(continueSandboxButton);
            if (continueSandboxButton) continueSandboxButton.listeners.click[0]();
            state.strategyCampaign.world.strategic_status = originalStrategicStatus;
            renderStrategyPanel();
            const originalStatus = state.strategyCampaign.status;
            const originalResume = state.strategyCampaign.resume;
            lockStrategyCampaign = function (campaignId) { globalThis.warStateLockCampaignId = campaignId; };
            state.strategyCampaign.status = "lobby";
            state.strategyCampaign.resume = { can_resume: false, online_initial_user_ids: [1], missing_initial_user_ids: [], initial_user_ids: [1], campaign_status: "lobby" };
            renderStrategyPanel();
            globalThis.hasStrategyWarState = Boolean(findByClass(document.elements["strategy-current"], "strategy-war-state"));
            const warStateLockButton = findButtonByText(document.elements["strategy-current"], "锁定并启用 AI");
            globalThis.hasWarStateLockButton = Boolean(warStateLockButton);
            if (warStateLockButton) warStateLockButton.listeners.click[0]();
            state.strategyCampaign.status = originalStatus;
            state.strategyCampaign.resume = originalResume;
            renderStrategyPanel();
            globalThis.canResumeWhenNoInitialPlayersMissing = strategyCanResume({
              status: "active",
              resume: { can_resume: false, missing_initial_user_ids: [], initial_user_ids: [1], campaign_status: "active" },
            });
            globalThis.cannotResumeLobbyWithoutMissing = strategyCanResume({
              status: "lobby",
              resume: { can_resume: false, missing_initial_user_ids: [], initial_user_ids: [1], campaign_status: "lobby" },
            });
            document.activeElement = {
              tagName: "SELECT",
              closest(selector) { return selector === "#strategy-panel" ? {} : null; },
            };
            globalThis.strategySelectIsActive = isStrategyControlActive();
            document.activeElement = {
              tagName: "BUTTON",
              closest(selector) { return selector === "#strategy-panel" ? {} : null; },
            };
            globalThis.strategyButtonIsActive = isStrategyControlActive();
            const filterSelect = createElement("select");
            const filterA = createElement("option");
            filterA.value = "bard";
            filterA.textContent = "吟游诗人";
            const filterB = createElement("option");
            filterB.value = "li";
            filterB.textContent = "李";
            filterSelect.append(filterA, filterB);
            filterSelect.value = "bard";
            filterSelect.selectedIndex = 0;
            globalThis.heroFilterHasMatch = filterStrategySelectOptions(filterSelect, "li");
            globalThis.heroFilterFirstHidden = filterA.hidden;
            globalThis.heroFilterSecondVisible = !filterB.hidden;
            const policyDraftSelect = findSelectWithOption(document.elements["strategy-current"], "金钱优先");
            policyDraftSelect.value = "金钱优先";
            policyDraftSelect.listeners.change[0]();
            renderStrategyPanel();
            const restoredPolicyDraft = findSelectWithOption(document.elements["strategy-current"], "金钱优先");
            globalThis.restoredPolicyDraft = restoredPolicyDraft.value;
            const zeroBudgetCampaign = {
              command_points_by_faction: { faction_1: { maximum: 4, used: 4, remaining: 0 } },
              queued_actions: [
                { faction_id: "faction_1", action_type: "resolve_story_event", action_key: "story_existing", command_cost: 1, payload: {} },
              ],
            };
            globalThis.storyChoiceCanReplaceAtZero = strategyCanAffordCommand(
              zeroBudgetCampaign,
              { id: "faction_1" },
              "resolve_story_event",
              { event_id: "story_existing", choice_id: "new_choice" },
              "story_existing"
            );
            globalThis.newStoryChoiceBlockedAtZero = strategyCanAffordCommand(
              zeroBudgetCampaign,
              { id: "faction_1" },
              "resolve_story_event",
              { event_id: "story_new", choice_id: "new_choice" },
              "story_new"
            );
            """
        )

        self.assertEqual(ctx.eval("globalThis.strategyCampaignCount"), 1)
        self.assertIn("晨星城", ctx.eval("globalThis.strategyText"))
        self.assertIn("成员与邀请", ctx.eval("globalThis.strategyText"))
        self.assertIn("当前加入码：ABC234", ctx.eval("globalThis.strategyText"))
        self.assertIn("角色：房主", ctx.eval("globalThis.strategyText"))
        self.assertIn("角色：AI 接管", ctx.eval("globalThis.strategyText"))
        self.assertTrue(ctx.eval("globalThis.hasStrategyWarTabs"))
        self.assertTrue(ctx.eval("globalThis.hasStrategyMapPlan"))
        self.assertTrue(ctx.eval("globalThis.hasStrategyCommandPlan"))
        self.assertTrue(ctx.eval("globalThis.hasStrategyWarState"))
        self.assertTrue(ctx.eval("globalThis.hasWarStateLockButton"))
        self.assertEqual(ctx.eval("globalThis.warStateLockCampaignId"), 7)
        self.assertTrue(ctx.eval("globalThis.canResumeWhenNoInitialPlayersMissing"))
        self.assertFalse(ctx.eval("globalThis.cannotResumeLobbyWithoutMissing"))
        self.assertTrue(ctx.eval("globalThis.strategySelectIsActive"))
        self.assertFalse(ctx.eval("globalThis.strategyButtonIsActive"))
        self.assertIn("本月已计划 1/2 条军令", ctx.eval("globalThis.strategyText"))
        self.assertIn("军令 x1", ctx.eval("globalThis.strategyText"))
        self.assertIn("房主账号 · 需由主公职位推进", ctx.eval("globalThis.strategyText"))
        self.assertIn("初始玩家", ctx.eval("globalThis.strategyText"))
        self.assertTrue(ctx.eval("globalThis.hasRotateJoinCodeButton"))
        self.assertEqual(ctx.eval("globalThis.rotatedCampaignId"), 7)
        self.assertIn("初始玩家在线状态", ctx.eval("globalThis.strategyText"))
        self.assertIn("所有真人初始玩家在线，AI 空席会自动操作。", ctx.eval("globalThis.strategyText"))
        self.assertIn("状态：在线", ctx.eval("globalThis.strategyText"))
        self.assertIn("状态：AI 托管", ctx.eval("globalThis.strategyText"))
        self.assertIn("当前账号", ctx.eval("globalThis.strategyText"))
        self.assertIn("战略地图", ctx.eval("globalThis.strategyText"))
        self.assertIn("本月军令", ctx.eval("globalThis.strategyText"))
        self.assertIn("3/4 可用", ctx.eval("globalThis.strategyText"))
        self.assertIn("晨星城叛军集结", ctx.eval("globalThis.strategyText"))
        self.assertIn("雾港城防线薄弱", ctx.eval("globalThis.strategyText"))
        self.assertIn("斥候推测：第二势力准备进攻", ctx.eval("globalThis.strategyText"))
        self.assertIn("月度决策", ctx.eval("globalThis.strategyText"))
        self.assertIn("前三个月战役引导", ctx.eval("globalThis.strategyText"))
        self.assertIn("本月关键决策", ctx.eval("globalThis.strategyText"))
        self.assertIn("优先处理 2 项高后果决定", ctx.eval("globalThis.strategyText"))
        self.assertIn("常规维护 · 1 座城市", ctx.eval("globalThis.strategyText"))
        self.assertIn("AI 官职只在缺粮或叛乱风险下自动调整一座城", ctx.eval("globalThis.strategyText"))
        self.assertIn("命令与请求回执", ctx.eval("globalThis.strategyText"))
        self.assertIn("1 军令 · 预计第 2 月", ctx.eval("globalThis.strategyText"))
        self.assertIn("晨星城已由城主设为粮食优先", ctx.eval("globalThis.strategyText"))
        self.assertIn("第一月 · 读局与治理 · 查看边境", ctx.eval("globalThis.strategyText"))
        self.assertIn("第二月 · 建立执行力量 · 祭祀或任命", ctx.eval("globalThis.strategyText"))
        self.assertIn("第三月 · 准备冲突", ctx.eval("globalThis.strategyText"))
        self.assertIn("不会获得或失去资源", ctx.eval("globalThis.strategyText"))
        self.assertTrue(ctx.eval("globalThis.hasGuideBorderButton"))
        self.assertTrue(ctx.eval("globalThis.hasGuideSkipButton"))
        self.assertEqual(json.loads(ctx.eval("JSON.stringify(globalThis.guideActions)")), ["survey_border", "skip"])
        self.assertIn("上月发生了什么", ctx.eval("globalThis.strategyText"))
        self.assertIn("本月必须处理什么", ctx.eval("globalThis.strategyText"))
        self.assertIn("推进后预计发生什么", ctx.eval("globalThis.strategyText"))
        self.assertIn("粮 +95（维护 17）", ctx.eval("globalThis.strategyText"))
        self.assertIn("行动队列：1 项", ctx.eval("globalThis.strategyText"))
        self.assertIn("下一步：处理突发事件", ctx.eval("globalThis.strategyText"))
        self.assertIn("行会争端", ctx.eval("globalThis.strategyText"))
        self.assertIn("本月底未处理将自动采用放任结果", ctx.eval("globalThis.strategyText"))
        self.assertIn("未完影响 · 第 3 月", ctx.eval("globalThis.strategyText"))
        self.assertTrue(ctx.eval("globalThis.hasStoryChoiceButton"))
        self.assertEqual(
            json.loads(ctx.eval("globalThis.queuedStoryPayload")),
            {"event_id": "story_2_faction_1_guild", "choice_id": "mediate_guilds"},
        )
        self.assertIn("定位 晨星城", ctx.eval("globalThis.strategyText"))
        self.assertTrue(ctx.eval("globalThis.hasGuideLocateButton"))
        self.assertIn("查看地图", ctx.eval("globalThis.strategyText"))
        self.assertIn("打开军令", ctx.eval("globalThis.strategyText"))
        self.assertTrue(ctx.eval("globalThis.hasStrategyMapStage"))
        self.assertTrue(ctx.eval("globalThis.hasStrategyCityCommandCard"))
        self.assertTrue(ctx.eval("globalThis.hasStrategyDossier"))
        self.assertTrue(ctx.eval("globalThis.hasStrategyRouteDrawer"))
        self.assertFalse(ctx.eval("globalThis.routeDrawerInitiallyOpen"))
        self.assertTrue(ctx.eval("globalThis.routeDrawerOpenAfterClick"))
        self.assertTrue(ctx.eval("globalThis.routeDrawerStateAfterClick"))
        self.assertFalse(ctx.eval("globalThis.dossierInitiallyOpen"))
        self.assertTrue(ctx.eval("globalThis.dossierOpenAfterClick"))
        self.assertTrue(ctx.eval("globalThis.dossierStateAfterClick"))
        self.assertTrue(ctx.eval("globalThis.hasDossierTabs"))
        self.assertTrue(ctx.eval("globalThis.hasDossierTechTab"))
        self.assertEqual(ctx.eval("globalThis.dossierTabAfterTechClick"), "tech")
        self.assertTrue(ctx.eval("globalThis.heroFilterHasMatch"))
        self.assertTrue(ctx.eval("globalThis.heroFilterFirstHidden"))
        self.assertTrue(ctx.eval("globalThis.heroFilterSecondVisible"))
        self.assertEqual(ctx.eval("globalThis.restoredPolicyDraft"), "金钱优先")
        self.assertTrue(ctx.eval("globalThis.storyChoiceCanReplaceAtZero"))
        self.assertFalse(ctx.eval("globalThis.newStoryChoiceBlockedAtZero"))
        self.assertIn("战报卷宗", ctx.eval("globalThis.strategyText"))
        self.assertIn("路线情报", ctx.eval("globalThis.strategyText"))
        self.assertIn("城市军令", ctx.eval("globalThis.strategyText"))
        self.assertIn("召唤祭祀", ctx.eval("globalThis.strategyText"))
        self.assertIn("祭祀场 1 级", ctx.eval("globalThis.strategyText"))
        self.assertIn("点击城市选择命令目标", ctx.eval("globalThis.strategyText"))
        self.assertIn("己方", ctx.eval("globalThis.strategyText"))
        self.assertIn("敌方", ctx.eval("globalThis.strategyText"))
        self.assertIn("选中", ctx.eval("globalThis.strategyText"))
        self.assertIn("警报", ctx.eval("globalThis.strategyText"))
        self.assertIn("本月没有当前职位必须处理的紧急事项", ctx.eval("globalThis.strategyText"))
        self.assertIn("晨星城 ↔ 雾港城", ctx.eval("globalThis.strategyText"))
        self.assertIn("相邻：雾港城", ctx.eval("globalThis.strategyText"))
        self.assertIn("可进攻：雾港城", ctx.eval("globalThis.strategyText"))
        self.assertTrue(ctx.eval("globalThis.hasFogMapButton"))
        self.assertEqual(ctx.eval("globalThis.selectedCityAfterMapClick"), "city_2")
        self.assertIn("选择己方城市下达军令", ctx.eval("globalThis.strategyTextAfterMapClick"))
        self.assertIn("乡勇编练", ctx.eval("globalThis.strategyText"))
        self.assertIn("本势力武将", ctx.eval("globalThis.strategyText"))
        self.assertIn("艾莉", ctx.eval("globalThis.strategyText"))
        self.assertIn("李", ctx.eval("globalThis.strategyText"))
        self.assertIn("举行祭祀", ctx.eval("globalThis.strategyText"))
        self.assertNotIn("发布招募武将令", ctx.eval("globalThis.strategyText"))
        self.assertTrue(ctx.eval("globalThis.hasHeroDeploySelect"))
        self.assertTrue(ctx.eval("globalThis.hasPlanAttackButton"))
        self.assertEqual(json.loads(ctx.eval("globalThis.queuedAttackPayload"))["attacker_hero_codes"], ["li"])
        self.assertTrue(ctx.eval("globalThis.hasDefenseHeroButton"))
        self.assertEqual(ctx.eval("globalThis.defenseHeroCode"), "li")
        self.assertTrue(ctx.eval("globalThis.hasBattleDefenseButton"))
        self.assertTrue(ctx.eval("globalThis.hasBattleDefenseSelect"))
        self.assertEqual(json.loads(ctx.eval("globalThis.battleDefensePayload")), {"battleId": "battle_2_defense", "heroCode": "li"})
        self.assertTrue(ctx.eval("globalThis.hasCityRitualButton"))
        self.assertEqual(json.loads(ctx.eval("globalThis.cityRitualPayload")), {"city_id": "city_1"})
        ctx.eval(
            """
            function collectInputs(node, typeName) {
              if (!node) return [];
              let found = [];
              if (node.tagName === "INPUT" && node.type === typeName) found.push(node);
              for (const child of node.children || []) found = found.concat(collectInputs(child, typeName));
              return found;
            }
            queueStrategyAction = function (actionType, payload) {
              if (actionType === "declare_attack") globalThis.multiQueuedAttackPayload = JSON.stringify(payload);
            };
            setStrategyBattleDefenseHero = function (battleId, heroCodes) {
              globalThis.multiBattleDefensePayload = JSON.stringify({ battleId, heroCodes });
            };
            state.strategyCampaign.world.factions[0].strategic_hero_deployment_limit = 2;
            state.strategyCampaign.world.factions[0].strategic_heroes.push({
              code: "chanter",
              name: "咏唱者",
              role: "法师",
              attribute: "暗",
              race: "精灵",
              level: 3,
              home_faction_id: "faction_1",
              status: "serving",
              summon_cost_ether: 35,
            });
            state.strategySelectedCityId = "city_1";
            renderStrategyPanel();
            const checkboxes = collectInputs(document.elements["strategy-current"], "checkbox");
            globalThis.multiHeroCheckboxCount = checkboxes.length;
            for (const input of checkboxes) {
              if (input.value === "li" || input.value === "chanter") input.checked = true;
            }
            const multiPlanAttackButton = findButtonByText(document.elements["strategy-current"], "计划进攻 · 2 军令");
            if (multiPlanAttackButton) multiPlanAttackButton.listeners.click[0]();
            const multiBattleDefenseButton = findButtonByText(document.elements["strategy-current"], "设置本场防守");
            if (multiBattleDefenseButton) multiBattleDefenseButton.listeners.click[0]();
            """
        )
        self.assertGreaterEqual(ctx.eval("globalThis.multiHeroCheckboxCount"), 2)
        self.assertEqual(json.loads(ctx.eval("globalThis.multiQueuedAttackPayload"))["attacker_hero_codes"], ["li", "chanter"])
        self.assertEqual(
            json.loads(ctx.eval("globalThis.multiBattleDefensePayload")),
            {"battleId": "battle_2_defense", "heroCodes": ["li", "chanter"]},
        )
        self.assertFalse(ctx.eval("globalThis.hasSummonHeroButton"))
        self.assertEqual(ctx.eval("globalThis.queuedStrategyActionType"), "perform_hero_ritual")
        self.assertIn("ABC234", ctx.eval("globalThis.strategyText"))
        self.assertIn("AB12CD", ctx.eval("globalThis.strategyText"))
        self.assertIn("进入真实战斗", ctx.eval("globalThis.strategyText"))
        self.assertIn("攻方单位", ctx.eval("globalThis.strategyText"))
        self.assertIn("守备兵", ctx.eval("globalThis.strategyText"))
        self.assertIn("战斗记录", ctx.eval("globalThis.strategyText"))
        self.assertIn("守方胜利", ctx.eval("globalThis.strategyText"))
        self.assertIn("守城成功", ctx.eval("globalThis.strategyText"))
        self.assertIn("损失：攻方 400", ctx.eval("globalThis.strategyText"))
        self.assertIn("剩余兵力：攻方 400", ctx.eval("globalThis.strategyText"))
        self.assertIn("存活单位：攻方 4/8", ctx.eval("globalThis.strategyText"))
        self.assertIn("英灵：攻方 参战 ellie", ctx.eval("globalThis.strategyText"))
        self.assertIn("沉睡 ellie", ctx.eval("globalThis.strategyText"))
        self.assertIn("英灵：守方 参战 fire_funeral", ctx.eval("globalThis.strategyText"))
        self.assertIn("查看真实战斗", ctx.eval("globalThis.strategyText"))
        self.assertIn("本月行动队列", ctx.eval("globalThis.strategyText"))
        self.assertIn("战略目标与流亡", ctx.eval("globalThis.strategyText"))
        self.assertIn("统一城邦", ctx.eval("globalThis.strategyText"))
        self.assertIn("已达成", ctx.eval("globalThis.strategyText"))
        self.assertIn("世界主线", ctx.eval("globalThis.strategyText"))
        self.assertIn("未开放", ctx.eval("globalThis.strategyText"))
        self.assertIn("流亡势力", ctx.eval("globalThis.strategyText"))
        self.assertIn("第二势力", ctx.eval("globalThis.strategyText"))
        self.assertIn("方针计划为 征兵优先", ctx.eval("globalThis.strategyText"))
        self.assertIn("叛乱风险 80 正式叛乱", ctx.eval("globalThis.strategyText"))
        self.assertIn("叛军 160", ctx.eval("globalThis.strategyText"))
        self.assertIn("治理", ctx.eval("globalThis.strategyText"))
        self.assertIn("计划方针", ctx.eval("globalThis.strategyText"))
        self.assertIn("叛乱", ctx.eval("globalThis.strategyText"))
        self.assertIn("叛乱处理", ctx.eval("globalThis.strategyText"))
        self.assertIn("计划处理", ctx.eval("globalThis.strategyText"))
        self.assertIn("计划清剿", ctx.eval("globalThis.strategyText"))
        self.assertTrue(ctx.eval("globalThis.hasRebellionSelect"))
        self.assertTrue(ctx.eval("globalThis.hasRebellionButton"))
        self.assertTrue(ctx.eval("globalThis.hasRebellionBattleButton"))
        self.assertEqual(json.loads(ctx.eval("globalThis.queuedRebellionPayload")), {"rebellion_action_id": "suppress", "city_id": "city_1"})
        self.assertEqual(json.loads(ctx.eval("globalThis.queuedRebellionBattlePayload")), {"city_id": "city_1", "troops": 160})
        self.assertIn("计划进攻", ctx.eval("globalThis.strategyText"))
        self.assertIn("加入月度计划", ctx.eval("globalThis.strategyText"))
        self.assertFalse(ctx.eval("globalThis.advanceDisabled"))
        self.assertFalse(ctx.eval("globalThis.firstSelectDisabled"))
        self.assertTrue(ctx.eval("globalThis.settledAdvanceDisabled"))
        self.assertTrue(ctx.eval("globalThis.hasContinueSandboxButton"))
        self.assertTrue(ctx.eval("globalThis.continueSandboxClicked"))
        self.assertIn("十二月城邦争衡", ctx.eval("globalThis.settledStrategyText"))
        self.assertIn("第 1 名 第一势力：400 分", ctx.eval("globalThis.settledStrategyText"))
        self.assertIn("等待房主选择", ctx.eval("globalThis.settledStrategyText"))

        ctx.eval(
            """
            queueStrategyAction = function (actionType, payload) {
              globalThis.queuedExileActionType = actionType;
              globalThis.queuedExilePayload = JSON.stringify(payload);
            };
            state.authUser = { id: 1, username: "Alice" };
            state.strategyCampaign.world.exile_action_choices = [
              { id: "seek_aid", name: "求援", description: "获得流亡援助", requires_target_city: false },
              { id: "build_network", name: "潜伏联络", description: "提高目标城市支持度", requires_target_city: true },
            ];
            state.strategyCampaign.world.strategic_status.exiled_faction_ids = ["faction_1"];
            state.strategyCampaign.world.strategic_status.exiled_factions = [
              { id: "faction_1", name: "第一势力", city_count: 0 },
            ];
            renderStrategyPanel();
            globalThis.strategyExileText = collectText(document.elements["strategy-current"]);
            const exilePlanButton = findButtonByText(document.elements["strategy-current"], "加入月度计划 · 1 军令");
            globalThis.hasExilePlanButton = Boolean(exilePlanButton);
            if (exilePlanButton) exilePlanButton.listeners.click[0]();
            """
        )
        self.assertIn("你的流亡行动", ctx.eval("globalThis.strategyExileText"))
        self.assertIn("求援", ctx.eval("globalThis.strategyExileText"))
        self.assertTrue(ctx.eval("globalThis.hasExilePlanButton"))
        self.assertEqual(ctx.eval("globalThis.queuedExileActionType"), "exile_action")
        self.assertEqual(json.loads(ctx.eval("globalThis.queuedExilePayload"))["exile_action_id"], "seek_aid")

        ctx.eval(
            """
            state.strategyCampaign.members.push({ user_id: 2, username: "Bob", faction_id: "faction_2", is_initial_player: true });
            state.strategyCampaign.resume = {
              can_resume: false,
              online_initial_user_ids: [1],
              missing_initial_user_ids: [2],
              initial_user_ids: [1, 2],
              campaign_status: "active",
            };
            renderStrategyPanel();
            globalThis.strategyWaitingText = collectText(document.elements["strategy-current"]);
            globalThis.strategyWaitingListText = collectText(document.elements["strategy-campaign-list"]);
            globalThis.waitingAdvanceDisabled = document.elements["strategy-advance-month"].disabled;
            """
        )
        self.assertIn("等待初始玩家：Bob", ctx.eval("globalThis.strategyWaitingText"))
        self.assertIn("等待初始玩家：Bob", ctx.eval("globalThis.strategyWaitingListText"))
        self.assertIn("Bob", ctx.eval("globalThis.strategyWaitingText"))
        self.assertIn("第二势力", ctx.eval("globalThis.strategyWaitingText"))
        self.assertIn("状态：缺席", ctx.eval("globalThis.strategyWaitingText"))
        self.assertTrue(ctx.eval("globalThis.waitingAdvanceDisabled"))

        ctx.eval(
            """
            state.authUser = { id: 2, username: "Bob" };
            state.strategyCampaign.resume = {
              can_resume: true,
              online_initial_user_ids: [1, 2],
              missing_initial_user_ids: [],
              initial_user_ids: [1, 2],
              campaign_status: "active",
            };
            renderStrategyPanel();
            globalThis.strategyMemberPermissionText = collectText(document.elements["strategy-current"]);
            globalThis.memberAdvanceDisabled = document.elements["strategy-advance-month"].disabled;
            """
        )
        self.assertIn("仅房主的主公职位可推进", ctx.eval("globalThis.strategyMemberPermissionText"))
        self.assertTrue(ctx.eval("globalThis.memberAdvanceDisabled"))

        ctx.eval(
            """
            state.authUser = { id: 1, username: "Alice" };
            const syncedCampaign = JSON.parse(JSON.stringify(state.strategyCampaigns[0]));
            syncedCampaign.name = "战后战役";
            state.strategyCampaigns = [];
            state.strategyCampaign = null;
            state.screen = "battle";
            applyRoomPayload({
              heroes: [],
              rooms: [],
              room: {
                room_id: "CD34EF",
                status: "finished",
                mode: "classic",
                viewer_player_id: 1,
                viewer_team_id: 1,
                viewer_name: "Alice",
                viewer_is_host: true,
                can_rematch: false,
                replay: { available: false },
                seats: [],
              },
              battle: {
                winner: 2,
                board: { width: 8, height: 8 },
                units: [],
                active_units: [],
                logs: ["玩家 2 获胜。"],
                visual_events: [],
              },
              strategy_campaign: syncedCampaign,
            }, { preserveScreen: true });
            renderGameOverOverlay();
            globalThis.syncedStrategyName = state.strategyCampaign.name;
            globalThis.syncedStrategyMessage = state.strategyMessage;
            globalThis.gameOverText = document.elements["game-over-text"].textContent;
            globalThis.strategyReturnDisabled = document.elements["game-over-strategy"].disabled;
            returnToStrategyCampaign();
            globalThis.returnedScreen = state.screen;
            renderStrategyPanel();
            globalThis.returnedStrategyText = collectText(document.elements["strategy-current"]);
            """
        )
        self.assertEqual(ctx.eval("globalThis.syncedStrategyName"), "战后战役")
        self.assertIn("战役结算已同步", ctx.eval("globalThis.syncedStrategyMessage"))
        self.assertIn("战役结算已同步", ctx.eval("globalThis.gameOverText"))
        self.assertFalse(ctx.eval("globalThis.strategyReturnDisabled"))
        self.assertEqual(ctx.eval("globalThis.returnedScreen"), "draft")
        self.assertIn("战后战役", ctx.eval("globalThis.returnedStrategyText"))
        self.assertIn("守方胜利", ctx.eval("globalThis.returnedStrategyText"))

        ctx.eval(
            """
            state.strategyCampaign.world.office_system = {
              office_types: [
                { id: "lord", workspace: "LordWorkspace" },
                { id: "grand_general", workspace: "GrandGeneralWorkspace" },
                { id: "general", workspace: "GeneralWorkspace" },
                { id: "governor", workspace: "GovernorWorkspace" },
              ],
            };
            state.strategyCampaign.world.offices = [
              { id: "office:faction_1:lord", faction_id: "faction_1", office_type: "lord", controller_type: "player", controller_user_id: 1, status: "active", managed_entity_ids: ["faction_1"], parent_office_id: null, subordinate_office_ids: ["office:faction_1:grand_general:1", "office:faction_1:governor:city_1"] },
              { id: "office:faction_1:grand_general:1", faction_id: "faction_1", office_type: "grand_general", controller_type: "player", controller_user_id: 1, status: "active", managed_entity_ids: ["theater:faction_1:1"], parent_office_id: "office:faction_1:lord", subordinate_office_ids: ["office:faction_1:general:1"], unit_inventory: {} },
              { id: "office:faction_1:general:1", faction_id: "faction_1", office_type: "general", controller_type: "player", controller_user_id: 1, status: "active", managed_entity_ids: ["army:faction_1:1", "city_1"], parent_office_id: "office:faction_1:grand_general:1", subordinate_office_ids: [], unit_inventory: { infantry: 1 } },
              { id: "office:faction_1:governor:city_1", faction_id: "faction_1", office_type: "governor", controller_type: "player", controller_user_id: 1, status: "active", managed_entity_ids: ["city_1"], parent_office_id: "office:faction_1:lord", subordinate_office_ids: [] },
            ];
            state.strategyCampaign.world.office_duties = [
              { id: "duty:lord", office_id: "office:faction_1:lord", duty_type: "review_national_strategy", priority: 1, status: "pending" },
            ];
            state.strategyCampaign.world.office_orders = [
              { id: "unit-request-1", order_type: "unit_request", issuer_office_id: "office:faction_1:general:1", receiver_office_id: "office:faction_1:grand_general:1", objective: "申请 步兵 1", target_entity_id: "city_1", details: { city_id: "city_1", unit_type: "infantry", count: 1 }, status: "pending" },
            ];
            queueStrategyAction = function (actionType, payload) {
              globalThis.roleActionType = actionType;
              globalThis.roleActionPayload = JSON.stringify(payload);
            };
            state.strategyActiveOfficeId = "office:faction_1:lord";
            renderStrategyPanel();
            const tutorialStoryDelegation = findButtonByText(document.elements["strategy-current"], "处理事件");
            globalThis.hasTutorialStoryDelegation = Boolean(tutorialStoryDelegation);
            if (tutorialStoryDelegation) tutorialStoryDelegation.listeners.click[0]();
            globalThis.tutorialDelegationType = globalThis.roleActionType;
            globalThis.tutorialDelegationPayload = globalThis.roleActionPayload;
            globalThis.hasLordWorkspace = Boolean(findByClass(document.elements["strategy-current"], "LordWorkspace"));
            globalThis.hasOfficeSwitcher = Boolean(findByClass(document.elements["strategy-current"], "strategy-office-switcher"));
            globalThis.lordCanOrder = Boolean(findButtonByText(document.elements["strategy-current"], "下达命令 · 1军令"));
            globalThis.lordCanSummon = Boolean(findButtonByText(document.elements["strategy-current"], "举行祭祀 · 1 军令"));
            const lordRitual = findButtonByText(document.elements["strategy-current"], "举行祭祀 · 1 军令");
            if (lordRitual) lordRitual.listeners.click[0]();
            globalThis.lordRitualType = globalThis.roleActionType;
            globalThis.lordRitualPayload = globalThis.roleActionPayload;
            const lordTech = findButtonByText(document.elements["strategy-current"], "研究科技 · 1 军令");
            globalThis.lordCanResearch = Boolean(lordTech);
            if (lordTech) lordTech.listeners.click[0]();
            globalThis.lordTechType = globalThis.roleActionType;
            const lordUnbind = findButtonByText(document.elements["strategy-current"], "解除绑定");
            globalThis.lordCanUnbind = Boolean(lordUnbind);
            if (lordUnbind) lordUnbind.listeners.click[0]();
            globalThis.lordUnbindType = globalThis.roleActionType;
            globalThis.lordUnbindPayload = globalThis.roleActionPayload;
            globalThis.lordCanAttack = Boolean(findButtonByText(document.elements["strategy-current"], "计划进攻 · 2 军令"));

            state.strategyActiveOfficeId = "office:faction_1:governor:city_1";
            renderStrategyPanel();
            globalThis.hasGovernorWorkspace = Boolean(findByClass(document.elements["strategy-current"], "GovernorWorkspace"));
            globalThis.governorCanSetPolicy = Boolean(findButtonByText(document.elements["strategy-current"], "计划方针 · 1 军令"));
            const governorIncrease = findButtonByText(document.elements["strategy-current"], "增加本城兵力 · 1 军令");
            globalThis.governorCanIncrease = Boolean(governorIncrease);
            if (governorIncrease) governorIncrease.listeners.click[0]();
            globalThis.governorIncreaseType = globalThis.roleActionType;
            const governorRegister = findButtonByText(document.elements["strategy-current"], "注册选定数量 · 1 军令");
            globalThis.governorCanRegister = Boolean(governorRegister);
            if (governorRegister) governorRegister.listeners.click[0]();
            globalThis.governorRegisterType = globalThis.roleActionType;
            globalThis.governorRegisterPayload = globalThis.roleActionPayload;
            globalThis.governorCanBuild = Boolean(findButtonByText(document.elements["strategy-current"], "建造 / 升级 · 1 军令"));
            globalThis.governorCanAttack = Boolean(findButtonByText(document.elements["strategy-current"], "计划进攻 · 2 军令"));
            globalThis.governorCanRequest = Boolean(findButtonByText(document.elements["strategy-current"], "提交请求"));

            state.strategyActiveOfficeId = "office:faction_1:general:1";
            renderStrategyPanel();
            globalThis.hasGeneralWorkspace = Boolean(findByClass(document.elements["strategy-current"], "GeneralWorkspace"));
            const generalRequestUnits = findButtonByText(document.elements["strategy-current"], "请示直属大将军");
            globalThis.generalCanRequestUnits = Boolean(generalRequestUnits);
            if (generalRequestUnits) generalRequestUnits.listeners.click[0]();
            globalThis.generalRequestType = globalThis.roleActionType;
            globalThis.generalRequestPayload = globalThis.roleActionPayload;
            globalThis.generalCanAttack = Boolean(findButtonByText(document.elements["strategy-current"], "计划进攻 · 2 军令"));
            globalThis.generalCanSetPolicy = Boolean(findButtonByText(document.elements["strategy-current"], "计划方针 · 1 军令"));

            state.strategyActiveOfficeId = "office:faction_1:grand_general:1";
            renderStrategyPanel();
            globalThis.hasGrandGeneralWorkspace = Boolean(findByClass(document.elements["strategy-current"], "GrandGeneralWorkspace"));
            const grandGeneralTransfer = findButtonByText(document.elements["strategy-current"], "调拨给直属将军 · 1 军令");
            globalThis.grandGeneralCanTransfer = Boolean(grandGeneralTransfer);
            if (grandGeneralTransfer) grandGeneralTransfer.listeners.click[0]();
            globalThis.grandGeneralTransferType = globalThis.roleActionType;
            globalThis.grandGeneralTransferPayload = globalThis.roleActionPayload;
            const grandGeneralApprove = findButtonByText(document.elements["strategy-current"], "批准调拨");
            globalThis.grandGeneralCanApprove = Boolean(grandGeneralApprove);
            if (grandGeneralApprove) grandGeneralApprove.listeners.click[0]();
            globalThis.grandGeneralApproveType = globalThis.roleActionType;
            globalThis.grandGeneralApprovePayload = globalThis.roleActionPayload;
            globalThis.grandGeneralCanDefend = Boolean(findButtonByText(document.elements["strategy-current"], "设为防守"));
            """
        )
        self.assertTrue(ctx.eval("globalThis.hasLordWorkspace"))
        self.assertTrue(ctx.eval("globalThis.hasTutorialStoryDelegation"))
        self.assertEqual(ctx.eval("globalThis.tutorialDelegationType"), "issue_office_order")
        self.assertTrue(json.loads(ctx.eval("globalThis.tutorialDelegationPayload"))["objective"].startswith("[引导:resolve_event]"))
        self.assertTrue(ctx.eval("globalThis.hasOfficeSwitcher"))
        self.assertTrue(ctx.eval("globalThis.lordCanOrder"))
        self.assertTrue(ctx.eval("globalThis.lordCanSummon"))
        self.assertEqual(ctx.eval("globalThis.lordRitualType"), "perform_hero_ritual")
        self.assertEqual(json.loads(ctx.eval("globalThis.lordRitualPayload")), {"city_id": "city_1"})
        self.assertTrue(ctx.eval("globalThis.lordCanResearch"))
        self.assertEqual(ctx.eval("globalThis.lordTechType"), "unlock_tactic_tech")
        self.assertTrue(ctx.eval("globalThis.lordCanUnbind"))
        self.assertEqual(ctx.eval("globalThis.lordUnbindType"), "unbind_strategic_hero")
        self.assertEqual(json.loads(ctx.eval("globalThis.lordUnbindPayload")), {"hero_code": "li"})
        self.assertFalse(ctx.eval("globalThis.lordCanAttack"))
        self.assertTrue(ctx.eval("globalThis.hasGovernorWorkspace"))
        self.assertTrue(ctx.eval("globalThis.governorCanSetPolicy"))
        self.assertTrue(ctx.eval("globalThis.governorCanIncrease"))
        self.assertEqual(ctx.eval("globalThis.governorIncreaseType"), "increase_city_troops")
        self.assertTrue(ctx.eval("globalThis.governorCanRegister"))
        self.assertEqual(ctx.eval("globalThis.governorRegisterType"), "register_city_soldiers")
        self.assertEqual(json.loads(ctx.eval("globalThis.governorRegisterPayload"))["city_id"], "city_1")
        self.assertTrue(ctx.eval("globalThis.governorCanBuild"))
        self.assertFalse(ctx.eval("globalThis.governorCanAttack"))
        self.assertTrue(ctx.eval("globalThis.governorCanRequest"))
        self.assertTrue(ctx.eval("globalThis.hasGeneralWorkspace"))
        self.assertTrue(ctx.eval("globalThis.generalCanRequestUnits"))
        self.assertEqual(ctx.eval("globalThis.generalRequestType"), "request_registered_units")
        self.assertEqual(json.loads(ctx.eval("globalThis.generalRequestPayload"))["city_id"], "city_1")
        self.assertTrue(ctx.eval("globalThis.generalCanAttack"))
        self.assertFalse(ctx.eval("globalThis.generalCanSetPolicy"))
        self.assertTrue(ctx.eval("globalThis.hasGrandGeneralWorkspace"))
        self.assertTrue(ctx.eval("globalThis.grandGeneralCanTransfer"))
        self.assertEqual(ctx.eval("globalThis.grandGeneralTransferType"), "transfer_registered_units")
        self.assertEqual(json.loads(ctx.eval("globalThis.grandGeneralTransferPayload"))["general_office_id"], "office:faction_1:general:1")
        self.assertTrue(ctx.eval("globalThis.grandGeneralCanApprove"))
        self.assertEqual(ctx.eval("globalThis.grandGeneralApproveType"), "approve_registered_unit_request")
        self.assertEqual(json.loads(ctx.eval("globalThis.grandGeneralApprovePayload")), {"request_id": "unit-request-1"})
        self.assertTrue(ctx.eval("globalThis.grandGeneralCanDefend"))

        ctx.eval(
            """
            state.strategyCampaign.status = "lobby";
            state.strategyCampaign.resume = { can_resume: false, missing_initial_user_ids: [] };
            renderStrategyPanel();
            globalThis.strategyLobbyText = collectText(document.elements["strategy-current"]);
            globalThis.lobbyAdvanceDisabled = document.elements["strategy-advance-month"].disabled;
            """
        )
        self.assertIn("锁定并启用 AI", ctx.eval("globalThis.strategyLobbyText"))
        self.assertTrue(ctx.eval("globalThis.lobbyAdvanceDisabled"))

    def test_scenario_random_room_roster_size_control_reflects_state_and_wires_host_change(self) -> None:
        app_source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        ctx = quickjs.Context()
        ctx.eval(
            """
            function createClassList() {
              return {
                add() {},
                remove() {},
                toggle() {},
                contains() { return false; },
              };
            }

            function createElement(tagName, id) {
              const element = {
                tagName: String(tagName || "div").toUpperCase(),
                id: id || "",
                children: [],
                listeners: {},
                className: "",
                value: "",
                disabled: false,
                dataset: {},
                style: {},
                _textContent: "",
                _innerHTML: "",
                classList: createClassList(),
                append(...nodes) {
                  this.children.push(...nodes);
                },
                appendChild(node) {
                  this.children.push(node);
                  return node;
                },
                addEventListener(type, handler) {
                  if (!this.listeners[type]) this.listeners[type] = [];
                  this.listeners[type].push(handler);
                },
                querySelector() {
                  return null;
                },
                querySelectorAll() {
                  return [];
                },
                replaceWith() {},
                focus() {},
                set innerHTML(value) {
                  this._innerHTML = String(value);
                },
                get innerHTML() {
                  return this._innerHTML;
                },
                set textContent(value) {
                  this._textContent = String(value);
                },
                get textContent() {
                  return this._textContent;
                },
              };
              return element;
            }

            const document = {
              elements: {},
              listeners: {},
              body: createElement("body", "body"),
              getElementById(id) {
                if (!this.elements[id]) this.elements[id] = createElement("div", id);
                return this.elements[id];
              },
              createElement(tagName) {
                return createElement(tagName);
              },
              querySelector() {
                return null;
              },
              querySelectorAll() {
                return [];
              },
              addEventListener(type, handler) {
                if (!this.listeners[type]) this.listeners[type] = [];
                this.listeners[type].push(handler);
              },
            };
            document.body.classList = createClassList();

            const storageFactory = () => ({
              _store: {},
              getItem(key) {
                return Object.prototype.hasOwnProperty.call(this._store, key) ? this._store[key] : null;
              },
              setItem(key, value) {
                this._store[key] = String(value);
              },
              removeItem(key) {
                delete this._store[key];
              },
            });

            const window = {
              location: { href: "http://example.test/?room=AB12CD", search: "?room=AB12CD", hash: "#draft" },
              listeners: {},
              addEventListener(type, handler) {
                if (!this.listeners[type]) this.listeners[type] = [];
                this.listeners[type].push(handler);
              },
              requestAnimationFrame(callback) { return callback(); },
              cancelAnimationFrame() {},
              setInterval() { return 1; },
              clearInterval() {},
            };

            const history = { replaceState() {} };
            const Element = Object;
            const localStorage = storageFactory();
            const sessionStorage = storageFactory();
            """
        )
        ctx.eval(app_source)
        ctx.eval(
            """
            globalThis.randomRosterSizeCall = "";
            setRandomRosterSize = function (value) { globalThis.randomRosterSizeCall = String(value); };
            state.profileReady = true;
            state.playerToken = "host-token";
            state.room = {
              room_id: "AB12CD",
              status: "lobby",
              mode: "random",
              mode_name: "随机选人",
              random_roster_size: 4,
              viewer_player_id: 1,
              viewer_name: "Alice",
              viewer_is_host: true,
              can_start: false,
              can_rematch: false,
              is_full: true,
              invite_url: "http://example.test/?room=AB12CD",
              invite_path: "/?room=AB12CD",
              available_modes: [
                { code: "classic", name: "标准选将", description: "" },
                { code: "random", name: "随机选人", description: "" }
              ],
              seats: [
                { player_id: 1, occupied: true, name: "Alice", hero_total_count: 0, hero_summary: null, is_host: true },
                { player_id: 2, occupied: true, name: "Bob", hero_total_count: 0, hero_summary: null, is_host: false }
              ]
            };
            bindEvents();
            applyRandomRoomPanelState();
            globalThis.randomRosterInputValue = document.elements["random-roster-size-input"].value;
            globalThis.randomRosterNoteText = document.elements["random-roster-size-note"].textContent;
            document.activeElement = document.elements["random-roster-size-input"];
            document.elements["random-roster-size-input"].value = "5";
            document.elements["random-roster-size-input"].listeners.input[0]({ target: document.elements["random-roster-size-input"] });
            applyRandomRoomPanelState();
            globalThis.randomRosterEditingValue = document.elements["random-roster-size-input"].value;
            document.elements["random-roster-size-input"].value = "5";
            document.elements["random-roster-size-input"].listeners.change[0]({ target: document.elements["random-roster-size-input"] });
            """
        )

        self.assertEqual(ctx.eval("globalThis.randomRosterInputValue"), "4")
        self.assertIn("4", ctx.eval("globalThis.randomRosterNoteText"))
        self.assertIn("不重复", ctx.eval("globalThis.randomRosterNoteText"))
        self.assertEqual(ctx.eval("globalThis.randomRosterEditingValue"), "5")
        self.assertEqual(ctx.eval("globalThis.randomRosterSizeCall"), "5")

    def test_scenario_multi_seat_room_panel_renders_four_seats_and_ai_configuration_state(self) -> None:
        app_source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        ctx = quickjs.Context()
        ctx.eval(
            """
            function createClassList() {
              return {
                add() {},
                remove() {},
                toggle() {},
                contains() { return false; },
              };
            }

            function createElement(tagName, id) {
              const element = {
                tagName: String(tagName || "div").toUpperCase(),
                id: id || "",
                children: [],
                listeners: {},
                className: "",
                value: "",
                disabled: false,
                dataset: {},
                style: {},
                _textContent: "",
                _innerHTML: "",
                classList: createClassList(),
                append(...nodes) {
                  this.children.push(...nodes);
                },
                appendChild(node) {
                  this.children.push(node);
                  return node;
                },
                addEventListener(type, handler) {
                  if (!this.listeners[type]) this.listeners[type] = [];
                  this.listeners[type].push(handler);
                },
                querySelector() {
                  return null;
                },
                querySelectorAll() {
                  return [];
                },
                replaceWith() {},
                focus() {},
                set innerHTML(value) {
                  this._innerHTML = String(value);
                },
                get innerHTML() {
                  return this._innerHTML;
                },
                set textContent(value) {
                  this._textContent = String(value);
                },
                get textContent() {
                  return this._textContent;
                },
              };
              return element;
            }

            const document = {
              elements: {},
              listeners: {},
              body: createElement("body", "body"),
              getElementById(id) {
                if (!this.elements[id]) this.elements[id] = createElement("div", id);
                return this.elements[id];
              },
              createElement(tagName) {
                return createElement(tagName);
              },
              querySelector() {
                return null;
              },
              querySelectorAll() {
                return [];
              },
              addEventListener(type, handler) {
                if (!this.listeners[type]) this.listeners[type] = [];
                this.listeners[type].push(handler);
              },
            };
            document.body.classList = createClassList();

            const storageFactory = () => ({
              _store: {},
              getItem(key) {
                return Object.prototype.hasOwnProperty.call(this._store, key) ? this._store[key] : null;
              },
              setItem(key, value) {
                this._store[key] = String(value);
              },
              removeItem(key) {
                delete this._store[key];
              },
            });

            const window = {
              location: { href: "http://example.test/?room=AB12CD", search: "?room=AB12CD", hash: "#draft" },
              listeners: {},
              addEventListener(type, handler) {
                if (!this.listeners[type]) this.listeners[type] = [];
                this.listeners[type].push(handler);
              },
              requestAnimationFrame(callback) { return callback(); },
              cancelAnimationFrame() {},
              setInterval() { return 1; },
              clearInterval() {},
            };

            const history = { replaceState() {} };
            const Element = Object;
            const localStorage = storageFactory();
            const sessionStorage = storageFactory();
            """
        )
        ctx.eval(app_source)
        ctx.eval(
            """
            canReclaimSeatByName = function () { return false; };
            state.profileReady = true;
            state.playerToken = "host-token";
            state.room = {
              room_id: "AB12CD",
              status: "lobby",
              mode: "random",
              mode_name: "随机选人",
              random_roster_size: 2,
              viewer_player_id: 1,
              viewer_team_id: 1,
              viewer_name: "Alice",
              viewer_is_host: true,
              can_start: false,
              can_rematch: false,
              is_full: true,
              seat_count: 4,
              seat_count_min: 2,
              seat_count_max: 6,
              start_blocker: "红队的随机武将配额之和必须等于 n = 2。",
              invite_url: "http://example.test/?room=AB12CD",
              invite_path: "/?room=AB12CD",
              available_modes: [
                { code: "classic", name: "标准选将", description: "" },
                { code: "random", name: "随机选人", description: "" }
              ],
              seats: [
                { player_id: 1, occupied: true, is_human: true, is_ai: false, controller_type: "human", team_id: 1, team_name: "红队", name: "Alice", hero_total_count: 0, hero_summary: null, random_quota: 2, is_host: true },
                { player_id: 2, occupied: true, is_human: true, is_ai: false, controller_type: "human", team_id: 2, team_name: "蓝队", name: "Bob", hero_total_count: 0, hero_summary: null, random_quota: 2, is_host: false },
                { player_id: 3, occupied: true, is_human: false, is_ai: true, controller_type: "ai", team_id: 1, team_name: "红队", name: "AI 3", hero_total_count: 0, hero_summary: null, random_quota: 0, is_host: false },
                { player_id: 4, occupied: false, is_human: false, is_ai: false, controller_type: "open", team_id: 2, team_name: "蓝队", name: null, hero_total_count: 0, hero_summary: null, random_quota: 0, is_host: false }
              ]
            };
            renderRoomPanels();
            globalThis.seatCardCount = document.elements["seat-cards"].children.length;
            globalThis.seatCountValue = document.elements["room-seat-count-input"].value;
            globalThis.viewerSeatLabel = document.elements["viewer-seat-label"].textContent;
            globalThis.roomMessageText = document.elements["room-message"].textContent;
            globalThis.thirdSeatHtml = document.elements["seat-cards"].children[2].innerHTML;
            """
        )

        self.assertEqual(ctx.eval("globalThis.seatCardCount"), 4)
        self.assertEqual(ctx.eval("globalThis.seatCountValue"), "4")
        self.assertEqual(ctx.eval("globalThis.viewerSeatLabel"), "席位 1")
        self.assertIn("随机武将配额", ctx.eval("globalThis.roomMessageText"))
        self.assertIn("AI", ctx.eval("globalThis.thirdSeatHtml"))
        self.assertIn("红队", ctx.eval("globalThis.thirdSeatHtml"))

    def test_scenario_room_panel_does_not_rerender_while_seat_controller_select_is_active(self) -> None:
        app_source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        ctx = quickjs.Context()
        ctx.eval(
            """
            function createClassList() {
              return {
                add() {},
                remove() {},
                toggle() {},
                contains() { return false; },
              };
            }

            function createElement(tagName, id) {
              const element = {
                tagName: String(tagName || "div").toUpperCase(),
                id: id || "",
                children: [],
                listeners: {},
                className: "",
                value: "",
                disabled: false,
                dataset: {},
                style: {},
                _textContent: "",
                _innerHTML: "",
                classList: createClassList(),
                append(...nodes) { this.children.push(...nodes); },
                appendChild(node) { this.children.push(node); return node; },
                addEventListener(type, handler) {
                  if (!this.listeners[type]) this.listeners[type] = [];
                  this.listeners[type].push(handler);
                },
                querySelector() { return null; },
                querySelectorAll() { return []; },
                replaceWith() {},
                focus() {},
                blur() { document.activeElement = null; },
                set innerHTML(value) { this._innerHTML = String(value); },
                get innerHTML() { return this._innerHTML; },
                set textContent(value) { this._textContent = String(value); },
                get textContent() { return this._textContent; },
              };
              return element;
            }

            const document = {
              elements: {},
              listeners: {},
              activeElement: null,
              body: createElement("body", "body"),
              getElementById(id) {
                if (!this.elements[id]) this.elements[id] = createElement("div", id);
                return this.elements[id];
              },
              createElement(tagName) { return createElement(tagName); },
              querySelector() { return null; },
              querySelectorAll() { return []; },
              addEventListener(type, handler) {
                if (!this.listeners[type]) this.listeners[type] = [];
                this.listeners[type].push(handler);
              },
            };
            document.body.classList = createClassList();

            const storageFactory = () => ({
              _store: {},
              getItem(key) { return Object.prototype.hasOwnProperty.call(this._store, key) ? this._store[key] : null; },
              setItem(key, value) { this._store[key] = String(value); },
              removeItem(key) { delete this._store[key]; },
            });

            const window = {
              location: { href: "http://example.test/?room=AB12CD", search: "?room=AB12CD", hash: "#draft" },
              listeners: {},
              addEventListener(type, handler) {
                if (!this.listeners[type]) this.listeners[type] = [];
                this.listeners[type].push(handler);
              },
              requestAnimationFrame(callback) { return callback(); },
              cancelAnimationFrame() {},
              setInterval() { return 1; },
              clearInterval() {},
            };
            const history = { replaceState() {} };
            const localStorage = storageFactory();
            const sessionStorage = storageFactory();
            """
        )
        ctx.eval(app_source)
        ctx.eval(
            """
            state.profileReady = true;
            state.screen = "lobby";
            state.playerToken = "host-token";
            state.room = {
              room_id: "AB12CD",
              status: "lobby",
              mode: "classic",
              mode_name: "标准选将",
              viewer_player_id: 1,
              viewer_team_id: 1,
              viewer_name: "Alice",
              viewer_is_host: true,
              can_start: false,
              can_rematch: false,
              is_full: true,
              seats: [
                { player_id: 1, occupied: true, is_human: true, is_ai: false, controller_type: "human", team_id: 1, team_name: "红队", name: "Alice", hero_total_count: 1, hero_summary: "吟游诗人", is_host: true },
                { player_id: 2, occupied: false, is_human: false, is_ai: false, controller_type: "open", team_id: 2, team_name: "蓝队", name: null, hero_total_count: 0, hero_summary: null, is_host: false }
              ]
            };
            [
              "seat-cards",
              "room-message",
              "viewer-seat-label",
              "viewer-seat-note",
              "room-seat-count-input",
              "room-seat-count-note",
              "room-random-panel",
              "random-roster-size-input",
              "random-roster-size-note",
              "room-hero-grid",
              "message",
              "topbar-pill",
              "topbar-caption",
              "board",
              "board-stage",
              "board-zoom-controls",
              "logs",
              "selected-card",
              "action-panel",
              "unit-strip",
              "chain-panel",
              "hover-card",
              "battle-right-rail",
              "toggle-right-rail",
              "battle-effects",
              "floating-toast-stack",
              "end-turn",
              "skip-chain",
              "target-cancel",
              "target-complete"
            ].forEach((id) => document.getElementById(id));
            document.getElementById("seat-cards").innerHTML = "preserved";
            let roomPanelsRendered = 0;
            renderScreens = function () {};
            renderNavigation = function () {};
            renderProfilePanel = function () {};
            renderProfileModal = function () {};
            renderRoomPanels = function () { roomPanelsRendered += 1; document.getElementById("seat-cards").innerHTML = "rerendered"; };
            applyRandomRoomPanelState = function () {};
            renderResumePanel = function () {};
            renderRoomListActive = function () {};
            renderHeroCards = function () {};
            renderHeader = function () {};
            renderBoardZoomControls = function () {};
            renderMessage = function () {};
            renderBattleEffects = function () {};
            renderBoard = function () {};
            renderBoardOverlays = function () {};
            renderHoverCard = function () {};
            renderSidebarPanels = function () {};
            renderSelectedCard = function () {};
            renderActionPanel = function () {};
            renderUnitStrip = function () {};
            renderChainPanel = function () {};
            renderLogs = function () {};
            renderFloatingToasts = function () {};
            renderGameOverOverlay = function () {};
            renderReplayToolbar = function () {};
            renderRoomActionButtons = function () {};
            renderTargetCancelButton = function () {};
            renderTargetCompleteButton = function () {};
            ensureDraftSelection = function () {};
            ensureSelectedUnit = function () {};
            clearActionSelection = function () {};
            hasBattle = function () { return false; };
            isGameOver = function () { return false; };
            canInteract = function () { return false; };
            isChainMode = function () { return false; };
            isRespawnMode = function () { return false; };
            const active = createElement("select", "active-seat-controller");
            active.dataset.seatController = "2";
            document.activeElement = active;
            render();
            globalThis.roomPanelsRendered = roomPanelsRendered;
            globalThis.seatCardsMarkup = document.getElementById("seat-cards").innerHTML;
            """
        )

        self.assertEqual(ctx.eval("globalThis.roomPanelsRendered"), 0)
        self.assertEqual(ctx.eval("globalThis.seatCardsMarkup"), "preserved")

    def test_scenario_render_header_shows_dynamic_next_turn_summary(self) -> None:
        app_source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        ctx = quickjs.Context()
        ctx.eval(
            """
            function createClassList() {
              return {
                add() {},
                remove() {},
                toggle() {},
                contains() { return false; },
              };
            }

            function createElement(tagName, id) {
              const element = {
                tagName: String(tagName || "div").toUpperCase(),
                id: id || "",
                children: [],
                listeners: {},
                className: "",
                value: "",
                disabled: false,
                dataset: {},
                style: {},
                _textContent: "",
                _innerHTML: "",
                classList: createClassList(),
                append(...nodes) {
                  this.children.push(...nodes);
                },
                appendChild(node) {
                  this.children.push(node);
                  return node;
                },
                addEventListener(type, handler) {
                  if (!this.listeners[type]) this.listeners[type] = [];
                  this.listeners[type].push(handler);
                },
                querySelector() {
                  return null;
                },
                querySelectorAll() {
                  return [];
                },
                replaceWith() {},
                focus() {},
                set innerHTML(value) {
                  this._innerHTML = String(value);
                },
                get innerHTML() {
                  return this._innerHTML;
                },
                set textContent(value) {
                  this._textContent = String(value);
                },
                get textContent() {
                  return this._textContent;
                },
              };
              return element;
            }

            const document = {
              elements: {},
              listeners: {},
              body: createElement("body", "body"),
              getElementById(id) {
                if (!this.elements[id]) this.elements[id] = createElement("div", id);
                return this.elements[id];
              },
              createElement(tagName) {
                return createElement(tagName);
              },
              querySelector() {
                return null;
              },
              querySelectorAll() {
                return [];
              },
              addEventListener(type, handler) {
                if (!this.listeners[type]) this.listeners[type] = [];
                this.listeners[type].push(handler);
              },
            };
            document.body.classList = createClassList();

            const storageFactory = () => ({
              _store: {},
              getItem(key) {
                return Object.prototype.hasOwnProperty.call(this._store, key) ? this._store[key] : null;
              },
              setItem(key, value) {
                this._store[key] = String(value);
              },
              removeItem(key) {
                delete this._store[key];
              },
            });

            const window = {
              location: { href: "http://example.test/?room=AB12CD", search: "?room=AB12CD", hash: "#battle" },
              listeners: {},
              addEventListener(type, handler) {
                if (!this.listeners[type]) this.listeners[type] = [];
                this.listeners[type].push(handler);
              },
              requestAnimationFrame(callback) { return callback(); },
              cancelAnimationFrame() {},
              setInterval() { return 1; },
              clearInterval() {},
            };

            const history = { replaceState() {} };
            const localStorage = storageFactory();
            const sessionStorage = storageFactory();
            """
        )
        ctx.eval(app_source)
        ctx.eval(
            """
            state.room = {
              room_id: "AB12CD",
              status: "battle",
              mode: "classic",
              mode_name: "标准选将",
              viewer_player_id: 1,
              viewer_name: "Alice",
              viewer_is_host: true,
              can_start: false,
              can_rematch: false,
              is_full: true,
              seats: [],
            };
            state.battle = {
              winner: null,
              pending_chain: null,
              pending_respawn: null,
              round_number: 3,
              input_player: 1,
              active_turn_unit_name: "艾莉",
              next_turn_unit_name: "吟游诗人",
              next_turn_player_id: 2,
            };
            renderHeader();
            globalThis.turnPillText = document.elements["turn-pill"].textContent;
            globalThis.topbarSublineText = document.elements["topbar-subline"].textContent;
            globalThis.boardCaptionText = document.elements["board-caption"].textContent;
            """
        )

        self.assertIn("艾莉", ctx.eval("globalThis.turnPillText"))
        self.assertIn("下回合：玩家 2 的 吟游诗人。", ctx.eval("globalThis.topbarSublineText"))
        self.assertIn("下回合：玩家 2 的 吟游诗人。", ctx.eval("globalThis.boardCaptionText"))

    def test_scenario_board_zoom_and_stage_scroll_reposition_board_overlays(self) -> None:
        app_source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        ctx = quickjs.Context()
        ctx.eval(
            """
            function createClassList() {
              return {
                add() {},
                remove() {},
                toggle() {},
                contains() { return false; },
              };
            }

            function createElement(tagName, id) {
              const element = {
                tagName: String(tagName || "div").toUpperCase(),
                id: id || "",
                children: [],
                listeners: {},
                className: "",
                value: "",
                disabled: false,
                dataset: {},
                style: {},
                _textContent: "",
                _innerHTML: "",
                classList: createClassList(),
                append(...nodes) {
                  this.children.push(...nodes);
                },
                appendChild(node) {
                  this.children.push(node);
                  return node;
                },
                addEventListener(type, handler) {
                  if (!this.listeners[type]) this.listeners[type] = [];
                  this.listeners[type].push(handler);
                },
                querySelector() {
                  return null;
                },
                querySelectorAll() {
                  return [];
                },
                replaceWith() {},
                focus() {},
                set innerHTML(value) {
                  this._innerHTML = String(value);
                },
                get innerHTML() {
                  return this._innerHTML;
                },
                set textContent(value) {
                  this._textContent = String(value);
                },
                get textContent() {
                  return this._textContent;
                },
              };
              return element;
            }

            const document = {
              elements: {},
              listeners: {},
              body: createElement("body", "body"),
              getElementById(id) {
                if (!this.elements[id]) this.elements[id] = createElement("div", id);
                return this.elements[id];
              },
              createElement(tagName) {
                return createElement(tagName);
              },
              querySelector() {
                return null;
              },
              querySelectorAll() {
                return [];
              },
              addEventListener(type, handler) {
                if (!this.listeners[type]) this.listeners[type] = [];
                this.listeners[type].push(handler);
              },
            };
            document.body.classList = createClassList();

            const storageFactory = () => ({
              _store: {},
              getItem(key) {
                return Object.prototype.hasOwnProperty.call(this._store, key) ? this._store[key] : null;
              },
              setItem(key, value) {
                this._store[key] = String(value);
              },
              removeItem(key) {
                delete this._store[key];
              },
            });

            const window = {
              location: { href: "http://example.test/", search: "", hash: "" },
              listeners: {},
              addEventListener(type, handler) {
                if (!this.listeners[type]) this.listeners[type] = [];
                this.listeners[type].push(handler);
              },
              requestAnimationFrame(callback) { return callback(); },
              cancelAnimationFrame() {},
              setInterval() { return 1; },
              clearInterval() {},
            };

            const history = { replaceState() {} };
            const localStorage = storageFactory();
            const sessionStorage = storageFactory();
            """
        )
        ctx.eval(app_source)
        ctx.eval(
            """
            globalThis.actionWheelRenderCount = 0;
            globalThis.boardAlertRenderCount = 0;
            renderActionWheel = function () { globalThis.actionWheelRenderCount += 1; };
            renderBoardAlert = function () { globalThis.boardAlertRenderCount += 1; };
            renderBoard = function () {};
            renderBoardZoomControls = function () {};
            state.battle = { board: { width: 10, height: 10 } };
            bindEvents();
            document.elements["board-stage"].listeners.scroll[0]();
            adjustBoardZoom(0.15);
            window.listeners.resize[0]();
            """
        )

        self.assertEqual(ctx.eval("globalThis.actionWheelRenderCount"), 3)
        self.assertEqual(ctx.eval("globalThis.boardAlertRenderCount"), 3)

    def test_scenario_board_stage_drag_and_wheel_zoom_are_bound_to_battlefield(self) -> None:
        app_source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        ctx = quickjs.Context()
        ctx.eval(
            """
            function createClassList() {
              return {
                add() {},
                remove() {},
                toggle() {},
                contains() { return false; },
              };
            }

            function Element() {}

            function matchesSimpleSelector(node, selector) {
              if (!node || !selector) return false;
              if (selector[0] === ".") {
                const token = selector.slice(1);
                return String(node.className || "").split(/\\s+/).indexOf(token) !== -1;
              }
              if (selector[0] === "#") return String(node.id || "") === selector.slice(1);
              return String(node.tagName || "").toLowerCase() === selector.toLowerCase();
            }

            function createElement(tagName, id) {
              const element = new Element();
              Object.assign(element, {
                tagName: String(tagName || "div").toUpperCase(),
                id: id || "",
                children: [],
                listeners: {},
                className: "",
                value: "",
                disabled: false,
                dataset: {},
                style: { setProperty(key, value) { this[key] = value; } },
                parentNode: null,
                scrollLeft: 0,
                scrollTop: 0,
                rect: { left: 0, top: 0, width: 0, height: 0 },
                _textContent: "",
                _innerHTML: "",
                classList: createClassList(),
                append(...nodes) {
                  nodes.forEach((node) => {
                    if (!node) return;
                    node.parentNode = this;
                    this.children.push(node);
                  });
                },
                appendChild(node) {
                  if (node) {
                    node.parentNode = this;
                    this.children.push(node);
                  }
                  return node;
                },
                addEventListener(type, handler) {
                  if (!this.listeners[type]) this.listeners[type] = [];
                  this.listeners[type].push(handler);
                },
                querySelector() {
                  return null;
                },
                querySelectorAll() {
                  return [];
                },
                replaceWith() {},
                focus() {},
                contains(node) {
                  let current = node || null;
                  while (current) {
                    if (current === this) return true;
                    current = current.parentNode || null;
                  }
                  return false;
                },
                closest(selector) {
                  const selectors = String(selector || "")
                    .split(",")
                    .map((part) => part.trim())
                    .filter(Boolean);
                  let current = this;
                  while (current) {
                    if (selectors.some((part) => matchesSimpleSelector(current, part))) return current;
                    current = current.parentNode || null;
                  }
                  return null;
                },
                getBoundingClientRect() {
                  const rect = this.rect || { left: 0, top: 0, width: 0, height: 0 };
                  return {
                    left: rect.left,
                    top: rect.top,
                    width: rect.width,
                    height: rect.height,
                    right: rect.left + rect.width,
                    bottom: rect.top + rect.height,
                  };
                },
                setPointerCapture(pointerId) {
                  this.capturedPointerId = pointerId;
                },
                releasePointerCapture(pointerId) {
                  this.releasedPointerId = pointerId;
                },
                set innerHTML(value) {
                  this._innerHTML = String(value);
                },
                get innerHTML() {
                  return this._innerHTML;
                },
                set textContent(value) {
                  this._textContent = String(value);
                },
                get textContent() {
                  return this._textContent;
                },
              });
              return element;
            }

            const document = {
              elements: {},
              listeners: {},
              body: createElement("body", "body"),
              getElementById(id) {
                if (!this.elements[id]) this.elements[id] = createElement("div", id);
                return this.elements[id];
              },
              createElement(tagName) {
                return createElement(tagName);
              },
              querySelector() {
                return null;
              },
              querySelectorAll() {
                return [];
              },
              addEventListener(type, handler) {
                if (!this.listeners[type]) this.listeners[type] = [];
                this.listeners[type].push(handler);
              },
            };
            document.body.classList = createClassList();

            const storageFactory = () => ({
              _store: {},
              getItem(key) {
                return Object.prototype.hasOwnProperty.call(this._store, key) ? this._store[key] : null;
              },
              setItem(key, value) {
                this._store[key] = String(value);
              },
              removeItem(key) {
                delete this._store[key];
              },
            });

            const window = {
              location: { href: "http://example.test/", search: "", hash: "" },
              listeners: {},
              addEventListener(type, handler) {
                if (!this.listeners[type]) this.listeners[type] = [];
                this.listeners[type].push(handler);
              },
              requestAnimationFrame(callback) { return callback(); },
              cancelAnimationFrame() {},
              setInterval() { return 1; },
              clearInterval() {},
            };

            const history = { replaceState() {} };
            const localStorage = storageFactory();
            const sessionStorage = storageFactory();
            """
        )
        ctx.eval(app_source)
        ctx.eval(
            """
            globalThis.overlayRenderCount = 0;
            renderActionWheel = function () { globalThis.overlayRenderCount += 1; };
            renderBoardAlert = function () { globalThis.overlayRenderCount += 1; };
            renderBoard = function () {};
            renderBoardZoomControls = function () {};
            state.battle = { board: { width: 10, height: 10 } };
            state.boardZoom = 1;
            const boardStage = document.getElementById("board-stage");
            const board = document.getElementById("board");
            boardStage.rect = { left: 0, top: 0, width: 420, height: 320 };
            board.rect = { left: -80, top: -40, width: 840, height: 640 };
            boardStage.scrollLeft = 120;
            boardStage.scrollTop = 90;
            boardStage.appendChild(board);
            const cell = document.createElement("button");
            cell.className = "cell";
            cell.dataset = { x: "3", y: "4" };
            board.appendChild(cell);
            bindEvents();
            boardStage.listeners.pointerdown[0]({
              button: 0,
              pointerId: 7,
              clientX: 140,
              clientY: 150,
              target: cell,
            });
            boardStage.listeners.pointermove[0]({
              pointerId: 7,
              clientX: 185,
              clientY: 210,
              target: cell,
            });
            boardStage.listeners.pointerup[0]({ pointerId: 7 });
            globalThis.dragScrollLeft = boardStage.scrollLeft;
            globalThis.dragScrollTop = boardStage.scrollTop;
            globalThis.pointerCaptured = boardStage.capturedPointerId;
            globalThis.pointerReleased = boardStage.releasedPointerId;
            globalThis.wheelPrevented = false;
            boardStage.listeners.wheel[0]({
              target: cell,
              deltaY: -120,
              clientX: 210,
              clientY: 180,
              preventDefault() { globalThis.wheelPrevented = true; },
            });
            globalThis.zoomAfterWheel = state.boardZoom;
            """
        )

        self.assertEqual(ctx.eval("globalThis.dragScrollLeft"), 75)
        self.assertEqual(ctx.eval("globalThis.dragScrollTop"), 30)
        self.assertEqual(ctx.eval("globalThis.pointerCaptured"), 7)
        self.assertEqual(ctx.eval("globalThis.pointerReleased"), 7)
        self.assertTrue(ctx.eval("globalThis.wheelPrevented"))
        self.assertGreater(ctx.eval("globalThis.zoomAfterWheel"), 1)

    def test_scenario_action_wheel_renders_around_selected_unit_inside_board_stage(self) -> None:
        app_source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        ctx = quickjs.Context()
        ctx.eval(
            """
            function createClassList() {
              return {
                add() {},
                remove() {},
                toggle() {},
                contains() { return false; },
              };
            }

            function createElement(tagName, id) {
              const element = {
                tagName: String(tagName || "div").toUpperCase(),
                id: id || "",
                children: [],
                listeners: {},
                className: "",
                value: "",
                disabled: false,
                dataset: {},
                style: { setProperty(key, value) { this[key] = value; } },
                parentNode: null,
                _textContent: "",
                _innerHTML: "",
                classList: createClassList(),
                append(...nodes) {
                  nodes.forEach((node) => {
                    node.parentNode = this;
                    this.children.push(node);
                  });
                },
                appendChild(node) {
                  node.parentNode = this;
                  this.children.push(node);
                  return node;
                },
                addEventListener(type, handler) {
                  if (!this.listeners[type]) this.listeners[type] = [];
                  this.listeners[type].push(handler);
                },
                querySelector() {
                  return null;
                },
                querySelectorAll() {
                  return [];
                },
                replaceWith() {},
                focus() {},
                getBoundingClientRect() {
                  return { left: 0, top: 0, width: 0, height: 0, right: 0, bottom: 0 };
                },
                set innerHTML(value) {
                  this._innerHTML = String(value);
                  this.children = [];
                },
                get innerHTML() {
                  return this._innerHTML;
                },
                set textContent(value) {
                  this._textContent = String(value);
                },
                get textContent() {
                  return this._textContent;
                },
              };
              return element;
            }

            const document = {
              elements: {},
              listeners: {},
              body: createElement("body", "body"),
              getElementById(id) {
                if (!this.elements[id]) this.elements[id] = createElement("div", id);
                return this.elements[id];
              },
              createElement(tagName) {
                return createElement(tagName);
              },
              querySelector() {
                return null;
              },
              querySelectorAll() {
                return [];
              },
              addEventListener(type, handler) {
                if (!this.listeners[type]) this.listeners[type] = [];
                this.listeners[type].push(handler);
              },
            };
            document.body.classList = createClassList();

            const storageFactory = () => ({
              _store: {},
              getItem(key) {
                return Object.prototype.hasOwnProperty.call(this._store, key) ? this._store[key] : null;
              },
              setItem(key, value) {
                this._store[key] = String(value);
              },
              removeItem(key) {
                delete this._store[key];
              },
            });

            const window = {
              location: { href: "http://example.test/", search: "", hash: "" },
              listeners: {},
              addEventListener(type, handler) {
                if (!this.listeners[type]) this.listeners[type] = [];
                this.listeners[type].push(handler);
              },
              requestAnimationFrame(callback) { return callback(); },
              cancelAnimationFrame() {},
              setInterval() { return 1; },
              clearInterval() {},
            };

            const history = { replaceState() {} };
            const localStorage = storageFactory();
            const sessionStorage = storageFactory();
            """
        )
        ctx.eval(app_source)
        ctx.eval(
            """
            const boardStage = document.getElementById("board-stage");
            boardStage.getBoundingClientRect = function () {
              return { left: 0, top: 0, width: 420, height: 320, right: 420, bottom: 320 };
            };
            const board = document.getElementById("board");
            function addCell(x, y, left, top, size) {
              const cell = document.createElement("div");
              cell.dataset.x = String(x);
              cell.dataset.y = String(y);
              cell.getBoundingClientRect = function () {
                return {
                  left,
                  top,
                  width: size,
                  height: size,
                  right: left + size,
                  bottom: top + size,
                };
              };
              board.appendChild(cell);
            }
            addCell(2, 1, 182, 98, 64);
            const actionPanel = document.getElementById("action-panel");
            state.screen = "battle";
            state.room = { viewer_player_id: 1 };
            state.selectedUnitId = "u1";
            state.battle = {
              input_player: 1,
              units: [
                {
                  id: "u1",
                  player_id: 1,
                  position: { x: 2, y: 1 },
                  occupied_cells: [{ x: 2, y: 1 }],
                }
              ],
              active_units: [
                {
                  unit_id: "u1",
                  actions: {
                    actions: [
                      { code: "move", kind: "move", timing: "active", available: true, name: "ç§»åŠ¨" },
                      { code: "attack", kind: "attack", timing: "active", available: true, name: "æ”»å‡»" },
                      { code: "machine_gun", kind: "skill", timing: "active", available: true, name: "æœºæžª", action_name: "æœºæžª" },
                    ]
                  },
                  reactions: { actions: [] },
                }
              ],
            };
            renderActionPanel();
            renderActionWheel();
            const actionWheel = document.getElementById("action-wheel");
            globalThis.actionPanelChildCount = actionPanel.children.length;
            globalThis.actionWheelChildCount = actionWheel.children.length;
            globalThis.boardStageChildCount = boardStage.children.length;
            globalThis.firstActionLeft = actionWheel.children[0].style.left;
            globalThis.firstActionTop = actionWheel.children[0].style.top;
            """
        )

        self.assertEqual(ctx.eval("globalThis.actionPanelChildCount"), 1)
        self.assertEqual(ctx.eval("globalThis.actionWheelChildCount"), 3)
        self.assertEqual(ctx.eval("globalThis.boardStageChildCount"), 1)
        self.assertGreaterEqual(int(float(ctx.eval("globalThis.firstActionLeft").replace("px", ""))), 0)
        self.assertGreaterEqual(int(float(ctx.eval("globalThis.firstActionTop").replace("px", ""))), 0)

    def test_scenario_targeting_action_hides_action_wheel_and_keeps_board_clickable(self) -> None:
        app_source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        ctx = quickjs.Context()
        ctx.eval(
            """
            function createClassList() {
              return {
                add() {},
                remove() {},
                toggle() {},
                contains() { return false; },
              };
            }

            function createElement(tagName, id) {
              const element = {
                tagName: String(tagName || "div").toUpperCase(),
                id: id || "",
                children: [],
                listeners: {},
                className: "",
                value: "",
                disabled: false,
                dataset: {},
                style: { setProperty(key, value) { this[key] = value; } },
                parentNode: null,
                _textContent: "",
                _innerHTML: "",
                classList: createClassList(),
                append(...nodes) {
                  nodes.forEach((node) => {
                    node.parentNode = this;
                    this.children.push(node);
                  });
                },
                appendChild(node) {
                  node.parentNode = this;
                  this.children.push(node);
                  return node;
                },
                addEventListener(type, handler) {
                  if (!this.listeners[type]) this.listeners[type] = [];
                  this.listeners[type].push(handler);
                },
                querySelector() {
                  return null;
                },
                querySelectorAll() {
                  return [];
                },
                closest(selector) {
                  if (!selector) return null;
                  if (selector === ".cell") {
                    let node = this;
                    while (node) {
                      if (String(node.className || "").split(" ").includes("cell")) return node;
                      node = node.parentNode;
                    }
                  }
                  if (selector === "input, select, textarea, label, .board-alert" || selector === "button") {
                    return null;
                  }
                  return null;
                },
                contains(node) {
                  if (node === this) return true;
                  return this.children.some((child) => child === node || (child.contains && child.contains(node)));
                },
                replaceWith() {},
                focus() {},
                getBoundingClientRect() {
                  return { left: 0, top: 0, width: 0, height: 0, right: 0, bottom: 0 };
                },
                set innerHTML(value) {
                  this._innerHTML = String(value);
                  this.children = [];
                },
                get innerHTML() {
                  return this._innerHTML;
                },
                set textContent(value) {
                  this._textContent = String(value);
                },
                get textContent() {
                  return this._textContent;
                },
              };
              return element;
            }

            const document = {
              elements: {},
              listeners: {},
              body: createElement("body", "body"),
              getElementById(id) {
                if (!this.elements[id]) this.elements[id] = createElement("div", id);
                return this.elements[id];
              },
              createElement(tagName) {
                return createElement(tagName);
              },
              querySelector() {
                return null;
              },
              querySelectorAll() {
                return [];
              },
              addEventListener(type, handler) {
                if (!this.listeners[type]) this.listeners[type] = [];
                this.listeners[type].push(handler);
              },
            };
            document.body.classList = createClassList();

            const storageFactory = () => ({
              _store: {},
              getItem(key) {
                return Object.prototype.hasOwnProperty.call(this._store, key) ? this._store[key] : null;
              },
              setItem(key, value) {
                this._store[key] = String(value);
              },
              removeItem(key) {
                delete this._store[key];
              },
            });

            const window = {
              location: { href: "http://example.test/", search: "", hash: "" },
              listeners: {},
              addEventListener(type, handler) {
                if (!this.listeners[type]) this.listeners[type] = [];
                this.listeners[type].push(handler);
              },
              requestAnimationFrame(callback) { return callback(); },
              cancelAnimationFrame() {},
              setInterval() { return 1; },
              clearInterval() {},
            };

            const history = { replaceState() {} };
            const Element = Object;
            const localStorage = storageFactory();
            const sessionStorage = storageFactory();
            """
        )
        ctx.eval(app_source)
        ctx.eval(
            """
            const boardStage = document.getElementById("board-stage");
            boardStage.getBoundingClientRect = function () {
              return { left: 0, top: 0, width: 420, height: 320, right: 420, bottom: 320 };
            };
            boardStage.setPointerCapture = function (pointerId) {
              this.capturedPointerId = pointerId;
            };
            const board = document.getElementById("board");
            function addCell(x, y, left, top) {
              const cell = document.createElement("div");
              cell.className = "cell";
              cell.dataset.x = String(x);
              cell.dataset.y = String(y);
              cell.getBoundingClientRect = function () {
                return { left, top, width: 64, height: 64, right: left + 64, bottom: top + 64 };
              };
              board.appendChild(cell);
            }
            addCell(0, 0, 24, 24);
            addCell(1, 1, 96, 96);
            state.screen = "battle";
            state.room = { viewer_player_id: 1 };
            state.selectedUnitId = "u1";
            state.battle = {
              input_player: 1,
              board: { width: 8, height: 8 },
              units: [
                {
                  id: "u1",
                  player_id: 1,
                  banished: false,
                  position: { x: 0, y: 0 },
                  occupied_cells: [{ x: 0, y: 0 }],
                  statuses: [],
                }
              ],
              active_units: [
                {
                  unit_id: "u1",
                  actions: {
                    actions: [
                      {
                        code: "move",
                        kind: "move",
                        timing: "active",
                        available: true,
                        preview: {
                          cells: [{ x: 1, y: 1 }],
                          target_unit_ids: [],
                          secondary_cells: [],
                          requires_target: true,
                        },
                      }
                    ]
                  },
                  reactions: { actions: [] },
                }
              ],
            };
            const payloads = [];
            performAction = function (payload) {
              payloads.push(payload);
            };
            bindEvents();
            renderActionWheel();
            globalThis.wheelBeforeTargeting = document.getElementById("action-wheel").children.length;
            render = function () {
              renderActionWheel();
            };
            onActionClick(actionByCode("move"));
            globalThis.selectedActionAfterClick = state.selectedActionCode;
            globalThis.wheelDuringTargeting = document.getElementById("action-wheel").children.length;
            globalThis.previewStillHasCell = currentPreview().cellKeys.has("1,1");
            document.getElementById("board-stage").listeners.pointerdown[0]({
              button: 0,
              pointerId: 7,
              clientX: 100,
              clientY: 100,
              target: board.children[1],
            });
            globalThis.pointerCapturedDuringTargeting = document.getElementById("board-stage").capturedPointerId || null;
            globalThis.boardDragStateDuringTargeting = boardDragState === null ? "none" : "dragging";
            onBoardClick(1, 1, null);
            globalThis.performedPayload = JSON.stringify(payloads[0] || null);
            """
        )

        self.assertEqual(ctx.eval("globalThis.wheelBeforeTargeting"), 1)
        self.assertEqual(ctx.eval("globalThis.selectedActionAfterClick"), "move")
        self.assertEqual(ctx.eval("globalThis.wheelDuringTargeting"), 0)
        self.assertTrue(ctx.eval("globalThis.previewStillHasCell"))
        self.assertIsNone(ctx.eval("globalThis.pointerCapturedDuringTargeting"))
        self.assertEqual(ctx.eval("globalThis.boardDragStateDuringTargeting"), "none")
        self.assertEqual(
            json.loads(ctx.eval("globalThis.performedPayload")),
            {"type": "move", "unit_id": "u1", "x": 1, "y": 1},
        )

    def test_scenario_precise_target_previews_do_not_mark_entire_multicell_unit(self) -> None:
        app_source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        ctx = quickjs.Context()
        ctx.eval(
            """
            function createClassList() {
              return {
                add() {},
                remove() {},
                toggle() {},
                contains() { return false; },
              };
            }

            function createElement(tagName, id) {
              const element = {
                tagName: String(tagName || "div").toUpperCase(),
                id: id || "",
                children: [],
                listeners: {},
                className: "",
                value: "",
                disabled: false,
                dataset: {},
                style: {},
                _textContent: "",
                _innerHTML: "",
                classList: createClassList(),
                append(...nodes) {
                  this.children.push(...nodes);
                },
                appendChild(node) {
                  this.children.push(node);
                  return node;
                },
                addEventListener(type, handler) {
                  if (!this.listeners[type]) this.listeners[type] = [];
                  this.listeners[type].push(handler);
                },
                querySelector() {
                  return null;
                },
                querySelectorAll() {
                  return [];
                },
                replaceWith() {},
                focus() {},
                contains(node) {
                  return this.children.indexOf(node) !== -1;
                },
                set innerHTML(value) {
                  this._innerHTML = String(value);
                  this.children = [];
                },
                get innerHTML() {
                  return this._innerHTML;
                },
                set textContent(value) {
                  this._textContent = String(value);
                },
                get textContent() {
                  return this._textContent;
                },
              };
              return element;
            }

            const document = {
              elements: {},
              listeners: {},
              body: createElement("body", "body"),
              getElementById(id) {
                if (!this.elements[id]) this.elements[id] = createElement("div", id);
                return this.elements[id];
              },
              createElement(tagName) {
                return createElement(tagName);
              },
              querySelector() {
                return null;
              },
              querySelectorAll() {
                return [];
              },
              addEventListener(type, handler) {
                if (!this.listeners[type]) this.listeners[type] = [];
                this.listeners[type].push(handler);
              },
            };
            document.body.classList = createClassList();

            const storageFactory = () => ({
              _store: {},
              getItem(key) {
                return Object.prototype.hasOwnProperty.call(this._store, key) ? this._store[key] : null;
              },
              setItem(key, value) {
                this._store[key] = String(value);
              },
              removeItem(key) {
                delete this._store[key];
              },
            });

            const window = {
              location: { href: "http://example.test/", search: "", hash: "" },
              listeners: {},
              addEventListener(type, handler) {
                if (!this.listeners[type]) this.listeners[type] = [];
                this.listeners[type].push(handler);
              },
              requestAnimationFrame(callback) { return callback(); },
              cancelAnimationFrame() {},
              setInterval() { return 1; },
              clearInterval() {},
            };

            const history = { replaceState() {} };
            const localStorage = storageFactory();
            const sessionStorage = storageFactory();
            """
        )
        ctx.eval(app_source)
        ctx.eval(
            """
            state.screen = "battle";
            state.room = { viewer_player_id: 1 };
            state.selectedUnitId = "u1";
            state.selectedActionCode = "machine_gun";
            state.battle = {
              board: { width: 8, height: 8 },
              units: [
                {
                  id: "u1",
                  unit_id: "u1",
                  player_id: 1,
                  banished: false,
                  cannot_be_targeted: false,
                  statuses: [],
                  position: { x: 1, y: 1 },
                  occupied_cells: [{ x: 1, y: 1 }],
                },
                {
                  id: "u2",
                  unit_id: "u2",
                  player_id: 2,
                  banished: false,
                  cannot_be_targeted: false,
                  statuses: [],
                  position: { x: 3, y: 1 },
                  occupied_cells: [{ x: 3, y: 1 }, { x: 4, y: 1 }, { x: 3, y: 2 }, { x: 4, y: 2 }],
                }
              ],
              active_units: [
                {
                  unit_id: "u1",
                  actions: {
                    actions: [
                      {
                        code: "machine_gun",
                        kind: "skill",
                        timing: "active",
                        available: true,
                        target_mode: "cell",
                        preview: {
                          cells: [{ x: 3, y: 1 }, { x: 4, y: 1 }, { x: 5, y: 1 }],
                          target_unit_ids: ["u2"],
                          secondary_cells: [],
                          requires_target: true,
                          selection: {
                            mode: "pattern_cells",
                            patterns: [[{ x: 3, y: 1 }, { x: 4, y: 1 }, { x: 5, y: 1 }]],
                            ordered: false,
                          },
                        },
                      },
                      {
                        code: "attack",
                        kind: "attack",
                        timing: "active",
                        available: true,
                        target_mode: "enemy",
                        preview: {
                          cells: [{ x: 3, y: 1 }],
                          target_unit_ids: ["u2"],
                          secondary_cells: [],
                          requires_target: true,
                        },
                      },
                      {
                        code: "split",
                        kind: "skill",
                        timing: "active",
                        available: true,
                        target_mode: "cell",
                        preview: {
                          cells: [{ x: 1, y: 2 }, { x: 2, y: 2 }, { x: 2, y: 3 }],
                          target_unit_ids: [],
                          secondary_cells: [],
                          requires_target: true,
                          selection: {
                            mode: "pattern_cells",
                            patterns: [],
                            ordered: false,
                            required_cells: 3,
                          },
                        },
                      }
                    ]
                  },
                  reactions: { actions: [] },
                }
              ],
            };
            const preview = currentPreview();
            globalThis.previewTargetCount = preview.targetIds.size;
            globalThis.previewHasHitCell = preview.cellKeys.has("3,1");
            globalThis.previewHasOffLineTargetCell = preview.cellKeys.has("4,2");
            state.selectedActionCode = "attack";
            const attackPreview = currentPreview();
            globalThis.attackPreviewTargetCount = attackPreview.targetIds.size;
            globalThis.attackPreviewHasHitCell = attackPreview.cellKeys.has("3,1");
            globalThis.attackPreviewHasOffLineTargetCell = attackPreview.cellKeys.has("4,2");
            state.selectedActionCode = "split";
            const splitPreview = currentPreview();
            globalThis.splitPreviewHasFirst = splitPreview.cellKeys.has("1,2");
            setStagedPatternCells([{ x: 1, y: 2 }, { x: 2, y: 2 }]);
            const splitNextPreview = currentPreview();
            globalThis.splitPreviewAfterTwoHasChosen = splitNextPreview.cellKeys.has("1,2");
            globalThis.splitPreviewAfterTwoHasRemaining = splitNextPreview.cellKeys.has("2,3");
            globalThis.splitCanCompleteAfterTwo = canCompleteTargetSelection();
            setStagedPatternCells([{ x: 1, y: 2 }, { x: 2, y: 2 }, { x: 2, y: 3 }]);
            globalThis.splitCanCompleteAfterThree = canCompleteTargetSelection();
            """
        )

        self.assertEqual(ctx.eval("globalThis.previewTargetCount"), 0)
        self.assertTrue(ctx.eval("globalThis.previewHasHitCell"))
        self.assertFalse(ctx.eval("globalThis.previewHasOffLineTargetCell"))
        self.assertEqual(ctx.eval("globalThis.attackPreviewTargetCount"), 1)
        self.assertTrue(ctx.eval("globalThis.attackPreviewHasHitCell"))
        self.assertFalse(ctx.eval("globalThis.attackPreviewHasOffLineTargetCell"))
        self.assertTrue(ctx.eval("globalThis.splitPreviewHasFirst"))
        self.assertFalse(ctx.eval("globalThis.splitPreviewAfterTwoHasChosen"))
        self.assertTrue(ctx.eval("globalThis.splitPreviewAfterTwoHasRemaining"))
        self.assertFalse(ctx.eval("globalThis.splitCanCompleteAfterTwo"))
        self.assertTrue(ctx.eval("globalThis.splitCanCompleteAfterThree"))

    def test_scenario_clicking_enemy_unit_still_opens_info_when_viewer_cannot_act(self) -> None:
        app_source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        ctx = quickjs.Context()
        ctx.eval(
            """
            function createClassList() {
              return {
                add() {},
                remove() {},
                toggle() {},
                contains() { return false; },
              };
            }

            function createElement(tagName, id) {
              const element = {
                tagName: String(tagName || "div").toUpperCase(),
                id: id || "",
                children: [],
                listeners: {},
                className: "",
                value: "",
                disabled: false,
                dataset: {},
                style: {},
                _textContent: "",
                _innerHTML: "",
                classList: createClassList(),
                append(...nodes) {
                  this.children.push(...nodes);
                },
                appendChild(node) {
                  this.children.push(node);
                  return node;
                },
                addEventListener(type, handler) {
                  if (!this.listeners[type]) this.listeners[type] = [];
                  this.listeners[type].push(handler);
                },
                querySelector() {
                  return null;
                },
                querySelectorAll() {
                  return [];
                },
                replaceWith() {},
                focus() {},
                contains(node) {
                  return this.children.indexOf(node) !== -1;
                },
                set innerHTML(value) {
                  this._innerHTML = String(value);
                  this.children = [];
                },
                get innerHTML() {
                  return this._innerHTML;
                },
                set textContent(value) {
                  this._textContent = String(value);
                },
                get textContent() {
                  return this._textContent;
                },
              };
              return element;
            }

            const document = {
              elements: {},
              listeners: {},
              body: createElement("body", "body"),
              getElementById(id) {
                if (!this.elements[id]) this.elements[id] = createElement("div", id);
                return this.elements[id];
              },
              createElement(tagName) {
                return createElement(tagName);
              },
              querySelector() {
                return null;
              },
              querySelectorAll() {
                return [];
              },
              addEventListener(type, handler) {
                if (!this.listeners[type]) this.listeners[type] = [];
                this.listeners[type].push(handler);
              },
            };
            document.body.classList = createClassList();

            const storageFactory = () => ({
              _store: {},
              getItem(key) {
                return Object.prototype.hasOwnProperty.call(this._store, key) ? this._store[key] : null;
              },
              setItem(key, value) {
                this._store[key] = String(value);
              },
              removeItem(key) {
                delete this._store[key];
              },
            });

            const window = {
              location: { href: "http://example.test/", search: "", hash: "" },
              listeners: {},
              addEventListener(type, handler) {
                if (!this.listeners[type]) this.listeners[type] = [];
                this.listeners[type].push(handler);
              },
              requestAnimationFrame(callback) { return callback(); },
              cancelAnimationFrame() {},
              setInterval() { return 1; },
              clearInterval() {},
            };

            const history = { replaceState() {} };
            const localStorage = storageFactory();
            const sessionStorage = storageFactory();
            """
        )
        ctx.eval(app_source)
        ctx.eval(
            """
            render = function () {};
            canInteract = function () { return false; };
            state.screen = "battle";
            state.room = { viewer_player_id: 1, viewer_team_id: 1 };
            state.selectedUnitId = "u1";
            state.sidebarExpanded = "logs";
            state.battle = {
              board: { width: 8, height: 8 },
              units: [
                {
                  id: "u1",
                  unit_id: "u1",
                  player_id: 1,
                  banished: false,
                  cannot_be_targeted: false,
                  statuses: [],
                  position: { x: 1, y: 1 },
                  occupied_cells: [{ x: 1, y: 1 }],
                },
                {
                  id: "u2",
                  unit_id: "u2",
                  player_id: 2,
                  banished: false,
                  cannot_be_targeted: false,
                  statuses: [{ name: "中毒" }],
                  position: { x: 3, y: 1 },
                  occupied_cells: [{ x: 3, y: 1 }],
                }
              ],
              active_units: [],
            };
            onBoardClick(3, 1, unitById("u2"));
            globalThis.selectedUnitIdAfterClick = state.selectedUnitId;
            globalThis.sidebarAfterClick = state.sidebarExpanded;
            """
        )

        self.assertEqual(ctx.eval("globalThis.selectedUnitIdAfterClick"), "u2")
        self.assertEqual(ctx.eval("globalThis.sidebarAfterClick"), "info")

    def test_scenario_new_visual_events_create_board_vfx_nodes(self) -> None:
        app_source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        ctx = quickjs.Context()
        ctx.eval(
            """
            function createClassList() {
              return {
                add() {},
                remove() {},
                toggle() {},
                contains() { return false; },
              };
            }

            function createElement(tagName, id) {
              const element = {
                tagName: String(tagName || "div").toUpperCase(),
                id: id || "",
                children: [],
                listeners: {},
                className: "",
                value: "",
                disabled: false,
                dataset: {},
                style: {
                  setProperty(name, value) {
                    this[name] = String(value);
                  },
                },
                _textContent: "",
                _innerHTML: "",
                classList: createClassList(),
                append(...nodes) {
                  this.children.push(...nodes);
                },
                appendChild(node) {
                  this.children.push(node);
                  return node;
                },
                addEventListener(type, handler) {
                  if (!this.listeners[type]) this.listeners[type] = [];
                  this.listeners[type].push(handler);
                },
                querySelector() {
                  return null;
                },
                querySelectorAll() {
                  return [];
                },
                replaceWith() {},
                remove() {
                  this.removed = true;
                },
                focus() {},
                getBoundingClientRect() {
                  return { left: 0, top: 0, width: 320, height: 320, right: 320, bottom: 320 };
                },
                set innerHTML(value) {
                  this._innerHTML = String(value);
                  this.children = [];
                },
                get innerHTML() {
                  return this._innerHTML;
                },
                set textContent(value) {
                  this._textContent = String(value);
                },
                get textContent() {
                  return this._textContent;
                },
              };
              return element;
            }

            const document = {
              elements: {},
              listeners: {},
              body: createElement("body", "body"),
              getElementById(id) {
                if (!this.elements[id]) this.elements[id] = createElement("div", id);
                return this.elements[id];
              },
              createElement(tagName) {
                return createElement(tagName);
              },
              querySelector() {
                return null;
              },
              querySelectorAll() {
                return [];
              },
              addEventListener(type, handler) {
                if (!this.listeners[type]) this.listeners[type] = [];
                this.listeners[type].push(handler);
              },
            };
            document.body.classList = createClassList();

            const storageFactory = () => ({
              _store: {},
              getItem(key) {
                return Object.prototype.hasOwnProperty.call(this._store, key) ? this._store[key] : null;
              },
              setItem(key, value) {
                this._store[key] = String(value);
              },
              removeItem(key) {
                delete this._store[key];
              },
            });

            const window = {
              location: { href: "http://example.test/", search: "", hash: "" },
              listeners: {},
              addEventListener(type, handler) {
                if (!this.listeners[type]) this.listeners[type] = [];
                this.listeners[type].push(handler);
              },
              requestAnimationFrame(callback) { return callback(); },
              cancelAnimationFrame() {},
              setInterval() { return 1; },
              clearInterval() {},
              setTimeout() { return 1; },
              clearTimeout() {},
            };

            const history = { replaceState() {} };
            const localStorage = storageFactory();
            const sessionStorage = storageFactory();
            function URLSearchParams(search) {
              this._params = {};
              const raw = String(search || "").replace(/^\\?/, "");
              if (raw) {
                raw.split("&").forEach((entry) => {
                  if (!entry) return;
                  const parts = entry.split("=");
                  this._params[decodeURIComponent(parts[0])] = decodeURIComponent(parts[1] || "");
                });
              }
            }
            URLSearchParams.prototype.get = function (key) {
              return Object.prototype.hasOwnProperty.call(this._params, key) ? this._params[key] : null;
            };
            URLSearchParams.prototype.set = function (key, value) {
              this._params[key] = String(value);
            };
            URLSearchParams.prototype.delete = function (key) {
              delete this._params[key];
            };
            URLSearchParams.prototype.toString = function () {
              return Object.keys(this._params).map((key) => `${key}=${this._params[key]}`).join("&");
            };
            function URL(href) {
              this.href = String(href || "http://example.test/");
              this.hash = "";
              this.searchParams = new URLSearchParams("");
            }
            """
        )
        ctx.eval(app_source)
        ctx.eval(
            """
            cellCenterPoint = function (cell) {
              if (!cell) return null;
              return { x: Number(cell.x) * 40 + 20, y: Number(cell.y) * 40 + 20 };
            };
            unitCenterPoint = function () {
              return { x: 60, y: 60 };
            };
            state.screen = "battle";
            state.room = { viewer_player_id: 1 };
            applyRoomPayload({
              heroes: [],
              room: { viewer_player_id: 1, room_id: "ROOM01" },
              battle: {
                board: { width: 8, height: 8 },
                units: [],
                field_effects: [],
                pending_chain: null,
                pending_respawn: null,
                logs: [],
                visual_events: [
                  {
                    id: 1,
                    kind: "attack",
                    display_name: "普攻",
                    actor_id: "u1",
                    actor_player_id: 1,
                    action_type: "attack",
                    action_code: "attack",
                    target_unit_ids: [],
                    target_cells: [{ x: 4, y: 4 }],
                    source_cell: { x: 3, y: 4 },
                    defense_reason: "",
                    metadata: {},
                  },
                ],
              },
            });
            globalThis.initialVfxCount = state.activeBattleVfx.length;
            applyRoomPayload({
              heroes: [],
              room: { viewer_player_id: 1, room_id: "ROOM01" },
              battle: {
                board: { width: 8, height: 8 },
                units: [],
                field_effects: [],
                pending_chain: null,
                pending_respawn: null,
                logs: [],
                visual_events: [
                  {
                    id: 1,
                    kind: "attack",
                    display_name: "普攻",
                    actor_id: "u1",
                    actor_player_id: 1,
                    action_type: "attack",
                    action_code: "attack",
                    target_unit_ids: [],
                    target_cells: [{ x: 4, y: 4 }],
                    source_cell: { x: 3, y: 4 },
                    defense_reason: "",
                    metadata: {},
                  },
                  {
                    id: 2,
                    kind: "skill",
                    display_name: "大圣光",
                    actor_id: "u2",
                    actor_player_id: 2,
                    action_type: "skill",
                    action_code: "great_holy_light",
                    target_unit_ids: [],
                    target_cells: [{ x: 4, y: 4 }, { x: 4, y: 5 }],
                    source_cell: { x: 2, y: 4 },
                    defense_reason: "",
                    metadata: {},
                  },
                ],
              },
            });
            renderBattleVfx();
            globalThis.afterVfxCount = state.activeBattleVfx.length;
            globalThis.renderedNodeCount = document.elements["battle-vfx"].children.length;
            """
        )

        self.assertEqual(ctx.eval("globalThis.initialVfxCount"), 0)
        self.assertEqual(ctx.eval("globalThis.afterVfxCount"), 1)
        self.assertGreater(ctx.eval("globalThis.renderedNodeCount"), 0)

    def test_scenario_replay_toolbar_reflects_live_and_replay_state(self) -> None:
        app_source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        replay_source = (ROOT / "static" / "replay-ui.js").read_text(encoding="utf-8")
        ctx = quickjs.Context()
        ctx.eval(
            """
            function createClassList(owner) {
              return {
                add(name) {
                  if (!owner.className.includes(name)) owner.className = `${owner.className} ${name}`.trim();
                },
                remove(name) {
                  owner.className = owner.className
                    .split(/\\s+/)
                    .filter((token) => token && token !== name)
                    .join(" ");
                },
                toggle(name, force) {
                  const shouldAdd = force === undefined ? !this.contains(name) : Boolean(force);
                  if (shouldAdd) this.add(name);
                  else this.remove(name);
                },
                contains(name) {
                  return owner.className.split(/\\s+/).includes(name);
                },
              };
            }

            function createElement(tagName, id) {
              const element = {
                tagName: String(tagName || "div").toUpperCase(),
                id: id || "",
                children: [],
                listeners: {},
                className: "",
                value: "",
                checked: false,
                disabled: false,
                dataset: {},
                style: {},
                _textContent: "",
                _innerHTML: "",
                append(...nodes) {
                  this.children.push(...nodes);
                },
                appendChild(node) {
                  this.children.push(node);
                  return node;
                },
                addEventListener(type, handler) {
                  if (!this.listeners[type]) this.listeners[type] = [];
                  this.listeners[type].push(handler);
                },
                querySelector() { return null; },
                querySelectorAll() { return []; },
                replaceWith() {},
                focus() {},
                set innerHTML(value) { this._innerHTML = String(value); },
                get innerHTML() { return this._innerHTML; },
                set textContent(value) { this._textContent = String(value); },
                get textContent() { return this._textContent; },
              };
              element.classList = createClassList(element);
              return element;
            }

            const document = {
              elements: {},
              listeners: {},
              body: createElement("body", "body"),
              getElementById(id) {
                if (!this.elements[id]) this.elements[id] = createElement("div", id);
                return this.elements[id];
              },
              createElement(tagName) { return createElement(tagName); },
              querySelector() { return null; },
              querySelectorAll() { return []; },
              addEventListener(type, handler) {
                if (!this.listeners[type]) this.listeners[type] = [];
                this.listeners[type].push(handler);
              },
            };
            document.body.classList = createClassList(document.body);

            const storageFactory = () => ({
              _store: {},
              getItem(key) { return Object.prototype.hasOwnProperty.call(this._store, key) ? this._store[key] : null; },
              setItem(key, value) { this._store[key] = String(value); },
              removeItem(key) { delete this._store[key]; },
            });

            const window = {
              location: { href: "http://example.test/?room=ROOM01", search: "?room=ROOM01", hash: "#battle" },
              listeners: {},
              addEventListener(type, handler) {
                if (!this.listeners[type]) this.listeners[type] = [];
                this.listeners[type].push(handler);
              },
              requestAnimationFrame(callback) { return callback(); },
              cancelAnimationFrame() {},
              setInterval() { return 1; },
              clearInterval() {},
            };
            const history = { replaceState() {} };
            const localStorage = storageFactory();
            const sessionStorage = storageFactory();
            """
        )
        ctx.eval(replay_source)
        ctx.eval(app_source)
        ctx.eval(
            """
            [
              "replay-toolbar",
              "replay-step-back",
              "replay-pause",
              "replay-live",
              "replay-step-forward",
              "replay-speed",
              "replay-omniscient",
              "replay-timeline",
              "replay-status",
            ].forEach((id) => document.getElementById(id));
            state.room = {
              room_id: "ROOM01",
              viewer_is_host: true,
              replay: { available: true, last_step_index: 6, can_use_omniscient: true },
              simulation: { enabled: true, paused: true, speed: 2, can_control: true, live_step_index: 4 },
            };
            state.battle = { winner: null, board: { width: 8, height: 8 } };
            state.replayMode = true;
            state.replayStepIndex = 2;
            state.replayOmniscient = true;
            renderReplayToolbar();
            globalThis.toolbarHidden = document.elements["replay-toolbar"].classList.contains("hidden");
            globalThis.pauseText = document.elements["replay-pause"].textContent;
            globalThis.timelineValue = document.elements["replay-timeline"].value;
            globalThis.omniscientChecked = document.elements["replay-omniscient"].checked;
            globalThis.liveDisabled = document.elements["replay-live"].disabled;
            """
        )

        self.assertFalse(ctx.eval("globalThis.toolbarHidden"))
        self.assertEqual(ctx.eval("globalThis.pauseText"), "\u25b6")
        self.assertEqual(ctx.eval("globalThis.timelineValue"), "2")
        self.assertTrue(ctx.eval("globalThis.omniscientChecked"))
        self.assertFalse(ctx.eval("globalThis.liveDisabled"))


    def test_scenario_replay_toolbar_scaffolding_uses_readable_chinese_labels(self) -> None:
        app_source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        ctx = quickjs.Context()
        ctx.eval(
            """
            function createClassList(owner) {
              return {
                _owner: owner,
                _set: {},
                add(...names) { names.forEach((name) => { this._set[name] = true; }); },
                remove(...names) { names.forEach((name) => { delete this._set[name]; }); },
                contains(name) { return !!this._set[name]; },
                toggle(name, force) {
                  const shouldAdd = force === undefined ? !this.contains(name) : !!force;
                  if (shouldAdd) this.add(name);
                  else this.remove(name);
                  return shouldAdd;
                },
              };
            }

            function createElement(tagName, id = "") {
              const element = {
                tagName: String(tagName || "div").toUpperCase(),
                id,
                children: [],
                listeners: {},
                disabled: false,
                value: "",
                checked: false,
                dataset: {},
                style: {},
                _textContent: "",
                _innerHTML: "",
                append(...nodes) { this.children.push(...nodes); },
                appendChild(node) { this.children.push(node); return node; },
                insertBefore(node) { this.children.push(node); this.lastInserted = node; return node; },
                addEventListener(type, handler) {
                  if (!this.listeners[type]) this.listeners[type] = [];
                  this.listeners[type].push(handler);
                },
                querySelector(selector) {
                  if (selector === ".legend") return this.legend || null;
                  return null;
                },
                querySelectorAll() { return []; },
                replaceWith() {},
                focus() {},
                set innerHTML(value) { this._innerHTML = String(value); },
                get innerHTML() { return this._innerHTML; },
                set textContent(value) { this._textContent = String(value); },
                get textContent() { return this._textContent; },
              };
              element.classList = createClassList(element);
              return element;
            }

            const boardHead = createElement("div", "board-head");
            const footer = createElement("div", "board-footer");
            const endTurn = createElement("button", "end-turn");
            const document = {
              elements: { "end-turn": endTurn },
              listeners: {},
              body: createElement("body", "body"),
              getElementById(id) {
                return Object.prototype.hasOwnProperty.call(this.elements, id) ? this.elements[id] : null;
              },
              createElement(tagName) { return createElement(tagName); },
              querySelector(selector) {
                if (selector === ".room-hero-head p") return null;
                if (selector === ".board-wrap .section-head") return boardHead;
                if (selector === ".board-footer") return footer;
                return null;
              },
              querySelectorAll() { return []; },
              addEventListener(type, handler) {
                if (!this.listeners[type]) this.listeners[type] = [];
                this.listeners[type].push(handler);
              },
            };
            document.body.classList = createClassList(document.body);

            const storageFactory = () => ({
              _store: {},
              getItem(key) { return Object.prototype.hasOwnProperty.call(this._store, key) ? this._store[key] : null; },
              setItem(key, value) { this._store[key] = String(value); },
              removeItem(key) { delete this._store[key]; },
            });

            const window = {
              location: { href: "http://example.test/", search: "", hash: "" },
              listeners: {},
              addEventListener(type, handler) {
                if (!this.listeners[type]) this.listeners[type] = [];
                this.listeners[type].push(handler);
              },
              requestAnimationFrame(callback) { return callback(); },
              cancelAnimationFrame() {},
              setInterval() { return 1; },
              clearInterval() {},
            };
            const history = { replaceState() {} };
            const localStorage = storageFactory();
            const sessionStorage = storageFactory();
            """
        )
        ctx.eval(app_source)
        ctx.eval(
            """
            globalThis.$ = function (id) {
              return document.getElementById(id);
            };
            ensureDynamicUiScaffolding();
            globalThis.toolbarMarkup = document.querySelector(".board-footer").lastInserted.innerHTML;
            globalThis.zoomMarkup = document.querySelector(".board-wrap .section-head").children[0].innerHTML;
            """
        )

        self.assertIn("&lt;&lt;", ctx.eval("globalThis.toolbarMarkup"))
        self.assertIn("II", ctx.eval("globalThis.toolbarMarkup"))
        self.assertIn("LIVE", ctx.eval("globalThis.toolbarMarkup"))
        self.assertIn("&gt;&gt;", ctx.eval("globalThis.toolbarMarkup"))
        self.assertIn("\u901f\u5ea6", ctx.eval("globalThis.toolbarMarkup"))
        self.assertIn("\u5168\u77e5", ctx.eval("globalThis.toolbarMarkup"))
        self.assertIn("-", ctx.eval("globalThis.zoomMarkup"))
        self.assertIn("1:1", ctx.eval("globalThis.zoomMarkup"))
        self.assertIn("+", ctx.eval("globalThis.zoomMarkup"))
if __name__ == "__main__":
    unittest.main()
