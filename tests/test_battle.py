from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wujiang.engine.core import ActionError, DamageContext, HealContext, Position, StatusEffect  # noqa: E402
from wujiang.heroes.first_five import GreatFireFuneralField, MedusaSummon  # noqa: E402
from wujiang.heroes.common import SlowStatus  # noqa: E402
from wujiang.heroes.next_five import BloodDanceLockStatus, ErasureCounterStatus, RockAbsorbFootprintStatus, SandstormWeatherEffect, StandardCloneSummon  # noqa: E402
from wujiang.heroes.excel_roster import EXCEL_HERO_REGISTRY, MountainGodCounterStatus  # noqa: E402
from wujiang.heroes.registry import HERO_REGISTRY, RANDOM_HERO_BATTLE_MODE, create_battle, create_classic_battle, create_hero, list_heroes  # noqa: E402
from wujiang.web.ai import attack_payloads_for_action, build_attack_candidates, difficulty_profile, reaction_payloads_for_option, skill_payloads_for_action  # noqa: E402


def primary_hero(battle, player_id: int):
    return next(unit for unit in battle.player_units(player_id) if not unit.is_summon)


def summon_by_code(battle, player_id: int, hero_code: str):
    return next(unit for unit in battle.player_units(player_id) if unit.is_summon and getattr(unit, "hero_code", "") == hero_code)


def skill_by_code(unit, skill_code: str):
    return unit.get_skill(skill_code)


def resolve_pending_chain(battle) -> None:
    while battle.pending_chain is not None:
        battle.perform_action({"type": "chain_skip"})


class BattleSmokeTests(unittest.TestCase):
    def test_excel_roster_registers_every_generated_hero(self) -> None:
        self.assertEqual(len(EXCEL_HERO_REGISTRY), 370)
        self.assertEqual(len(HERO_REGISTRY), 388)
        public_heroes = list_heroes()
        self.assertEqual(len(public_heroes), 59)
        public_codes = {str(hero["code"]) for hero in public_heroes}
        self.assertIn("excel_r030", public_codes)
        self.assertIn("excel_r031", public_codes)
        self.assertIn("excel_r032", public_codes)
        self.assertIn("excel_r033", public_codes)
        self.assertIn("excel_r034", public_codes)
        self.assertIn("excel_r035", public_codes)
        self.assertIn("excel_r037", public_codes)
        self.assertNotIn("excel_r038", public_codes)

        for code in EXCEL_HERO_REGISTRY:
            unit = create_hero(code, 1)
            self.assertEqual(unit.hero_code, code)
            self.assertTrue(unit.name)
            self.assertTrue(unit.raw_skill_text or unit.raw_trait_text)

    def test_excel_roster_maps_common_skills_and_traits_without_special_overrides(self) -> None:
        oberon = create_hero("excel_r020", 1)
        self.assertEqual(oberon.name, "妖精王奥尔贝隆")
        self.assertEqual([skill.code for skill in oberon.skills], ["light_wall", "judgment_stone", "world_seed", "heaven_lock"])
        self.assertTrue(oberon.has_flying)
        self.assertIn("世界之种连根", [trait.name for trait in oberon.traits])

        old_swordsman = create_hero("excel_r021", 1)
        self.assertIn("pierce", old_swordsman.skill_map())
        self.assertIn("protection", old_swordsman.skill_map())
        self.assertIn("ghost_step", old_swordsman.skill_map())
        self.assertIn("iaido_charge", old_swordsman.skill_map())
        self.assertIn("time_stop", old_swordsman.skill_map())
        self.assertIn("focus_reset", old_swordsman.skill_map())
        self.assertEqual(old_swordsman.attack_actions_per_turn(), 2)

        panther = create_hero("excel_r022", 1)
        self.assertIn("mimic_skill", panther.skill_map())
        self.assertIn("D。魔力点", [trait.name for trait in panther.traits])

    def test_oberon_judgment_stone_must_be_summoned_in_surrounding_empty_cell(self) -> None:
        battle = create_battle("excel_r020", "bard")
        oberon = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        oberon.position = Position(1, 1)
        bard.position = Position(6, 6)
        bard.max_health = 10
        bard.current_hp = 10
        oberon.current_mana = 5

        with self.assertRaises(ActionError):
            oberon.get_skill("judgment_stone").execute(battle, oberon, {"x": 6, "y": 6})
        battle.perform_action({"type": "skill", "unit_id": oberon.unit_id, "skill_code": "judgment_stone", "x": 2, "y": 1})
        resolve_pending_chain(battle)

        self.assertAlmostEqual(bard.current_hp, 10.0)
        self.assertTrue(any(getattr(unit, "hero_code", "") == "judgment_stone" for unit in battle.all_units()))
        self.assertTrue(oberon.alive)

    def test_oberon_world_seed_summons_numbered_roots_that_protect_seed(self) -> None:
        battle = create_battle("excel_r020", "bard")
        battle.width = 12
        battle.height = 12
        oberon = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        oberon.position = Position(0, 0)
        bard.position = Position(10, 10)

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": oberon.unit_id,
                "skill_code": "world_seed",
                "x": 3,
                "y": 3,
                "root_edges": ["north", "east", "south"],
                "root_numbers": [1, 2, 3],
            }
        )
        resolve_pending_chain(battle)

        seed = next(unit for unit in battle.player_units(1) if getattr(unit, "hero_code", "") == "world_seed")
        roots = [unit for unit in battle.player_units(1) if getattr(unit, "hero_code", "") == "world_root"]
        self.assertEqual(sorted(getattr(root, "root_number", None) for root in roots), [1, 2, 3])

        ctx = battle.resolve_damage(DamageContext(source=bard, target=seed, attack_power=0, is_skill=False, raw_damage=5, action_name="测试伤害"))
        self.assertTrue(ctx.cancelled)
        self.assertAlmostEqual(seed.current_hp, 1.0)

        seed.alive = False
        battle.cleanup_dead_units()

        self.assertFalse(any(getattr(unit, "hero_code", "") == "world_root" for unit in battle.all_units()))

    def test_perfect_swordsman_iaido_attack_moves_then_pierces_shield(self) -> None:
        battle = create_battle("excel_r021", "bard")
        swordsman = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        swordsman.position = Position(1, 1)
        bard.position = Position(3, 1)
        bard.max_health = 10
        bard.current_hp = 10
        bard.shields = 1

        battle.perform_action({"type": "skill", "unit_id": swordsman.unit_id, "skill_code": "iaido_charge"})
        self.assertTrue(swordsman.cannot_attack)
        self.assertTrue(swordsman.has_status("聚气。拔刀斩"))

        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})
        battle.perform_action(
            {
                "type": "attack",
                "unit_id": swordsman.unit_id,
                "target_unit_id": bard.unit_id,
                "x": 3,
                "y": 1,
                "move_x": 2,
                "move_y": 1,
            }
        )
        resolve_pending_chain(battle)

        self.assertEqual(swordsman.position, Position(2, 1))
        self.assertEqual(bard.shields, 0)
        self.assertAlmostEqual(bard.current_hp, 9.0)
        self.assertFalse(swordsman.has_status("聚气。拔刀斩"))

    def test_time_stop_inserts_one_temporary_turn_and_restores_order(self) -> None:
        battle = create_battle(["excel_r021", "bard"], "ellie")
        swordsman = next(unit for unit in battle.hero_units(1) if unit.hero_code == "excel_r021")
        bard = next(unit for unit in battle.hero_units(1) if unit.hero_code == "bard")
        ellie = primary_hero(battle, 2)
        swordsman.position = Position(2, 2)
        ellie.position = Position(3, 2)
        battle.configure_turn_order([ellie.unit_id, swordsman.unit_id, bard.unit_id], starting_index=0)
        battle.start_current_turn()

        battle.perform_action({"type": "skill", "unit_id": swordsman.unit_id, "skill_code": "time_stop"})

        self.assertEqual(battle.current_turn_unit().unit_id, swordsman.unit_id)
        self.assertEqual([battle.units[unit_id].hero_code for unit_id in battle.turn_order_unit_ids], ["ellie", "excel_r021", "excel_r021", "bard"])

        battle.perform_action({"type": "end_turn"})
        self.assertEqual(battle.current_turn_unit().unit_id, swordsman.unit_id)
        self.assertEqual([battle.units[unit_id].hero_code for unit_id in battle.turn_order_unit_ids], ["ellie", "excel_r021", "bard"])

        battle.perform_action({"type": "end_turn"})
        self.assertEqual(battle.current_turn_unit().unit_id, bard.unit_id)

    def test_d_panther_gains_capped_mana_points_from_allied_d_names(self) -> None:
        battle = create_battle(["excel_r022", "excel_r022"], "bard")
        panthers = [unit for unit in battle.hero_units(1) if unit.hero_code == "excel_r022"]
        panthers[0].mana_points = 2

        for trait in panthers[0].traits:
            trait.on_owner_turn_start(battle)

        self.assertEqual(panthers[0].mana_points, 3.0)

    def test_d_panther_mimics_visible_skill_with_own_resources(self) -> None:
        battle = create_battle("excel_r022", "bard")
        panther = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        panther.position = Position(1, 1)
        bard.position = Position(3, 1)
        panther.current_hp = 0.5
        panther.mana_points = 2

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": panther.unit_id,
                "skill_code": "mimic_skill",
                "target_unit_id": bard.unit_id,
                "mimic_skill_code": "heal",
                "copied_payload": {"target_unit_id": panther.unit_id},
            }
        )
        resolve_pending_chain(battle)

        self.assertAlmostEqual(panther.current_hp, 0.75)
        self.assertEqual(panther.mana_points, 1)
        self.assertEqual(panther.current_mana, panther.max_mana())

    def test_d_panther_ai_generates_copied_skill_payloads(self) -> None:
        from wujiang.web.ai import build_skill_candidates, difficulty_profile

        battle = create_battle("excel_r022", "bard")
        panther = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        panther.position = Position(1, 1)
        bard.position = Position(3, 1)
        panther.current_hp = 0.5
        panther.mana_points = 2
        action = next(entry for entry in battle.action_snapshot_for(panther)["actions"] if entry["code"] == "mimic_skill")

        candidates = build_skill_candidates(
            battle,
            panther,
            action,
            difficulty_profile("standard"),
            instant_only=False,
        )

        heal_candidates = [
            candidate
            for candidate in candidates
            if candidate.payload.get("mimic_skill_code") == "heal"
            and (candidate.payload.get("copied_payload") or {}).get("target_unit_id") == panther.unit_id
        ]
        self.assertTrue(heal_candidates)

    def test_fried_inspire_and_royal_soldier_use_answered_rules(self) -> None:
        battle = create_battle("excel_r024", "bard")
        fried = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        fried.position = Position(1, 1)
        bard.position = Position(5, 5)

        battle.perform_action({"type": "skill", "unit_id": fried.unit_id, "skill_code": "fried_inspire", "target_unit_id": fried.unit_id})

        self.assertEqual(fried.stat("speed"), 4)
        self.assertEqual(fried.normal_move_actions_per_turn(), 2)

        battle = create_battle("excel_r024", "bard")
        fried = primary_hero(battle, 1)
        fried.position = Position(1, 1)
        battle.perform_action({"type": "skill", "unit_id": fried.unit_id, "skill_code": "royal_soldier", "x": 2, "y": 1, "attack": 5, "defense": 4, "range": 1})
        soldier = next(unit for unit in battle.player_units(1) if getattr(unit, "hero_code", "") == "royal_soldier")

        self.assertEqual((soldier.stat("attack"), soldier.stat("defense"), soldier.stat("speed"), soldier.stat("attack_range")), (5.0, 4.0, 2.0, 1.0))
        self.assertTrue(soldier.has_block_counter)

        soldier.current_hp = 0.5
        ctx = battle.resolve_damage(DamageContext(source=fried, target=soldier, attack_power=fried.stat("attack"), is_skill=False, action_name="测试普攻", tags={"attack"}))
        self.assertTrue(ctx.cancelled)
        self.assertAlmostEqual(soldier.current_hp, 0.75)

    def test_fried_ai_generates_inspire_and_royal_soldier_payloads(self) -> None:
        from wujiang.web.ai import build_skill_candidates, difficulty_profile

        battle = create_battle(["excel_r024", "bard"], "ellie")
        fried = next(unit for unit in battle.hero_units(1) if unit.hero_code == "excel_r024")
        bard = next(unit for unit in battle.hero_units(1) if unit.hero_code == "bard")
        fried.position = Position(4, 4)
        bard.position = Position(5, 4)
        actions = {entry["code"]: entry for entry in battle.action_snapshot_for(fried)["actions"]}

        inspire = build_skill_candidates(battle, fried, actions["fried_inspire"], difficulty_profile("standard"), instant_only=False)
        soldiers = build_skill_candidates(battle, fried, actions["royal_soldier"], difficulty_profile("standard"), instant_only=False)

        self.assertTrue(any(candidate.payload.get("target_unit_id") == bard.unit_id for candidate in inspire))
        self.assertTrue(any(candidate.payload.get("attack") and candidate.payload.get("range") for candidate in soldiers))

    def test_agency_contract_attaches_then_cancels_with_damage_and_drain(self) -> None:
        battle = create_battle(["excel_r025", "bard"], "ellie")
        mubie = next(unit for unit in battle.hero_units(1) if unit.hero_code == "excel_r025")
        bard = next(unit for unit in battle.hero_units(1) if unit.hero_code == "bard")
        ellie = primary_hero(battle, 2)
        mubie.position = Position(1, 1)
        bard.position = Position(2, 1)
        ellie.position = Position(3, 1)
        ellie.max_health = 10
        ellie.current_hp = 10
        ellie.current_mana = 3

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": mubie.unit_id,
                "skill_code": "agency_contract",
                "target_unit_id": bard.unit_id,
                "stat_name": "attack",
                "copied_skill_code": "heal",
            }
        )

        self.assertTrue(mubie.cannot_be_targeted)
        self.assertEqual(mubie.position, bard.position)
        self.assertEqual(mubie.stat("attack"), 6)

        battle.perform_action({"type": "end_turn"})
        while battle.current_turn_unit().unit_id != mubie.unit_id:
            battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "skill", "unit_id": mubie.unit_id, "skill_code": "agency_contract"})

        self.assertFalse(mubie.cannot_be_targeted)
        self.assertEqual(mubie.stat("defense"), 5)
        self.assertAlmostEqual(ellie.current_hp, 9.0)
        self.assertEqual(ellie.current_mana, 2.0)

    def test_agency_ai_generates_contract_and_borrowed_skill_payloads(self) -> None:
        from wujiang.web.ai import build_skill_candidates, difficulty_profile

        battle = create_battle(["excel_r025", "bard"], "ellie")
        mubie = next(unit for unit in battle.hero_units(1) if unit.hero_code == "excel_r025")
        bard = next(unit for unit in battle.hero_units(1) if unit.hero_code == "bard")
        mubie.position = Position(2, 2)
        bard.position = Position(5, 5)
        actions = {entry["code"]: entry for entry in battle.action_snapshot_for(mubie)["actions"]}

        contract = build_skill_candidates(battle, mubie, actions["agency_contract"], difficulty_profile("standard"), instant_only=False)
        self.assertTrue(any(candidate.payload.get("target_unit_id") == bard.unit_id for candidate in contract))

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": mubie.unit_id,
                "skill_code": "agency_contract",
                "target_unit_id": bard.unit_id,
                "stat_name": "attack",
                "copied_skill_code": "heal",
            }
        )
        battle.perform_action({"type": "end_turn"})
        while battle.current_turn_unit().unit_id != mubie.unit_id:
            battle.perform_action({"type": "end_turn"})
        bard.current_hp = 0.5
        borrowed_action = next(entry for entry in battle.action_snapshot_for(mubie)["actions"] if entry["code"] == "agency_borrowed_skill")
        borrowed = build_skill_candidates(battle, mubie, borrowed_action, difficulty_profile("standard"), instant_only=False)

        self.assertTrue(any((candidate.payload.get("contract_payload") or {}).get("target_unit_id") == bard.unit_id for candidate in borrowed))

    def test_agency_borrowed_movement_skill_unavailable_while_attached(self) -> None:
        battle = create_battle(["excel_r025", "excel_r022"], "ellie")
        mubie = next(unit for unit in battle.hero_units(1) if unit.hero_code == "excel_r025")
        black_panther = next(unit for unit in battle.hero_units(1) if unit.hero_code == "excel_r022")
        mubie.position = Position(2, 2)
        black_panther.position = Position(3, 2)

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": mubie.unit_id,
                "skill_code": "agency_contract",
                "target_unit_id": black_panther.unit_id,
                "stat_name": "attack",
                "copied_skill_code": "fly_leap",
            }
        )
        borrowed_action = next(entry for entry in battle.action_snapshot_for(mubie)["actions"] if entry["code"] == "agency_borrowed_skill")

        self.assertFalse(borrowed_action["available"])

    def test_mubie_is_immune_to_mana_drain(self) -> None:
        battle = create_battle("ellie", "excel_r025")
        ellie = primary_hero(battle, 1)
        mubie = primary_hero(battle, 2)
        ellie.position = Position(1, 1)
        mubie.position = Position(2, 1)
        battle.configure_turn_order([ellie.unit_id, mubie.unit_id], starting_index=0)
        battle.start_current_turn()
        before_target = mubie.current_mana
        before_actor = ellie.current_mana

        battle.perform_action({"type": "skill", "unit_id": ellie.unit_id, "skill_code": "drain_mana", "target_unit_id": mubie.unit_id})

        self.assertEqual(mubie.current_mana, before_target)
        self.assertEqual(ellie.current_mana, before_actor)

    def test_wuchang_mist_can_fail_actions_and_mark_grants_immunity(self) -> None:
        battle = create_battle("excel_r027", "bard")
        wuchang = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        wuchang.position = Position(1, 1)
        bard.position = Position(3, 1)

        battle.perform_action({"type": "skill", "unit_id": wuchang.unit_id, "skill_code": "wuchang_mist"})
        battle.perform_action({"type": "end_turn"})
        with mock.patch("wujiang.heroes.excel_roster.random.random", return_value=0.1):
            battle.perform_action({"type": "attack", "unit_id": bard.unit_id, "target_unit_id": wuchang.unit_id, "x": 1, "y": 1})
            resolve_pending_chain(battle)

        self.assertAlmostEqual(wuchang.current_hp, 1.0)
        self.assertEqual(bard.attacks_used, 1)

        battle = create_battle("excel_r027", "bard")
        wuchang = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        wuchang.position = Position(1, 1)
        bard.position = Position(3, 1)
        battle.perform_action({"type": "skill", "unit_id": wuchang.unit_id, "skill_code": "migratory_bird_mark", "target_unit_id": bard.unit_id})
        resolve_pending_chain(battle)

        self.assertTrue(bard.has_status("侯鸟标记"))

    def test_wuchang_basic_attack_seals_attack_and_skills(self) -> None:
        battle = create_battle("excel_r027", "bard")
        wuchang = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        wuchang.position = Position(1, 1)
        bard.position = Position(3, 1)
        bard.max_health = 10
        bard.current_hp = 10

        battle.perform_action({"type": "attack", "unit_id": wuchang.unit_id, "target_unit_id": bard.unit_id, "x": 3, "y": 1})
        resolve_pending_chain(battle)

        self.assertTrue(bard.cannot_attack)
        self.assertTrue(bard.cannot_use_skills)
        self.assertTrue(bard.has_status("无常普攻封锁"))

    def test_excel_roster_uses_trait_footprint_for_generated_heroes(self) -> None:
        heracles = create_hero("excel_r038", 1)
        self.assertEqual((heracles.footprint_width, heracles.footprint_height), (2, 2))

        battle = create_battle("excel_r038", "bard")
        generated = primary_hero(battle, 1)
        self.assertEqual(len(battle.unit_cells(generated)), 4)

    def test_excel_roster_remote_pierce_uses_remote_line_selection(self) -> None:
        battle = create_battle("excel_r039", "bard")
        actor = primary_hero(battle, 1)
        target = primary_hero(battle, 2)
        actor.position = Position(1, 1)
        target.position = Position(5, 1)

        payload = {
            "type": "skill",
            "unit_id": actor.unit_id,
            "skill_code": "remote_pierce",
            "cells": [{"x": 4, "y": 1}, {"x": 5, "y": 1}],
        }
        battle.perform_action(payload)
        self.assertIsNotNone(battle.pending_chain)
        battle.perform_action({"type": "chain_skip"})

        self.assertLess(target.current_hp, target.max_health)

    def test_excel_roster_guardian_finale_uses_answered_special_rules(self) -> None:
        battle = create_battle("excel_r026", "bard")
        guardian = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        guardian.position = Position(1, 1)
        bard.position = Position(2, 1)

        battle.perform_action(
            {"type": "skill", "unit_id": guardian.unit_id, "skill_code": "guardian_finale"}
        )

        self.assertIsNotNone(guardian.get_status("终结"))
        self.assertEqual(guardian.stat("attack"), 6)
        self.assertEqual(guardian.stat("speed"), 6)
        self.assertEqual(skill_by_code(guardian, "dragon_breath").mana_cost_for_payload(battle, guardian, {}), 0)
        self.assertEqual(skill_by_code(guardian, "fly_leap").mana_cost_for_payload(battle, guardian, {}), 0)
        guardian.current_mana = 0
        ok, reason = skill_by_code(guardian, "dragon_breath").can_use(battle, guardian, {})
        self.assertTrue(ok, reason)
        public_costs = {skill["code"]: skill["mana_cost"] for skill in battle.action_snapshot_for(guardian)["skills"]}
        self.assertEqual(public_costs["dragon_breath"], 0)
        self.assertEqual(public_costs["fly_leap"], 0)

        guardian.current_hp = 0.5
        heal_ctx = battle.heal(HealContext(source=bard, target=guardian, amount=0.25, action_name="测试治疗"))
        self.assertTrue(heal_ctx.cancelled)
        self.assertEqual(guardian.current_hp, 0.5)

        bard.max_health = 2
        bard.current_hp = 2
        bard.shields = 1
        battle.perform_action({"type": "attack", "unit_id": guardian.unit_id, "target_unit_id": bard.unit_id})
        resolve_pending_chain(battle)

        self.assertEqual(bard.shields, 0)
        self.assertLess(bard.current_hp, 2)
        self.assertEqual(guardian.current_hp, 0.75)

        battle.perform_action({"type": "end_turn"})
        self.assertEqual(guardian.current_hp, 0.5)

    def test_excel_roster_large_pierce_uses_answered_big_variant(self) -> None:
        battle = create_battle("excel_r093", "bard")
        kaiser = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        kaiser.position = Position(1, 1)
        bard.position = Position(4, 1)

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": kaiser.unit_id,
                "skill_code": "large_pierce",
                "cells": [{"x": 2, "y": 1}, {"x": 3, "y": 1}, {"x": 4, "y": 1}],
            }
        )
        resolve_pending_chain(battle)

        self.assertEqual([skill.code for skill in kaiser.skills], ["large_pierce", "harden", "protection", "kaiser_fist"])
        self.assertAlmostEqual(bard.current_hp, 0.5)

    def test_excel_roster_kaiser_fist_gains_mana_when_no_damage(self) -> None:
        battle = create_battle("excel_r093", "bard")
        kaiser = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        kaiser.position = Position(1, 1)
        bard.position = Position(6, 1)
        kaiser.current_mana = 1
        bard.shields = 1

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": kaiser.unit_id,
                "skill_code": "kaiser_fist",
                "target_unit_id": bard.unit_id,
            }
        )
        resolve_pending_chain(battle)

        self.assertEqual(bard.shields, 0)
        self.assertEqual(bard.current_hp, 1)
        self.assertEqual(kaiser.current_mana, 3)
        self.assertEqual(skill_by_code(kaiser, "kaiser_fist").cooldown_remaining, 2)

    def test_excel_roster_water_ninja_summons_clone_after_basic_attack(self) -> None:
        battle = create_battle("excel_r352", "bard")
        ninja = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        ninja.position = Position(1, 1)
        bard.position = Position(2, 1)

        self.assertIn("水忍分身", [trait.name for trait in ninja.traits])
        battle.perform_action({"type": "attack", "unit_id": ninja.unit_id, "target_unit_id": bard.unit_id})
        resolve_pending_chain(battle)

        clones = [
            unit
            for unit in battle.all_units()
            if unit.is_clone and unit.summoner_id == ninja.unit_id
        ]
        self.assertEqual(len(clones), 1)
        clone = clones[0]
        self.assertEqual(clone.position, Position(0, 0))
        self.assertTrue(clone.is_summon)
        self.assertTrue(clone.cannot_attack)
        self.assertTrue(clone.cannot_use_skills)

    def test_excel_roster_snow_giant_avalanche_stops_next_turn(self) -> None:
        battle = create_battle("excel_r071", "bard")
        giant = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        giant.position = Position(1, 1)
        bard.position = Position(4, 1)

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": giant.unit_id,
                "skill_code": "snow_avalanche",
                "cells": [
                    {"x": 2, "y": 1},
                    {"x": 3, "y": 1},
                    {"x": 4, "y": 1},
                    {"x": 5, "y": 1},
                    {"x": 6, "y": 1},
                    {"x": 7, "y": 1},
                    {"x": 2, "y": 2},
                    {"x": 3, "y": 2},
                    {"x": 4, "y": 2},
                    {"x": 5, "y": 2},
                    {"x": 6, "y": 2},
                    {"x": 7, "y": 2},
                ],
            }
        )
        resolve_pending_chain(battle)

        self.assertAlmostEqual(bard.current_hp, 0.75)
        self.assertIsNotNone(bard.get_status("雪崩"))
        battle.perform_action({"type": "end_turn"})
        self.assertEqual(battle.current_turn_unit().unit_id, bard.unit_id)
        self.assertFalse(bard.can_take_turn_actions(battle))
        battle.perform_action({"type": "end_turn"})
        self.assertIsNone(bard.get_status("雪崩"))
        self.assertFalse(bard.cannot_move)
        self.assertFalse(bard.cannot_attack)
        self.assertFalse(bard.cannot_use_skills)

    def test_excel_roster_snow_giant_big_avalanche_sets_weather(self) -> None:
        battle = create_battle("excel_r071", "bard")
        giant = primary_hero(battle, 1)

        battle.perform_action({"type": "skill", "unit_id": giant.unit_id, "skill_code": "big_avalanche"})

        self.assertTrue(battle.has_weather("大雪崩"))
        weather = next(effect for effect in battle.field_effects if getattr(effect, "weather_name", "") == "大雪崩")
        self.assertEqual(weather.duration, 5)

    def test_excel_roster_magic_warrior_seal_lasts_until_enemy_turn_end(self) -> None:
        battle = create_battle("excel_r158", "bard")
        warrior = primary_hero(battle, 1)
        warrior.current_hp = 0.5
        warrior.current_mana = 1

        battle.perform_action({"type": "skill", "unit_id": warrior.unit_id, "skill_code": "martial_god_seal"})

        self.assertIsNotNone(warrior.get_status("魔界武神之印"))
        self.assertEqual(warrior.stat("attack"), 5)
        self.assertEqual(warrior.stat("defense"), 5)
        self.assertEqual(warrior.stat("speed"), 4)
        self.assertEqual(warrior.stat("attack_range"), 3)
        self.assertEqual(warrior.max_mana(), 5)
        self.assertEqual(warrior.current_mana, 3)
        self.assertEqual(warrior.current_hp, 1)

        battle.perform_action({"type": "end_turn"})
        self.assertIsNotNone(warrior.get_status("魔界武神之印"))
        battle.perform_action({"type": "end_turn"})

        self.assertIsNone(warrior.get_status("魔界武神之印"))
        self.assertEqual(warrior.stat("attack"), 3)
        self.assertEqual(warrior.max_mana(), 3)
        self.assertLessEqual(warrior.current_mana, warrior.max_mana())

    def test_excel_roster_magic_warrior_hell_slash_hits_line(self) -> None:
        battle = create_battle("excel_r158", "bard")
        warrior = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        warrior.position = Position(1, 1)
        bard.position = Position(5, 1)

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": warrior.unit_id,
                "skill_code": "hell_slash",
                "cells": [
                    {"x": 2, "y": 1},
                    {"x": 3, "y": 1},
                    {"x": 4, "y": 1},
                    {"x": 5, "y": 1},
                    {"x": 6, "y": 1},
                    {"x": 7, "y": 1},
                ],
            }
        )
        resolve_pending_chain(battle)

        self.assertAlmostEqual(bard.current_hp, 0.75)
        self.assertEqual(skill_by_code(warrior, "hell_slash").uses_this_battle, 1)

    def test_excel_roster_wetland_lord_heals_and_sets_weather(self) -> None:
        battle = create_battle("excel_r337", "bard")
        tina = primary_hero(battle, 1)
        tina.current_hp = 0.5

        battle.perform_action({"type": "skill", "unit_id": tina.unit_id, "skill_code": "heal", "target_unit_id": tina.unit_id})
        self.assertEqual(tina.current_hp, 0.75)

        battle.perform_action({"type": "skill", "unit_id": tina.unit_id, "skill_code": "wetland_grassland"})
        self.assertTrue(battle.has_weather("湿地草原"))
        self.assertIn("wetland_grassland", tina.skill_map())

    def test_excel_roster_demon_leader_pandemonium_grants_weather_speed(self) -> None:
        battle = create_battle("excel_r187", "bard")
        demon = primary_hero(battle, 1)

        self.assertEqual(demon.stat("speed"), 3)
        battle.perform_action({"type": "skill", "unit_id": demon.unit_id, "skill_code": "pandemonium"})

        self.assertTrue(battle.has_weather("万魔殿"))
        self.assertEqual(demon.stat("speed"), 6)
        self.assertIsNotNone(demon.get_status("万魔殿加速"))

    def test_excel_roster_honest_purify_reduces_enemy_mana(self) -> None:
        battle = create_battle("excel_r113", "bard")
        honest = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        honest.position = Position(1, 1)
        bard.position = Position(4, 1)
        bard.current_mana = 5

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": honest.unit_id,
                "skill_code": "purify_mana",
                "target_unit_id": bard.unit_id,
            }
        )
        resolve_pending_chain(battle)

        self.assertEqual(bard.current_mana, 0)
        self.assertEqual(skill_by_code(honest, "purify_mana").cooldown_remaining, 5)

    def test_excel_roster_honest_sacred_duel_pierces_shield_and_blocks_active_skill(self) -> None:
        battle = create_battle("excel_r113", "bard")
        honest = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        honest.position = Position(1, 1)
        bard.position = Position(4, 1)
        bard.shields = 1

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": honest.unit_id,
                "skill_code": "sacred_duel",
                "target_unit_id": bard.unit_id,
            }
        )
        resolve_pending_chain(battle)

        self.assertEqual(bard.shields, 0)
        self.assertIsNotNone(bard.get_status("神圣决斗"))
        self.assertTrue(bard.cannot_move)

        battle.perform_action({"type": "end_turn"})
        with self.assertRaises(ActionError):
            battle.perform_action({"type": "skill", "unit_id": bard.unit_id, "skill_code": "heal", "target_unit_id": bard.unit_id})

    def test_excel_roster_sola_illumination_pierces_dark_heroes_and_harvest_aura_heals_allies(self) -> None:
        battle = create_battle(["excel_r139", "bard"], "ellie")
        sola = next(unit for unit in battle.hero_units(1) if unit.hero_code == "excel_r139")
        ally = next(unit for unit in battle.hero_units(1) if unit.hero_code == "bard")
        ellie = primary_hero(battle, 2)
        sola.position = Position(1, 1)
        ally.position = Position(2, 1)
        ellie.position = Position(4, 1)
        ellie.max_health = 2
        ellie.current_hp = 2
        ellie.shields = 1

        battle.perform_action({"type": "skill", "unit_id": sola.unit_id, "skill_code": "illumination_light"})
        resolve_pending_chain(battle)

        self.assertEqual(ellie.shields, 0)
        self.assertEqual(ellie.current_hp, 1)
        self.assertIn("holy_wall", sola.skill_map())
        self.assertIn("illumination_light", sola.skill_map())

        ally.current_hp = 0.5
        ally.current_mana = 0
        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})

        self.assertEqual(battle.current_turn_unit().unit_id, ally.unit_id)
        self.assertEqual(ally.current_hp, 0.75)
        self.assertEqual(ally.current_mana, 1)

    def test_excel_roster_oboro_air_slash_moves_then_deals_piercing_defense_based_damage(self) -> None:
        battle = create_battle("excel_r136", "bard")
        oboro = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        oboro.position = Position(1, 1)
        bard.position = Position(7, 1)
        oboro.current_mana = 1.5
        bard.current_mana = 2
        bard.max_health = 2
        bard.current_hp = 2
        bard.shields = 1

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": oboro.unit_id,
                "skill_code": "true_blade_air_slash",
                "target_unit_id": bard.unit_id,
                "x": 6,
                "y": 1,
            }
        )
        resolve_pending_chain(battle)

        self.assertEqual(oboro.position, Position(6, 1))
        self.assertEqual(bard.shields, 0)
        self.assertEqual(bard.current_hp, 1)
        self.assertEqual(oboro.current_mana, 2)

    def test_excel_roster_oboro_meditate_gains_mana(self) -> None:
        battle = create_battle("excel_r136", "bard")
        oboro = primary_hero(battle, 1)
        oboro.current_mana = 0

        battle.perform_action({"type": "skill", "unit_id": oboro.unit_id, "skill_code": "oboro_meditate"})

        self.assertEqual(oboro.current_mana, 1.5)
        self.assertEqual(skill_by_code(oboro, "oboro_meditate").cooldown_remaining, 3)

    def test_excel_roster_jirobo_bird_burial_locks_movement_skills_even_when_damage_shielded(self) -> None:
        battle = create_battle("excel_r047", "excel_r136")
        jirobo = primary_hero(battle, 1)
        oboro = primary_hero(battle, 2)
        jirobo.position = Position(1, 1)
        oboro.position = Position(4, 1)
        oboro.shields = 1
        cells = [{"x": x, "y": y} for y in (1, 2, 3) for x in range(2, 8)]

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": jirobo.unit_id,
                "skill_code": "hundred_bird_burial",
                "cells": cells,
            }
        )
        resolve_pending_chain(battle)

        self.assertEqual(oboro.shields, 0)
        self.assertEqual(oboro.current_hp, 1)
        self.assertIsNotNone(oboro.get_status("百鸟葬禁位移"))

        battle.perform_action({"type": "end_turn"})
        with self.assertRaisesRegex(ActionError, "禁位移"):
            battle.perform_action(
                {
                    "type": "skill",
                    "unit_id": oboro.unit_id,
                    "skill_code": "true_blade_air_slash",
                    "target_unit_id": jirobo.unit_id,
                    "x": 2,
                    "y": 1,
                }
            )

    def test_excel_roster_jirobo_can_follow_step_after_basic_attack_and_keeps_defense_buff(self) -> None:
        battle = create_battle("excel_r047", "bard")
        jirobo = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        jirobo.position = Position(1, 1)
        bard.position = Position(2, 1)

        battle.perform_action({"type": "attack", "unit_id": jirobo.unit_id, "target_unit_id": bard.unit_id, "x": 2, "y": 1})
        resolve_pending_chain(battle)

        self.assertIsNotNone(jirobo.get_status("次郎坊攻击后守备"))
        self.assertEqual(jirobo.stat("defense"), 4)

        battle.perform_action({"type": "skill", "unit_id": jirobo.unit_id, "skill_code": "jirobo_follow_step", "x": 1, "y": 3})

        self.assertEqual(jirobo.position, Position(1, 3))
        self.assertEqual(jirobo.stat("defense"), 4)
        with self.assertRaises(ActionError):
            battle.perform_action({"type": "skill", "unit_id": jirobo.unit_id, "skill_code": "jirobo_follow_step", "x": 1, "y": 4})

    def test_excel_roster_undead_boy_devours_and_survives_lethal_damage_above_half(self) -> None:
        battle = create_battle("excel_r137", "bard")
        undead = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        undead.position = Position(1, 1)
        bard.position = Position(2, 1)
        undead.current_hp = 0.5
        bard.current_hp = 1
        bard.shields = 1

        battle.perform_action({"type": "skill", "unit_id": undead.unit_id, "skill_code": "undead_boy_devour", "target_unit_id": bard.unit_id})
        resolve_pending_chain(battle)

        self.assertEqual(bard.shields, 0)
        self.assertEqual(bard.current_hp, 0.5)
        self.assertEqual(undead.current_hp, 1)
        self.assertEqual(skill_by_code(undead, "undead_boy_devour").cooldown_remaining, 2)

        undead.current_hp = 0.5
        battle.resolve_damage(DamageContext(source=bard, target=undead, attack_power=99, is_skill=False, action_name="测试致命伤害"))

        self.assertTrue(undead.alive)
        self.assertEqual(undead.current_hp, 0.25)

    def test_excel_roster_electric_wind_blocks_skills_and_reduces_speed(self) -> None:
        battle = create_battle("excel_r166", "bard")
        electric = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        electric.position = Position(3, 3)
        bard.position = Position(3, 1)
        while battle.current_turn_unit().unit_id != electric.unit_id:
            battle.perform_action({"type": "end_turn"})
        cells = [{"x": x, "y": y} for y in (1, 2) for x in (2, 3, 4)]

        battle.perform_action({"type": "skill", "unit_id": electric.unit_id, "skill_code": "electric_wind", "cells": cells})
        resolve_pending_chain(battle)

        self.assertIsNotNone(bard.get_status("电风"))
        self.assertEqual(bard.stat("speed"), 1)
        battle.perform_action({"type": "end_turn"})
        with self.assertRaisesRegex(ActionError, "电风"):
            battle.perform_action({"type": "skill", "unit_id": bard.unit_id, "skill_code": "heal", "target_unit_id": bard.unit_id})

    def test_excel_roster_electric_person_auto_uses_wind_on_own_turn_start(self) -> None:
        battle = create_battle("excel_r166", "bard")
        electric = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        electric.position = Position(3, 3)
        bard.position = Position(4, 3)
        bard.statuses.clear()

        battle.perform_action({"type": "end_turn"})
        while battle.current_turn_unit().unit_id != electric.unit_id:
            battle.perform_action({"type": "end_turn"})

        self.assertIsNotNone(bard.get_status("电风"))

    def test_excel_roster_light_road_guardian_sets_sky_sanctuary_and_blasts_line(self) -> None:
        battle = create_battle("excel_r188", "bard")
        guardian = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        guardian.position = Position(1, 1)
        bard.position = Position(4, 1)
        bard.max_health = 2
        bard.current_hp = 2

        ok, reason = skill_by_code(guardian, "vitality_blast").can_use(battle, guardian, {})
        self.assertFalse(ok)
        self.assertIn("天空的圣域", reason)

        battle.perform_action({"type": "skill", "unit_id": guardian.unit_id, "skill_code": "sky_sanctuary"})
        self.assertTrue(battle.has_weather("天空的圣域"))

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": guardian.unit_id,
                "skill_code": "vitality_blast",
                "cells": [{"x": x, "y": 1} for x in range(2, 7)],
            }
        )
        resolve_pending_chain(battle)

        self.assertLess(bard.current_hp, 2)
        self.assertEqual(skill_by_code(guardian, "vitality_blast").cooldown_remaining, 2)

    def test_excel_roster_heaven_punishment_pierces_and_disables_selected_active_skill(self) -> None:
        battle = create_battle("excel_r070", "bard")
        crab = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        crab.position = Position(1, 1)
        bard.position = Position(4, 1)
        bard.shields = 1
        cells = [{"x": x, "y": y} for y in range(0, 5) for x in range(2, 7)]

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": crab.unit_id,
                "skill_code": "heaven_punishment",
                "cells": cells,
                "target_unit_id": bard.unit_id,
                "disabled_skill_code": "heal",
            }
        )
        resolve_pending_chain(battle)

        self.assertEqual(bard.shields, 0)
        self.assertIsNotNone(bard.get_status("技能封印：回血"))
        battle.perform_action({"type": "end_turn"})
        with self.assertRaisesRegex(ActionError, "封印"):
            battle.perform_action({"type": "skill", "unit_id": bard.unit_id, "skill_code": "heal", "target_unit_id": bard.unit_id})

    def test_heaven_punishment_preview_excludes_units_without_active_skills(self) -> None:
        battle = create_battle("excel_r070", ["bard", "fire_funeral"])
        crab = primary_hero(battle, 1)
        bard = next(unit for unit in battle.hero_units(2) if unit.hero_code == "bard")
        fire_funeral = next(unit for unit in battle.hero_units(2) if unit.hero_code == "fire_funeral")
        crab.position = Position(1, 1)
        bard.position = Position(3, 1)
        fire_funeral.position = Position(4, 1)
        bard.skills = []

        preview = skill_by_code(crab, "heaven_punishment").preview(battle, crab)

        self.assertNotIn(bard.unit_id, preview["target_unit_ids"])
        self.assertIn(fire_funeral.unit_id, preview["target_unit_ids"])

    def test_heaven_punishment_misses_if_queued_area_loses_valid_targets(self) -> None:
        battle = create_battle("excel_r070", "excel_r023")
        crab = primary_hero(battle, 1)
        frey = primary_hero(battle, 2)
        crab.position = Position(1, 1)
        frey.position = Position(4, 1)
        cells = [{"x": x, "y": y} for y in range(0, 5) for x in range(2, 7)]

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": crab.unit_id,
                "skill_code": "heaven_punishment",
                "cells": cells,
                "target_unit_id": frey.unit_id,
                "disabled_skill_code": "drain_mana",
            }
        )
        self.assertIsNotNone(battle.pending_chain)
        frey.position = Position(7, 5)

        resolve_pending_chain(battle)

        self.assertIsNone(frey.get_status("技能封印：吸魔"))
        self.assertTrue(any("天罚" in log and "没有可封印" in log for log in battle.logs))

    def test_ai_attack_candidates_respect_lina_attack_lock(self) -> None:
        battle = create_battle(["bard", "ellie"], "undead_king_lina")
        bard = next(unit for unit in battle.hero_units(1) if unit.hero_code == "bard")
        ellie = next(unit for unit in battle.hero_units(1) if unit.hero_code == "ellie")
        lina = primary_hero(battle, 2)
        lina.position = Position(3, 3)
        bard.position = Position(4, 3)
        ellie.position = Position(3, 4)
        for trait in lina.traits:
            if hasattr(trait, "locked_target_id"):
                trait.locked_target_id = bard.unit_id

        attack_action = next(action for action in battle.action_snapshot_for(lina)["actions"] if action.get("kind") == "attack")
        candidates = build_attack_candidates(battle, lina, attack_action, difficulty_profile("standard"))
        target_ids = {str(candidate.payload.get("target_unit_id")) for candidate in candidates}

        self.assertIn(bard.unit_id, target_ids)
        self.assertNotIn(ellie.unit_id, target_ids)

    def test_lina_attack_lock_tracks_mounted_effect_recipient(self) -> None:
        battle = create_battle("excel_r032", "undead_king_lina")
        aaron = primary_hero(battle, 1)
        unicorn = summon_by_code(battle, 1, "great_unicorn")
        lina = primary_hero(battle, 2)
        aaron.position = Position(1, 3)
        unicorn.position = Position(1, 3)
        lina.position = Position(3, 3)
        while battle.current_turn_unit().unit_id != lina.unit_id:
            battle.perform_action({"type": "end_turn"})

        battle.perform_action(
            {
                "type": "attack",
                "unit_id": lina.unit_id,
                "attack_variant": "default",
                "target_unit_id": aaron.unit_id,
                "x": 1,
                "y": 3,
            }
        )

        lock_trait = next(trait for trait in lina.traits if hasattr(trait, "locked_target_id"))
        self.assertEqual(lock_trait.locked_target_id, unicorn.unit_id)

    def test_excel_roster_noise_interference_destroys_clones_and_takes_summon_control(self) -> None:
        battle = create_battle("excel_r094", "bard")
        noise = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        noise.position = Position(1, 1)
        bard.position = Position(6, 6)
        clone = StandardCloneSummon(2, bard)
        summon = MedusaSummon(2)
        battle.add_unit(clone, Position(3, 3))
        battle.add_unit(summon, Position(4, 3))
        skill = skill_by_code(noise, "interference")
        cells = [
            cell.to_dict()
            for cell in next(
                pattern
                for pattern in skill.patterns(battle, noise)
                if clone.position in pattern and summon.position in pattern
            )
        ]

        battle.perform_action({"type": "skill", "unit_id": noise.unit_id, "skill_code": "interference", "cells": cells})
        resolve_pending_chain(battle)

        self.assertFalse(clone.alive)
        self.assertNotIn(clone.unit_id, battle.units)
        self.assertEqual(summon.player_id, noise.player_id)
        self.assertEqual(summon.summoner_id, noise.unit_id)

    def test_excel_roster_noise_wave_pierces_and_blocks_movement_skills(self) -> None:
        battle = create_battle("excel_r094", "excel_r136")
        noise = primary_hero(battle, 1)
        oboro = primary_hero(battle, 2)
        noise.position = Position(1, 1)
        oboro.position = Position(3, 1)
        cells = [{"x": x, "y": y} for y in range(0, 3) for x in range(2, 5)]
        oboro.shields = 1

        battle.perform_action({"type": "skill", "unit_id": noise.unit_id, "skill_code": "noise_wave", "cells": cells})
        resolve_pending_chain(battle)

        self.assertEqual(oboro.shields, 0)
        self.assertIsNotNone(oboro.get_status("乱音电波"))
        self.assertEqual(oboro.stat("speed"), 1)
        battle.perform_action({"type": "end_turn"})
        with self.assertRaisesRegex(ActionError, "乱音电波"):
            battle.perform_action(
                {
                    "type": "skill",
                    "unit_id": oboro.unit_id,
                    "skill_code": "true_blade_air_slash",
                    "target_unit_id": noise.unit_id,
                    "x": 2,
                    "y": 1,
                }
            )

    def test_excel_roster_florenza_shadow_and_basic_attack_followup(self) -> None:
        battle = create_battle("excel_r326", "bard")
        florenza = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        florenza.position = Position(1, 1)
        bard.position = Position(2, 1)
        bard.shields = 1

        battle.perform_action({"type": "skill", "unit_id": florenza.unit_id, "skill_code": "vain_giant_shadow", "target_unit_id": bard.unit_id})
        resolve_pending_chain(battle)

        self.assertEqual(bard.shields, 0)
        self.assertIsNotNone(bard.get_status("虚荣巨影"))
        self.assertTrue(bard.cannot_attack)
        self.assertEqual(bard.stat("attack"), bard.base_stats.attack + 2)

        battle = create_battle("excel_r326", "bard")
        florenza = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        florenza.position = Position(1, 1)
        bard.position = Position(2, 1)
        florenza.current_mana = 0
        bard.current_mana = 2
        bard.max_health = 2
        bard.current_hp = 2
        bard.shields = 1

        battle.perform_action({"type": "attack", "unit_id": florenza.unit_id, "target_unit_id": bard.unit_id, "x": 2, "y": 1})
        resolve_pending_chain(battle)

        self.assertEqual(bard.shields, 0)
        self.assertIsNotNone(bard.get_status("弗伦萨普攻弱化"))
        self.assertEqual(bard.current_mana, 1)
        self.assertEqual(florenza.current_mana, 1)
        self.assertLess(bard.stat("attack"), 4)

    def test_excel_roster_punisher_sky_sanctuary_heal_banish_and_judgment(self) -> None:
        battle = create_battle("excel_r036", "bard")
        punisher = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        punisher.position = Position(1, 1)
        bard.position = Position(3, 1)

        self.assertTrue(battle.unit_in_weather("天空圣域", punisher))
        self.assertTrue(battle.unit_in_weather("天空圣域", bard))

        punisher.current_hp = 0.5
        punisher.current_mana = 5
        battle.perform_action({"type": "skill", "unit_id": punisher.unit_id, "skill_code": "punisher_heal", "target_unit_id": punisher.unit_id})

        self.assertEqual(punisher.current_hp, 0.75)
        self.assertEqual(punisher.current_mana, 5)

        bard.shields = 1
        battle.perform_action({"type": "skill", "unit_id": punisher.unit_id, "skill_code": "sanctuary_banish"})

        self.assertEqual(bard.shields, 0)
        self.assertIsNotNone(bard.get_status("圣殿放逐"))
        self.assertTrue(bard.cannot_attack)
        ok, reason = bard.get_status("圣殿放逐").blocks_skill_use(battle, bard, skill_by_code(bard, "heal"))
        self.assertTrue(ok)
        self.assertIn("圣殿放逐", reason)

        bard.max_health = 3
        bard.current_hp = 3
        battle.perform_action({"type": "skill", "unit_id": punisher.unit_id, "skill_code": "sanctuary_judgment"})

        self.assertLess(bard.current_hp, 3)
        self.assertEqual(skill_by_code(punisher, "sanctuary_judgment").uses_this_battle, 1)

    def test_excel_roster_remi_chaos_bat_and_undying_mana_cost(self) -> None:
        battle = create_battle("excel_r056", "bard")
        remi = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        remi.position = Position(1, 1)
        bard.position = Position(1, 5)
        bard.max_health = 2
        bard.current_hp = 2

        battle.perform_action({"type": "skill", "unit_id": remi.unit_id, "skill_code": "remi_chaos", "x": 1, "y": 4})
        resolve_pending_chain(battle)

        self.assertEqual(remi.position, Position(1, 4))
        self.assertLess(bard.current_hp, 2)

        battle = create_battle("excel_r056", "bard")
        remi = primary_hero(battle, 1)
        remi.position = Position(1, 1)
        battle.perform_action({"type": "skill", "unit_id": remi.unit_id, "skill_code": "summon_remi_bat", "x": 2, "y": 1})
        bat = summon_by_code(battle, 1, "remi_bat")

        self.assertEqual(bat.stat("attack"), 3)
        self.assertTrue(bat.has_flying)
        self.assertTrue(bat.turn_ready)
        self.assertTrue(bat.can_take_turn_actions(battle))

        battle = create_battle("excel_r056", "bard")
        remi = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        remi.current_mana = 2
        battle.resolve_damage(DamageContext(source=bard, target=remi, attack_power=99, is_skill=False, action_name="测试致命伤害"))

        self.assertTrue(remi.alive)
        self.assertEqual(remi.current_hp, 0.25)
        self.assertEqual(remi.current_mana, 1)

        battle.resolve_damage(DamageContext(source=bard, target=remi, attack_power=99, is_skill=False, action_name="测试致命伤害"))

        self.assertFalse(remi.alive)
        self.assertNotIn(remi.unit_id, battle.units)

    def test_excel_roster_kiku_sun_slash_and_death_grants_extra_fixed_attack(self) -> None:
        battle = create_battle("excel_r379", "bard")
        kiku = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        kiku.position = Position(1, 1)
        bard.position = Position(2, 1)
        bard.shields = 1

        battle.perform_action({"type": "skill", "unit_id": kiku.unit_id, "skill_code": "sun_slash", "target_unit_id": bard.unit_id})
        resolve_pending_chain(battle)

        self.assertEqual(bard.shields, 0)
        self.assertIsNotNone(bard.get_status("被动封锁"))
        self.assertLess(bard.current_hp, 1)

        battle = create_battle(["excel_r379", "bard"], ["bard"])
        kiku = next(unit for unit in battle.hero_units(1) if unit.hero_code == "excel_r379")
        ally = next(unit for unit in battle.hero_units(1) if unit.hero_code == "bard")
        enemy = primary_hero(battle, 2)
        kiku.position = Position(1, 1)
        enemy.position = Position(3, 1)
        ally.position = Position(2, 1)

        battle.resolve_damage(DamageContext(source=enemy, target=kiku, attack_power=99, is_skill=False, action_name="测试破坏"))

        self.assertNotIn(kiku.unit_id, battle.units)
        self.assertIsNotNone(ally.get_status("菊之遗击"))
        self.assertEqual(ally.attack_actions_per_turn(), 2)
        actions = battle.action_snapshot_for(ally)["actions"]
        self.assertIn("kiku_legacy_attack", [action["code"] for action in actions])

        while battle.current_turn_unit() is None or battle.current_turn_unit().unit_id != ally.unit_id:
            battle.perform_action({"type": "end_turn"})

        enemy.max_health = 3
        enemy.current_hp = 3
        battle.perform_action({"type": "attack", "unit_id": ally.unit_id, "target_unit_id": enemy.unit_id, "x": 3, "y": 1})
        resolve_pending_chain(battle)

        with self.assertRaisesRegex(ActionError, "只剩"):
            battle.perform_action({"type": "attack", "unit_id": ally.unit_id, "target_unit_id": enemy.unit_id, "x": 3, "y": 1})

        battle.perform_action(
            {
                "type": "attack",
                "unit_id": ally.unit_id,
                "target_unit_id": enemy.unit_id,
                "x": 3,
                "y": 1,
                "attack_variant": "kiku_legacy",
                "attack_name": "菊之遗击",
            }
        )
        resolve_pending_chain(battle)

        self.assertLess(enemy.current_hp, 3)
        self.assertEqual(battle.basic_attack_preview_power(ally, {"attack_variant": "kiku_legacy"}), 4)

    def test_kiku_death_grants_legacy_status_to_existing_allied_clones(self) -> None:
        battle = create_battle(["excel_r379", "bard"], ["bard"])
        kiku = next(unit for unit in battle.hero_units(1) if unit.hero_code == "excel_r379")
        ally = next(unit for unit in battle.hero_units(1) if unit.hero_code == "bard")
        enemy = primary_hero(battle, 2)
        kiku.position = Position(1, 1)
        ally.position = Position(2, 1)
        enemy.position = Position(4, 1)
        clone = StandardCloneSummon(1, ally)
        battle.add_unit(clone, Position(3, 1))

        battle.resolve_damage(DamageContext(source=enemy, target=kiku, attack_power=99, is_skill=False, action_name="测试破坏"))

        self.assertIsNotNone(clone.get_status("菊之遗击"))

    def test_excel_roster_frey_caps_damage_and_all_skills_pierce(self) -> None:
        battle = create_battle("excel_r023", "bard")
        frey = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        frey.position = Position(1, 1)
        bard.position = Position(3, 1)
        frey.current_hp = 1

        battle.resolve_damage(DamageContext(source=bard, target=frey, attack_power=99, is_skill=False, action_name="测试大伤害"))

        self.assertTrue(frey.alive)
        self.assertEqual(frey.current_hp, 0.75)

        bard.max_health = 2
        bard.current_hp = 2
        bard.shields = 1
        battle.perform_action(
            {
                "type": "skill",
                "unit_id": frey.unit_id,
                "skill_code": "frey_quick_flash",
                "target_unit_id": bard.unit_id,
                "x": 2,
                "y": 1,
            }
        )
        resolve_pending_chain(battle)

        self.assertEqual(frey.position, Position(2, 1))
        self.assertEqual(bard.shields, 0)
        self.assertLess(bard.current_hp, 2)

        battle = create_battle("excel_r023", "bard")
        frey = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        frey.position = Position(1, 1)
        bard.position = Position(2, 2)
        bard.max_health = 2
        bard.current_hp = 2
        bard.shields = 1

        battle.perform_action({"type": "skill", "unit_id": frey.unit_id, "skill_code": "frey_lion_spear"})

        self.assertEqual(bard.shields, 0)
        self.assertLess(bard.current_hp, 2)

        battle = create_battle("excel_r023", "bard")
        frey = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        frey.position = Position(1, 1)
        bard.position = Position(3, 1)
        bard.shields = 1

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": frey.unit_id,
                "skill_code": "frey_god_stab",
                "cells": [{"x": x, "y": 1} for x in range(2, 6)],
            }
        )
        resolve_pending_chain(battle)

        self.assertEqual(bard.shields, 0)
        self.assertLess(bard.current_hp, 1)

    def test_excel_roster_zero_dash_damages_each_passed_unit_and_gains_mana(self) -> None:
        battle = create_battle("excel_r118", "bard")
        battle.width = 10
        battle.height = 10
        zero = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        zero.position = Position(1, 1)
        bard.position = Position(4, 1)
        zero.current_mana = 0
        bard.max_health = 2
        bard.current_hp = 2

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": zero.unit_id,
                "skill_code": "zero_dash",
                "x": 9,
                "y": 1,
                "direction": {"dx": 1, "dy": 0},
            }
        )
        resolve_pending_chain(battle)

        self.assertEqual(zero.position, Position(9, 1))
        self.assertLess(bard.current_hp, 2)
        self.assertEqual(zero.current_mana, 0.5)

    def test_zero_normal_move_can_reenter_same_unit_for_repeated_damage_and_mana(self) -> None:
        battle = create_battle("excel_r118", "bard")
        zero = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        zero.position = Position(1, 1)
        bard.position = Position(2, 1)
        zero.current_mana = 0
        bard.max_health = 10
        bard.current_hp = 10

        battle.perform_action(
            {
                "type": "move",
                "unit_id": zero.unit_id,
                "x": 1,
                "y": 2,
                "path": [
                    {"x": 2, "y": 1},
                    {"x": 1, "y": 1},
                    {"x": 2, "y": 1},
                    {"x": 1, "y": 1},
                    {"x": 1, "y": 2},
                ],
            }
        )

        self.assertEqual(zero.position, Position(1, 2))
        self.assertEqual(zero.current_mana, 1.0)
        self.assertLess(bard.current_hp, 10)

    def test_zero_continuous_passage_through_multicell_unit_counts_once_until_reentry(self) -> None:
        battle = create_battle("excel_r118", "bard")
        zero = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        zero.position = Position(1, 1)
        bard.position = Position(2, 1)
        bard.set_footprint_offsets([(0, 0), (1, 0)])
        zero.current_mana = 0
        bard.max_health = 10
        bard.current_hp = 10

        battle.perform_action(
            {
                "type": "move",
                "unit_id": zero.unit_id,
                "x": 1,
                "y": 2,
                "path": [
                    {"x": 2, "y": 1},
                    {"x": 3, "y": 1},
                    {"x": 4, "y": 1},
                    {"x": 3, "y": 1},
                    {"x": 2, "y": 1},
                    {"x": 1, "y": 2},
                ],
            }
        )

        self.assertEqual(zero.current_mana, 1.0)

    def test_judgment_stone_can_move_onto_enemy_and_explode(self) -> None:
        battle = create_battle("excel_r020", "bard")
        oberon = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        oberon.position = Position(1, 1)
        bard.position = Position(4, 1)
        bard.max_health = 10
        bard.current_hp = 10
        oberon.current_mana = 5
        battle.perform_action({"type": "skill", "unit_id": oberon.unit_id, "skill_code": "judgment_stone", "x": 2, "y": 1})
        resolve_pending_chain(battle)
        stone = next(unit for unit in battle.player_units(1) if getattr(unit, "hero_code", "") == "judgment_stone")

        battle.move_unit(stone, bard.position)

        self.assertEqual(bard.current_hp, 5)
        self.assertFalse(stone.alive)

    def test_judgment_stone_first_cast_each_turn_is_free_then_costs_half_mana(self) -> None:
        battle = create_battle("excel_r020", "bard")
        oberon = primary_hero(battle, 1)
        oberon.position = Position(1, 1)
        oberon.current_mana = 5

        battle.perform_action({"type": "skill", "unit_id": oberon.unit_id, "skill_code": "judgment_stone", "x": 2, "y": 1})
        resolve_pending_chain(battle)
        self.assertEqual(oberon.current_mana, 5)
        battle.perform_action({"type": "skill", "unit_id": oberon.unit_id, "skill_code": "judgment_stone", "x": 1, "y": 2})
        resolve_pending_chain(battle)

        self.assertEqual(oberon.current_mana, 4.5)

    def test_excel_roster_fuma_pursuit_trap_random_mana_and_shuriken(self) -> None:
        battle = create_battle("excel_r123", "bard")
        fuma = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        fuma.position = Position(1, 1)
        bard.position = Position(3, 1)
        bard.max_health = 2
        bard.current_hp = 2
        bard.shields = 1

        with mock.patch("wujiang.heroes.excel_roster.random.random", return_value=0.9):
            battle.perform_action(
                {
                    "type": "skill",
                    "unit_id": fuma.unit_id,
                    "skill_code": "fuma_pursuit",
                    "x": 6,
                    "y": 1,
                    "direction": {"dx": 1, "dy": 0},
                }
            )
        resolve_pending_chain(battle)

        self.assertEqual(fuma.position, Position(6, 1))
        self.assertEqual(bard.shields, 0)
        self.assertLess(bard.current_hp, 2)

        battle = create_battle("excel_r123", "bard")
        fuma = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        fuma.position = Position(1, 1)
        bard.position = Position(2, 1)
        bard.max_health = 2
        bard.current_hp = 2
        fuma.current_mana = 1

        with mock.patch("wujiang.heroes.excel_roster.random.random", return_value=0.1):
            battle.perform_action({"type": "skill", "unit_id": fuma.unit_id, "skill_code": "fuma_trap", "x": 2, "y": 1})
        resolve_pending_chain(battle)

        self.assertEqual(fuma.current_mana, 1.5)
        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})

        self.assertLess(bard.current_hp, 2)

        battle = create_battle("excel_r123", "bard")
        fuma = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        fuma.position = Position(1, 1)
        bard.position = Position(4, 1)
        bard.max_health = 2
        bard.current_hp = 2

        with mock.patch("wujiang.heroes.excel_roster.random.random", return_value=0.9):
            battle.perform_action(
                {
                    "type": "skill",
                    "unit_id": fuma.unit_id,
                    "skill_code": "fuma_shuriken",
                    "cells": [{"x": x, "y": 1} for x in range(2, 5)],
                }
            )
        resolve_pending_chain(battle)

        self.assertLess(bard.current_hp, 2)

    def test_excel_roster_nian_roar_and_jade_flash_use_piercing_followups(self) -> None:
        battle = create_battle("excel_r059", "bard")
        nian = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        nian.position = Position(1, 1)
        bard.position = Position(3, 1)
        bard.max_health = 2
        bard.current_hp = 2
        bard.shields = 1

        battle.perform_action({"type": "skill", "unit_id": nian.unit_id, "skill_code": "nian_roar", "target_unit_id": bard.unit_id})
        resolve_pending_chain(battle)

        self.assertEqual(bard.shields, 0)
        self.assertLess(bard.current_hp, 2)
        self.assertIsNotNone(bard.get_status("怒吼"))

        ellie = create_hero("ellie", 2)
        battle.add_unit(ellie, Position(4, 1))
        ellie.max_health = 2
        ellie.current_hp = 2
        battle.resolve_damage(DamageContext(source=bard, target=ellie, attack_power=10, is_skill=True, action_name="测试伤害", tags={"skill"}))
        self.assertEqual(ellie.current_hp, 2)

        bard.current_hp = 1
        battle.perform_action(
            {
                "type": "skill",
                "unit_id": nian.unit_id,
                "skill_code": "nian_jade_flash",
                "cells": [{"x": x, "y": y} for x in range(2, 5) for y in range(0, 3)],
            }
        )
        resolve_pending_chain(battle)

        self.assertIsNotNone(bard.get_status("碧玉闪光"))
        heal_ctx = battle.heal(HealContext(source=nian, target=bard, amount=0.25, action_name="测试治疗"))
        self.assertTrue(heal_ctx.cancelled)

    def test_excel_roster_black_cat_form_and_paw_special_rules(self) -> None:
        battle = create_battle("excel_r066", "bard")
        cat = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        cat.position = Position(1, 1)
        bard.position = Position(2, 1)
        bard.current_mana = 2
        cat.current_mana = 0

        battle.perform_action({"type": "skill", "unit_id": cat.unit_id, "skill_code": "black_cat_form"})

        self.assertEqual(cat.stat("attack"), 1)
        self.assertEqual(cat.stat("defense"), 1)
        self.assertTrue(cat.magic_immunity)

        battle.perform_action({"type": "skill", "unit_id": cat.unit_id, "skill_code": "black_cat_paw"})
        resolve_pending_chain(battle)

        self.assertEqual(bard.current_mana, 1)
        self.assertEqual(cat.current_mana, 1)

        cat.move_used = True
        cat.moved_this_turn = True
        cat.normal_move_steps_used = 1
        cat.normal_move_actions_used = 1
        battle.perform_action({"type": "attack", "unit_id": cat.unit_id, "target_unit_id": bard.unit_id})
        resolve_pending_chain(battle)

        self.assertFalse(cat.move_used)
        self.assertFalse(cat.moved_this_turn)
        self.assertEqual(cat.normal_move_steps_used, 0)
        self.assertEqual(cat.normal_move_actions_used, 0)

    def test_excel_roster_fantasy_bird_moves_targets_and_friendly_mirror_blocks_strong_damage(self) -> None:
        battle = create_battle("excel_r127", "bard")
        bird = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        bird.position = Position(1, 1)
        bard.position = Position(3, 1)
        bard.max_health = 2
        bard.current_hp = 2

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": bird.unit_id,
                "skill_code": "fantasy_move",
                "target_unit_id": bard.unit_id,
                "x": 7,
                "y": 1,
            }
        )
        resolve_pending_chain(battle)

        self.assertEqual(bard.position, Position(7, 1))
        self.assertLess(bard.current_hp, 2)

        battle = create_battle(["excel_r127", "bard"], "ellie")
        bird = next(unit for unit in battle.hero_units(1) if unit.hero_code == "excel_r127")
        ally = next(unit for unit in battle.hero_units(1) if unit.hero_code == "bard")
        enemy = primary_hero(battle, 2)
        bird.position = Position(3, 3)
        ally.position = Position(7, 7)
        enemy.position = Position(7, 6)

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": bird.unit_id,
                "skill_code": "rainbow_mirror",
                "target_unit_id": ally.unit_id,
                "x": 4,
                "y": 3,
            }
        )

        self.assertEqual(ally.position, Position(4, 3))
        self.assertTrue(ally.cannot_move)

        battle = create_battle("excel_r127", "bard")
        bird = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        bird.position = Position(1, 1)
        bard.position = Position(2, 1)
        bard.base_stats.attack = 3
        bird.max_health = 2
        bird.current_hp = 2

        battle.perform_action({"type": "skill", "unit_id": bird.unit_id, "skill_code": "friendly_mirror"})
        battle.resolve_damage(DamageContext(source=bard, target=bird, attack_power=5, is_skill=False, action_name="测试普攻", tags={"attack"}))

        self.assertEqual(bird.current_hp, 2)

    def test_excel_roster_fei_wang_registers_and_ignores_pierce_against_shields(self) -> None:
        fei_wang = create_hero("excel_r028", 1)
        self.assertEqual(
            [skill.code for skill in fei_wang.skills],
            ["big_shensu", "knockback", "gale", "large_pierce_plus", "inner_dimension_sword", "kings_insight"],
        )
        self.assertIn("不受破魔", [trait.name for trait in fei_wang.traits])

        battle = create_battle("bard", "excel_r028")
        bard = primary_hero(battle, 1)
        fei_wang = primary_hero(battle, 2)
        fei_wang.shields = 1
        fei_wang.current_hp = 1

        ctx = battle.resolve_damage(
            DamageContext(
                source=bard,
                target=fei_wang,
                attack_power=0,
                raw_damage=1,
                is_skill=True,
                ignore_shield=True,
                ignore_magic_immunity=True,
                action_name="测试破魔",
                tags={"skill"},
            )
        )

        self.assertTrue(ctx.cancelled)
        self.assertEqual(fei_wang.shields, 0)
        self.assertEqual(fei_wang.current_hp, 1)

    def test_excel_roster_fei_wang_gale_ai_generates_direction_payloads(self) -> None:
        battle = create_battle("excel_r028", "bard")
        fei_wang = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        fei_wang.position = Position(1, 1)
        bard.position = Position(3, 1)
        action = next(action for action in battle.action_snapshot_for(fei_wang)["actions"] if action["code"] == "gale")

        payloads = skill_payloads_for_action(battle, fei_wang, action)

        self.assertIn({"type": "skill", "unit_id": fei_wang.unit_id, "skill_code": "gale", "direction": "east"}, payloads)
        self.assertFalse(any("x" in payload or "y" in payload for payload in payloads))

    def test_excel_roster_fei_wang_gale_and_inner_dimension_sword(self) -> None:
        battle = create_battle("excel_r028", ["bard", "ellie"])
        fei_wang = primary_hero(battle, 1)
        bard = next(unit for unit in battle.hero_units(2) if unit.hero_code == "bard")
        ellie = next(unit for unit in battle.hero_units(2) if unit.hero_code == "ellie")
        fei_wang.position = Position(1, 1)
        bard.position = Position(3, 1)
        ellie.position = Position(3, 2)
        clone = StandardCloneSummon(2, bard)
        battle.add_unit(clone, Position(2, 1))

        battle.perform_action({"type": "skill", "unit_id": fei_wang.unit_id, "skill_code": "gale", "direction": "east"})
        resolve_pending_chain(battle)

        self.assertNotIn(clone.unit_id, battle.units)
        self.assertTrue(bard.position.distance_to(Position(4, 1)) <= 2)

        battle = create_battle("excel_r028", ["bard", "fire_funeral"])
        fei_wang = primary_hero(battle, 1)
        bard = next(unit for unit in battle.hero_units(2) if unit.hero_code == "bard")
        fire = next(unit for unit in battle.hero_units(2) if unit.hero_code == "fire_funeral")
        fei_wang.position = Position(1, 1)
        bard.position = Position(2, 1)
        fire.position = Position(2, 2)
        bard.max_health = fire.max_health = 10
        bard.current_hp = fire.current_hp = 10

        battle.perform_action({"type": "skill", "unit_id": fei_wang.unit_id, "skill_code": "inner_dimension_sword"})
        battle.perform_action({"type": "attack", "unit_id": fei_wang.unit_id, "target_unit_id": bard.unit_id})
        resolve_pending_chain(battle)

        self.assertLess(bard.current_hp, 10)
        self.assertLess(fire.current_hp, 10)

    def test_excel_roster_fei_wang_gale_skips_units_already_on_gather_cell(self) -> None:
        battle = create_battle("excel_r028", "bard")
        fei_wang = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        fei_wang.position = Position(1, 1)
        skill = skill_by_code(fei_wang, "gale")
        cells = skill.area(battle, fei_wang, {"direction": "east"})
        center = sorted(cells, key=lambda cell: sum(cell.distance_to(other) for other in cells))[len(cells) // 2]
        bard.position = center

        battle.perform_action({"type": "skill", "unit_id": fei_wang.unit_id, "skill_code": "gale", "direction": "east"})

        self.assertEqual(bard.position, center)

    def test_excel_roster_red_charge_deadly_bow_and_copy(self) -> None:
        red = create_hero("excel_r029", 1)
        self.assertIn("deadly_bow", red.skill_map())
        self.assertIn("魔力点上限 5", [trait.name for trait in red.traits])

        battle = create_battle("excel_r029", "bard")
        red = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        red.position = Position(1, 1)
        bard.position = Position(4, 1)
        red.mana_points = 4.5
        battle.perform_action({"type": "skill", "unit_id": red.unit_id, "skill_code": "red_charge"})
        self.assertEqual(red.mana_points, 5)

        bard.max_health = 10
        bard.current_hp = 10
        bard.shields = 1
        red.mana_points = 3
        red.current_mana = 4
        battle.perform_action({"type": "skill", "unit_id": red.unit_id, "skill_code": "deadly_bow", "direction": "east"})
        resolve_pending_chain(battle)

        self.assertEqual(red.mana_points, 0)
        self.assertEqual(bard.shields, 0)
        self.assertEqual(bard.current_hp, 7)

        battle = create_battle(["excel_r029", "bard"], "ellie")
        red = next(unit for unit in battle.hero_units(1) if unit.hero_code == "excel_r029")
        bard = next(unit for unit in battle.hero_units(1) if unit.hero_code == "bard")
        red.position = Position(1, 1)
        bard.position = Position(2, 1)
        bard.base_stats.attack = 4

        battle.perform_action({"type": "skill", "unit_id": red.unit_id, "skill_code": "weapon_copy", "target_unit_id": bard.unit_id})

        self.assertEqual(red.stat("attack"), 4)

    def test_excel_roster_fusion_circle_attack_nuclear_rush_and_death_explosion(self) -> None:
        fusion = create_hero("excel_r030", 1)
        self.assertIn("攻击一周", [trait.name for trait in fusion.traits])
        self.assertIn("聚变爆炸", [trait.name for trait in fusion.traits])

        battle = create_battle("excel_r030", ["bard", "ellie"])
        fusion = primary_hero(battle, 1)
        bard = next(unit for unit in battle.hero_units(2) if unit.hero_code == "bard")
        ellie = next(unit for unit in battle.hero_units(2) if unit.hero_code == "ellie")
        fusion.position = Position(2, 2)
        bard.position = Position(3, 2)
        ellie.position = Position(2, 3)
        bard.max_health = ellie.max_health = 10
        bard.current_hp = ellie.current_hp = 10

        battle.perform_action({"type": "attack", "unit_id": fusion.unit_id, "target_unit_id": bard.unit_id})
        resolve_pending_chain(battle)

        self.assertLess(bard.current_hp, 10)
        self.assertLess(ellie.current_hp, 10)

        battle = create_battle("excel_r030", "bard")
        fusion = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        fusion.position = Position(1, 1)
        bard.position = Position(3, 1)
        bard.max_health = 10
        bard.current_hp = 10

        battle.perform_action({"type": "skill", "unit_id": fusion.unit_id, "skill_code": "nuclear_rush"})
        self.assertEqual(fusion.mana_points, 4)
        battle.perform_action({"type": "attack", "unit_id": fusion.unit_id, "direction": "east"})
        resolve_pending_chain(battle)

        self.assertLess(bard.current_hp, 10)
        self.assertEqual(fusion.position, Position(6, 1))

        battle = create_battle("excel_r030", "bard")
        fusion = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        fusion.position = Position(2, 2)
        bard.position = Position(3, 2)
        bard.max_health = 10
        bard.current_hp = 10
        bard.shields = 1
        fusion.mana_points = 2

        battle.remove_unit(fusion)

        self.assertEqual(bard.shields, 0)
        self.assertEqual(bard.current_hp, 8)

    def test_excel_roster_natsume_wind_wall_blocks_piercing_effects(self) -> None:
        battle = create_battle("bard", "excel_r031")
        bard = primary_hero(battle, 1)
        natsume = primary_hero(battle, 2)
        bard.position = Position(4, 4)
        natsume.position = Position(5, 4)
        natsume.max_health = 10
        natsume.current_hp = 10
        natsume.current_mana = 5

        battle.perform_action({"type": "attack", "unit_id": bard.unit_id, "target_unit_id": natsume.unit_id})

        self.assertIsNotNone(battle.pending_chain)
        battle.perform_action(
            {
                "type": "chain_react",
                "unit_id": natsume.unit_id,
                "action_code": "natsume_wind_wall",
                "target_unit_id": natsume.unit_id,
            }
        )

        self.assertEqual(natsume.current_hp, 10)
        self.assertAlmostEqual(natsume.current_mana, 4.0)
        self.assertFalse(natsume.has_status("风壁"))

    def test_excel_roster_natsume_ally_attack_counter_and_dispel(self) -> None:
        battle = create_battle(["excel_r031", "bard"], "dark_human")
        natsume = next(unit for unit in battle.hero_units(1) if unit.hero_code == "excel_r031")
        ally = next(unit for unit in battle.hero_units(1) if unit.hero_code == "bard")
        dark = primary_hero(battle, 2)
        natsume.position = Position(2, 2)
        ally.position = Position(3, 2)
        dark.position = Position(5, 5)
        ally.max_health = 10
        ally.current_hp = 10
        ally.current_mana = 0
        battle.configure_turn_order([natsume.unit_id, dark.unit_id, ally.unit_id], starting_index=0)
        battle.active_player = 1
        natsume.turn_ready = True

        battle.perform_action({"type": "attack", "unit_id": natsume.unit_id, "target_unit_id": ally.unit_id})
        resolve_pending_chain(battle)

        self.assertEqual(ally.current_hp, 10)
        self.assertAlmostEqual(ally.current_mana, 1.0)
        self.assertTrue(ally.has_status("风壁计数点"))

        clone = StandardCloneSummon(2, dark)
        battle.add_unit(clone, Position(4, 4))
        dark.add_status(StatusEffect("隐身"))
        natsume.current_mana = 0
        natsume.base_stats.mana = 10

        battle.perform_action({"type": "skill", "unit_id": natsume.unit_id, "skill_code": "natsume_dispel"})

        self.assertNotIn(clone.unit_id, battle.units)
        self.assertFalse(dark.has_status("隐身"))
        self.assertAlmostEqual(natsume.current_mana, 2.0)

    def test_excel_roster_aaron_unicorn_and_morning_holy_light(self) -> None:
        battle = create_battle("excel_r032", "chanter")
        aaron = primary_hero(battle, 1)
        dark = primary_hero(battle, 2)
        unicorn = summon_by_code(battle, 1, "great_unicorn")
        aaron.position = Position(1, 1)
        unicorn.position = Position(1, 1)
        dark.position = Position(4, 1)
        aaron.current_mana = 5
        dark.max_health = 10
        dark.current_hp = 10

        self.assertIs(battle.mounted_unit_for(aaron), unicorn)
        self.assertTrue(unicorn.is_mount)
        self.assertEqual({(cell.x, cell.y) for cell in battle.unit_cells(unicorn)}, {(1, 1), (1, 2)})
        self.assertFalse(skill_by_code(aaron, "summon_great_unicorn").can_use(battle, aaron, {})[0])
        self.assertEqual(unicorn.stat("attack"), 4)
        self.assertEqual(unicorn.stat("defense"), 6)
        dark.shields = 1
        ctx = battle.resolve_damage(
            DamageContext(
                source=unicorn,
                target=dark,
                attack_power=unicorn.stat("attack"),
                is_skill=False,
                action_name="普攻",
                ignore_shield=battle.attack_ignores_shield(unicorn, dark),
                tags={"attack"},
            )
        )
        self.assertFalse(ctx.cancelled)
        self.assertEqual(dark.shields, 0)
        hp_before_light = dark.current_hp

        light = skill_by_code(aaron, "morning_holy_light")
        cells = next(pattern for pattern in light.patterns(battle, aaron) if dark.position in pattern)
        battle.perform_action(
            {
                "type": "skill",
                "unit_id": aaron.unit_id,
                "skill_code": "morning_holy_light",
                "cells": [cell.to_dict() for cell in cells],
            }
        )
        resolve_pending_chain(battle)

        light_wall = skill_by_code(dark, "light_wall")
        self.assertTrue(any(status.blocks_skill_use(battle, dark, light_wall)[0] for status in dark.statuses))
        self.assertAlmostEqual(dark.current_hp, max(0.0, hp_before_light - 6.0))

        aaron.current_hp = 0.25
        aaron.current_mana = 0
        unicorn.alive = False
        battle.cleanup_dead_units()

        self.assertAlmostEqual(aaron.current_hp, aaron.max_health)
        self.assertIsInstance(aaron.current_mana, float)
        self.assertAlmostEqual(aaron.current_mana, aaron.max_mana())
        self.assertEqual(aaron.stat("attack"), aaron.base_stats.attack + 2)

    def test_blood_guard_is_once_per_turn(self) -> None:
        battle = create_battle("blood_eater", "ellie")
        blood = primary_hero(battle, 1)
        blood.position = Position(4, 4)
        blood.current_mana = 5

        battle.perform_action({"type": "skill", "unit_id": blood.unit_id, "skill_code": "blood_guard", "target_unit_id": blood.unit_id})

        with self.assertRaises(ActionError):
            battle.perform_action({"type": "skill", "unit_id": blood.unit_id, "skill_code": "blood_guard", "target_unit_id": blood.unit_id})

    def _first_skill_pattern_hitting(self, battle, actor, skill_code: str, target) -> list[dict[str, int]]:
        skill = skill_by_code(actor, skill_code)
        preview = skill.preview(battle, actor)
        target_cells = {(cell.x, cell.y) for cell in battle.unit_cells(target)}
        for pattern in preview["selection"]["patterns"]:
            cells = [Position(int(cell["x"]), int(cell["y"])) for cell in pattern]
            if any((cell.x, cell.y) in target_cells for cell in cells):
                return [{"x": cell.x, "y": cell.y} for cell in cells]
        self.fail(f"no {skill_code} pattern hits target")

    def test_excel_roster_lao_wave_bullet_paid_and_free_cast(self) -> None:
        paid_battle = create_battle("excel_r033", "dark_human")
        paid_lao = primary_hero(paid_battle, 1)
        paid_target = primary_hero(paid_battle, 2)
        paid_lao.position = Position(1, 1)
        paid_target.position = Position(4, 1)
        paid_target.max_health = 10
        paid_target.current_hp = 10
        paid_target.base_stats.defense = 2
        paid_cells = self._first_skill_pattern_hitting(paid_battle, paid_lao, "lao_wave_bullet", paid_target)
        paid_battle.perform_action({"type": "skill", "unit_id": paid_lao.unit_id, "skill_code": "lao_wave_bullet", "cells": paid_cells})
        resolve_pending_chain(paid_battle)

        free_battle = create_battle("excel_r033", "dark_human")
        free_lao = primary_hero(free_battle, 1)
        free_target = primary_hero(free_battle, 2)
        free_lao.position = Position(1, 1)
        free_lao.current_mana = 0
        free_target.position = Position(4, 1)
        free_target.max_health = 10
        free_target.current_hp = 10
        free_target.base_stats.defense = 2
        free_cells = self._first_skill_pattern_hitting(free_battle, free_lao, "lao_wave_bullet", free_target)
        free_battle.perform_action(
            {
                "type": "skill",
                "unit_id": free_lao.unit_id,
                "skill_code": "lao_wave_bullet",
                "cells": free_cells,
                "free_cast": True,
            }
        )
        resolve_pending_chain(free_battle)

        self.assertLess(paid_target.current_hp, free_target.current_hp)
        self.assertAlmostEqual(paid_lao.current_mana, 4.0)
        self.assertAlmostEqual(free_lao.current_mana, 0.0)

    def test_excel_roster_lao_mage_hand_pierces_and_pushes(self) -> None:
        battle = create_battle("excel_r033", "dark_human")
        lao = primary_hero(battle, 1)
        target = primary_hero(battle, 2)
        lao.position = Position(1, 1)
        target.position = Position(2, 1)
        target.max_health = 10
        target.current_hp = 10
        target.base_stats.defense = 1
        target.shields = 1

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": lao.unit_id,
                "skill_code": "lao_mage_hand",
                "target_unit_id": target.unit_id,
                "direction": {"dx": 1, "dy": 0},
            }
        )
        resolve_pending_chain(battle)

        self.assertEqual(target.shields, 0)
        self.assertLess(target.current_hp, 10)
        self.assertEqual(target.position, Position(5, 1))

    def test_excel_roster_lao_mage_cloak_equip_and_detach(self) -> None:
        battle = create_battle("excel_r033", "bard")
        lao = primary_hero(battle, 1)
        lao.position = Position(1, 1)
        battle.perform_action({"type": "skill", "unit_id": lao.unit_id, "skill_code": "summon_mage_cloak", "x": 2, "y": 1})
        cloak = summon_by_code(battle, 1, "mage_cloak")
        self.assertTrue(cloak.turn_ready)

        battle.perform_action({"type": "skill", "unit_id": cloak.unit_id, "skill_code": "equip_mage_cloak", "target_unit_id": lao.unit_id})
        self.assertFalse(cloak.alive)
        self.assertIsNotNone(lao.get_status("法师斗篷"))
        self.assertEqual(lao.stat("speed"), 6)
        self.assertEqual(lao.normal_move_actions_per_turn(), 2)
        self.assertTrue(lao.has_flying)

        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "skill", "unit_id": lao.unit_id, "skill_code": "summon_mage_cloak", "x": 2, "y": 1})
        new_cloak = summon_by_code(battle, 1, "mage_cloak")
        self.assertTrue(new_cloak.alive)
        self.assertEqual(new_cloak.position, Position(2, 1))
        self.assertIsNone(lao.get_status("法师斗篷"))

    def test_excel_roster_lao_stat_cancel_prevents_damage(self) -> None:
        battle = create_battle("bard", "excel_r033")
        bard = primary_hero(battle, 1)
        lao = primary_hero(battle, 2)
        before_hp = lao.current_hp
        before_range = lao.stat("attack_range")
        ctx = battle.resolve_damage(
            DamageContext(source=bard, target=lao, attack_power=10, is_skill=False, action_name="测试伤害", tags={"attack"})
        )
        self.assertTrue(ctx.cancelled)
        self.assertAlmostEqual(lao.current_hp, before_hp)
        self.assertEqual(lao.stat("attack_range"), before_range - 1)

    def test_excel_roster_sakura_floating_cannons_summon_and_restore(self) -> None:
        battle = create_battle("excel_r034", "bard")
        sakura = primary_hero(battle, 1)
        sakura.position = Position(3, 3)

        battle.perform_action({"type": "skill", "unit_id": sakura.unit_id, "skill_code": "floating_cannons", "x": 2, "y": 2})
        cannons = [unit for unit in battle.player_units(1) if getattr(unit, "hero_code", "") == "floating_cannon" and unit.alive]
        self.assertEqual(len(cannons), 4)
        self.assertTrue(all(cannon.magic_immunity for cannon in cannons))

        cannons[0].alive = False
        cannons[0].position = None
        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})
        restored = [unit for unit in battle.player_units(1) if getattr(unit, "hero_code", "") == "floating_cannon" and unit.alive]
        self.assertEqual(len(restored), 4)

    def test_excel_roster_sakura_berserk_buffs_and_forces_nearest_target(self) -> None:
        battle = create_battle("excel_r034", ["bard", "ellie"])
        sakura = primary_hero(battle, 1)
        bard = next(unit for unit in battle.hero_units(2) if unit.hero_code == "bard")
        ellie = next(unit for unit in battle.hero_units(2) if unit.hero_code == "ellie")
        sakura.position = Position(3, 3)
        bard.position = Position(5, 3)
        ellie.position = Position(8, 3)

        battle.perform_action({"type": "skill", "unit_id": sakura.unit_id, "skill_code": "floating_cannons", "x": 4, "y": 3})
        cannon = next(unit for unit in battle.player_units(1) if getattr(unit, "hero_code", "") == "floating_cannon")
        battle.perform_action({"type": "end_turn"})
        for _ in range(5):
            current = battle.current_turn_unit()
            if current is not None and current.unit_id == sakura.unit_id:
                break
            battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "skill", "unit_id": sakura.unit_id, "skill_code": "floating_cannon_berserk"})

        self.assertEqual(cannon.stat("attack"), 5)
        self.assertEqual(cannon.stat("speed"), 6)
        self.assertEqual(cannon.stat("attack_range"), 2)
        self.assertEqual(cannon.attack_actions_per_turn(), 2)
        trait = next(trait for trait in cannon.traits if trait.name == "浮游炮狂暴属性")
        ok, _ = trait.can_attack_target_with_payload(battle, cannon, bard, {})
        self.assertTrue(ok)
        ok, reason = trait.can_attack_target_with_payload(battle, cannon, ellie, {})
        self.assertFalse(ok, reason)

    def test_excel_roster_sakura_cover_consumes_cannon_for_single_target(self) -> None:
        battle = create_battle(["excel_r034", "ellie"], "bard")
        sakura = next(unit for unit in battle.hero_units(1) if unit.hero_code == "excel_r034")
        ally = next(unit for unit in battle.hero_units(1) if unit.hero_code == "ellie")
        bard = primary_hero(battle, 2)
        sakura.position = Position(3, 3)
        ally.position = Position(4, 3)
        bard.position = Position(5, 3)
        battle.perform_action({"type": "skill", "unit_id": sakura.unit_id, "skill_code": "floating_cannons", "x": 2, "y": 2})
        cannons_before = [unit for unit in battle.player_units(1) if getattr(unit, "hero_code", "") == "floating_cannon" and unit.alive]
        before_hp = ally.current_hp

        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "attack", "unit_id": bard.unit_id, "target_unit_id": ally.unit_id})
        battle.perform_action({"type": "chain_react", "unit_id": sakura.unit_id, "action_code": "floating_cannon_cover", "target_unit_id": ally.unit_id})
        resolve_pending_chain(battle)

        cannons_after = [unit for unit in battle.player_units(1) if getattr(unit, "hero_code", "") == "floating_cannon" and unit.alive]
        self.assertAlmostEqual(ally.current_hp, before_hp)
        self.assertEqual(len(cannons_after), len(cannons_before) - 1)

    def test_excel_roster_ushioni_demon_blade_and_large_drain(self) -> None:
        battle = create_battle("excel_r035", ["bard", "ellie"])
        oni = primary_hero(battle, 1)
        bard = next(unit for unit in battle.hero_units(2) if unit.hero_code == "bard")
        ellie = next(unit for unit in battle.hero_units(2) if unit.hero_code == "ellie")
        oni.position = Position(1, 1)
        bard.position = Position(2, 1)
        ellie.position = Position(4, 1)
        for target in (bard, ellie):
            target.max_health = 10
            target.current_hp = 10
            target.base_stats.defense = 4

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": oni.unit_id,
                "skill_code": "demon_blade",
                "cells": [{"x": 2, "y": 1}, {"x": 3, "y": 1}, {"x": 4, "y": 1}],
            }
        )
        resolve_pending_chain(battle)
        self.assertLess(bard.current_hp, ellie.current_hp)

        battle = create_battle("excel_r035", "bard")
        oni = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        oni.position = Position(1, 1)
        bard.position = Position(3, 1)
        oni.current_mana = 4
        bard.current_mana = 2
        before = oni.current_mana
        battle.perform_action({"type": "skill", "unit_id": oni.unit_id, "skill_code": "large_drain_mana", "target_unit_id": bard.unit_id})
        resolve_pending_chain(battle)
        self.assertAlmostEqual(bard.current_mana, 1.0)
        self.assertAlmostEqual(oni.current_mana, before + 1.0)

    def test_excel_roster_ushioni_ultimates_and_awakening_reset(self) -> None:
        battle = create_battle("excel_r035", "bard")
        oni = primary_hero(battle, 1)
        oni.current_mana = oni.max_mana()
        battle.perform_action({"type": "skill", "unit_id": oni.unit_id, "skill_code": "mountain_god_muro"})
        self.assertTrue(oni.allow_unbounded_mana)
        oni.gain_mana(10)
        self.assertGreater(oni.current_mana, oni.base_stats.mana)

        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})
        for _ in range(8):
            oni.add_status(MountainGodCounterStatus())
        battle.perform_action({"type": "skill", "unit_id": oni.unit_id, "skill_code": "mountain_awakening"})
        self.assertEqual(sum(1 for status in oni.statuses if isinstance(status, MountainGodCounterStatus)), 0)
        self.assertEqual(skill_by_code(oni, "mountain_god_muro").uses_this_battle, 0)

        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "skill", "unit_id": oni.unit_id, "skill_code": "mountain_escape"})
        self.assertTrue(oni.cannot_move)
        self.assertEqual(oni.stat("defense"), oni.base_stats.defense + 2)
        self.assertAlmostEqual(oni.current_hp, oni.max_health)

    def test_excel_roster_seventh_dragon_nuclear_mutation_and_area_guard(self) -> None:
        battle = create_battle("excel_r037", "bard")
        dragon = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        self.assertEqual(len(battle.unit_cells(dragon)), 4)
        self.assertTrue(dragon.has_flying)

        dragon.position = Position(1, 1)
        bard.position = Position(5, 5)
        skill = skill_by_code(dragon, "nuclear_mutation")
        pattern = next(pattern for pattern in skill.patterns(battle, dragon) if any(cell == bard.position for cell in pattern))
        before = bard.current_hp
        battle.perform_action(
            {
                "type": "skill",
                "unit_id": dragon.unit_id,
                "skill_code": "nuclear_mutation",
                "cells": [{"x": cell.x, "y": cell.y} for cell in pattern],
            }
        )
        resolve_pending_chain(battle)
        self.assertLess(bard.current_hp, before)

        guard_battle = create_battle("bard", "excel_r037")
        attacker = primary_hero(guard_battle, 1)
        guarded = primary_hero(guard_battle, 2)
        ctx = guard_battle.resolve_damage(
            DamageContext(
                source=attacker,
                target=guarded,
                attack_power=attacker.stat("attack"),
                is_skill=True,
                action_name="范围测试",
                area_cell_hits=4,
                tags={"skill", "area"},
            )
        )
        self.assertEqual(ctx.area_cell_hits, 1)

    def test_excel_roster_seventh_dragon_gravity_field_half_pierce_and_drain(self) -> None:
        battle = create_battle("excel_r037", "bard")
        dragon = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        dragon.position = Position(1, 1)
        bard.position = Position(5, 5)
        dragon.current_mana = 4
        bard.current_mana = 2
        bard.shields = 1

        with mock.patch("wujiang.heroes.excel_roster.random.random", side_effect=[0.75, 0.75, 0.75]):
            battle.perform_action(
                {
                    "type": "skill",
                    "unit_id": dragon.unit_id,
                    "skill_code": "gravity_field",
                    "x": 3,
                    "y": 3,
                }
            )
        resolve_pending_chain(battle)

        self.assertEqual(bard.shields, 0)
        self.assertAlmostEqual(bard.current_mana, 1.0)
        self.assertAlmostEqual(dragon.current_mana, 5.0)
        self.assertLess(bard.current_hp, bard.max_health)

    def test_damage_formula_equal_attack_only_deals_half(self) -> None:
        battle = create_battle("bard", "ellie")
        bard = battle.player_units(1)[0]
        ellie = battle.player_units(2)[0]
        bard.position = Position(4, 4)
        ellie.position = Position(5, 4)

        battle.perform_action({"type": "attack", "unit_id": bard.unit_id, "target_unit_id": ellie.unit_id})

        self.assertIsNotNone(battle.pending_chain)
        battle.perform_action({"type": "chain_skip"})

        self.assertAlmostEqual(ellie.current_hp, 0.5)

    def test_damage_formula_breaks_for_exactly_one_hp_when_attack_exceeds_defense(self) -> None:
        battle = create_battle("fire_funeral", "elite_soldier")
        fire = battle.player_units(1)[0]
        soldier = battle.player_units(2)[0]
        fire.position = Position(4, 4)
        soldier.position = Position(5, 4)

        battle.perform_action({"type": "attack", "unit_id": fire.unit_id, "target_unit_id": soldier.unit_id})
        self.assertIsNotNone(battle.pending_chain)
        battle.perform_action({"type": "chain_skip"})

        self.assertFalse(soldier.alive)

    def test_ellie_crystal_ball_enables_long_range_pull(self) -> None:
        battle = create_battle("ellie", "fire_funeral")
        ellie = battle.player_units(1)[0]
        target = battle.player_units(2)[0]

        battle.perform_action({"type": "skill", "unit_id": ellie.unit_id, "skill_code": "crystal_ball"})
        battle.perform_action(
            {
                "type": "skill",
                "unit_id": ellie.unit_id,
                "skill_code": "mana_pull",
                "target_unit_id": target.unit_id,
                "dest_x": 4,
                "dest_y": 4,
            }
        )

        self.assertIsNotNone(battle.pending_chain)
        battle.perform_action({"type": "chain_skip"})

        self.assertEqual(target.position, Position(4, 4))
        self.assertTrue(target.cannot_normal_move)

    def test_battle_randomly_resolves_winner_after_turn_timeout(self) -> None:
        battle = create_battle("bard", "ellie")
        self.assertEqual(battle.initial_hero_count, 2)
        self.assertEqual(battle.turn_timeout_limit, 40)

        for _ in range(39):
            battle.perform_action({"type": "end_turn"})

        self.assertIsNone(battle.winner)
        self.assertEqual(battle.completed_turns, 39)

        with mock.patch("wujiang.engine.core.random.choice", return_value=2):
            battle.perform_action({"type": "end_turn"})

        self.assertEqual(battle.completed_turns, 40)
        self.assertEqual(battle.winner, 2)
        self.assertIn("40 个武将回合上限", battle.logs[-1])

    def test_ellie_experiment_counts_target_own_rounds_not_global_turns(self) -> None:
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

        experiment_status = dark.get_status("实验")
        countdown_status = dark.get_status("实验倒计时")
        self.assertIsNotNone(experiment_status)
        self.assertIsNotNone(countdown_status)
        self.assertEqual(experiment_status.duration, 3)
        self.assertEqual(countdown_status.duration, 3)

        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})

        self.assertTrue(dark.alive)
        self.assertEqual(dark.get_status("实验").duration, 2)
        self.assertEqual(dark.get_status("实验倒计时").duration, 2)

        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})
        self.assertTrue(dark.alive)
        self.assertEqual(dark.get_status("实验").duration, 1)
        self.assertEqual(dark.get_status("实验倒计时").duration, 1)

        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})

        self.assertFalse(dark.alive)

    def test_ellie_crystal_ball_counts_own_rounds_not_global_turns(self) -> None:
        battle = create_battle(["dark_human", "ellie"], ["bard"])
        ellie = next(unit for unit in battle.hero_units(1) if unit.hero_code == "ellie")

        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "skill", "unit_id": ellie.unit_id, "skill_code": "crystal_ball"})

        crystal_ball = ellie.get_status("水晶球")
        self.assertIsNotNone(crystal_ball)
        self.assertEqual(crystal_ball.duration, 4)

        battle.perform_action({"type": "end_turn"})
        self.assertEqual(ellie.get_status("水晶球").duration, 4)
        battle.perform_action({"type": "end_turn"})
        self.assertEqual(ellie.get_status("水晶球").duration, 4)
        battle.perform_action({"type": "end_turn"})
        self.assertEqual(ellie.get_status("水晶球").duration, 3)

    def test_public_state_reports_next_turn_unit_and_skips_destroyed_slots(self) -> None:
        battle = create_battle(["dark_human", "fire_funeral", "bard"], ["undead_king_lina", "jade", "doomlight_dragon"])

        public_before = battle.to_public_dict()
        turn_order = public_before["turn_order_unit_ids"]
        current_id = public_before["active_turn_unit_id"]
        current_index = turn_order.index(current_id)

        expected_next_id = None
        for offset in range(1, len(turn_order) + 1):
            candidate_id = turn_order[(current_index + offset) % len(turn_order)]
            candidate = battle.units.get(candidate_id)
            if candidate is not None and candidate.alive and not candidate.is_summon:
                expected_next_id = candidate_id
                break
        self.assertEqual(public_before["next_turn_unit_id"], expected_next_id)

        doomed = battle.get_unit(expected_next_id)
        assert doomed is not None
        doomed.take_damage_fraction(doomed.current_hp)
        battle.cleanup_dead_units()

        public_after = battle.to_public_dict()
        expected_after_id = None
        for offset in range(1, len(turn_order) + 1):
            candidate_id = turn_order[(current_index + offset) % len(turn_order)]
            candidate = battle.units.get(candidate_id)
            if candidate is not None and candidate.alive and not candidate.is_summon:
                expected_after_id = candidate_id
                break
        self.assertEqual(public_after["next_turn_unit_id"], expected_after_id)

    def test_blood_eater_starts_with_base_mana_points_and_blocks_skill_damage_at_eight(self) -> None:
        battle = create_battle("blood_eater", "fire_funeral")
        blood = primary_hero(battle, 1)
        enemy = primary_hero(battle, 2)

        self.assertEqual(blood.mana_points, 5)

        blood.mana_points = 8
        ctx = battle.resolve_damage(
            DamageContext(source=enemy, target=blood, attack_power=9, is_skill=True, action_name="test skill")
        )

        self.assertTrue(ctx.cancelled)
        self.assertAlmostEqual(blood.current_hp, 1.0)

    def test_blood_eater_sacrifice_ritual_revives_any_destroyed_unit_adjacent_with_quarter_hp_and_full_mana(self) -> None:
        battle = create_battle("blood_eater", ["fire_funeral", "bard"])
        blood = primary_hero(battle, 1)
        bard = next(unit for unit in battle.hero_units(2) if unit.hero_code == "bard")
        blood.position = Position(3, 3)
        bard.position = Position(6, 6)

        battle.resolve_damage(
            DamageContext(source=blood, target=bard, attack_power=9, is_skill=True, action_name="test kill")
        )

        self.assertFalse(bard.alive)
        self.assertIn(bard.unit_id, [unit.unit_id for unit in battle.destroyed_units])
        blood.mana_points = 4
        battle.perform_action(
            {
                "type": "skill",
                "unit_id": blood.unit_id,
                "skill_code": "sacrifice_ritual",
                "revive_unit_id": bard.unit_id,
                "x": 4,
                "y": 3,
            }
        )

        self.assertTrue(bard.alive)
        self.assertEqual(bard.position, Position(4, 3))
        self.assertAlmostEqual(bard.max_health, 0.25)
        self.assertAlmostEqual(bard.current_hp, 0.25)
        self.assertAlmostEqual(bard.current_mana, bard.max_mana())
        self.assertNotIn(bard.unit_id, [unit.unit_id for unit in battle.destroyed_units])

    def test_mana_pull_moves_adjacent_target_after_chain_skip(self) -> None:
        battle = create_battle("ellie", "bard")
        ellie = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        ellie.position = Position(4, 4)
        bard.position = Position(5, 4)

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": ellie.unit_id,
                "skill_code": "mana_pull",
                "target_unit_id": bard.unit_id,
                "dest_x": 7,
                "dest_y": 4,
            }
        )

        self.assertIsNotNone(battle.pending_chain)
        battle.perform_action({"type": "chain_skip"})

        self.assertEqual(bard.position, Position(7, 4))
        self.assertTrue(bard.cannot_normal_move)

    def test_mana_pull_chain_preview_includes_target_path_and_destination(self) -> None:
        battle = create_battle("ellie", "bard")
        ellie = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        ellie.position = Position(4, 4)
        bard.position = Position(5, 4)

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": ellie.unit_id,
                "skill_code": "mana_pull",
                "target_unit_id": bard.unit_id,
                "dest_x": 7,
                "dest_y": 4,
            }
        )

        self.assertIsNotNone(battle.pending_chain)
        queued_action = battle.pending_chain.queued_action
        self.assertEqual(
            {(cell.x, cell.y) for cell in queued_action.target_cells},
            {(5, 4), (6, 4), (7, 4)},
        )

    def test_mana_pull_only_blocks_normal_move_and_not_movement_skills(self) -> None:
        battle = create_battle("ellie", "dark_human")
        ellie = battle.player_units(1)[0]
        dark = battle.player_units(2)[0]
        ellie.position = Position(4, 4)
        dark.position = Position(5, 4)

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": ellie.unit_id,
                "skill_code": "mana_pull",
                "target_unit_id": dark.unit_id,
                "dest_x": 7,
                "dest_y": 4,
            }
        )

        self.assertIsNotNone(battle.pending_chain)
        battle.perform_action({"type": "chain_skip"})
        battle.perform_action({"type": "end_turn"})

        self.assertTrue(dark.cannot_normal_move)
        with self.assertRaises(ActionError):
            battle.perform_action({"type": "move", "unit_id": dark.unit_id, "x": 6, "y": 4})

        battle.perform_action({"type": "skill", "unit_id": dark.unit_id, "skill_code": "fly_leap", "x": 4, "y": 1})
        self.assertEqual(dark.position, Position(4, 1))

    def test_stealth_resets_paralyzing_glove_usage(self) -> None:
        battle = create_battle("dark_human", "fire_funeral")
        dark = battle.player_units(1)[0]
        target = battle.player_units(2)[0]
        glove = dark.get_skill("paralyzing_glove")
        dark.position = Position(4, 4)
        target.position = Position(5, 4)
        glove.uses_this_battle = 1

        battle.perform_action({"type": "skill", "unit_id": dark.unit_id, "skill_code": "stealth"})

        self.assertEqual(glove.uses_this_battle, 0)
        self.assertTrue(dark.has_status("\u9690\u8eab"))
        self.assertFalse(dark.cannot_be_targeted)

        battle.perform_action({"type": "end_turn"})
        hp_before = dark.current_hp
        with self.assertRaises(ActionError):
            battle.perform_action({"type": "attack", "unit_id": target.unit_id, "target_unit_id": dark.unit_id})

        self.assertIsNone(battle.pending_chain)
        self.assertEqual(dark.current_hp, hp_before)
        self.assertTrue(dark.has_status("\u9690\u8eab"))

        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "attack", "unit_id": dark.unit_id, "target_unit_id": target.unit_id})

        self.assertFalse(dark.has_status("\u9690\u8eab"))
        if battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

    def test_all_on_field_heroes_lose_stealth_when_everyone_is_hidden(self) -> None:
        battle = create_battle("dark_human", "dark_human")
        first = battle.player_units(1)[0]
        second = battle.player_units(2)[0]
        first.position = Position(2, 4)
        second.position = Position(5, 4)

        battle.perform_action({"type": "skill", "unit_id": first.unit_id, "skill_code": "stealth"})
        self.assertTrue(first.has_status("隐身"))

        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "skill", "unit_id": second.unit_id, "skill_code": "stealth"})

        self.assertFalse(first.has_status("隐身"))
        self.assertFalse(second.has_status("隐身"))
        self.assertTrue(any("所有在场武将的隐身自动解除" in line for line in battle.logs))

    def test_great_fire_funeral_creates_persistent_field(self) -> None:
        battle = create_battle("fire_funeral", "bard")
        fire = battle.player_units(1)[0]

        battle.perform_action({"type": "skill", "unit_id": fire.unit_id, "skill_code": "great_funeral"})

        self.assertIsNotNone(battle.pending_chain)
        battle.perform_action({"type": "chain_skip"})

        self.assertEqual(fire.base_stats.attack, 3)
        self.assertEqual(len(battle.field_effects), 1)

    def test_unmoved_active_skill_can_target_dark_human(self) -> None:
        battle = create_battle("dark_human", "ellie")
        dark = battle.player_units(1)[0]
        ellie = battle.player_units(2)[0]
        dark.position = Position(4, 4)
        ellie.position = Position(5, 4)

        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "skill", "unit_id": ellie.unit_id, "skill_code": "curse", "target_unit_id": dark.unit_id})

        self.assertIsNotNone(battle.pending_chain)
        battle.perform_action({"type": "chain_skip"})

        self.assertTrue(dark.has_status("\u8bc5\u5492"))

    def test_great_holy_light_damages_enemy_on_move(self) -> None:
        battle = create_battle("bard", "dark_human")
        bard = battle.player_units(1)[0]
        dark = battle.player_units(2)[0]

        battle.perform_action({"type": "skill", "unit_id": bard.unit_id, "skill_code": "great_holy_light"})
        battle.perform_action({"type": "end_turn"})
        hp_before = dark.current_hp
        battle.perform_action({"type": "move", "unit_id": dark.unit_id, "x": 5, "y": 4})

        self.assertLess(dark.current_hp, hp_before)

    def test_great_holy_light_only_damages_when_enemy_ends_move_in_range(self) -> None:
        battle = create_battle("bard", "dark_human")
        bard = battle.player_units(1)[0]
        dark = battle.player_units(2)[0]
        bard.position = Position(1, 1)
        dark.position = Position(6, 1)
        dark.max_health = 4.0
        dark.current_hp = 4.0

        battle.perform_action({"type": "skill", "unit_id": bard.unit_id, "skill_code": "great_holy_light"})
        battle.perform_action({"type": "end_turn"})
        hp_before = dark.current_hp
        battle.perform_action({"type": "move", "unit_id": dark.unit_id, "x": 7, "y": 1})

        self.assertEqual(dark.current_hp, hp_before)

    def test_great_holy_light_does_not_trigger_on_skill_movement(self) -> None:
        battle = create_battle("bard", "dark_human")
        bard = battle.player_units(1)[0]
        dark = battle.player_units(2)[0]
        bard.position = Position(4, 4)
        dark.position = Position(0, 4)
        dark.max_health = 4.0
        dark.current_hp = 4.0

        battle.perform_action({"type": "skill", "unit_id": bard.unit_id, "skill_code": "great_holy_light"})
        battle.perform_action({"type": "end_turn"})
        hp_before = dark.current_hp
        battle.perform_action({"type": "skill", "unit_id": dark.unit_id, "skill_code": "fly_leap", "x": 3, "y": 4})

        self.assertEqual(dark.current_hp, hp_before)

    def test_great_holy_light_defense_bonus_expires_before_next_owner_turn_start(self) -> None:
        battle = create_battle("bard", "ellie")
        bard = battle.player_units(1)[0]
        ellie = battle.player_units(2)[0]
        ally = create_hero("dark_human", 1)
        bard.position = Position(4, 4)
        ellie.position = Position(7, 7)
        battle.add_unit(ally, Position(5, 4))
        base_defense = ally.base_stats.defense

        battle.perform_action({"type": "skill", "unit_id": bard.unit_id, "skill_code": "great_holy_light"})
        battle.perform_action({"type": "end_turn"})

        self.assertEqual(ally.stat("defense"), base_defense + 1)

        battle.perform_action({"type": "end_turn"})

        self.assertEqual(ally.stat("defense"), base_defense)

    def test_great_holy_light_exposes_dynamic_field_cells(self) -> None:
        battle = create_battle("bard", "ellie")
        bard = battle.player_units(1)[0]
        ellie = battle.player_units(2)[0]
        bard.position = Position(1, 1)
        ellie.position = Position(7, 7)

        battle.perform_action({"type": "skill", "unit_id": bard.unit_id, "skill_code": "great_holy_light"})

        effect = battle.to_public_dict()["field_effects"][0]
        self.assertEqual(effect["board_marker"], "圣")
        self.assertIn({"x": 0, "y": 0}, effect["cells"])
        self.assertIn({"x": 6, "y": 6}, effect["cells"])
        self.assertNotIn({"x": 7, "y": 7}, effect["cells"])

    def test_magic_immunity_blocks_enemy_active_skill(self) -> None:
        battle = create_battle("bard", "ellie")
        bard = battle.player_units(1)[0]
        ellie = battle.player_units(2)[0]
        bard.position = Position(4, 4)
        ellie.position = Position(5, 4)

        battle.perform_action({"type": "skill", "unit_id": bard.unit_id, "skill_code": "baptism", "target_unit_id": bard.unit_id})
        battle.perform_action({"type": "end_turn"})
        mana_before = bard.current_mana
        enemy_mana_before = ellie.current_mana
        battle.perform_action({"type": "skill", "unit_id": ellie.unit_id, "skill_code": "drain_mana", "target_unit_id": bard.unit_id})

        self.assertEqual(bard.current_mana, mana_before)
        self.assertEqual(ellie.current_mana, enemy_mana_before)

    def test_magic_immunity_does_not_block_great_holy_light_field(self) -> None:
        battle = create_battle("bard", "bard")
        first = battle.player_units(1)[0]
        second = battle.player_units(2)[0]
        first.position = Position(2, 4)
        second.position = Position(6, 4)
        first.max_health = 5.0
        first.current_hp = 5.0

        battle.perform_action({"type": "skill", "unit_id": first.unit_id, "skill_code": "baptism", "target_unit_id": first.unit_id})
        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "skill", "unit_id": second.unit_id, "skill_code": "great_holy_light"})
        battle.perform_action({"type": "end_turn"})

        hp_before = first.current_hp
        battle.perform_action({"type": "move", "unit_id": first.unit_id, "x": 3, "y": 4})

        self.assertLess(first.current_hp, hp_before)

    def test_stealthed_ally_can_be_targeted_by_friendly_heal(self) -> None:
        battle = create_battle("dark_human", "ellie")
        dark = battle.player_units(1)[0]
        enemy = battle.player_units(2)[0]
        bard = create_hero("bard", 1)
        battle.add_unit(bard, Position(4, 4))
        dark.position = Position(5, 4)
        enemy.position = Position(7, 7)
        dark.current_hp = 0.5

        battle.perform_action({"type": "skill", "unit_id": dark.unit_id, "skill_code": "stealth"})
        battle.perform_action({"type": "skill", "unit_id": bard.unit_id, "skill_code": "heal", "target_unit_id": dark.unit_id})

        self.assertEqual(dark.current_hp, 0.75)

    def test_stealthed_unit_actions_do_not_write_battle_logs(self) -> None:
        battle = create_battle("dark_human", "elite_soldier")
        dark = battle.player_units(1)[0]
        soldier = battle.player_units(2)[0]
        dark.position = Position(4, 4)
        soldier.position = Position(5, 4)

        battle.perform_action({"type": "skill", "unit_id": dark.unit_id, "skill_code": "stealth"})
        log_count = len(battle.logs)
        battle.perform_action({"type": "attack", "unit_id": dark.unit_id, "target_unit_id": soldier.unit_id})
        if battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

        self.assertFalse(any(dark.name in line for line in battle.logs[log_count:]))

    def test_stealthed_unit_taking_damage_does_not_write_battle_logs(self) -> None:
        battle = create_battle("elite_soldier", "dark_human")
        soldier = battle.player_units(1)[0]
        dark = battle.player_units(2)[0]
        soldier.position = Position(4, 4)
        dark.position = Position(5, 4)
        dark.max_health = 4.0
        dark.current_hp = 4.0

        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "skill", "unit_id": dark.unit_id, "skill_code": "stealth"})
        battle.perform_action({"type": "end_turn"})
        log_count = len(battle.logs)
        battle.perform_action(
            {
                "type": "skill",
                "unit_id": soldier.unit_id,
                "skill_code": "machine_gun",
                "cells": [{"x": 5, "y": 4}, {"x": 6, "y": 4}, {"x": 7, "y": 4}],
            }
        )
        if battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

        self.assertLess(dark.current_hp, 4.0)
        self.assertFalse(any(dark.name in line for line in battle.logs[log_count:]))

    def test_chant_adds_mana_points_without_changing_mana(self) -> None:
        battle = create_battle("bard", "ellie")
        bard = battle.player_units(1)[0]
        enemy = battle.player_units(2)[0]
        bard.position = Position(4, 4)
        enemy.position = Position(7, 7)
        bard.current_mana = 4.0

        battle.perform_action({"type": "skill", "unit_id": bard.unit_id, "skill_code": "chant", "target_unit_id": bard.unit_id})

        self.assertEqual(bard.current_mana, 4.0)
        self.assertEqual(bard.mana_points, 2.0)

    def test_chant_only_offers_and_affects_targets_within_range(self) -> None:
        battle = create_battle("bard", "ellie")
        bard = battle.player_units(1)[0]
        enemy = battle.player_units(2)[0]
        bard.position = Position(0, 0)
        enemy.position = Position(5, 5)

        snapshot = battle.action_snapshot_for(bard)
        chant = next(action for action in snapshot["actions"] if action["code"] == "chant")

        self.assertNotIn(enemy.unit_id, chant["preview"]["target_unit_ids"])

        with self.assertRaises(ActionError):
            battle.perform_action(
                {"type": "skill", "unit_id": bard.unit_id, "skill_code": "chant", "target_unit_id": enemy.unit_id}
            )
        self.assertEqual(enemy.mana_points, 0.0)

        enemy.position = Position(4, 4)
        battle.perform_action(
            {"type": "skill", "unit_id": bard.unit_id, "skill_code": "chant", "target_unit_id": enemy.unit_id}
        )
        resolve_pending_chain(battle)
        self.assertEqual(enemy.mana_points, 2.0)

    def test_shensu_only_boosts_the_next_normal_move(self) -> None:
        battle = create_battle("fire_funeral", "ellie")
        fire = battle.player_units(1)[0]
        enemy = battle.player_units(2)[0]
        fire.position = Position(1, 4)
        enemy.position = Position(7, 7)

        battle.perform_action({"type": "skill", "unit_id": fire.unit_id, "skill_code": "shensu"})
        move_action = next(action for action in battle.action_snapshot_for(fire)["actions"] if action["code"] == "move")
        move_cells = {(cell["x"], cell["y"]) for cell in move_action["preview"]["cells"]}

        self.assertIn((6, 4), move_cells)
        self.assertTrue(fire.has_status("神速"))

        battle.perform_action({"type": "move", "unit_id": fire.unit_id, "x": 6, "y": 4})

        self.assertEqual(fire.position, Position(6, 4))
        self.assertIsNone(fire.get_status("神速"))

    def test_explicit_move_path_controls_pass_through_field_effects(self) -> None:
        battle = create_battle("dark_human", "fire_funeral")
        dark = battle.player_units(1)[0]
        fire = battle.player_units(2)[0]
        dark.position = Position(3, 3)
        fire.position = Position(7, 7)
        dark.max_health = 4.0
        dark.current_hp = 4.0
        battle.add_field_effect(GreatFireFuneralField(fire.unit_id, {(4, 4)}))

        battle.perform_action(
            {
                "type": "move",
                "unit_id": dark.unit_id,
                "x": 5,
                "y": 4,
                "path": [{"x": 4, "y": 4}, {"x": 5, "y": 4}],
            }
        )

        self.assertLess(dark.current_hp, 4.0)

        battle = create_battle("dark_human", "fire_funeral")
        dark = battle.player_units(1)[0]
        fire = battle.player_units(2)[0]
        dark.position = Position(3, 3)
        fire.position = Position(7, 7)
        dark.max_health = 4.0
        dark.current_hp = 4.0
        battle.add_field_effect(GreatFireFuneralField(fire.unit_id, {(4, 4)}))

        battle.perform_action(
            {
                "type": "move",
                "unit_id": dark.unit_id,
                "x": 5,
                "y": 4,
                "path": [{"x": 4, "y": 3}, {"x": 5, "y": 4}],
            }
        )

        self.assertEqual(dark.current_hp, 4.0)

    def test_stealthed_unit_cannot_move_onto_another_unit_cell(self) -> None:
        battle = create_battle("dark_human", "bard")
        dark = battle.player_units(1)[0]
        ally = create_hero("bard", 1)
        dark.position = Position(5, 4)
        battle.add_unit(ally, Position(4, 4))

        battle.perform_action({"type": "skill", "unit_id": dark.unit_id, "skill_code": "stealth"})
        with self.assertRaises(ActionError):
            battle.perform_action(
                {
                    "type": "move",
                    "unit_id": dark.unit_id,
                    "x": 4,
                    "y": 4,
                    "path": [{"x": 4, "y": 4}],
                }
            )

    def test_visible_unit_cannot_move_onto_a_stealthed_ally_cell(self) -> None:
        battle = create_battle("dark_human", "bard")
        dark = battle.player_units(1)[0]
        enemy = battle.player_units(2)[0]
        ally = create_hero("bard", 1)
        dark.position = Position(5, 4)
        enemy.position = Position(7, 7)
        battle.add_unit(ally, Position(4, 4))

        battle.perform_action({"type": "skill", "unit_id": dark.unit_id, "skill_code": "stealth"})
        with self.assertRaises(ActionError):
            battle.perform_action(
                {
                    "type": "move",
                    "unit_id": ally.unit_id,
                    "x": 5,
                    "y": 4,
                    "path": [{"x": 5, "y": 4}],
                }
            )

    def test_medusa_summon_on_stealthed_unit_does_not_open_chain_and_allows_overlap(self) -> None:
        battle = create_battle("ellie", "dark_human")
        ellie = battle.player_units(1)[0]
        dark = battle.player_units(2)[0]
        ellie.position = Position(4, 4)
        dark.position = Position(5, 4)
        dark.add_status(StatusEffect("隐身"))

        battle.perform_action({"type": "skill", "unit_id": ellie.unit_id, "skill_code": "medusa", "x": 5, "y": 4})

        medusa = next(unit for unit in battle.player_units(1) if unit.is_summon and getattr(unit, "hero_code", "") == "medusa")
        self.assertIsNone(battle.pending_chain)
        self.assertEqual(medusa.position, Position(5, 4))
        self.assertEqual(dark.position, Position(5, 4))
        with self.assertRaises(ActionError):
            battle.perform_action({"type": "attack", "unit_id": dark.unit_id, "target_unit_id": ellie.unit_id})

    def test_defend_twice_can_target_ally(self) -> None:
        battle = create_battle("bard", "ellie")
        bard = battle.player_units(1)[0]
        enemy = battle.player_units(2)[0]
        ally = create_hero("elite_soldier", 1)
        bard.position = Position(4, 4)
        enemy.position = Position(7, 7)
        battle.add_unit(ally, Position(5, 4))

        battle.perform_action({"type": "skill", "unit_id": bard.unit_id, "skill_code": "defend_twice", "target_unit_id": ally.unit_id})

        self.assertEqual(ally.stat("defense"), ally.base_stats.defense + 1)

    def test_machine_gun_hits_the_selected_three_cell_line(self) -> None:
        battle = create_battle("elite_soldier", "elite_soldier")
        soldier = battle.player_units(1)[0]
        enemy_front = battle.player_units(2)[0]
        enemy_mid = create_hero("elite_soldier", 2)
        enemy_offline = create_hero("elite_soldier", 2)
        soldier.position = Position(4, 4)
        enemy_front.position = Position(5, 4)
        battle.add_unit(enemy_mid, Position(6, 4))
        battle.add_unit(enemy_offline, Position(6, 5))
        for unit in (enemy_front, enemy_mid, enemy_offline):
            unit.max_health = 4.0
            unit.current_hp = 4.0

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": soldier.unit_id,
                "skill_code": "machine_gun",
                "cells": [{"x": 5, "y": 4}, {"x": 6, "y": 4}, {"x": 7, "y": 4}],
            }
        )
        while battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

        self.assertLess(enemy_front.current_hp, 4.0)
        self.assertLess(enemy_mid.current_hp, 4.0)
        self.assertEqual(enemy_offline.current_hp, 4.0)

    def test_machine_gun_can_hit_stealthed_enemy_with_cell_targeting(self) -> None:
        battle = create_battle("elite_soldier", "dark_human")
        soldier = battle.player_units(1)[0]
        dark = battle.player_units(2)[0]
        soldier.position = Position(4, 4)
        dark.position = Position(5, 4)
        dark.max_health = 4.0
        dark.current_hp = 4.0

        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "skill", "unit_id": dark.unit_id, "skill_code": "stealth"})
        battle.perform_action({"type": "end_turn"})
        battle.perform_action(
            {
                "type": "skill",
                "unit_id": soldier.unit_id,
                "skill_code": "machine_gun",
                "cells": [{"x": 5, "y": 4}, {"x": 6, "y": 4}, {"x": 7, "y": 4}],
            }
        )

        self.assertIsNotNone(battle.pending_chain)
        self.assertEqual(battle.pending_chain.current_unit_id(), dark.unit_id)
        battle.perform_action({"type": "chain_skip"})
        self.assertLess(dark.current_hp, 4.0)
        self.assertTrue(dark.has_status("隐身"))

    def test_machine_gun_accepts_any_contiguous_line_that_touches_the_caster(self) -> None:
        battle = create_battle("elite_soldier", "elite_soldier")
        soldier = battle.player_units(1)[0]
        enemy_bottom = battle.player_units(2)[0]
        enemy_mid = create_hero("elite_soldier", 2)
        enemy_offline = create_hero("elite_soldier", 2)
        soldier.position = Position(1, 0)
        enemy_bottom.position = Position(0, 2)
        battle.add_unit(enemy_mid, Position(0, 1))
        battle.add_unit(enemy_offline, Position(1, 2))
        for unit in (enemy_mid, enemy_bottom, enemy_offline):
            unit.max_health = 4.0
            unit.current_hp = 4.0

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": soldier.unit_id,
                "skill_code": "machine_gun",
                "cells": [{"x": 0, "y": 0}, {"x": 0, "y": 1}, {"x": 0, "y": 2}],
            }
        )
        while battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

        self.assertLess(enemy_mid.current_hp, 4.0)
        self.assertLess(enemy_bottom.current_hp, 4.0)
        self.assertEqual(enemy_offline.current_hp, 4.0)

    def test_machine_gun_rejects_lines_that_do_not_touch_the_caster(self) -> None:
        battle = create_battle("elite_soldier", "elite_soldier")
        soldier = battle.player_units(1)[0]
        target = battle.player_units(2)[0]
        soldier.position = Position(4, 4)
        target.position = Position(6, 4)

        with self.assertRaises(ActionError):
            battle.perform_action(
                {
                    "type": "skill",
                    "unit_id": soldier.unit_id,
                    "skill_code": "machine_gun",
                    "cells": [{"x": 6, "y": 4}, {"x": 7, "y": 4}],
                }
            )

    def test_headshot_only_allows_straight_line_attacks_for_this_turn(self) -> None:
        battle = create_battle("elite_soldier", "bard")
        soldier = battle.player_units(1)[0]
        enemy_offline = battle.player_units(2)[0]
        enemy_inline = create_hero("bard", 2)
        soldier.position = Position(4, 4)
        enemy_offline.position = Position(6, 5)
        battle.add_unit(enemy_inline, Position(6, 4))

        battle.perform_action({"type": "skill", "unit_id": soldier.unit_id, "skill_code": "headshot"})
        snapshot = battle.action_snapshot_for(soldier)
        attack_targets = set(snapshot["attack_targets"])

        self.assertIn(enemy_inline.unit_id, attack_targets)
        self.assertNotIn(enemy_offline.unit_id, attack_targets)
        with self.assertRaises(ActionError):
            battle.perform_action({"type": "attack", "unit_id": soldier.unit_id, "target_unit_id": enemy_offline.unit_id})

    def test_headshot_bonus_attack_expires_at_end_of_turn(self) -> None:
        battle = create_battle("elite_soldier", "bard")
        soldier = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        soldier.position = Position(4, 4)
        bard.position = Position(5, 4)
        bard.max_health = 5.0
        bard.current_hp = 5.0

        battle.perform_action({"type": "skill", "unit_id": soldier.unit_id, "skill_code": "headshot"})
        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "attack", "unit_id": soldier.unit_id, "target_unit_id": bard.unit_id})
        if battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

        self.assertEqual(bard.current_hp, 4.75)

    def test_headshot_preview_and_clicked_cell_only_allow_aligned_cells_on_multi_cell_target(self) -> None:
        battle = create_battle("elite_soldier", "rock_god")
        soldier = battle.player_units(1)[0]
        rock = battle.player_units(2)[0]
        soldier.position = Position(1, 1)
        rock.position = Position(3, 1)

        battle.perform_action({"type": "skill", "unit_id": soldier.unit_id, "skill_code": "headshot"})
        snapshot = battle.action_snapshot_for(soldier)
        attack_action = next(action for action in snapshot["actions"] if action["code"] == "attack")
        attack_cells = {(cell["x"], cell["y"]) for cell in attack_action["preview"]["cells"]}

        self.assertIn((3, 1), attack_cells)
        self.assertNotIn((4, 2), attack_cells)

        with self.assertRaises(ActionError):
            battle.perform_action(
                {
                    "type": "attack",
                    "unit_id": soldier.unit_id,
                    "target_unit_id": rock.unit_id,
                    "x": 4,
                    "y": 2,
                }
            )

    def test_basic_attack_clicked_cell_must_be_in_range_on_multi_cell_target(self) -> None:
        battle = create_battle("fire_funeral", "rock_god")
        fire = battle.player_units(1)[0]
        rock = battle.player_units(2)[0]
        fire.position = Position(1, 1)
        rock.position = Position(3, 1)

        with self.assertRaises(ActionError):
            battle.perform_action(
                {
                    "type": "attack",
                    "unit_id": fire.unit_id,
                    "target_unit_id": rock.unit_id,
                    "x": 4,
                    "y": 2,
                }
            )

        battle.perform_action(
            {
                "type": "attack",
                "unit_id": fire.unit_id,
                "target_unit_id": rock.unit_id,
                "x": 3,
                "y": 1,
            }
        )
        if battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

        self.assertLess(rock.current_hp, 1.0)

    def test_precision_training_proc_applies_through_shield(self) -> None:
        battle = create_battle("elite_soldier", "bard")
        soldier = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        soldier.position = Position(4, 4)
        bard.position = Position(5, 4)
        bard.shields = 1

        with mock.patch("wujiang.heroes.common.random.random", return_value=0.2):
            battle.perform_action({"type": "attack", "unit_id": soldier.unit_id, "target_unit_id": bard.unit_id})

        self.assertEqual(bard.shields, 0)
        self.assertEqual(bard.stat("speed"), 1.0)
        self.assertTrue(any(status.name == "迟缓" for status in bard.statuses))

    def test_backstep_shot_requires_exactly_two_cells_and_can_pass_through_units(self) -> None:
        battle = create_battle("elite_soldier", "elite_soldier")
        attacker = battle.player_units(1)[0]
        defender = battle.player_units(2)[0]
        blocker = create_hero("bard", 2)
        attacker.position = Position(4, 4)
        defender.position = Position(5, 4)
        battle.add_unit(blocker, Position(6, 4))

        battle.perform_action({"type": "attack", "unit_id": attacker.unit_id, "target_unit_id": defender.unit_id})
        self.assertIsNotNone(battle.pending_chain)
        reactions = battle.reaction_snapshot_for(defender)["actions"]
        backstep = next(action for action in reactions if action["action_code"] == "backstep_shot")
        preview_cells = {(cell["x"], cell["y"]) for cell in backstep["preview"]["cells"]}

        self.assertIn((7, 4), preview_cells)
        self.assertNotIn((6, 4), preview_cells)
        self.assertNotIn((5, 5), preview_cells)

        battle.perform_action(
            {
                "type": "chain_react",
                "unit_id": defender.unit_id,
                "action_code": "backstep_shot",
                "x": 7,
                "y": 4,
                "target_unit_id": attacker.unit_id,
            }
        )
        if battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

        self.assertEqual(defender.position, Position(7, 4))
        self.assertFalse(attacker.alive)

    def test_backstep_shot_can_retreat_without_follow_up_attack(self) -> None:
        battle = create_battle("elite_soldier", "elite_soldier")
        attacker = battle.player_units(1)[0]
        defender = battle.player_units(2)[0]
        decoy = create_hero("bard", 1)
        attacker.position = Position(4, 4)
        defender.position = Position(5, 4)
        battle.add_unit(decoy, Position(7, 5))
        attacker.max_health = 4.0
        attacker.current_hp = 4.0
        decoy.max_health = 4.0
        decoy.current_hp = 4.0

        battle.perform_action({"type": "attack", "unit_id": attacker.unit_id, "target_unit_id": defender.unit_id})
        self.assertIsNotNone(battle.pending_chain)
        self.assertFalse(battle.pending_followup_actions)
        battle.perform_action(
            {
                "type": "chain_react",
                "unit_id": defender.unit_id,
                "action_code": "backstep_shot",
                "x": 7,
                "y": 4,
            }
        )

        self.assertEqual(defender.position, Position(7, 4))
        self.assertTrue(attacker.alive)
        self.assertEqual(attacker.current_hp, 4.0)
        self.assertEqual(decoy.current_hp, 4.0)
        self.assertIsNone(battle.pending_chain)
        self.assertFalse(battle.pending_followup_actions)

    def test_backstep_shot_can_react_to_enemy_skill_as_second_use_this_turn(self) -> None:
        battle = create_battle("fire_funeral", "elite_soldier")
        fire = battle.player_units(1)[0]
        soldier = battle.player_units(2)[0]
        fire.position = Position(4, 4)
        soldier.position = Position(5, 4)

        battle.perform_action({"type": "attack", "unit_id": fire.unit_id, "target_unit_id": soldier.unit_id})
        self.assertIsNotNone(battle.pending_chain)
        battle.perform_action(
            {
                "type": "chain_react",
                "unit_id": soldier.unit_id,
                "action_code": "backstep_shot",
                "x": 7,
                "y": 4,
                "target_unit_id": fire.unit_id,
            }
        )
        while battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

        self.assertEqual(soldier.get_skill("backstep_shot").uses_this_turn, 1)
        self.assertEqual(soldier.position, Position(7, 4))

        battle.perform_action({"type": "skill", "unit_id": fire.unit_id, "skill_code": "great_funeral"})

        self.assertIsNotNone(battle.pending_chain)
        reactions = battle.reaction_snapshot_for(soldier)["actions"]
        self.assertTrue(any(action["action_code"] == "backstep_shot" for action in reactions))

        battle.perform_action(
            {
                "type": "chain_react",
                "unit_id": soldier.unit_id,
                "action_code": "backstep_shot",
                "x": 5,
                "y": 2,
            }
        )
        while battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

        self.assertEqual(soldier.get_skill("backstep_shot").uses_this_turn, 2)
        self.assertEqual(soldier.position, Position(5, 2))
        self.assertTrue(soldier.alive)

    def test_protection_shields_last_until_end_of_turn(self) -> None:
        battle = create_battle("ellie", "bard")
        ellie = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        ellie.position = Position(5, 4)
        bard.position = Position(6, 4)

        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "skill", "unit_id": ellie.unit_id, "skill_code": "drain_mana", "target_unit_id": bard.unit_id})

        self.assertIsNotNone(battle.pending_chain)
        battle.perform_action({"type": "chain_react", "unit_id": bard.unit_id, "action_code": "protection"})

        self.assertEqual(bard.current_mana, 4.0)
        self.assertEqual(bard.shields, 1)
        self.assertEqual(bard.temporary_shields, 0)

        battle.perform_action({"type": "end_turn"})

        self.assertEqual(bard.shields, 0)

    def test_protection_only_shields_the_user(self) -> None:
        battle = create_battle("fire_funeral", "bard")
        fire = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        ally = create_hero("ellie", 2)
        fire.position = Position(4, 4)
        bard.position = Position(5, 4)
        battle.add_unit(ally, Position(6, 4))
        bard.max_health = 4.0
        bard.current_hp = 4.0
        ally.max_health = 4.0
        ally.current_hp = 4.0

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": fire.unit_id,
                "skill_code": "pierce",
                "cells": [{"x": 5, "y": 4}, {"x": 6, "y": 4}],
            }
        )

        self.assertIsNotNone(battle.pending_chain)
        while battle.pending_chain is not None and battle.pending_chain.current_unit_id() != bard.unit_id:
            battle.perform_action({"type": "chain_skip"})
        self.assertIsNotNone(battle.pending_chain)
        reactions = battle.reaction_snapshot_for(bard)["actions"]
        protection = next(action for action in reactions if action["action_code"] == "protection")
        self.assertFalse(protection["preview"]["requires_target"])
        battle.perform_action({"type": "chain_react", "unit_id": bard.unit_id, "action_code": "protection"})
        while battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

        self.assertEqual(bard.current_hp, 4.0)
        self.assertLess(ally.current_hp, 4.0)

    def test_magic_wall_costs_one_mana_per_selected_target(self) -> None:
        battle = create_battle("fire_funeral", "bard")
        fire = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        ellie = create_hero("ellie", 2)
        fire.position = Position(4, 4)
        bard.position = Position(5, 4)
        battle.add_unit(ellie, Position(6, 4))
        ellie.current_mana = 5.0

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": fire.unit_id,
                "skill_code": "pierce",
                "cells": [{"x": 5, "y": 4}, {"x": 6, "y": 4}],
            }
        )

        self.assertIsNotNone(battle.pending_chain)
        while battle.pending_chain is not None and battle.pending_chain.current_unit_id() != ellie.unit_id:
            battle.perform_action({"type": "chain_skip"})
        self.assertIsNotNone(battle.pending_chain)
        battle.perform_action(
            {
                "type": "chain_react",
                "unit_id": ellie.unit_id,
                "action_code": "magic_wall",
                "target_unit_ids": [bard.unit_id, ellie.unit_id],
            }
        )

        self.assertEqual(ellie.current_mana, 3.0)
        self.assertEqual(bard.temporary_shields, 0)
        self.assertEqual(ellie.temporary_shields, 0)

    def test_magic_wall_rejects_selecting_more_targets_than_current_mana_allows(self) -> None:
        battle = create_battle("fire_funeral", "bard")
        fire = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        ellie = create_hero("ellie", 2)
        fire.position = Position(4, 4)
        bard.position = Position(5, 4)
        battle.add_unit(ellie, Position(6, 4))
        ellie.current_mana = 1.0

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": fire.unit_id,
                "skill_code": "pierce",
                "cells": [{"x": 5, "y": 4}, {"x": 6, "y": 4}],
            }
        )

        while battle.pending_chain is not None and battle.pending_chain.current_unit_id() != ellie.unit_id:
            battle.perform_action({"type": "chain_skip"})
        self.assertIsNotNone(battle.pending_chain)
        with self.assertRaises(ActionError):
            battle.perform_action(
                {
                    "type": "chain_react",
                    "unit_id": ellie.unit_id,
                    "action_code": "magic_wall",
                    "target_unit_ids": [bard.unit_id, ellie.unit_id],
                }
            )

    def test_evasion_moves_one_cell_and_attack_hits_original_cell(self) -> None:
        battle = create_battle("ellie", "dark_human")
        ellie = battle.player_units(1)[0]
        dark = battle.player_units(2)[0]
        ellie.position = Position(4, 4)
        dark.position = Position(5, 4)

        battle.perform_action({"type": "attack", "unit_id": ellie.unit_id, "target_unit_id": dark.unit_id})

        self.assertIsNotNone(battle.pending_chain)
        battle.perform_action(
            {"type": "chain_react", "unit_id": dark.unit_id, "action_code": "evasion", "x": 6, "y": 4}
        )

        self.assertEqual(dark.position, Position(6, 4))
        self.assertEqual(dark.current_hp, 1.0)

    def test_evasion_makes_direct_target_skill_hit_original_cell(self) -> None:
        battle = create_battle("ellie", "dark_human")
        ellie = battle.player_units(1)[0]
        dark = battle.player_units(2)[0]
        ellie.position = Position(4, 4)
        dark.position = Position(5, 4)

        battle.perform_action({"type": "skill", "unit_id": ellie.unit_id, "skill_code": "curse", "target_unit_id": dark.unit_id})

        self.assertIsNotNone(battle.pending_chain)
        battle.perform_action(
            {"type": "chain_react", "unit_id": dark.unit_id, "action_code": "evasion", "x": 6, "y": 4}
        )

        self.assertEqual(dark.position, Position(6, 4))
        self.assertEqual(ellie.current_hp, 1.0)
        self.assertFalse(any(type(status).__name__ == "CurseStatus" for status in dark.statuses))

    def test_evasion_can_choose_any_legal_cell_at_board_edge(self) -> None:
        battle = create_battle("ellie", "dark_human")
        ellie = battle.player_units(1)[0]
        dark = battle.player_units(2)[0]
        ellie.position = Position(1, 0)
        dark.position = Position(0, 0)

        battle.perform_action({"type": "attack", "unit_id": ellie.unit_id, "target_unit_id": dark.unit_id})

        self.assertIsNotNone(battle.pending_chain)
        battle.perform_action(
            {"type": "chain_react", "unit_id": dark.unit_id, "action_code": "evasion", "x": 0, "y": 1}
        )

        self.assertEqual(dark.position, Position(0, 1))
        self.assertEqual(dark.current_hp, 1.0)

    def test_evasion_requires_exactly_one_cell(self) -> None:
        battle = create_battle("ellie", "dark_human")
        ellie = battle.player_units(1)[0]
        dark = battle.player_units(2)[0]
        ellie.position = Position(4, 4)
        dark.position = Position(5, 4)

        battle.perform_action({"type": "attack", "unit_id": ellie.unit_id, "target_unit_id": dark.unit_id})

        self.assertIsNotNone(battle.pending_chain)
        with self.assertRaises(ActionError):
            battle.perform_action(
                {"type": "chain_react", "unit_id": dark.unit_id, "action_code": "evasion", "x": 7, "y": 4}
            )

    def test_evasion_can_move_to_adjacent_empty_cell_even_if_further_cell_is_blocked(self) -> None:
        battle = create_battle("ellie", "dark_human")
        ellie = battle.player_units(1)[0]
        dark = battle.player_units(2)[0]
        blocker = create_hero("bard", 2)
        ellie.position = Position(4, 4)
        dark.position = Position(5, 4)
        battle.add_unit(blocker, Position(6, 4))

        battle.perform_action({"type": "attack", "unit_id": ellie.unit_id, "target_unit_id": dark.unit_id})

        self.assertIsNotNone(battle.pending_chain)
        battle.perform_action(
            {"type": "chain_react", "unit_id": dark.unit_id, "action_code": "evasion", "x": 5, "y": 5}
        )
        if battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

        self.assertEqual(dark.position, Position(5, 5))
        self.assertEqual(blocker.position, Position(6, 4))
        self.assertEqual(dark.current_hp, 1.0)

    def test_evasion_preview_does_not_offer_current_cell_as_target(self) -> None:
        battle = create_battle("ellie", "dark_human")
        ellie = battle.player_units(1)[0]
        dark = battle.player_units(2)[0]
        ellie.position = Position(4, 4)
        dark.position = Position(5, 4)

        battle.perform_action({"type": "attack", "unit_id": ellie.unit_id, "target_unit_id": dark.unit_id})

        self.assertIsNotNone(battle.pending_chain)
        reactions = battle.reaction_snapshot_for(dark)["actions"]
        evasion = next(action for action in reactions if action["action_code"] == "evasion")

        self.assertEqual(evasion["preview"]["target_unit_ids"], [])
        self.assertEqual(evasion["preview"]["secondary_cells"], [dark.position.to_dict()])
        self.assertNotIn(dark.position.to_dict(), evasion["preview"]["cells"])

    def test_summon_cannot_act_on_entry_turn_but_can_act_next_owner_turn(self) -> None:
        battle = create_battle("ellie", "bard")
        ellie = battle.player_units(1)[0]
        ellie.position = Position(3, 4)

        battle.perform_action({"type": "skill", "unit_id": ellie.unit_id, "skill_code": "medusa", "x": 4, "y": 4})

        summon = next(unit for unit in battle.all_units() if unit.is_summon)
        self.assertFalse(summon.turn_ready)
        self.assertFalse(summon.can_take_turn_actions(battle))
        blink = next(action for action in battle.action_snapshot_for(summon)["actions"] if action["code"] == "medusa_blink")
        self.assertFalse(blink["available"])

        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})

        self.assertTrue(summon.turn_ready)
        self.assertTrue(summon.can_take_turn_actions(battle))
        blink = next(action for action in battle.action_snapshot_for(summon)["actions"] if action["code"] == "medusa_blink")
        self.assertTrue(blink["available"])

    def test_clone_is_destroyed_immediately_when_damage_connects(self) -> None:
        battle = create_battle("bard", "ellie")
        bard = battle.player_units(1)[0]
        ellie = battle.player_units(2)[0]
        bard.position = Position(4, 4)
        ellie.position = Position(5, 4)
        ellie.is_clone = True

        battle.perform_action({"type": "attack", "unit_id": bard.unit_id, "target_unit_id": ellie.unit_id})
        if battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

        self.assertFalse(ellie.alive)
        self.assertEqual(ellie.current_hp, 0.0)
        self.assertTrue(all(unit.unit_id != ellie.unit_id for unit in battle.all_units()))
        self.assertTrue(
            any("\u5206\u8eab" in line and "\u76f4\u63a5\u7834\u574f" in line for line in battle.logs)
        )

    def test_paralyzing_glove_breaks_one_shield_and_applies_effect(self) -> None:
        battle = create_battle("dark_human", "bard")
        dark = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        dark.position = Position(4, 4)
        bard.position = Position(5, 4)
        bard.max_health = 5.0
        bard.current_hp = 5.0
        bard.shields = 1

        battle.perform_action(
            {"type": "skill", "unit_id": dark.unit_id, "skill_code": "paralyzing_glove", "target_unit_id": bard.unit_id}
        )

        self.assertIsNone(battle.pending_chain)
        self.assertEqual(bard.current_hp, 4.5)
        self.assertEqual(bard.shields, 0)
        self.assertFalse(bard.cannot_move)
        self.assertTrue(bard.cannot_normal_move)

    def test_paralyzing_glove_only_blocks_normal_move_not_movement_skills(self) -> None:
        battle = create_battle("dark_human", "dark_human")
        attacker = battle.player_units(1)[0]
        target = battle.player_units(2)[0]
        attacker.position = Position(1, 1)
        target.position = Position(2, 1)
        target.max_health = 5.0
        target.current_hp = 5.0

        battle.perform_action(
            {"type": "skill", "unit_id": attacker.unit_id, "skill_code": "paralyzing_glove", "target_unit_id": target.unit_id}
        )
        if battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})
        battle.perform_action({"type": "end_turn"})

        actions = {action["code"]: action for action in battle.action_snapshot_for(target)["actions"]}
        self.assertFalse(actions["move"]["available"])
        self.assertTrue(actions["fly_leap"]["available"])

        battle.perform_action({"type": "skill", "unit_id": target.unit_id, "skill_code": "fly_leap", "x": 5, "y": 1})

        self.assertEqual(target.position, Position(5, 1))

    def test_fate_kick_moves_first_and_only_then_opens_chain_for_target_disappearance(self) -> None:
        battle = create_battle("dark_human", "bard")
        dark = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        dark.position = Position(4, 4)
        bard.position = Position(6, 4)

        with mock.patch("wujiang.heroes.first_five.random.random", return_value=0.6):
            battle.perform_action({"type": "skill", "unit_id": dark.unit_id, "skill_code": "fate_kick", "x": 5, "y": 4})
        self.assertEqual(dark.position, Position(5, 4))
        self.assertIsNotNone(battle.pending_chain)
        self.assertEqual(battle.pending_chain.queued_action.action_type, "skill_effect")
        self.assertEqual(battle.pending_chain.queued_action.payload.get("effect_code"), "banish")
        self.assertEqual(battle.pending_chain.current_unit_id(), bard.unit_id)
        self.assertFalse(bard.banished)

        battle.perform_action({"type": "chain_skip"})

        self.assertFalse(dark.banished)
        self.assertEqual(dark.position, Position(5, 4))
        self.assertTrue(bard.banished)
        self.assertEqual(bard.position, Position(6, 4))
        self.assertFalse(battle.is_occupied(Position(6, 4)))

    def test_fate_kick_self_banishes_without_opening_chain_when_coin_hits_heads(self) -> None:
        battle = create_battle("dark_human", "bard")
        dark = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        dark.position = Position(4, 4)
        bard.position = Position(6, 4)

        with mock.patch("wujiang.heroes.first_five.random.random", return_value=0.4):
            battle.perform_action({"type": "skill", "unit_id": dark.unit_id, "skill_code": "fate_kick", "x": 5, "y": 4})

        self.assertEqual(dark.position, Position(5, 4))
        self.assertTrue(dark.banished)
        self.assertFalse(bard.banished)
        self.assertIsNone(battle.pending_chain)

    def test_fate_kick_follow_up_obeys_shield_auto_block_before_chain(self) -> None:
        battle = create_battle("dark_human", "bard")
        dark = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        dark.position = Position(4, 4)
        bard.position = Position(6, 4)
        bard.shields = 1

        with mock.patch("wujiang.heroes.first_five.random.random", return_value=0.6):
            battle.perform_action({"type": "skill", "unit_id": dark.unit_id, "skill_code": "fate_kick", "x": 5, "y": 4})

        self.assertEqual(dark.position, Position(5, 4))
        self.assertEqual(bard.shields, 0)
        self.assertFalse(bard.banished)
        self.assertIsNone(battle.pending_chain)

    def test_ellie_trait_cancels_damage_from_unit_that_used_active_skill_and_logs_reason(self) -> None:
        battle = create_battle("dark_human", "ellie")
        dark = battle.player_units(1)[0]
        ellie = battle.player_units(2)[0]
        dark.position = Position(4, 4)
        ellie.position = Position(5, 4)
        ellie.max_health = 5.0
        ellie.current_hp = 5.0

        battle.perform_action({"type": "skill", "unit_id": dark.unit_id, "skill_code": "stealth"})
        battle.perform_action({"type": "attack", "unit_id": dark.unit_id, "target_unit_id": ellie.unit_id})

        self.assertIsNotNone(battle.pending_chain)
        battle.perform_action({"type": "chain_skip"})

        self.assertEqual(ellie.current_hp, 5.0)
        self.assertTrue(any("\u5df2\u4f7f\u7528\u8fc7\u4e3b\u52a8\u6280\u80fd" in line for line in battle.logs))

    def test_shielded_unit_auto_blocks_non_break_magic_without_chain(self) -> None:
        battle = create_battle("ellie", "bard")
        ellie = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        ellie.position = Position(4, 4)
        bard.position = Position(5, 4)
        bard.shields = 1
        mana_before = bard.current_mana

        battle.perform_action({"type": "skill", "unit_id": ellie.unit_id, "skill_code": "drain_mana", "target_unit_id": bard.unit_id})

        self.assertIsNone(battle.pending_chain)
        self.assertEqual(bard.shields, 0)
        self.assertEqual(bard.current_mana, mana_before)

    def test_direct_unit_skill_requires_line_selectable_target(self) -> None:
        battle = create_battle("bard", "fire_funeral")
        bard = battle.player_units(1)[0]
        enemy = battle.player_units(2)[0]
        ally = create_hero("elite_soldier", 1)
        bard.position = Position(3, 3)
        enemy.position = Position(7, 7)
        battle.add_unit(ally, Position(5, 4))
        ally.max_health = 2.0
        ally.current_hp = 1.0

        actions = {action["code"]: action for action in battle.action_snapshot_for(bard)["actions"]}
        self.assertNotIn(ally.unit_id, actions["heal"]["preview"]["target_unit_ids"])
        with self.assertRaises(ActionError):
            battle.perform_action({"type": "skill", "unit_id": bard.unit_id, "skill_code": "heal", "target_unit_id": ally.unit_id})

        ally.position = Position(5, 5)
        actions = {action["code"]: action for action in battle.action_snapshot_for(bard)["actions"]}
        self.assertIn(ally.unit_id, actions["heal"]["preview"]["target_unit_ids"])

        battle.perform_action({"type": "skill", "unit_id": bard.unit_id, "skill_code": "heal", "target_unit_id": ally.unit_id})
        self.assertAlmostEqual(ally.current_hp, 1.25)

    def test_shielded_unit_cannot_chain_against_break_magic(self) -> None:
        battle = create_battle("dark_human", "ellie")
        dark = battle.player_units(1)[0]
        ellie = battle.player_units(2)[0]
        dark.position = Position(4, 4)
        ellie.position = Position(5, 4)
        ellie.max_health = 5.0
        ellie.current_hp = 5.0
        ellie.shields = 1
        mana_before = ellie.current_mana

        battle.perform_action(
            {"type": "skill", "unit_id": dark.unit_id, "skill_code": "paralyzing_glove", "target_unit_id": ellie.unit_id}
        )

        self.assertIsNone(battle.pending_chain)
        self.assertEqual(ellie.current_mana, mana_before)
        self.assertEqual(ellie.current_hp, 4.0)
        self.assertEqual(ellie.shields, 0)
        self.assertFalse(ellie.cannot_move)
        self.assertTrue(ellie.cannot_normal_move)

    def test_magic_wall_can_chain_for_adjacent_ally(self) -> None:
        battle = create_battle("dark_human", "ellie")
        dark = battle.player_units(1)[0]
        helper = battle.player_units(2)[0]
        ally = create_hero("bard", 2)
        battle.add_unit(ally, Position(5, 4))
        dark.position = Position(4, 4)
        helper.position = Position(6, 4)
        ally.max_health = 5.0
        ally.current_hp = 5.0

        battle.perform_action({"type": "attack", "unit_id": dark.unit_id, "target_unit_id": ally.unit_id})

        self.assertIsNotNone(battle.pending_chain)
        self.assertEqual(battle.pending_chain.current_unit_id(), ally.unit_id)
        battle.perform_action({"type": "chain_skip"})
        self.assertEqual(battle.pending_chain.current_unit_id(), helper.unit_id)
        battle.perform_action(
            {
                "type": "chain_react",
                "unit_id": helper.unit_id,
                "action_code": "magic_wall",
                "target_unit_id": ally.unit_id,
            }
        )

        self.assertEqual(helper.current_mana, 4.0)
        self.assertEqual(ally.current_hp, 5.0)
        self.assertEqual(ally.shields, 0)

    def test_light_wall_requires_line_selectable_threatened_ally(self) -> None:
        battle = create_battle("fire_funeral", "element_hunter")
        fire = battle.player_units(1)[0]
        helper = battle.player_units(2)[0]
        ally = create_hero("bard", 2)
        fire.position = Position(4, 4)
        helper.position = Position(3, 5)
        battle.add_unit(ally, Position(5, 4))
        ally.max_health = 5.0
        ally.current_hp = 5.0

        battle.perform_action({"type": "attack", "unit_id": fire.unit_id, "target_unit_id": ally.unit_id})

        self.assertIsNotNone(battle.pending_chain)
        off_line_reactions = battle.reaction_snapshot_for(helper)["actions"]
        self.assertFalse(any(action["action_code"] == "light_wall" for action in off_line_reactions))

        battle = create_battle("fire_funeral", "element_hunter")
        fire = battle.player_units(1)[0]
        helper = battle.player_units(2)[0]
        ally = create_hero("bard", 2)
        fire.position = Position(4, 4)
        helper.position = Position(3, 4)
        battle.add_unit(ally, Position(5, 4))
        ally.max_health = 5.0
        ally.current_hp = 5.0

        battle.perform_action({"type": "attack", "unit_id": fire.unit_id, "target_unit_id": ally.unit_id})

        self.assertIsNotNone(battle.pending_chain)
        aligned_reactions = battle.reaction_snapshot_for(helper)["actions"]
        light_wall = next(action for action in aligned_reactions if action["action_code"] == "light_wall")
        self.assertIn(ally.unit_id, light_wall["preview"]["target_unit_ids"])

    def test_shielded_helper_cannot_chain_for_adjacent_ally(self) -> None:
        battle = create_battle("dark_human", "ellie")
        dark = battle.player_units(1)[0]
        helper = battle.player_units(2)[0]
        ally = create_hero("bard", 2)
        battle.add_unit(ally, Position(5, 4))
        dark.position = Position(4, 4)
        helper.position = Position(6, 4)
        helper.shields = 1
        helper_mana_before = helper.current_mana
        ally.max_health = 5.0
        ally.current_hp = 5.0

        battle.perform_action({"type": "attack", "unit_id": dark.unit_id, "target_unit_id": ally.unit_id})

        self.assertIsNotNone(battle.pending_chain)
        self.assertEqual(battle.pending_chain.current_unit_id(), ally.unit_id)
        battle.perform_action({"type": "chain_skip"})

        self.assertIsNone(battle.pending_chain)
        self.assertEqual(helper.current_mana, helper_mana_before)
        self.assertEqual(helper.shields, 1)
        self.assertLess(ally.current_hp, 5.0)

    def test_paralyzing_glove_does_not_remove_medusa_summon(self) -> None:
        battle = create_battle("dark_human", "ellie")
        dark = battle.player_units(1)[0]
        ellie = battle.player_units(2)[0]
        dark.position = Position(4, 4)
        ellie.position = Position(6, 4)

        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "skill", "unit_id": ellie.unit_id, "skill_code": "medusa", "x": 5, "y": 4})
        summon = next(unit for unit in battle.all_units() if unit.is_summon)
        battle.perform_action({"type": "end_turn"})

        battle.perform_action(
            {"type": "skill", "unit_id": dark.unit_id, "skill_code": "paralyzing_glove", "target_unit_id": summon.unit_id}
        )
        self.assertIsNotNone(battle.pending_chain)
        battle.perform_action({"type": "chain_skip"})

        self.assertTrue(summon.alive)
        self.assertEqual(summon.position, Position(5, 4))
        self.assertFalse(summon.cannot_move)
        self.assertTrue(summon.cannot_normal_move)

    def test_curse_triggers_on_target_owner_turn_start(self) -> None:
        battle = create_battle("ellie", "fire_funeral")
        ellie = battle.player_units(1)[0]
        target = battle.player_units(2)[0]
        ellie.position = Position(4, 4)
        target.position = Position(5, 4)
        target.max_health = 4.0
        target.current_hp = 4.0

        battle.perform_action({"type": "skill", "unit_id": ellie.unit_id, "skill_code": "curse", "target_unit_id": target.unit_id})

        self.assertIsNotNone(battle.pending_chain)
        battle.perform_action({"type": "chain_skip"})
        self.assertEqual(target.current_hp, 4.0)

        battle.perform_action({"type": "end_turn"})

        self.assertEqual(target.current_hp, 2.0)

    def test_summons_die_with_summoner_and_enemy_hero_destroyed_immediately_ends_game(self) -> None:
        battle = create_battle("ellie", "bard")
        ellie = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        ellie.position = Position(3, 4)
        bard.position = Position(6, 4)

        battle.perform_action({"type": "skill", "unit_id": ellie.unit_id, "skill_code": "medusa", "x": 4, "y": 4})

        summon = next(unit for unit in battle.all_units() if unit.is_summon)
        ellie.take_damage_fraction(ellie.current_hp)
        battle.cleanup_dead_units()

        self.assertEqual(battle.winner, 2)
        self.assertTrue(all(unit.unit_id != summon.unit_id for unit in battle.all_units()))

    def test_game_over_locks_all_follow_up_actions(self) -> None:
        battle = create_battle("ellie", "bard")
        bard = battle.player_units(2)[0]

        bard.take_damage_fraction(bard.current_hp)
        battle.cleanup_dead_units()

        self.assertEqual(battle.winner, 1)
        with self.assertRaises(ActionError):
            battle.perform_action({"type": "end_turn"})

    def test_judgment_fire_is_unavailable_until_attack_is_one(self) -> None:
        battle = create_battle("fire_funeral", "bard")
        fire = battle.player_units(1)[0]

        actions = {action["code"]: action for action in battle.action_snapshot_for(fire)["actions"]}
        self.assertFalse(actions["judgment_fire"]["available"])

        fire.base_stats.attack = 1
        actions = {action["code"]: action for action in battle.action_snapshot_for(fire)["actions"]}
        self.assertTrue(actions["judgment_fire"]["available"])

    def test_knockback_pushes_adjacent_units_but_original_attack_still_hits_shield(self) -> None:
        battle = create_battle("ellie", "fire_funeral")
        ellie = battle.player_units(1)[0]
        fire = battle.player_units(2)[0]
        ellie.position = Position(4, 4)
        fire.position = Position(5, 4)
        fire.current_mana = 2.0

        battle.perform_action({"type": "attack", "unit_id": ellie.unit_id, "target_unit_id": fire.unit_id})

        self.assertIsNotNone(battle.pending_chain)
        battle.perform_action({"type": "chain_react", "unit_id": fire.unit_id, "action_code": "knockback"})

        self.assertEqual(ellie.position, Position(3, 4))
        self.assertEqual(fire.current_hp, 1.0)
        self.assertEqual(fire.shields, 0)
        self.assertEqual(fire.current_mana, 1.0)

    def test_knockback_pushes_adjacent_medusa_summon(self) -> None:
        battle = create_battle("rock_god", "ellie")
        rock = battle.player_units(1)[0]
        ellie = battle.player_units(2)[0]
        rock.position = Position(4, 4)
        ellie.position = Position(3, 3)
        medusa = MedusaSummon(2)
        battle.add_unit(medusa, Position(6, 5))

        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "attack", "unit_id": ellie.unit_id, "target_unit_id": rock.unit_id})

        self.assertIsNotNone(battle.pending_chain)
        battle.perform_action({"type": "chain_react", "unit_id": rock.unit_id, "action_code": "knockback"})

        self.assertEqual(medusa.position, Position(7, 6))

    def test_knockback_respects_field_when_pushing(self) -> None:
        battle = create_battle("ellie", "fire_funeral")
        ellie = battle.player_units(1)[0]
        fire = battle.player_units(2)[0]
        ellie.position = Position(4, 4)
        fire.position = Position(5, 4)
        battle.add_field_effect(GreatFireFuneralField(fire.unit_id, {(3, 4)}))

        battle.perform_action({"type": "attack", "unit_id": ellie.unit_id, "target_unit_id": fire.unit_id})

        self.assertIsNotNone(battle.pending_chain)
        battle.perform_action({"type": "chain_react", "unit_id": fire.unit_id, "action_code": "knockback"})

        self.assertEqual(ellie.position, Position(4, 4))
        self.assertEqual(fire.current_hp, 1.0)
        self.assertEqual(fire.shields, 0)

    def test_knockback_requires_one_mana_to_chain(self) -> None:
        battle = create_battle("ellie", "fire_funeral")
        ellie = battle.player_units(1)[0]
        fire = battle.player_units(2)[0]
        ellie.position = Position(4, 4)
        fire.position = Position(5, 4)
        fire.current_mana = 0.0

        battle.perform_action({"type": "attack", "unit_id": ellie.unit_id, "target_unit_id": fire.unit_id})

        self.assertIsNotNone(battle.pending_chain)
        with self.assertRaises(ActionError):
            battle.perform_action({"type": "chain_react", "unit_id": fire.unit_id, "action_code": "knockback"})

    def test_pierce_hits_two_cells_without_break_magic(self) -> None:
        battle = create_battle("fire_funeral", "bard")
        fire = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        soldier = create_hero("elite_soldier", 2)
        fire.position = Position(4, 4)
        bard.position = Position(5, 4)
        bard.shields = 1
        battle.add_unit(soldier, Position(6, 4))

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": fire.unit_id,
                "skill_code": "pierce",
                "cells": [{"x": 5, "y": 4}, {"x": 6, "y": 4}],
            }
        )

        if battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})
        self.assertEqual(bard.shields, 0)
        self.assertEqual(bard.current_hp, 1.0)
        self.assertFalse(soldier.alive)

    def test_pierce_requires_full_pattern_away_from_edge(self) -> None:
        battle = create_battle("fire_funeral", "bard")
        fire = battle.player_units(1)[0]
        fire.position = Position(4, 4)

        with self.assertRaises(ActionError):
            battle.perform_action(
                {
                    "type": "skill",
                    "unit_id": fire.unit_id,
                    "skill_code": "pierce",
                    "cells": [{"x": 5, "y": 4}],
                }
            )

    def test_pierce_allows_edge_truncated_line(self) -> None:
        battle = create_battle("fire_funeral", "bard")
        fire = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        fire.position = Position(6, 4)
        bard.position = Position(7, 4)
        bard.max_health = 4.0
        bard.current_hp = 4.0

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": fire.unit_id,
                "skill_code": "pierce",
                "cells": [{"x": 7, "y": 4}],
            }
        )
        if battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

        self.assertLess(bard.current_hp, 4.0)

    def test_pierce_accepts_any_contiguous_line_that_touches_the_caster(self) -> None:
        battle = create_battle("fire_funeral", "bard")
        fire = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        ally = create_hero("elite_soldier", 2)
        fire.position = Position(1, 0)
        bard.position = Position(0, 0)
        battle.add_unit(ally, Position(0, 1))
        bard.max_health = 4.0
        bard.current_hp = 4.0
        ally.max_health = 4.0
        ally.current_hp = 4.0

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": fire.unit_id,
                "skill_code": "pierce",
                "cells": [{"x": 0, "y": 0}, {"x": 0, "y": 1}],
            }
        )
        while battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

        self.assertLess(bard.current_hp, 4.0)
        self.assertLess(ally.current_hp, 4.0)

    def test_pierce_rejects_lines_that_do_not_touch_the_caster(self) -> None:
        battle = create_battle("fire_funeral", "bard")
        fire = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        fire.position = Position(4, 4)
        bard.position = Position(6, 4)

        with self.assertRaises(ActionError):
            battle.perform_action(
                {
                    "type": "skill",
                    "unit_id": fire.unit_id,
                    "skill_code": "pierce",
                    "cells": [{"x": 6, "y": 4}, {"x": 7, "y": 4}],
                }
            )

    def test_banished_hero_does_not_count_as_destroyed_for_victory(self) -> None:
        battle = create_battle("dark_human", "bard")
        dark = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        dark.position = Position(4, 4)
        bard.position = Position(6, 4)

        with mock.patch("wujiang.heroes.first_five.random.random", return_value=0.6):
            battle.perform_action({"type": "skill", "unit_id": dark.unit_id, "skill_code": "fate_kick", "x": 5, "y": 4})
            self.assertIsNotNone(battle.pending_chain)
            battle.perform_action({"type": "chain_skip"})

        self.assertTrue(bard.banished)
        self.assertIsNone(battle.winner)

    def test_banished_unit_reappears_on_nearest_available_cell_when_origin_is_occupied(self) -> None:
        battle = create_battle("dark_human", "bard")
        dark = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        occupier = create_hero("elite_soldier", 1)
        dark.position = Position(4, 4)
        bard.position = Position(6, 4)

        with mock.patch("wujiang.heroes.first_five.random.random", return_value=0.6):
            battle.perform_action({"type": "skill", "unit_id": dark.unit_id, "skill_code": "fate_kick", "x": 5, "y": 4})
            self.assertIsNotNone(battle.pending_chain)
            battle.perform_action({"type": "chain_skip"})

        battle.add_unit(occupier, Position(6, 4))
        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})

        prompt = battle.current_respawn_prompt()
        self.assertIsNotNone(prompt)
        self.assertEqual(prompt.unit_id, bard.unit_id)
        self.assertTrue(all(prompt.origin.distance_to(cell) == 1 for cell in prompt.options))

        with self.assertRaises(ActionError):
            battle.perform_action({"type": "end_turn"})

        battle.perform_action({"type": "respawn_select", "unit_id": bard.unit_id, "x": 7, "y": 4})

        self.assertFalse(bard.banished)
        self.assertEqual(bard.position, Position(7, 4))

    def test_great_fire_funeral_field_triggers_on_unit_owners_turn_end(self) -> None:
        battle = create_battle("fire_funeral", "bard")
        fire = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        enemy = create_hero("elite_soldier", 2)
        fire.position = Position(4, 4)
        bard.position = Position(7, 7)

        battle.perform_action({"type": "skill", "unit_id": fire.unit_id, "skill_code": "great_funeral"})
        if battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

        battle.add_unit(enemy, Position(0, 4))
        enemy.base_stats.defense = 5
        enemy.max_health = 4.0
        enemy.current_hp = 4.0
        hp_before = enemy.current_hp

        battle.perform_action({"type": "end_turn"})
        self.assertEqual(enemy.current_hp, hp_before)

        battle.perform_action({"type": "end_turn"})
        self.assertEqual(enemy.current_hp, 3.5)

    def test_great_fire_funeral_field_does_not_stack_overlapping_area(self) -> None:
        battle = create_battle("fire_funeral", "bard")
        fire = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        fire.position = Position(7, 7)
        bard.position = Position(4, 4)
        bard.base_stats.defense = 5
        bard.max_health = 4.0
        bard.current_hp = 4.0

        battle.add_field_effect(GreatFireFuneralField(fire.unit_id, {(4, 4), (4, 5)}))
        battle.add_field_effect(GreatFireFuneralField(fire.unit_id, {(4, 4), (5, 4)}))

        self.assertEqual(len(battle.field_effects), 1)
        public_effect = battle.to_public_dict()["field_effects"][0]
        self.assertIn({"x": 4, "y": 4}, public_effect["cells"])
        self.assertIn({"x": 4, "y": 5}, public_effect["cells"])
        self.assertIn({"x": 5, "y": 4}, public_effect["cells"])

        battle.perform_action({"type": "end_turn"})
        self.assertEqual(bard.current_hp, 4.0)
        battle.perform_action({"type": "end_turn"})

        self.assertEqual(bard.current_hp, 3.5)

    def test_great_fire_funeral_uses_attack_five_damage_rule_and_exposes_field_cells(self) -> None:
        battle = create_battle("fire_funeral", "bard")
        fire = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        fire.position = Position(4, 4)
        bard.position = Position(6, 4)
        bard.base_stats.defense = 5
        bard.max_health = 4.0
        bard.current_hp = 4.0

        battle.perform_action({"type": "skill", "unit_id": fire.unit_id, "skill_code": "great_funeral"})
        if battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

        self.assertEqual(bard.current_hp, 3.5)
        public_effect = battle.to_public_dict()["field_effects"][0]
        self.assertEqual(public_effect["board_marker"], "火")
        self.assertIn({"x": 0, "y": 4}, public_effect["cells"])
        self.assertIn({"x": 4, "y": 7}, public_effect["cells"])

    def test_block_only_applies_to_the_next_damage_during_that_chain(self) -> None:
        battle = create_battle("dark_human", "fire_funeral")
        dark = battle.player_units(1)[0]
        fire = battle.player_units(2)[0]
        dark.position = Position(4, 4)
        fire.position = Position(5, 4)

        battle.perform_action({"type": "attack", "unit_id": dark.unit_id, "target_unit_id": fire.unit_id})

        self.assertIsNotNone(battle.pending_chain)
        battle.perform_action({"type": "chain_react", "unit_id": fire.unit_id, "action_code": "block"})

        self.assertAlmostEqual(fire.current_hp, 0.75, places=4)
        self.assertIsNone(fire.get_status("格挡"))

    def test_into_darkness_attack_breaks_stealth_and_buffs_that_attack(self) -> None:
        battle = create_battle("dark_human", "elite_soldier")
        dark = battle.player_units(1)[0]
        soldier = battle.player_units(2)[0]
        dark.position = Position(4, 4)
        soldier.position = Position(5, 4)
        soldier.shields = 1
        soldier.max_health = 4.0
        soldier.current_hp = 4.0

        battle.perform_action({"type": "skill", "unit_id": dark.unit_id, "skill_code": "into_darkness"})

        self.assertTrue(dark.has_status("隐身"))
        self.assertTrue(dark.has_status("遁入黑暗"))

        battle.perform_action({"type": "attack", "unit_id": dark.unit_id, "target_unit_id": soldier.unit_id})

        self.assertFalse(dark.has_status("隐身"))
        self.assertIsNone(dark.get_status("黑暗突袭"))
        self.assertEqual(soldier.shields, 0)
        self.assertLess(soldier.current_hp, 4.0)

    def test_into_darkness_skill_breaks_stealth_without_leaving_attack_buff(self) -> None:
        battle = create_battle("dark_human", "elite_soldier")
        dark = battle.player_units(1)[0]
        soldier = battle.player_units(2)[0]
        dark.position = Position(4, 4)
        soldier.position = Position(5, 4)

        battle.perform_action({"type": "skill", "unit_id": dark.unit_id, "skill_code": "into_darkness"})
        battle.perform_action(
            {"type": "skill", "unit_id": dark.unit_id, "skill_code": "paralyzing_glove", "target_unit_id": soldier.unit_id}
        )
        if battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

        self.assertFalse(dark.has_status("隐身"))
        self.assertIsNone(dark.get_status("黑暗突袭"))


    def test_fly_leap_next_to_enemy_does_not_open_chain_when_no_effect_applies(self) -> None:
        battle = create_battle("dark_human", "bard")
        dark = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        dark.position = Position(1, 4)
        bard.position = Position(6, 4)

        battle.perform_action({"type": "skill", "unit_id": dark.unit_id, "skill_code": "fly_leap", "x": 4, "y": 4})

        self.assertEqual(dark.position, Position(4, 4))
        self.assertIsNone(battle.pending_chain)

    def test_fly_leap_requires_exactly_three_cells(self) -> None:
        battle = create_battle("dark_human", "bard")
        dark = battle.player_units(1)[0]
        dark.position = Position(3, 3)

        actions = {action["code"]: action for action in battle.action_snapshot_for(dark)["actions"]}
        leap_cells = {(cell["x"], cell["y"]) for cell in actions["fly_leap"]["preview"]["cells"]}

        self.assertIn((6, 3), leap_cells)
        self.assertNotIn((5, 3), leap_cells)

        with self.assertRaises(ActionError):
            battle.perform_action({"type": "skill", "unit_id": dark.unit_id, "skill_code": "fly_leap", "x": 5, "y": 3})

    def test_pending_chain_state_includes_source_effect_summary(self) -> None:
        battle = create_battle("dark_human", "ellie")
        dark = battle.player_units(1)[0]
        ellie = battle.player_units(2)[0]
        dark.position = Position(4, 4)
        ellie.position = Position(5, 4)

        battle.perform_action(
            {"type": "skill", "unit_id": dark.unit_id, "skill_code": "paralyzing_glove", "target_unit_id": ellie.unit_id}
        )

        self.assertIsNotNone(battle.pending_chain)
        pending_chain = battle.to_public_dict()["pending_chain"]
        self.assertIn("【麻痹手套】", pending_chain["queued_action_effect_summary"])
        self.assertIn("破魔", pending_chain["queued_action_effect_summary"])
        self.assertIn("不能普通移动", pending_chain["queued_action_effect_summary"])
        self.assertIn("艾莉", pending_chain["queued_action_effect_summary"])

    def test_single_effect_composite_skills_keep_one_skill_reaction_window(self) -> None:
        battle = create_battle("ellie", "bard")
        ellie = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        ellie.position = Position(4, 4)
        bard.position = Position(5, 4)
        battle.perform_action(
            {
                "type": "skill",
                "unit_id": ellie.unit_id,
                "skill_code": "mana_pull",
                "target_unit_id": bard.unit_id,
                "dest_x": 7,
                "dest_y": 4,
            }
        )
        self.assertIsNotNone(battle.pending_chain)
        self.assertEqual(battle.pending_chain.queued_action.action_type, "skill")
        self.assertEqual(battle.pending_chain.queued_action.payload["skill_code"], "mana_pull")

        battle = create_battle("dark_human", "bard")
        dark = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        dark.position = Position(4, 4)
        bard.position = Position(5, 4)
        battle.perform_action(
            {"type": "skill", "unit_id": dark.unit_id, "skill_code": "paralyzing_glove", "target_unit_id": bard.unit_id}
        )
        self.assertIsNotNone(battle.pending_chain)
        self.assertEqual(battle.pending_chain.queued_action.action_type, "skill")
        self.assertEqual(battle.pending_chain.queued_action.payload["skill_code"], "paralyzing_glove")

        battle = create_battle("element_hunter", "bard")
        hunter = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        hunter.position = Position(0, 0)
        bard.position = Position(2, 2)
        burn_cells = [{"x": x, "y": y} for x in range(0, 4) for y in range(0, 4)]
        battle.perform_action(
            {"type": "skill", "unit_id": hunter.unit_id, "skill_code": "complete_burn", "cells": burn_cells}
        )
        self.assertIsNotNone(battle.pending_chain)
        self.assertEqual(battle.pending_chain.queued_action.action_type, "skill")
        self.assertEqual(battle.pending_chain.queued_action.payload["skill_code"], "complete_burn")

        battle = create_battle("element_hunter", "bard")
        hunter = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        hunter.position = Position(0, 0)
        bard.position = Position(2, 2)
        blizzard_cells = [{"x": x, "y": y} for x in range(0, 3) for y in range(0, 3)]
        battle.perform_action(
            {"type": "skill", "unit_id": hunter.unit_id, "skill_code": "blizzard", "cells": blizzard_cells}
        )
        self.assertIsNotNone(battle.pending_chain)
        self.assertEqual(battle.pending_chain.queued_action.action_type, "skill")
        self.assertEqual(battle.pending_chain.queued_action.payload["skill_code"], "blizzard")

        battle = create_battle("fire_funeral", "bard")
        fire = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        fire.position = Position(4, 4)
        bard.position = Position(5, 4)
        fire.base_stats.attack = 1
        battle.perform_action({"type": "skill", "unit_id": fire.unit_id, "skill_code": "judgment_fire"})
        self.assertIsNotNone(battle.pending_chain)
        self.assertEqual(battle.pending_chain.queued_action.action_type, "skill")
        self.assertEqual(battle.pending_chain.queued_action.payload["skill_code"], "judgment_fire")

    def test_pending_chain_attack_summary_includes_attack_value(self) -> None:
        battle = create_battle("dark_human", "ellie")
        dark = battle.player_units(1)[0]
        ellie = battle.player_units(2)[0]
        dark.position = Position(4, 4)
        ellie.position = Position(5, 4)

        battle.perform_action({"type": "attack", "unit_id": dark.unit_id, "target_unit_id": ellie.unit_id})

        self.assertIsNotNone(battle.pending_chain)
        pending_chain = battle.to_public_dict()["pending_chain"]
        self.assertIn("【普攻】", pending_chain["queued_action_effect_summary"])
        self.assertIn("攻 3", pending_chain["queued_action_effect_summary"])
        self.assertIn("艾莉", pending_chain["queued_action_effect_summary"])

    def test_headshot_chain_summary_includes_bonus_attack_and_basic_attack_effects(self) -> None:
        battle = create_battle("elite_soldier", "dark_human")
        soldier = battle.player_units(1)[0]
        dark = battle.player_units(2)[0]
        soldier.position = Position(1, 4)
        dark.position = Position(4, 4)

        battle.perform_action({"type": "skill", "unit_id": soldier.unit_id, "skill_code": "headshot"})
        battle.perform_action({"type": "attack", "unit_id": soldier.unit_id, "target_unit_id": dark.unit_id})

        self.assertIsNotNone(battle.pending_chain)
        summary = battle.to_public_dict()["pending_chain"]["queued_action_effect_summary"]
        self.assertIn("攻 5", summary)
        self.assertIn("破魔", summary)
        self.assertIn("爆头强化", summary)
        self.assertIn("压制射击", summary)

    def test_all_non_special_shields_expire_at_end_of_turn(self) -> None:
        battle = create_battle("ellie", "bard")
        ellie = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        ellie.shields = 1
        ellie.temporary_shields = 2
        bard.shields = 1

        battle.perform_action({"type": "end_turn"})

        self.assertEqual(ellie.shields, 0)
        self.assertEqual(ellie.temporary_shields, 0)
        self.assertEqual(bard.shields, 0)

    def test_complete_burn_area_effect_breaks_shield_and_ticks_mana_on_owner_turn_start(self) -> None:
        battle = create_battle("element_hunter", "bard")
        hunter = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        bard.position = Position(3, 4)
        bard.shields = 1
        hp_before = bard.current_hp
        mana_before = bard.current_mana
        cells = [{"x": x, "y": y} for x in range(2, 6) for y in range(3, 7)]

        battle.perform_action(
            {"type": "skill", "unit_id": hunter.unit_id, "skill_code": "complete_burn", "cells": cells}
        )

        self.assertIsNone(battle.pending_chain)

        self.assertEqual(bard.shields, 0)
        self.assertEqual(bard.current_hp, hp_before)
        self.assertTrue(bard.has_status("完全燃烧"))

        battle.perform_action({"type": "end_turn"})

        self.assertEqual(bard.current_mana, mana_before - 1)

    def test_element_hunter_raw_skill_text_mentions_complete_burn_initial_damage(self) -> None:
        battle = create_battle("element_hunter", "bard")
        hunter = battle.player_units(1)[0]

        self.assertIn("完全燃烧", hunter.raw_skill_text)
        self.assertIn("造成当前攻伤害", hunter.raw_skill_text)

    def test_remote_area_requires_complete_rectangle_but_allows_edge_truncation(self) -> None:
        battle = create_battle("element_hunter", "bard")
        hunter = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        hunter.position = Position(0, 0)
        bard.position = Position(2, 2)

        with self.assertRaises(ActionError):
            battle.perform_action(
                {
                    "type": "skill",
                    "unit_id": hunter.unit_id,
                    "skill_code": "complete_burn",
                    "cells": [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 0, "y": 1}],
                }
            )

        edge_cells = [{"x": x, "y": y} for x in range(0, 3) for y in range(0, 3)]
        battle.perform_action(
            {"type": "skill", "unit_id": hunter.unit_id, "skill_code": "complete_burn", "cells": edge_cells}
        )
        self.assertIsNotNone(battle.pending_chain)
        battle.perform_action({"type": "chain_skip"})

        self.assertTrue(bard.has_status("完全燃烧"))

    def test_plant_growth_area_allows_edge_truncation(self) -> None:
        battle = create_battle("element_hunter", "bard")
        hunter = battle.player_units(1)[0]
        hunter.position = Position(0, 0)

        with self.assertRaises(ActionError):
            battle.perform_action(
                {
                    "type": "skill",
                    "unit_id": hunter.unit_id,
                    "skill_code": "plant_growth",
                    "cells": [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 0, "y": 1}],
                }
            )

        edge_cells = [{"x": x, "y": y} for x in range(0, 3) for y in range(0, 3)]
        battle.perform_action(
            {"type": "skill", "unit_id": hunter.unit_id, "skill_code": "plant_growth", "cells": edge_cells}
        )

        self.assertTrue(any(effect.name == "植物生长" for effect in battle.field_effects))

    def test_plant_growth_expires_at_hunter_next_turn_start_not_after_two_global_turns(self) -> None:
        battle = create_battle(["dark_human", "element_hunter"], ["bard"])
        hunter = next(unit for unit in battle.hero_units(1) if unit.hero_code == "element_hunter")
        cells = [{"x": x, "y": y} for x in range(2, 7) for y in range(2, 7)]

        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "skill", "unit_id": hunter.unit_id, "skill_code": "plant_growth", "cells": cells})

        self.assertTrue(any(effect.name == "植物生长" for effect in battle.field_effects))

        battle.perform_action({"type": "end_turn"})
        self.assertTrue(any(effect.name == "植物生长" for effect in battle.field_effects))
        battle.perform_action({"type": "end_turn"})
        self.assertTrue(any(effect.name == "植物生长" for effect in battle.field_effects))
        battle.perform_action({"type": "end_turn"})

        self.assertFalse(any(effect.name == "植物生长" for effect in battle.field_effects))

    def test_plant_growth_charges_two_move_points_when_step_starts_in_area(self) -> None:
        battle = create_battle("element_hunter", "bard")
        hunter = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        cells = [{"x": x, "y": y} for x in range(2, 7) for y in range(2, 7)]

        battle.perform_action(
            {"type": "skill", "unit_id": hunter.unit_id, "skill_code": "plant_growth", "cells": cells}
        )
        if battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})
        battle.perform_action({"type": "end_turn"})

        with self.assertRaises(ActionError):
            battle.perform_action({"type": "move", "unit_id": bard.unit_id, "x": 4, "y": 4})

        battle.perform_action({"type": "move", "unit_id": bard.unit_id, "x": 5, "y": 4})

        self.assertEqual(bard.position, Position(5, 4))

    def test_plant_growth_does_not_charge_extra_for_steps_entering_area(self) -> None:
        battle = create_battle("element_hunter", "bard")
        hunter = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        bard.position = Position(7, 4)
        bard.base_stats.speed = 1
        cells = [{"x": x, "y": y} for x in range(2, 7) for y in range(2, 7)]

        battle.perform_action(
            {"type": "skill", "unit_id": hunter.unit_id, "skill_code": "plant_growth", "cells": cells}
        )
        if battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})
        battle.perform_action({"type": "end_turn"})

        battle.perform_action(
            {
                "type": "move",
                "unit_id": bard.unit_id,
                "x": 6,
                "y": 4,
                "path": [{"x": 6, "y": 4}],
            }
        )

        self.assertEqual(bard.position, Position(6, 4))

    def test_plant_growth_affects_flying_units(self) -> None:
        battle = create_battle("element_hunter", "dark_human")
        hunter = battle.player_units(1)[0]
        dark = battle.player_units(2)[0]
        dark.position = Position(6, 4)
        dark.base_stats.speed = 2
        cells = [{"x": x, "y": y} for x in range(2, 7) for y in range(2, 7)]

        battle.perform_action(
            {"type": "skill", "unit_id": hunter.unit_id, "skill_code": "plant_growth", "cells": cells}
        )
        if battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})
        battle.perform_action({"type": "end_turn"})

        self.assertTrue(dark.has_flying)
        with self.assertRaises(ActionError):
            battle.perform_action({"type": "move", "unit_id": dark.unit_id, "x": 4, "y": 4})

        battle.perform_action({"type": "move", "unit_id": dark.unit_id, "x": 5, "y": 4})
        self.assertEqual(dark.position, Position(5, 4))

    def test_thunder_god_resets_when_destroyed_by_enemy_attack_damage(self) -> None:
        battle = create_battle("element_hunter", "fire_funeral")
        hunter = battle.player_units(1)[0]
        fire = battle.player_units(2)[0]
        hunter.position = Position(1, 4)
        fire.position = Position(4, 4)

        battle.perform_action({"type": "skill", "unit_id": hunter.unit_id, "skill_code": "thunder_god", "x": 3, "y": 4})

        thunder = next(unit for unit in battle.all_units() if unit.name == "雷神")
        skill = hunter.get_skill("thunder_god")
        self.assertEqual(skill.uses_this_battle, 1)
        self.assertFalse(thunder.turn_ready)

        battle.perform_action({"type": "end_turn"})
        fire.base_stats.attack = 6
        battle.perform_action({"type": "attack", "unit_id": fire.unit_id, "target_unit_id": thunder.unit_id})
        self.assertIsNotNone(battle.pending_chain)
        battle.perform_action({"type": "chain_skip"})

        self.assertEqual(skill.uses_this_battle, 0)
        self.assertTrue(all(unit.unit_id != thunder.unit_id for unit in battle.all_units()))

    def test_earth_walker_clone_can_act_this_turn_and_expires_next_owner_turn_start(self) -> None:
        battle = create_battle("element_hunter", "bard")
        hunter = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        original_position = hunter.position

        battle.perform_action({"type": "skill", "unit_id": hunter.unit_id, "skill_code": "earth_walker", "x": 2, "y": 4})

        clones = [unit for unit in battle.all_units() if unit.is_clone]
        self.assertEqual(len(clones), 1)
        clone = clones[0]
        self.assertEqual(hunter.position, Position(2, 4))
        self.assertEqual(clone.position, original_position)
        self.assertFalse(hunter.turn_ready)
        self.assertTrue(clone.turn_ready)
        self.assertTrue(clone.can_take_turn_actions(battle))
        self.assertAlmostEqual(hunter.current_mana, 5)
        self.assertTrue(clone.cannot_attack)
        self.assertTrue(clone.cannot_use_skills)
        self.assertEqual(clone.skills, [])

        bard.position = Position(1, 5)
        with self.assertRaises(ActionError):
            battle.perform_action({"type": "attack", "unit_id": clone.unit_id, "target_unit_id": bard.unit_id})

        battle.perform_action({"type": "end_turn"})
        self.assertTrue(any(unit.unit_id == clone.unit_id for unit in battle.all_units()))
        battle.perform_action({"type": "end_turn"})

        self.assertTrue(all(unit.unit_id != clone.unit_id for unit in battle.all_units()))

    def test_water_wave_raises_max_mana_without_refilling_current_mana(self) -> None:
        battle = create_battle("element_hunter", "bard")
        hunter = battle.player_units(1)[0]

        battle.perform_action({"type": "skill", "unit_id": hunter.unit_id, "skill_code": "water_wave"})

        self.assertEqual(hunter.stat("attack"), 4)
        self.assertEqual(hunter.stat("defense"), 4)
        self.assertEqual(hunter.stat("speed"), 3)
        self.assertEqual(hunter.targeting_range(), 3)
        self.assertEqual(hunter.max_mana(), 6)
        self.assertEqual(hunter.current_mana, 5)
        self.assertEqual(hunter.get_skill("water_wave").cooldown_remaining, 8)

    def test_lina_occupies_four_cells_and_counts_range_from_any_cell(self) -> None:
        battle = create_battle("undead_king_lina", "bard")
        lina = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        bard.position = Position(5, 4)

        self.assertEqual(
            {(cell.x, cell.y) for cell in battle.unit_cells(lina)},
            {(1, 4), (1, 5), (2, 4), (2, 5)},
        )
        self.assertIn(lina, battle.units_at(Position(2, 5)))
        self.assertTrue(battle.attack_target_allowed(lina, bard)[0])
        self.assertEqual(len(battle.to_public_dict()["units"][0]["occupied_cells"]), 4)

    def test_lina_half_pierce_breaks_shield_and_deals_reduced_attack_damage(self) -> None:
        battle = create_battle("undead_king_lina", "bard")
        lina = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        lina.position = Position(1, 4)
        bard.position = Position(4, 4)
        lina.base_stats.attack = 5
        bard.base_stats.defense = 4
        bard.shields = 1

        battle.perform_action({"type": "attack", "unit_id": lina.unit_id, "target_unit_id": bard.unit_id})
        if battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

        self.assertEqual(bard.shields, 0)
        self.assertAlmostEqual(bard.current_hp, 0.5)

    def test_lina_area_damage_gets_bonus_for_each_extra_occupied_cell_hit(self) -> None:
        battle = create_battle("undead_king_lina", "undead_king_lina")
        attacker = battle.player_units(1)[0]
        target = battle.player_units(2)[0]
        attacker.position = Position(1, 4)
        target.position = Position(4, 4)
        target.max_health = 2
        target.current_hp = 2
        cells = [{"x": x, "y": y} for x in range(4, 8) for y in range(4, 6)]

        battle.perform_action({"type": "skill", "unit_id": attacker.unit_id, "skill_code": "wind_sand", "cells": cells})
        if battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

        self.assertAlmostEqual(target.current_hp, 1)

    def test_lina_wind_sand_creates_sandstorm_when_area_contains_unit(self) -> None:
        battle = create_battle("undead_king_lina", "bard")
        lina = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        bard.position = Position(5, 4)
        bard.max_health = 4
        bard.current_hp = 4
        cells = [{"x": x, "y": y} for x in range(4, 8) for y in range(3, 5)]

        battle.perform_action({"type": "skill", "unit_id": lina.unit_id, "skill_code": "wind_sand", "cells": cells})
        self.assertIsNotNone(battle.pending_chain)
        self.assertEqual(battle.pending_chain.queued_action.action_type, "skill")
        if battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

        self.assertTrue(battle.has_weather("沙尘"))
        self.assertEqual(battle.to_public_dict()["field_effects"][0]["weather_name"], "沙尘")
        self.assertIsNone(battle.pending_chain)
        self.assertFalse(battle.pending_followup_actions)

    def test_lina_recovers_naturally_on_own_turn_start_during_sandstorm(self) -> None:
        battle = create_battle("bard", "undead_king_lina")
        lina = battle.player_units(2)[0]
        lina.current_hp = 0.5
        lina.current_mana = 4
        battle.add_field_effect(SandstormWeatherEffect(duration=3))

        battle.perform_action({"type": "end_turn"})

        self.assertAlmostEqual(lina.current_hp, 0.75)
        self.assertAlmostEqual(lina.current_mana, 5)

    def test_into_darkness_stealth_is_suppressed_by_sandstorm_but_attack_buff_remains(self) -> None:
        battle = create_battle("dark_human", "bard")
        dark = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        dark.position = Position(4, 4)
        bard.position = Position(5, 4)
        bard.max_health = 2.0
        bard.current_hp = 2.0
        bard.shields = 1
        battle.add_field_effect(SandstormWeatherEffect(duration=3))

        battle.perform_action({"type": "skill", "unit_id": dark.unit_id, "skill_code": "into_darkness"})

        self.assertFalse(dark.has_status("隐身"))
        self.assertTrue(dark.has_status("遁入黑暗"))

        battle.perform_action({"type": "attack", "unit_id": dark.unit_id, "target_unit_id": bard.unit_id})
        if battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

        self.assertEqual(bard.shields, 0)
        self.assertLess(bard.current_hp, 2.0)

    def test_lina_crazy_sand_damages_line_and_teleports_to_sixth_cell(self) -> None:
        battle = create_battle("undead_king_lina", "bard")
        battle.width = 12
        battle.height = 12
        lina = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        lina.position = Position(2, 4)
        bard.position = Position(5, 4)
        bard.max_health = 4
        bard.current_hp = 4
        line = [{"x": x, "y": 4} for x in range(4, 9)]

        battle.perform_action({"type": "skill", "unit_id": lina.unit_id, "skill_code": "crazy_sand", "cells": line})
        if battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

        self.assertEqual(lina.position, Position(8, 4))
        self.assertLess(bard.current_hp, 4)

    def test_lina_attack_lock_prevents_switching_targets_until_target_breaks(self) -> None:
        battle = create_battle("undead_king_lina", "bard")
        lina = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        ellie = create_hero("ellie", 2)
        battle.add_unit(ellie, Position(4, 6))
        lina.position = Position(1, 4)
        bard.position = Position(4, 4)
        bard.max_health = 4
        bard.current_hp = 4

        battle.perform_action({"type": "attack", "unit_id": lina.unit_id, "target_unit_id": bard.unit_id})
        if battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

        with self.assertRaises(ActionError):
            battle.perform_action({"type": "attack", "unit_id": lina.unit_id, "target_unit_id": ellie.unit_id})

    def test_lina_destroy_reward_resets_move_and_attacks_once_per_turn(self) -> None:
        battle = create_battle("undead_king_lina", "bard")
        lina = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        lina.move_used = True
        lina.attacks_used = 2
        lina.current_mana = 3
        bard.current_mana = 1.5

        battle.resolve_damage(
            DamageContext(
                source=lina,
                target=bard,
                attack_power=10,
                is_skill=False,
                action_name="测试击破",
                tags={"attack"},
            )
        )

        self.assertFalse(lina.move_used)
        self.assertEqual(lina.attacks_used, 0)
        self.assertAlmostEqual(lina.current_mana, 4.5)

    def test_large_footprint_move_blocks_overlap_even_if_blocker_is_stealthed(self) -> None:
        battle = create_battle("undead_king_lina", "bard")
        lina = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        lina.position = Position(1, 4)
        bard.position = Position(3, 4)

        with self.assertRaises(ActionError):
            battle.perform_action(
                {
                    "type": "move",
                    "unit_id": lina.unit_id,
                    "x": 2,
                    "y": 4,
                    "path": [{"x": 2, "y": 4}],
                }
            )

    def test_flying_large_footprint_cannot_end_move_on_occupied_cells(self) -> None:
        battle = create_battle("doomlight_dragon", "bard")
        dragon = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        dragon.position = Position(0, 0)
        bard.position = Position(2, 0)

        with self.assertRaises(ActionError):
            battle.perform_action(
                {
                    "type": "move",
                    "unit_id": dragon.unit_id,
                    "x": 1,
                    "y": 0,
                    "path": [{"x": 1, "y": 0}],
                }
            )

        bard.add_status(StatusEffect("隐身"))
        with self.assertRaises(ActionError):
            battle.perform_action(
                {
                    "type": "move",
                    "unit_id": dragon.unit_id,
                    "x": 1,
                    "y": 0,
                    "path": [{"x": 1, "y": 0}],
                }
            )

    def test_rock_god_has_local_sandstorm_area(self) -> None:
        battle = create_battle("rock_god", "bard")
        rock = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        rock.position = Position(1, 4)
        bard.position = Position(7, 0)

        self.assertTrue(any(effect.name == "岩神沙尘" for effect in battle.field_effects))
        self.assertTrue(battle.cell_has_weather("沙尘", Position(5, 4)))
        self.assertFalse(battle.unit_in_weather("沙尘", bard))

    def test_rock_god_natural_mana_recovery_recovers_one_mana(self) -> None:
        battle = create_battle("bard", "rock_god")
        rock = battle.player_units(2)[0]
        rock.current_mana = 1

        battle.perform_action({"type": "end_turn"})

        self.assertEqual(rock.current_mana, 2)

    def test_same_named_sandstorm_weather_does_not_stack_damage(self) -> None:
        battle = create_battle("rock_god", "rock_god")
        left = battle.player_units(1)[0]
        right = battle.player_units(2)[0]
        left.position = Position(2, 3)
        right.position = Position(4, 3)
        bard = create_hero("bard", 2)
        battle.add_unit(bard, Position(3, 1))
        bard.max_health = 2
        bard.current_hp = 2

        local_sandstorms = [effect for effect in battle.field_effects if effect.name == "岩神沙尘"]
        self.assertEqual(len(local_sandstorms), 1)

        battle.add_field_effect(SandstormWeatherEffect(duration=3))
        battle.perform_action({"type": "end_turn"})

        self.assertAlmostEqual(bard.current_hp, 2 - 0.0625)

    def test_basic_attack_hits_irregular_multicell_target_when_anchor_is_unoccupied(self) -> None:
        battle = create_battle("ellie", "rock_god")
        ellie = battle.player_units(1)[0]
        rock = battle.player_units(2)[0]
        ellie.position = Position(4, 1)
        rock.position = Position(3, 2)
        rock.set_footprint_cells([Position(4, 2), Position(4, 3), Position(5, 2), Position(5, 3)])
        ellie.base_stats.attack = 8

        battle.perform_action({"type": "attack", "unit_id": ellie.unit_id, "target_unit_id": rock.unit_id})
        while battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

        self.assertFalse(any("没有命中有效目标" in line for line in battle.logs))
        self.assertLess(rock.current_hp, 1.0)

    def test_rock_absorb_chooses_stat_reduces_targets_and_expands_body(self) -> None:
        battle = create_battle("rock_god", "bard")
        rock = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        rock.position = Position(3, 3)
        bard.position = Position(6, 3)

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": rock.unit_id,
                "skill_code": "rock_absorb",
                "stat_name": "attack",
                "cells": [{"x": 2, "y": 3}],
            }
        )
        if battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

        self.assertEqual(rock.stat("attack"), 4)
        self.assertEqual(bard.stat("attack"), 1)
        self.assertIn(Position(2, 3), battle.unit_cells(rock))
        self.assertEqual(len(battle.unit_cells(rock)), 5)

    def test_protection_can_block_rock_absorb_even_when_stealthed(self) -> None:
        battle = create_battle("rock_god", "dark_human")
        rock = battle.player_units(1)[0]
        dark = battle.player_units(2)[0]
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

        self.assertIsNotNone(battle.pending_chain)
        options = battle.to_public_dict()["pending_chain"]["options_by_unit"].get(dark.unit_id, [])
        self.assertIn("protection", {option["action_code"] for option in options})

        battle.perform_action({"type": "chain_react", "unit_id": dark.unit_id, "action_code": "protection"})

        self.assertEqual(dark.stat("attack"), 3)
        self.assertEqual(rock.stat("attack"), 3)
        self.assertNotIn(Position(2, 3), battle.unit_cells(rock))
        self.assertEqual(dark.shields, 1)

    def test_multiple_declared_chain_shields_can_resolve_on_same_target(self) -> None:
        battle = create_classic_battle(["element_hunter", "jade"], ["excel_r352", "dragon_rider"])
        element = next(unit for unit in battle.player_units(1) if unit.hero_code == "element_hunter")
        jade = next(unit for unit in battle.player_units(1) if unit.hero_code == "jade")
        attacker = next(unit for unit in battle.player_units(2) if unit.hero_code == "excel_r352")
        element.position = Position(1, 5)
        jade.position = Position(1, 3)
        attacker.position = Position(2, 2)
        element.current_mana = 5
        jade.current_mana = 0

        battle.perform_action({"type": "attack", "unit_id": attacker.unit_id, "target_unit_id": element.unit_id})

        used: set[str] = set()
        while battle.pending_chain is not None:
            current = battle.pending_chain.current_unit_id()
            if current == jade.unit_id and "jade" not in used:
                battle.perform_action(
                    {
                        "type": "chain_react",
                        "unit_id": jade.unit_id,
                        "action_code": "ion_shield",
                        "target_unit_ids": [element.unit_id],
                    }
                )
                used.add("jade")
            elif current == element.unit_id and "element" not in used:
                battle.perform_action(
                    {
                        "type": "chain_react",
                        "unit_id": element.unit_id,
                        "action_code": "light_wall",
                        "target_unit_ids": [element.unit_id],
                    }
                )
                used.add("element")
            else:
                battle.perform_action({"type": "chain_skip"})

        self.assertEqual(used, {"jade", "element"})
        self.assertEqual(element.current_hp, 1.0)

    def test_multi_target_chain_order_uses_speed_level_then_random_tie(self) -> None:
        battle = create_battle("rock_god", "bard")
        rock = battle.player_units(1)[0]
        first_bard = battle.player_units(2)[0]
        second_bard = create_hero("bard", 2)
        rock.position = Position(3, 3)
        first_bard.position = Position(6, 3)
        battle.add_unit(second_bard, Position(6, 4))

        with mock.patch("wujiang.engine.core.random.random", side_effect=[0.9, 0.1]):
            battle.perform_action(
                {
                    "type": "skill",
                    "unit_id": rock.unit_id,
                    "skill_code": "rock_absorb",
                    "stat_name": "attack",
                    "cells": [{"x": 2, "y": 3}, {"x": 2, "y": 4}],
                }
            )

        self.assertIsNotNone(battle.pending_chain)
        self.assertEqual(battle.pending_chain.pending_reactor_ids[:2], [second_bard.unit_id, first_bard.unit_id])

    def test_rock_absorb_mana_changes_current_mana_and_cap(self) -> None:
        battle = create_battle("rock_god", "bard")
        rock = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        rock.position = Position(3, 3)
        bard.position = Position(6, 3)
        rock.current_mana = 1
        bard.current_mana = 5

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": rock.unit_id,
                "skill_code": "rock_absorb",
                "stat_name": "mana",
                "cells": [{"x": 2, "y": 3}],
            }
        )
        if battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

        self.assertEqual(rock.max_mana(), 4)
        self.assertEqual(rock.current_mana, 2)
        self.assertEqual(bard.max_mana(), 4)
        self.assertEqual(bard.current_mana, 4)

    def test_rock_absorb_restore_skips_occupied_base_cells(self) -> None:
        battle = create_battle("rock_god", "bard")
        rock = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        rock.position = Position(3, 3)
        rock.set_footprint_cells(
            [
                Position(4, 3),
                Position(3, 4),
                Position(4, 4),
                Position(2, 3),
            ]
        )
        bard.position = Position(3, 3)
        status = RockAbsorbFootprintStatus()
        rock.add_status(status)

        rock.remove_status(status, battle)

        self.assertNotIn(Position(3, 3), battle.unit_cells(rock))
        self.assertIn(Position(4, 3), battle.unit_cells(rock))
        self.assertIn(Position(3, 4), battle.unit_cells(rock))
        self.assertIn(Position(4, 4), battle.unit_cells(rock))
        self.assertEqual(rock.position, Position(3, 3))

    def test_rock_cannon_requires_remaining_body_not_blocking_direction(self) -> None:
        battle = create_battle("rock_god", "bard")
        rock = battle.player_units(1)[0]
        rock.position = Position(2, 2)

        with self.assertRaises(ActionError):
            battle.perform_action(
                {
                    "type": "skill",
                    "unit_id": rock.unit_id,
                    "skill_code": "rock_cannon",
                    "cells": [{"x": 2, "y": 2}, {"x": 2, "y": 3}],
                    "direction": {"dx": 1, "dy": 0},
                }
            )

    def test_rock_cannon_fires_body_cells_and_damages_impact_area(self) -> None:
        battle = create_battle("rock_god", "bard")
        rock = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        battle.width = 10
        battle.height = 10
        rock.position = Position(2, 2)
        bard.position = Position(5, 2)
        bard.max_health = 4
        bard.current_hp = 4

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": rock.unit_id,
                "skill_code": "rock_cannon",
                "cells": [{"x": 3, "y": 2}, {"x": 3, "y": 3}],
                "direction": {"dx": 1, "dy": 0},
            }
        )
        if battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

        self.assertEqual({(cell.x, cell.y) for cell in battle.unit_cells(rock)}, {(2, 2), (2, 3)})
        self.assertLess(bard.current_hp, 4)

    def test_rock_cannon_opens_chain_for_each_impact_damage_segment(self) -> None:
        battle = create_battle("rock_god", "bard")
        rock = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        extra_target = create_hero("dark_human", 2)
        battle.add_unit(extra_target, Position(5, 3))
        battle.width = 10
        battle.height = 10
        rock.position = Position(2, 2)
        bard.position = Position(5, 2)
        for unit in (bard, extra_target):
            unit.max_health = 10
            unit.current_hp = 10

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": rock.unit_id,
                "skill_code": "rock_cannon",
                "cells": [{"x": 3, "y": 2}, {"x": 3, "y": 3}],
                "direction": {"dx": 1, "dy": 0},
            }
        )

        self.assertIsNotNone(battle.pending_chain)
        self.assertEqual(battle.pending_chain.queued_action.action_type, "skill_effect")
        self.assertEqual(battle.pending_chain.queued_action.payload.get("effect_code"), "area_damage")
        self.assertEqual(battle.pending_chain.queued_action.payload.get("segment_index"), 1)

        while battle.pending_chain is not None and battle.pending_chain.queued_action.payload.get("segment_index") == 1:
            battle.perform_action({"type": "chain_skip"})

        self.assertIsNotNone(battle.pending_chain)
        self.assertEqual(battle.pending_chain.queued_action.action_type, "skill_effect")
        self.assertEqual(battle.pending_chain.queued_action.payload.get("effect_code"), "area_damage")
        self.assertEqual(battle.pending_chain.queued_action.payload.get("segment_index"), 2)

        while battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

        self.assertIsNone(battle.pending_chain)
        self.assertFalse(battle.pending_followup_actions)
        self.assertLess(bard.current_hp, 10)
        self.assertLess(extra_target.current_hp, 10)

    def test_dragon_breath_uses_nearby_two_by_two_selection(self) -> None:
        battle = create_battle("rock_god", "bard")
        rock = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        rock.position = Position(0, 0)
        bard.position = Position(1, 2)
        bard.max_health = 4
        bard.current_hp = 4

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": rock.unit_id,
                "skill_code": "dragon_breath",
                "cells": [{"x": 0, "y": 2}, {"x": 0, "y": 3}, {"x": 1, "y": 2}, {"x": 1, "y": 3}],
            }
        )
        if battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

        self.assertLess(bard.current_hp, 4)

    def test_doomlight_stone_wall_is_available_as_reaction(self) -> None:
        battle = create_battle("bard", "doomlight_dragon")
        bard = battle.player_units(1)[0]
        dragon = battle.player_units(2)[0]
        bard.position = Position(4, 4)
        dragon.position = Position(5, 4)

        battle.perform_action({"type": "attack", "unit_id": bard.unit_id, "target_unit_id": dragon.unit_id})

        self.assertIsNotNone(battle.pending_chain)
        options = battle.pending_chain.options_by_unit.get(dragon.unit_id, [])
        self.assertIn("stone_wall", [option.action_code for option in options])

    def test_remote_dragon_breath_uses_range_based_two_by_two_selection(self) -> None:
        battle = create_battle("doomlight_dragon", "bard")
        dragon = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        dragon.position = Position(0, 0)
        bard.position = Position(3, 3)
        bard.max_health = 4
        bard.current_hp = 4

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": dragon.unit_id,
                "skill_code": "remote_dragon_breath",
                "cells": [{"x": 3, "y": 3}, {"x": 3, "y": 4}, {"x": 4, "y": 3}, {"x": 4, "y": 4}],
            }
        )
        if battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

        self.assertLess(bard.current_hp, 4)

    def test_doom_light_tick_heals_dragon_above_base_hp(self) -> None:
        battle = create_battle("doomlight_dragon", "bard")
        dragon = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        dragon.position = Position(0, 0)
        bard.position = Position(5, 5)
        dragon.current_hp = 1.0

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": dragon.unit_id,
                "skill_code": "doom_light",
                "cells": [{"x": 2, "y": 2}, {"x": 2, "y": 3}, {"x": 2, "y": 4}, {"x": 2, "y": 5}, {"x": 2, "y": 6},
                          {"x": 2, "y": 7}, {"x": 3, "y": 2}, {"x": 3, "y": 3}, {"x": 3, "y": 4}, {"x": 3, "y": 5},
                          {"x": 3, "y": 6}, {"x": 3, "y": 7}, {"x": 4, "y": 2}, {"x": 4, "y": 3}, {"x": 4, "y": 4},
                          {"x": 4, "y": 5}, {"x": 4, "y": 6}, {"x": 4, "y": 7}, {"x": 5, "y": 2}, {"x": 5, "y": 3},
                          {"x": 5, "y": 4}, {"x": 5, "y": 5}, {"x": 5, "y": 6}, {"x": 5, "y": 7}, {"x": 6, "y": 2},
                          {"x": 6, "y": 3}, {"x": 6, "y": 4}, {"x": 6, "y": 5}, {"x": 6, "y": 6}, {"x": 6, "y": 7},
                          {"x": 7, "y": 2}, {"x": 7, "y": 3}, {"x": 7, "y": 4}, {"x": 7, "y": 5}, {"x": 7, "y": 6},
                          {"x": 7, "y": 7}],
            }
        )
        if battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

        battle.perform_action({"type": "end_turn"})

        self.assertTrue(bard.has_status("末日光"))
        self.assertAlmostEqual(bard.current_hp, 0.5)
        self.assertAlmostEqual(dragon.current_hp, 1.5)

    def test_attacking_doomlight_dragon_applies_doom_light_to_attacker(self) -> None:
        battle = create_battle("doomlight_dragon", "bard")
        dragon = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        dragon.position = Position(4, 4)
        bard.position = Position(5, 4)

        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "attack", "unit_id": bard.unit_id, "target_unit_id": dragon.unit_id})
        if battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

        self.assertTrue(bard.has_status("末日光"))

    def test_apocalypse_requires_hp_above_selected_n(self) -> None:
        battle = create_battle("doomlight_dragon", "bard")
        dragon = battle.player_units(1)[0]

        with self.assertRaises(ActionError):
            battle.perform_action(
                {
                    "type": "skill",
                    "unit_id": dragon.unit_id,
                    "skill_code": "apocalypse",
                    "choice_code": "1",
                    "cells": [{"x": 4, "y": 4}],
                }
            )

    def test_apocalypse_allows_n_one_when_current_hp_is_one_point_two_five(self) -> None:
        battle = create_battle("doomlight_dragon", "bard")
        dragon = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        dragon.position = Position(0, 0)
        bard.position = Position(3, 3)
        dragon.current_hp = 1.25

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": dragon.unit_id,
                "skill_code": "apocalypse",
                "choice_code": "1",
                "cells": [{"x": 3, "y": 3}],
            }
        )
        if battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

        self.assertAlmostEqual(dragon.current_hp, 0.25)
        self.assertLess(bard.current_hp, 1.0)

    def test_apocalypse_spends_hp_and_uses_selected_n_for_damage(self) -> None:
        battle = create_battle("doomlight_dragon", "bard")
        dragon = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        dragon.position = Position(0, 0)
        bard.position = Position(3, 3)
        dragon.current_hp = 2.25
        bard.max_health = 4
        bard.current_hp = 4

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": dragon.unit_id,
                "skill_code": "apocalypse",
                "choice_code": "2",
                "cells": [{"x": 3, "y": 3}, {"x": 3, "y": 4}, {"x": 4, "y": 3}, {"x": 4, "y": 4}],
            }
        )
        if battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

        self.assertAlmostEqual(dragon.current_hp, 0.25)
        self.assertLess(bard.current_hp, 4)

    def test_masamune_starts_battle_mounted_on_motor_horse(self) -> None:
        battle = create_battle("masamune", "bard")
        masamune = primary_hero(battle, 1)
        mount = summon_by_code(battle, 1, "motor_horse")

        self.assertIs(battle.mounted_unit_for(masamune), mount)
        self.assertEqual(masamune.mounted_on_unit_id, mount.unit_id)
        self.assertEqual(mount.ridden_by_unit_id, masamune.unit_id)
        self.assertEqual(masamune.position, mount.position)
        self.assertEqual({(cell.x, cell.y) for cell in battle.unit_cells(mount)}, {(1, 4), (1, 5)})

    def test_masamune_mounted_leap_moves_rider_and_dismounts(self) -> None:
        battle = create_battle("masamune", "bard")
        masamune = primary_hero(battle, 1)
        mount = summon_by_code(battle, 1, "motor_horse")
        mount.position = Position(1, 1)
        masamune.position = Position(1, 1)

        battle.perform_action({"type": "skill", "unit_id": masamune.unit_id, "skill_code": "mounted_leap", "x": 4, "y": 1})

        self.assertEqual(masamune.position, Position(4, 1))
        self.assertEqual(mount.position, Position(1, 1))
        self.assertIsNone(battle.mounted_unit_for(masamune))
        self.assertEqual(mount.normal_move_actions_used, 0)
        self.assertTrue(battle.action_snapshot_for(mount)["can_move"])
        self.assertNotIn("mounted_leap", {skill.code for skill in mount.skills})

    def test_masamune_mounted_leap_is_not_blocked_by_mount_normal_move(self) -> None:
        battle = create_battle("masamune", "bard")
        masamune = primary_hero(battle, 1)
        mount = summon_by_code(battle, 1, "motor_horse")
        mount.position = Position(1, 1)
        masamune.position = Position(1, 1)
        battle.perform_action({"type": "move", "unit_id": mount.unit_id, "x": 2, "y": 1})

        battle.perform_action({"type": "skill", "unit_id": masamune.unit_id, "skill_code": "mounted_leap", "x": 5, "y": 1})

        self.assertEqual(masamune.position, Position(5, 1))
        self.assertEqual(mount.position, Position(2, 1))
        self.assertIsNone(battle.mounted_unit_for(masamune))

    def test_random_mode_can_spawn_masamune_with_mount_entry_space(self) -> None:
        battle = create_battle("masamune", "bard", mode=RANDOM_HERO_BATTLE_MODE)
        masamune = primary_hero(battle, 1)
        mount = summon_by_code(battle, 1, "motor_horse")

        self.assertIsNotNone(mount.position)
        self.assertEqual(masamune.position, mount.position)
        self.assertTrue(all(battle.in_bounds(cell) for cell in battle.unit_cells(mount)))

    def test_random_mode_multihero_battle_reuses_classic_board_and_turn_order_with_random_spawns(self) -> None:
        roster1 = ["doomlight_dragon", "bard", "dark_human"]
        roster2 = ["rock_god", "elite_soldier", "ellie"]

        classic_battle = create_battle(roster1, roster2)
        with mock.patch("wujiang.heroes.registry.random.choice", side_effect=lambda seq: seq[-1]):
            random_battle = create_battle(roster1, roster2, mode=RANDOM_HERO_BATTLE_MODE)

        self.assertEqual(random_battle.width, classic_battle.width)
        self.assertEqual(random_battle.height, classic_battle.height)
        self.assertEqual(
            [random_battle.get_unit(unit_id).hero_code for unit_id in random_battle.turn_order_unit_ids],
            [classic_battle.get_unit(unit_id).hero_code for unit_id in classic_battle.turn_order_unit_ids],
        )

        classic_positions = {
            unit.hero_code: unit.position
            for unit in classic_battle.hero_units(1) + classic_battle.hero_units(2)
        }
        random_positions = {
            unit.hero_code: unit.position
            for unit in random_battle.hero_units(1) + random_battle.hero_units(2)
        }

        self.assertTrue(any(random_positions[code] != classic_positions[code] for code in random_positions))
        for unit in random_battle.hero_units(1) + random_battle.hero_units(2):
            self.assertTrue(all(random_battle.in_bounds(cell) for cell in random_battle.unit_cells(unit)))

    def test_masamune_basic_attack_requires_declared_direction(self) -> None:
        battle = create_battle("masamune", "bard")
        masamune = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        mount = summon_by_code(battle, 1, "motor_horse")
        mount.position = Position(4, 4)
        masamune.position = Position(4, 4)
        bard.position = Position(5, 4)
        bard.current_mana = 0

        with self.assertRaises(ActionError):
            battle.perform_action({"type": "attack", "unit_id": masamune.unit_id, "target_unit_id": bard.unit_id})

        battle.perform_action(
            {
                "type": "attack",
                "unit_id": masamune.unit_id,
                "target_unit_id": bard.unit_id,
                "choice_code": "right",
                "x": 5,
                "y": 4,
            }
        )
        if battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

        self.assertLess(bard.current_hp, 1.0)

    def test_masamune_arc_attack_adds_damage_when_hitting_multiple_cells_of_large_target(self) -> None:
        battle = create_battle("masamune", "undead_king_lina")
        masamune = primary_hero(battle, 1)
        lina = primary_hero(battle, 2)
        mount = summon_by_code(battle, 1, "motor_horse")
        mount.position = Position(3, 4)
        masamune.position = Position(3, 4)
        lina.position = Position(4, 4)

        battle.perform_action(
            {
                "type": "attack",
                "unit_id": masamune.unit_id,
                "target_unit_id": lina.unit_id,
                "choice_code": "right",
                "x": 4,
                "y": 4,
            }
        )
        if battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

        self.assertFalse(lina.alive)

    def test_targeting_masamune_redirects_attack_and_skill_to_motor_horse(self) -> None:
        battle = create_battle("ellie", "masamune")
        ellie = primary_hero(battle, 1)
        masamune = primary_hero(battle, 2)
        mount = summon_by_code(battle, 2, "motor_horse")
        ellie.position = Position(4, 4)
        mount.position = Position(5, 4)
        masamune.position = Position(5, 4)
        masamune.current_mana = 0

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": ellie.unit_id,
                "skill_code": "mana_pull",
                "target_unit_id": masamune.unit_id,
                "dest_x": 7,
                "dest_y": 4,
            }
        )
        if battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

        self.assertEqual(mount.position, Position(7, 4))
        self.assertEqual(masamune.position, Position(7, 4))

        battle = create_battle("fire_funeral", "masamune")
        fire = primary_hero(battle, 1)
        masamune = primary_hero(battle, 2)
        mount = summon_by_code(battle, 2, "motor_horse")
        fire.position = Position(4, 4)
        mount.position = Position(5, 4)
        masamune.position = Position(5, 4)
        masamune.current_mana = 0

        battle.perform_action({"type": "attack", "unit_id": fire.unit_id, "target_unit_id": masamune.unit_id, "x": 5, "y": 4})
        if battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

        self.assertAlmostEqual(masamune.current_hp, 1.0)
        self.assertLess(mount.current_hp, 1.0)

    def test_masamune_can_use_protection_for_mounted_motor_horse(self) -> None:
        battle = create_battle("fire_funeral", "masamune")
        fire = primary_hero(battle, 1)
        masamune = primary_hero(battle, 2)
        mount = summon_by_code(battle, 2, "motor_horse")
        fire.position = Position(4, 4)
        mount.position = Position(5, 4)
        masamune.position = Position(5, 4)
        masamune.current_mana = 1

        battle.perform_action({"type": "attack", "unit_id": fire.unit_id, "target_unit_id": masamune.unit_id, "x": 5, "y": 4})

        self.assertIsNotNone(battle.pending_chain)
        while battle.pending_chain is not None and battle.pending_chain.current_unit_id() != masamune.unit_id:
            battle.perform_action({"type": "chain_skip"})

        battle.perform_action({"type": "chain_react", "unit_id": masamune.unit_id, "action_code": "protection"})

        while battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

        self.assertAlmostEqual(mount.current_hp, 1.0)
        self.assertEqual(mount.total_shields(), 1)
        self.assertEqual(masamune.current_mana, 0)

    def test_motor_horse_movement_carries_masamune_and_leaving_cells_dismounts(self) -> None:
        battle = create_battle("masamune", "bard")
        masamune = primary_hero(battle, 1)
        mount = summon_by_code(battle, 1, "motor_horse")

        battle.move_unit(mount, Position(2, 4))
        self.assertEqual(mount.position, Position(2, 4))
        self.assertEqual(masamune.position, Position(2, 4))
        self.assertIs(battle.mounted_unit_for(masamune), mount)

        battle.move_unit(masamune, Position(3, 4))
        self.assertEqual(masamune.position, Position(3, 4))
        self.assertIsNone(battle.mounted_unit_for(masamune))
        self.assertIsNone(battle.rider_for(mount))

    def test_motor_horse_resummon_has_one_own_turn_cooldown(self) -> None:
        battle = create_battle("masamune", "bard")
        masamune = primary_hero(battle, 1)
        mount = summon_by_code(battle, 1, "motor_horse")

        battle.remove_unit(mount)

        self.assertTrue(masamune.has_status("摩托马召回冷却"))
        with self.assertRaises(ActionError):
            battle.perform_action({"type": "skill", "unit_id": masamune.unit_id, "skill_code": "motor_horse"})

        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})

        with self.assertRaises(ActionError):
            battle.perform_action({"type": "skill", "unit_id": masamune.unit_id, "skill_code": "motor_horse"})

        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "skill", "unit_id": masamune.unit_id, "skill_code": "motor_horse"})

        self.assertIsNotNone(battle.mounted_unit_for(masamune))

    def test_unmounted_masamune_six_blade_style_triple_strike_and_lifesteal(self) -> None:
        battle = create_battle("masamune", "bard")
        masamune = primary_hero(battle, 1)
        mount = summon_by_code(battle, 1, "motor_horse")
        bard = primary_hero(battle, 2)

        battle.clear_mounted_state(masamune)
        masamune.position = Position(4, 4)
        mount.position = Position(1, 1)
        bard.position = Position(5, 4)
        bard.current_mana = 0
        bard.max_health = 4
        bard.current_hp = 4
        masamune.current_hp = 0.5

        battle.perform_action({"type": "skill", "unit_id": masamune.unit_id, "skill_code": "six_blade_style"})

        self.assertEqual(masamune.attack_actions_per_turn(), 6)
        self.assertEqual(masamune.stat("attack"), 3)

        battle.perform_action(
            {
                "type": "attack",
                "unit_id": masamune.unit_id,
                "target_unit_id": bard.unit_id,
                "choice_code": "right",
                "attack_variant": "triple",
                "x": 5,
                "y": 4,
            }
        )
        if battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

        self.assertEqual(masamune.attacks_used, 3)
        self.assertAlmostEqual(masamune.current_hp, 0.75)
        self.assertLess(bard.current_hp, 4)

    def test_masamune_block_and_counter_only_exist_after_dismount(self) -> None:
        battle = create_battle("fire_funeral", "masamune")
        fire = primary_hero(battle, 1)
        masamune = primary_hero(battle, 2)
        mount = summon_by_code(battle, 2, "motor_horse")
        fire.position = Position(4, 4)
        mount.position = Position(5, 4)
        masamune.position = Position(5, 4)

        battle.perform_action({"type": "attack", "unit_id": fire.unit_id, "target_unit_id": masamune.unit_id, "x": 5, "y": 4})

        self.assertIsNotNone(battle.pending_chain)
        while battle.pending_chain is not None and battle.pending_chain.current_unit_id() != masamune.unit_id:
            battle.perform_action({"type": "chain_skip"})
        mounted_options = battle.pending_chain.options_by_unit.get(masamune.unit_id, [])
        mounted_codes = {option.action_code for option in mounted_options}
        self.assertNotIn("block", mounted_codes)
        self.assertNotIn("counter", mounted_codes)

        while battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

        battle.clear_mounted_state(masamune)
        battle.end_turn()
        battle.end_turn()
        fire.position = Position(4, 4)
        masamune.position = Position(5, 4)

        battle.perform_action({"type": "attack", "unit_id": fire.unit_id, "target_unit_id": masamune.unit_id, "x": 5, "y": 4})

        self.assertIsNotNone(battle.pending_chain)
        while battle.pending_chain is not None and battle.pending_chain.current_unit_id() != masamune.unit_id:
            battle.perform_action({"type": "chain_skip"})
        unmounted_options = battle.pending_chain.options_by_unit.get(masamune.unit_id, [])
        unmounted_codes = {option.action_code for option in unmounted_options}
        self.assertIn("block", unmounted_codes)
        self.assertIn("counter", unmounted_codes)


class JadeTests(unittest.TestCase):
    def test_missile_uses_two_round_window_and_resets_after_expiry(self) -> None:
        battle = create_battle("jade", "bard")
        jade = primary_hero(battle, 1)
        missile = skill_by_code(jade, "missile")
        cells = [{"x": 2, "y": 3}, {"x": 2, "y": 4}, {"x": 3, "y": 3}, {"x": 3, "y": 4}]

        battle.perform_action({"type": "skill", "unit_id": jade.unit_id, "skill_code": "missile", "cells": cells})

        self.assertTrue(missile.window_is_active())
        self.assertEqual(missile.available_uses(), 2)

        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})

        self.assertFalse(missile.window_is_active())
        battle.perform_action({"type": "skill", "unit_id": jade.unit_id, "skill_code": "missile", "cells": cells})
        self.assertTrue(missile.window_is_active())
        self.assertEqual(missile.available_uses(), 2)

    def test_missile_window_counts_jades_own_rounds_in_multihero_battle(self) -> None:
        battle = create_battle(["jade", "bard"], ["fire_funeral", "dark_human"])
        jade = primary_hero(battle, 1)
        missile = skill_by_code(jade, "missile")

        while battle.current_turn_unit().unit_id != jade.unit_id:
            battle.perform_action({"type": "end_turn"})

        cells = [cell.to_dict() for cell in missile.patterns(battle, jade)[0]]
        battle.perform_action({"type": "skill", "unit_id": jade.unit_id, "skill_code": "missile", "cells": cells})
        self.assertEqual(missile.window_remaining_turns, 2)

        for _ in range(3):
            battle.perform_action({"type": "end_turn"})
            self.assertEqual(missile.window_remaining_turns, 2)

        battle.perform_action({"type": "end_turn"})
        self.assertEqual(battle.current_turn_unit().unit_id, jade.unit_id)
        self.assertEqual(missile.window_remaining_turns, 1)

    def test_ion_shield_counts_one_cast_even_when_shielding_multiple_allies(self) -> None:
        battle = create_battle("jade", "bard")
        jade = primary_hero(battle, 1)
        ally = create_hero("dark_human", 1)
        battle.add_unit(ally, Position(4, 4))
        jade.position = Position(4, 3)
        enemy = primary_hero(battle, 2)
        enemy.position = Position(6, 3)
        skill = skill_by_code(jade, "ion_shield")
        threatened_cells = [Position(4, 3), Position(4, 4)]
        queued = battle.build_skill_effect_action(
            actor=enemy,
            display_name="测试范围伤害",
            effect_code="area_damage",
            payload={"cells": [cell.to_dict() for cell in threatened_cells], "attack_power": 4, "tags": ["skill"]},
            target_cells=threatened_cells,
            speed=1,
        )
        reaction_payload = {"target_unit_ids": [jade.unit_id, ally.unit_id]}

        ok, reason = skill.can_react_with_payload(battle, jade, queued, reaction_payload)
        self.assertTrue(ok, reason)

        skill.prepay_resources(battle, jade, reaction_payload)
        skill.react(battle, jade, reaction_payload, queued)

        self.assertEqual(skill.uses_this_turn, 1)
        self.assertGreaterEqual(jade.total_shields(), 1)
        self.assertGreaterEqual(ally.total_shields(), 1)

    def test_quantum_shield_allows_three_casts_then_blocks_next_cycle(self) -> None:
        battle = create_battle("jade", "bard")
        jade = primary_hero(battle, 1)
        ally = create_hero("dark_human", 1)
        battle.add_unit(ally, Position(4, 4))
        jade.position = Position(4, 3)
        enemy = primary_hero(battle, 2)
        enemy.position = Position(6, 3)
        skill = skill_by_code(jade, "quantum_shield")
        threatened_cells = [Position(4, 3), Position(4, 4)]
        queued = battle.build_skill_effect_action(
            actor=enemy,
            display_name="测试范围伤害",
            effect_code="area_damage",
            payload={"cells": [cell.to_dict() for cell in threatened_cells], "attack_power": 4, "tags": ["skill"]},
            target_cells=threatened_cells,
            speed=1,
        )
        reaction_payload = {"target_unit_ids": [jade.unit_id, ally.unit_id]}

        battle.perform_action({"type": "end_turn"})

        for expected_uses in range(1, 4):
            ok, reason = skill.can_react_with_payload(battle, jade, queued, reaction_payload)
            self.assertTrue(ok, reason)
            skill.prepay_resources(battle, jade, reaction_payload)
            skill.react(battle, jade, reaction_payload, queued)
            self.assertEqual(skill.uses_this_turn, expected_uses)
            battle.expire_chain_temporary_statuses()

        ok, _ = skill.can_react_with_payload(battle, jade, queued, reaction_payload)
        self.assertFalse(ok)
        self.assertTrue(skill.cooldown_pending)

        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})

        ok, reason = skill.can_react_with_payload(battle, jade, queued, reaction_payload)
        self.assertFalse(ok)
        self.assertEqual(reason, "技能冷却中。")
        self.assertEqual(skill.cooldown_remaining, 2)

        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})

        ok, reason = skill.can_react_with_payload(battle, jade, queued, reaction_payload)
        self.assertTrue(ok, reason)
        self.assertEqual(skill.cooldown_remaining, 0)

    def test_plasma_thruster_is_unavailable_when_jade_cannot_move(self) -> None:
        battle = create_battle("jade", "bard")
        jade = primary_hero(battle, 1)
        jade.position = Position(4, 4)
        jade.add_status(BloodDanceLockStatus("test"))
        skill = skill_by_code(jade, "plasma_thruster")

        ok, _ = skill.can_use(battle, jade, {})
        action = next(action for action in battle.action_snapshot_for(jade)["actions"] if action["code"] == "plasma_thruster")

        self.assertFalse(ok)
        self.assertFalse(action["available"])
        self.assertEqual(action["preview"]["cells"], [])
        with self.assertRaises(ActionError):
            battle.perform_action({"type": "skill", "unit_id": jade.unit_id, "skill_code": "plasma_thruster", "x": 4, "y": 0})

    def test_stance_blocks_damage_to_other_allies_only_during_next_enemy_turn(self) -> None:
        battle = create_battle("jade", "fire_funeral")
        jade = primary_hero(battle, 1)
        ally = create_hero("bard", 1)
        battle.add_unit(ally, Position(4, 4))
        jade.position = Position(3, 4)
        enemy = primary_hero(battle, 2)
        enemy.position = Position(6, 4)

        battle.perform_action({"type": "skill", "unit_id": jade.unit_id, "skill_code": "stance"})
        battle.perform_action({"type": "end_turn"})

        ally_ctx = battle.resolve_damage(
            DamageContext(source=enemy, target=ally, attack_power=5, is_skill=False, action_name="测试攻击")
        )
        jade_ctx = battle.resolve_damage(
            DamageContext(source=enemy, target=jade, attack_power=5, is_skill=False, action_name="测试攻击")
        )

        self.assertTrue(ally_ctx.cancelled)
        self.assertAlmostEqual(ally.current_hp, 1.0)
        self.assertFalse(jade_ctx.cancelled)
        self.assertLess(jade.current_hp, 1.0)

        battle.perform_action({"type": "end_turn"})

        self.assertFalse(any(effect.name == "立场" for effect in battle.field_effects))

    def test_enemy_chain_that_prevents_damage_grants_machine_gun_extra_use_next_turn(self) -> None:
        battle = create_battle("jade", "doomlight_dragon")
        jade = primary_hero(battle, 1)
        dragon = primary_hero(battle, 2)
        jade.position = Position(4, 4)
        dragon.position = Position(5, 4)
        machine_gun = skill_by_code(jade, "machine_gun")

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": jade.unit_id,
                "skill_code": "machine_gun",
                "cells": [{"x": 5, "y": 4}, {"x": 6, "y": 4}, {"x": 7, "y": 4}],
            }
        )

        self.assertIsNotNone(battle.pending_chain)
        while battle.pending_chain is not None and battle.pending_chain.current_unit_id() != dragon.unit_id:
            battle.perform_action({"type": "chain_skip"})
        battle.perform_action(
            {
                "type": "chain_react",
                "unit_id": dragon.unit_id,
                "action_code": "stone_wall",
                "target_unit_ids": [dragon.unit_id],
            }
        )
        while battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

        self.assertEqual(machine_gun.max_uses_per_turn, 1)

        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})

        self.assertEqual(machine_gun.max_uses_per_turn, 2)


class NTests(unittest.TestCase):
    def test_split_preview_exposes_cells_without_enumerating_combinations(self) -> None:
        battle = create_battle("n", "bard")
        caster = primary_hero(battle, 1)
        caster.position = Position(4, 4)
        split = skill_by_code(caster, "split")

        preview = split.preview(battle, caster)

        self.assertGreaterEqual(len(preview["cells"]), 3)
        self.assertEqual(preview["selection"]["required_cells"], 3)
        self.assertEqual(preview["selection"]["patterns"], [])

    def test_split_summons_three_clones_and_swaps_with_one(self) -> None:
        battle = create_battle("n", "bard")
        caster = primary_hero(battle, 1)
        caster.position = Position(4, 4)
        original_position = Position(4, 4)
        destinations = [Position(3, 3), Position(3, 4), Position(4, 3)]

        with mock.patch("wujiang.heroes.next_five.random.choice", side_effect=lambda seq: seq[0]):
            battle.perform_action(
                {
                    "type": "skill",
                    "unit_id": caster.unit_id,
                    "skill_code": "split",
                    "cells": [cell.to_dict() for cell in destinations],
                }
            )

        clones = [unit for unit in battle.all_units() if unit.is_clone]

        self.assertEqual(len(clones), 3)
        self.assertEqual(caster.position, destinations[0])
        self.assertFalse(caster.turn_ready)
        self.assertEqual(
            {(unit.position.x, unit.position.y) for unit in clones if unit.position is not None},
            {
                (original_position.x, original_position.y),
                (destinations[1].x, destinations[1].y),
                (destinations[2].x, destinations[2].y),
            },
        )
        self.assertTrue(all(clone.cannot_attack and clone.cannot_use_skills for clone in clones))

    def test_basic_attack_declaration_gains_mana_points_even_when_damage_is_blocked(self) -> None:
        battle = create_battle("n", "fire_funeral")
        caster = primary_hero(battle, 1)
        target = primary_hero(battle, 2)
        caster.position = Position(4, 4)
        target.position = Position(5, 4)
        target.shields = 1

        battle.perform_action({"type": "attack", "unit_id": caster.unit_id, "target_unit_id": target.unit_id})

        self.assertEqual(caster.mana_points, 1.0)
        self.assertAlmostEqual(target.current_hp, 1.0)

    def test_attack_count_snapshots_from_turn_start_mana(self) -> None:
        battle = create_battle("dark_human", "n")
        attacker = primary_hero(battle, 1)
        caster = primary_hero(battle, 2)
        attacker.position = Position(5, 4)
        caster.position = Position(4, 4)
        caster.current_mana = 1.5

        battle.perform_action({"type": "end_turn"})

        self.assertEqual(caster.attack_actions_per_turn(), 2)
        caster.mana_points = 1
        battle.perform_action({"type": "skill", "unit_id": caster.unit_id, "skill_code": "n_skill"})
        self.assertAlmostEqual(caster.current_mana, 2.5)
        self.assertEqual(caster.attack_actions_per_turn(), 2)

    def test_mana_guard_preserves_complete_burn_followup_effect(self) -> None:
        battle = create_battle("element_hunter", "n")
        hunter = primary_hero(battle, 1)
        caster = primary_hero(battle, 2)
        hunter.position = Position(4, 4)
        caster.position = Position(5, 4)
        caster.current_mana = 2

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": hunter.unit_id,
                "skill_code": "complete_burn",
                "cells": [
                    {"x": 4, "y": 3},
                    {"x": 4, "y": 4},
                    {"x": 4, "y": 5},
                    {"x": 4, "y": 6},
                    {"x": 5, "y": 3},
                    {"x": 5, "y": 4},
                    {"x": 5, "y": 5},
                    {"x": 5, "y": 6},
                    {"x": 6, "y": 3},
                    {"x": 6, "y": 4},
                    {"x": 6, "y": 5},
                    {"x": 6, "y": 6},
                    {"x": 7, "y": 3},
                    {"x": 7, "y": 4},
                    {"x": 7, "y": 5},
                    {"x": 7, "y": 6},
                ],
            }
        )
        while battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

        self.assertAlmostEqual(caster.current_hp, 1.0)
        self.assertAlmostEqual(caster.current_mana, 1.0)
        self.assertTrue(caster.has_status("完全燃烧"))

    def test_mana_guard_spends_one_mana_per_damage_instance(self) -> None:
        battle = create_battle("fire_funeral", "n")
        attacker = primary_hero(battle, 1)
        caster = primary_hero(battle, 2)
        caster.current_mana = 2

        first = battle.resolve_damage(
            DamageContext(source=attacker, target=caster, attack_power=5, is_skill=True, action_name="多段伤害")
        )
        second = battle.resolve_damage(
            DamageContext(source=attacker, target=caster, attack_power=5, is_skill=True, action_name="多段伤害")
        )
        third = battle.resolve_damage(
            DamageContext(source=attacker, target=caster, attack_power=5, is_skill=True, action_name="多段伤害")
        )

        self.assertTrue(first.cancelled)
        self.assertTrue(second.cancelled)
        self.assertFalse(third.cancelled)
        self.assertAlmostEqual(caster.current_mana, 0.0)
        self.assertLess(caster.current_hp, 1.0)

    def test_magnetic_wave_can_be_used_on_enemy_turn_and_stops_current_actor(self) -> None:
        battle = create_battle("dark_human", "n")
        attacker = primary_hero(battle, 1)
        caster = primary_hero(battle, 2)
        attacker.position = Position(5, 4)
        caster.position = Position(4, 4)
        caster.mana_points = 2

        battle.perform_action(
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
            }
        )

        self.assertFalse(attacker.turn_ready)
        self.assertAlmostEqual(caster.mana_points, 0.0)
        self.assertIsNone(battle.pending_chain)

    def test_magnetic_wave_ai_reaction_payloads_skip_empty_preview(self) -> None:
        battle = create_battle("dark_human", "n")
        attacker = primary_hero(battle, 1)
        caster = primary_hero(battle, 2)
        queued = battle.build_skill_effect_action(
            actor=attacker,
            display_name="test",
            effect_code="test",
            payload={},
            target_cells=[],
            speed=1,
        )
        option = {"action_code": "magnetic_wave", "preview": {"cells": [], "target_unit_ids": []}}

        self.assertEqual(reaction_payloads_for_option(battle, caster, queued, option), [])


class SoulWraithTests(unittest.TestCase):
    def test_soul_wraith_keeps_half_defense_and_blocks_basic_attack_damage(self) -> None:
        battle = create_battle("fire_funeral", "soul_wraith")
        fire = primary_hero(battle, 1)
        wraith = primary_hero(battle, 2)
        fire.position = Position(4, 4)
        wraith.position = Position(5, 4)

        self.assertEqual(wraith.stat("defense"), 0.5)
        battle.perform_action({"type": "attack", "unit_id": fire.unit_id, "target_unit_id": wraith.unit_id, "x": 5, "y": 4})
        resolve_pending_chain(battle)

        self.assertAlmostEqual(wraith.current_hp, 1.0)
        self.assertTrue(wraith.alive)

    def test_soul_wraith_physical_immunity_does_not_block_pierce_skill(self) -> None:
        battle = create_battle("fire_funeral", "soul_wraith")
        fire = primary_hero(battle, 1)
        wraith = primary_hero(battle, 2)
        fire.position = Position(4, 4)
        wraith.position = Position(5, 4)

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": fire.unit_id,
                "skill_code": "pierce",
                "cells": [{"x": 5, "y": 4}, {"x": 6, "y": 4}],
            }
        )
        resolve_pending_chain(battle)

        self.assertFalse(wraith.alive)

    def test_soul_wraith_magic_immunity_requires_no_nearby_enemy_hero(self) -> None:
        battle = create_battle("element_hunter", "soul_wraith")
        hunter = primary_hero(battle, 1)
        wraith = primary_hero(battle, 2)
        hunter.position = Position(3, 4)
        wraith.position = Position(5, 4)
        cells = [{"x": x, "y": y} for x in range(4, 8) for y in range(3, 7)]

        battle.perform_action({"type": "skill", "unit_id": hunter.unit_id, "skill_code": "complete_burn", "cells": cells})
        resolve_pending_chain(battle)

        self.assertAlmostEqual(wraith.current_hp, 1.0)
        self.assertTrue(wraith.alive)

        hunter.position = Position(4, 4)
        hunter.get_skill("complete_burn").uses_this_turn = 0
        battle.perform_action({"type": "skill", "unit_id": hunter.unit_id, "skill_code": "complete_burn", "cells": cells})
        resolve_pending_chain(battle)

        self.assertFalse(wraith.alive)

    def test_soul_wraith_arc_attack_drains_each_damaged_enemy(self) -> None:
        battle = create_battle("soul_wraith", ["bard", "ellie"])
        wraith = primary_hero(battle, 1)
        bard = next(unit for unit in battle.player_units(2) if unit.hero_code == "bard")
        ellie = next(unit for unit in battle.player_units(2) if unit.hero_code == "ellie")
        wraith.position = Position(3, 4)
        bard.position = Position(4, 3)
        ellie.position = Position(4, 4)
        wraith.current_mana = 0
        bard.current_mana = 2
        ellie.current_mana = 2

        battle.perform_action({"type": "attack", "unit_id": wraith.unit_id, "choice_code": "right"})
        resolve_pending_chain(battle)

        self.assertLess(bard.current_hp, 1.0)
        self.assertLess(ellie.current_hp, 1.0)
        self.assertAlmostEqual(bard.current_mana, 1.0)
        self.assertAlmostEqual(ellie.current_mana, 1.0)
        self.assertAlmostEqual(wraith.current_mana, 2.0)

    def test_soul_wraith_arc_attack_misses_after_target_evasion(self) -> None:
        battle = create_battle("soul_wraith", "dark_human")
        wraith = primary_hero(battle, 1)
        dark = primary_hero(battle, 2)
        wraith.position = Position(3, 4)
        dark.position = Position(4, 4)

        battle.perform_action({"type": "attack", "unit_id": wraith.unit_id, "choice_code": "right"})
        self.assertIsNotNone(battle.pending_chain)
        while battle.pending_chain is not None and battle.pending_chain.current_unit_id() != dark.unit_id:
            battle.perform_action({"type": "chain_skip"})
        battle.perform_action(
            {"type": "chain_react", "unit_id": dark.unit_id, "action_code": "evasion", "x": 3, "y": 3}
        )
        resolve_pending_chain(battle)

        self.assertEqual(dark.position, Position(3, 3))
        self.assertAlmostEqual(dark.current_hp, 1.0)

    def test_soul_wraith_growth_adds_attack_speed_and_extra_move_after_enemy_skill_blocks_attack(self) -> None:
        battle = create_battle("soul_wraith", "bard")
        wraith = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        wraith.position = Position(4, 4)
        bard.position = Position(5, 4)
        bard.current_mana = 1

        battle.perform_action({"type": "attack", "unit_id": wraith.unit_id, "choice_code": "right"})
        self.assertIsNotNone(battle.pending_chain)
        while battle.pending_chain is not None and battle.pending_chain.current_unit_id() != bard.unit_id:
            battle.perform_action({"type": "chain_skip"})
        battle.perform_action({"type": "chain_react", "unit_id": bard.unit_id, "action_code": "protection"})
        resolve_pending_chain(battle)

        self.assertTrue(wraith.has_status("销魂成长"))
        self.assertEqual(wraith.stat("attack"), 5)
        self.assertEqual(wraith.stat("speed"), 6)
        self.assertEqual(wraith.normal_move_actions_per_turn(), 2)

        battle.perform_action({"type": "move", "unit_id": wraith.unit_id, "x": 3, "y": 4})
        self.assertTrue(battle.action_snapshot_for(wraith)["can_move"])
        battle.perform_action({"type": "move", "unit_id": wraith.unit_id, "x": 2, "y": 4})
        self.assertFalse(battle.action_snapshot_for(wraith)["can_move"])

    def test_soul_wraith_growth_clears_when_later_basic_attack_deals_damage(self) -> None:
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
        self.assertTrue(wraith.has_status("销魂成长"))

        battle.perform_action({"type": "end_turn"})
        while battle.current_turn_unit().unit_id != wraith.unit_id:
            battle.perform_action({"type": "end_turn"})
        bard.current_mana = 0

        battle.perform_action({"type": "attack", "unit_id": wraith.unit_id, "choice_code": "right"})
        resolve_pending_chain(battle)

        self.assertFalse(wraith.has_status("销魂成长"))
        self.assertEqual(wraith.stat("attack"), 4)
        self.assertEqual(wraith.stat("speed"), 5)


class LiTests(unittest.TestCase):
    def test_li_can_split_normal_movement_until_speed_is_spent(self) -> None:
        battle = create_battle("li", "bard")
        li = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        li.position = Position(3, 4)
        bard.position = Position(8, 8)

        battle.perform_action({"type": "move", "unit_id": li.unit_id, "path": [{"x": 4, "y": 4}, {"x": 5, "y": 4}]})
        self.assertTrue(li.move_used)
        self.assertEqual(li.normal_move_steps_used, 2)
        self.assertEqual(li.remaining_normal_move_distance(battle), 1)

        battle.perform_action({"type": "move", "unit_id": li.unit_id, "x": 6, "y": 4})
        self.assertEqual(li.normal_move_steps_used, 3)
        self.assertEqual(li.remaining_normal_move_distance(battle), 0)
        self.assertFalse(battle.action_snapshot_for(li)["can_move"])

    def test_regular_units_still_cannot_split_normal_movement(self) -> None:
        battle = create_battle("bard", "li")
        bard = primary_hero(battle, 1)
        li = primary_hero(battle, 2)
        bard.position = Position(3, 4)
        li.position = Position(8, 8)

        battle.perform_action({"type": "move", "unit_id": bard.unit_id, "x": 4, "y": 4})

        with self.assertRaises(ActionError):
            battle.perform_action({"type": "move", "unit_id": bard.unit_id, "x": 5, "y": 4})

    def test_chain_pull_hits_first_unit_in_front_five_and_pulls_adjacent(self) -> None:
        battle = create_battle("li", ["bard", "ellie"])
        li = primary_hero(battle, 1)
        bard = next(unit for unit in battle.hero_units(2) if unit.hero_code == "bard")
        ellie = next(unit for unit in battle.hero_units(2) if unit.hero_code == "ellie")
        li.position = Position(3, 4)
        bard.position = Position(5, 4)
        ellie.position = Position(6, 4)

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": li.unit_id,
                "skill_code": "chain_pull",
                "cells": [{"x": x, "y": 4} for x in range(4, 9)],
            }
        )
        while battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

        self.assertAlmostEqual(li.current_mana, 4.5)
        self.assertEqual(bard.position, Position(4, 4))
        self.assertEqual(ellie.position, Position(6, 4))

        with self.assertRaisesRegex(ActionError, "本回合使用次数已满"):
            battle.perform_action(
                {
                    "type": "skill",
                    "unit_id": li.unit_id,
                    "skill_code": "chain_pull",
                    "cells": [{"x": x, "y": 4} for x in range(4, 9)],
                }
            )

    def test_red_heat_is_start_phase_toggle_with_turn_end_hp_loss(self) -> None:
        battle = create_battle("li", "bard")
        li = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        li.position = Position(3, 4)
        bard.position = Position(8, 8)

        battle.perform_action({"type": "skill", "unit_id": li.unit_id, "skill_code": "red_heat"})
        self.assertTrue(li.has_status("红热"))
        self.assertEqual(li.stat("attack"), 5)
        self.assertEqual(li.stat("speed"), 6)
        battle.perform_action({"type": "end_turn"})
        self.assertAlmostEqual(li.current_hp, 0.5)

        while battle.current_turn_unit().unit_id != li.unit_id:
            battle.perform_action({"type": "end_turn"})

        battle.perform_action({"type": "skill", "unit_id": li.unit_id, "skill_code": "red_heat"})
        self.assertFalse(li.has_status("红热"))
        self.assertEqual(li.stat("attack"), 3)
        self.assertEqual(li.stat("speed"), 3)

    def test_foresight_blocks_enemy_basic_attack_and_rewards_next_li_turn(self) -> None:
        battle = create_battle("fire_funeral", "li")
        fire = primary_hero(battle, 1)
        li = primary_hero(battle, 2)
        fire.position = Position(4, 4)
        li.position = Position(5, 4)

        while battle.current_turn_unit().unit_id != fire.unit_id:
            battle.perform_action({"type": "end_turn"})
        before_hp = li.current_hp
        battle.perform_action({"type": "attack", "unit_id": fire.unit_id, "target_unit_id": li.unit_id, "x": 5, "y": 4})
        self.assertIsNotNone(battle.pending_chain)
        while battle.pending_chain is not None and battle.pending_chain.current_unit_id() != li.unit_id:
            battle.perform_action({"type": "chain_skip"})
        battle.perform_action({"type": "chain_react", "unit_id": li.unit_id, "action_code": "foresight"})
        while battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

        self.assertAlmostEqual(li.current_hp, before_hp)
        self.assertTrue(li.has_status("见切奖励"))
        self.assertEqual(li.attack_actions_per_turn(), 4)
        self.assertEqual(li.stat("speed"), 4)

    def test_stillness_ends_red_heat_prevents_actions_and_heals_on_own_turn_start(self) -> None:
        battle = create_battle("li", "bard")
        li = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        li.position = Position(3, 4)
        bard.position = Position(8, 8)
        li.current_hp = 0.25

        battle.perform_action({"type": "skill", "unit_id": li.unit_id, "skill_code": "red_heat"})
        battle.perform_action({"type": "skill", "unit_id": li.unit_id, "skill_code": "stillness"})
        stillness = li.get_skill("stillness")
        self.assertEqual(stillness.timing, "active")
        self.assertEqual(stillness.max_uses_per_battle, 1)
        self.assertEqual(stillness.uses_this_battle, 1)

        self.assertFalse(li.has_status("红热"))
        self.assertTrue(li.cannot_move)
        self.assertTrue(li.cannot_attack)
        self.assertTrue(li.cannot_use_skills)
        self.assertTrue(li.magic_immunity)
        self.assertEqual(li.stat("defense"), 7)
        with self.assertRaises(ActionError):
            battle.perform_action({"type": "attack", "unit_id": li.unit_id, "target_unit_id": bard.unit_id})

        battle.perform_action({"type": "end_turn"})
        while battle.current_turn_unit().unit_id != li.unit_id:
            battle.perform_action({"type": "end_turn"})

        self.assertAlmostEqual(li.current_hp, 1.0)
        self.assertTrue(li.has_status("定"))

    def test_stillness_is_not_available_on_enemy_turn(self) -> None:
        battle = create_battle("fire_funeral", "li")
        fire = primary_hero(battle, 1)
        li = primary_hero(battle, 2)
        fire.position = Position(4, 4)
        li.position = Position(5, 4)
        li.current_hp = 0.25

        while battle.current_turn_unit().unit_id != fire.unit_id:
            battle.perform_action({"type": "end_turn"})
        waiting_actions = {action["code"]: action for action in battle.action_snapshot_for(li)["actions"]}
        self.assertFalse(waiting_actions["stillness"]["available"])
        self.assertEqual(waiting_actions["stillness"]["timing"], "active")
        with self.assertRaises(ActionError):
            battle.perform_action({"type": "skill", "unit_id": li.unit_id, "skill_code": "stillness"})

        self.assertFalse(li.has_status("定"))
        self.assertAlmostEqual(li.current_hp, 0.25)

    def test_li_ignores_slow_status_speed_reduction(self) -> None:
        battle = create_battle("li", "bard")
        li = primary_hero(battle, 1)

        li.add_status(SlowStatus(2))

        self.assertEqual(li.stat("speed"), 3)


class ChanterTests(unittest.TestCase):
    def test_paralysis_card_blocks_skills_on_center_and_surrounding_cells(self) -> None:
        battle = create_battle("chanter", "bard")
        chanter = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        chanter.position = Position(3, 4)
        bard.position = Position(5, 4)

        battle.perform_action({"type": "skill", "unit_id": chanter.unit_id, "skill_code": "paralysis_card", "x": 5, "y": 4})
        while battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})
        battle.perform_action({"type": "end_turn"})

        with self.assertRaises(ActionError):
            battle.perform_action({"type": "skill", "unit_id": bard.unit_id, "skill_code": "heal", "target_unit_id": bard.unit_id})

    def test_poison_and_drain_cards_trigger_on_affected_units_own_turn_end(self) -> None:
        battle = create_battle("chanter", "fire_funeral")
        chanter = primary_hero(battle, 1)
        target = primary_hero(battle, 2)
        chanter.position = Position(3, 4)
        target.position = Position(5, 4)
        chanter.current_mana = 3
        target.current_mana = 3
        before_hp = target.current_hp

        battle.perform_action({"type": "skill", "unit_id": chanter.unit_id, "skill_code": "poison_card", "x": 5, "y": 4})
        while battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})
        battle.perform_action({"type": "skill", "unit_id": chanter.unit_id, "skill_code": "drain_card", "x": 5, "y": 4})
        while battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})
        after_cast_mana = chanter.current_mana

        battle.perform_action({"type": "end_turn"})
        self.assertAlmostEqual(target.current_hp, before_hp)
        self.assertEqual(target.current_mana, 3)

        battle.perform_action({"type": "end_turn"})

        self.assertLess(target.current_hp, before_hp)
        self.assertEqual(target.current_mana, 2)
        self.assertEqual(chanter.current_mana, after_cast_mana + 2)

    def test_card_transposition_keeps_original_action_on_original_cell(self) -> None:
        battle = create_battle("chanter", "fire_funeral")
        chanter = primary_hero(battle, 1)
        fire = primary_hero(battle, 2)
        chanter.position = Position(3, 4)
        fire.position = Position(4, 4)

        battle.perform_action({"type": "skill", "unit_id": chanter.unit_id, "skill_code": "paralysis_card", "x": 2, "y": 4})
        while battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})
        battle.perform_action({"type": "end_turn"})

        before_hp = chanter.current_hp
        battle.perform_action({"type": "attack", "unit_id": fire.unit_id, "target_unit_id": chanter.unit_id, "x": 3, "y": 4})
        self.assertIsNotNone(battle.pending_chain)
        while battle.pending_chain is not None and battle.pending_chain.current_unit_id() != chanter.unit_id:
            battle.perform_action({"type": "chain_skip"})
        battle.perform_action({"type": "chain_react", "unit_id": chanter.unit_id, "action_code": "card_transposition", "x": 2, "y": 4})
        while battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

        self.assertEqual(chanter.position, Position(2, 4))
        self.assertAlmostEqual(chanter.current_hp, before_hp)

    def test_card_transposition_does_not_offer_card_under_self(self) -> None:
        battle = create_battle("chanter", "fire_funeral")
        chanter = primary_hero(battle, 1)
        fire = primary_hero(battle, 2)
        chanter.position = Position(3, 4)
        fire.position = Position(5, 4)
        chanter.current_mana = 5

        battle.perform_action({"type": "skill", "unit_id": chanter.unit_id, "skill_code": "paralysis_card", "x": 2, "y": 4})
        while battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})
        chanter.position = Position(2, 4)
        fire.position = Position(3, 4)
        battle.perform_action({"type": "end_turn"})

        battle.perform_action({"type": "attack", "unit_id": fire.unit_id, "target_unit_id": chanter.unit_id, "x": 2, "y": 4})
        self.assertIsNotNone(battle.pending_chain)
        while battle.pending_chain is not None and battle.pending_chain.current_unit_id() != chanter.unit_id:
            battle.perform_action({"type": "chain_skip"})
        actions = battle.reaction_snapshot_for(chanter)["actions"]
        card_action = next((action for action in actions if action["action_code"] == "card_transposition"), None)

        self.assertTrue(card_action is None or not card_action["available"])

    def test_magic_claw_uses_card_surrounding_cells_but_not_card_cell(self) -> None:
        battle = create_battle("chanter", ["bard", "ellie"])
        chanter = primary_hero(battle, 1)
        bard = next(unit for unit in battle.hero_units(2) if unit.hero_code == "bard")
        ellie = next(unit for unit in battle.hero_units(2) if unit.hero_code == "ellie")
        chanter.position = Position(3, 4)
        bard.position = Position(5, 4)
        ellie.position = Position(6, 4)

        battle.perform_action({"type": "skill", "unit_id": chanter.unit_id, "skill_code": "poison_card", "x": 5, "y": 4})
        while battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})
        battle.perform_action({"type": "skill", "unit_id": chanter.unit_id, "skill_code": "magic_claw", "x": 5, "y": 4})
        while battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

        self.assertFalse(bard.has_status("魔爪"))
        self.assertTrue(ellie.has_status("魔爪"))
        self.assertTrue(ellie.cannot_move)

    def test_form_shift_is_permanent_and_only_disables_three_card_skills(self) -> None:
        battle = create_battle("chanter", "bard")
        chanter = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        chanter.position = Position(3, 4)
        bard.position = Position(6, 4)

        battle.perform_action({"type": "skill", "unit_id": chanter.unit_id, "skill_code": "poison_card", "x": 4, "y": 4})
        while battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})
        battle.perform_action({"type": "skill", "unit_id": chanter.unit_id, "skill_code": "form_shift"})

        self.assertEqual(chanter.stat("attack"), 4)
        self.assertEqual(chanter.stat("defense"), 5)
        self.assertEqual(chanter.stat("speed"), 4)
        self.assertEqual(chanter.stat("attack_range"), 1)
        with self.assertRaises(ActionError):
            battle.perform_action({"type": "skill", "unit_id": chanter.unit_id, "skill_code": "poison_card", "x": 4, "y": 5})

        battle.perform_action({"type": "skill", "unit_id": chanter.unit_id, "skill_code": "magic_claw", "x": 4, "y": 4})


class ErasureApostleTests(unittest.TestCase):
    def erasure_counter_count(self, unit, source_unit_id: str | None = None) -> int:
        return sum(
            1
            for status in unit.statuses
            if isinstance(status, ErasureCounterStatus)
            and (source_unit_id is None or status.source_unit_id == source_unit_id)
        )

    def test_premature_burial_places_stackable_piercing_counters_and_erasure_raw_damage(self) -> None:
        battle = create_battle("erasure_apostle", "bard")
        apostle = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        apostle.position = Position(3, 4)
        bard.position = Position(5, 4)
        bard.shields = 1

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": apostle.unit_id,
                "skill_code": "premature_burial",
                "target_unit_id": bard.unit_id,
                "x": 5,
                "y": 4,
            }
        )
        resolve_pending_chain(battle)

        self.assertEqual(bard.shields, 0)
        self.assertEqual(self.erasure_counter_count(bard, apostle.unit_id), 1)

        bard.add_status(ErasureCounterStatus(apostle.unit_id))
        battle.perform_action({"type": "skill", "unit_id": apostle.unit_id, "skill_code": "erasure"})
        resolve_pending_chain(battle)

        self.assertTrue(bard.alive)
        self.assertAlmostEqual(bard.current_hp, 0.5)
        self.assertEqual(self.erasure_counter_count(bard, apostle.unit_id), 0)

    def test_erasure_kill_rewards_mana_and_resets_erasure(self) -> None:
        battle = create_battle("erasure_apostle", "bard")
        apostle = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        apostle.position = Position(3, 4)
        bard.position = Position(5, 4)
        apostle.current_mana = 0
        bard.current_mana = 2
        for _ in range(4):
            bard.add_status(ErasureCounterStatus(apostle.unit_id))

        battle.perform_action({"type": "skill", "unit_id": apostle.unit_id, "skill_code": "erasure"})
        resolve_pending_chain(battle)

        self.assertFalse(bard.alive)
        self.assertEqual(apostle.current_mana, 2)
        self.assertEqual(skill_by_code(apostle, "erasure").uses_this_battle, 0)

    def test_descent_moment_teleports_and_only_extra_attacks_are_locked_to_target(self) -> None:
        battle = create_battle("erasure_apostle", ["bard", "ellie"])
        apostle = primary_hero(battle, 1)
        bard = next(unit for unit in battle.hero_units(2) if unit.hero_code == "bard")
        ellie = next(unit for unit in battle.hero_units(2) if unit.hero_code == "ellie")
        apostle.position = Position(3, 4)
        bard.position = Position(5, 4)
        ellie.position = Position(4, 5)
        ellie.base_stats.defense = 5
        ellie.max_health = 4
        ellie.current_hp = 4
        bard.add_status(ErasureCounterStatus("other-source"))

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": apostle.unit_id,
                "skill_code": "descent_moment",
                "target_unit_id": bard.unit_id,
                "dest_x": 4,
                "dest_y": 4,
            }
        )

        self.assertEqual(apostle.position, Position(4, 4))
        self.assertEqual(apostle.attack_actions_per_turn(), 3)

        battle.perform_action({"type": "attack", "unit_id": apostle.unit_id, "target_unit_id": ellie.unit_id, "x": 4, "y": 5})
        resolve_pending_chain(battle)

        with self.assertRaises(ActionError):
            battle.perform_action(
                {"type": "attack", "unit_id": apostle.unit_id, "target_unit_id": ellie.unit_id, "x": 4, "y": 5}
            )

        battle.perform_action({"type": "attack", "unit_id": apostle.unit_id, "target_unit_id": bard.unit_id, "x": 5, "y": 4})
        resolve_pending_chain(battle)
        self.assertEqual(apostle.attacks_used, 2)

    def test_shadow_counter_moves_two_normal_steps_and_places_non_piercing_counters(self) -> None:
        battle = create_battle(["fire_funeral", "bard"], "erasure_apostle")
        fire = next(unit for unit in battle.hero_units(1) if unit.hero_code == "fire_funeral")
        bard = next(unit for unit in battle.hero_units(1) if unit.hero_code == "bard")
        apostle = primary_hero(battle, 2)
        fire.position = Position(5, 4)
        bard.position = Position(3, 5)
        apostle.position = Position(4, 4)

        while battle.current_turn_unit().unit_id != fire.unit_id:
            battle.perform_action({"type": "end_turn"})
        bard.shields = 1

        battle.perform_action({"type": "attack", "unit_id": fire.unit_id, "target_unit_id": apostle.unit_id, "x": 4, "y": 4})

        self.assertIsNotNone(battle.pending_chain)
        options = battle.pending_chain.options_by_unit.get(apostle.unit_id, [])
        self.assertIn("shadow_counter", [option.action_code for option in options])

        battle.perform_action(
            {
                "type": "chain_react",
                "unit_id": apostle.unit_id,
                "action_code": "shadow_counter",
                "x": 2,
                "y": 4,
            }
        )
        resolve_pending_chain(battle)

        self.assertEqual(apostle.position, Position(2, 4))
        self.assertEqual(self.erasure_counter_count(fire, apostle.unit_id), 1)
        self.assertEqual(self.erasure_counter_count(bard, apostle.unit_id), 0)
        self.assertEqual(bard.shields, 0)
        self.assertAlmostEqual(apostle.current_mana, 3.5)


class DragonRiderTests(unittest.TestCase):
    def dragon_pattern_hitting(self, skill_or_action, unit) -> list[dict[str, int]]:
        patterns = skill_or_action["preview"]["selection"]["patterns"] if isinstance(skill_or_action, dict) else skill_or_action
        for pattern in patterns:
            if any(cell["x"] == unit.position.x and cell["y"] == unit.position.y for cell in pattern):
                return pattern
        raise AssertionError("no pattern hits unit")

    def test_dragon_rider_starts_mounted_on_two_by_two_dragon_and_mount_has_own_cooldown(self) -> None:
        battle = create_battle("dragon_rider", "bard")
        rider = primary_hero(battle, 1)
        dragon = summon_by_code(battle, 1, "dragon_mount")

        self.assertIs(battle.mounted_unit_for(rider), dragon)
        self.assertEqual(dragon.footprint_width, 2)
        self.assertEqual(dragon.footprint_height, 2)
        self.assertIn(rider.position, battle.unit_cells(dragon))

        dragon.alive = False
        battle.cleanup_dead_units()

        self.assertTrue(rider.has_status("召龙冷却"))
        with self.assertRaises(ActionError):
            battle.perform_action({"type": "skill", "unit_id": rider.unit_id, "skill_code": "summon_dragon"})

    def test_dragon_area_attack_uses_pattern_cells_and_piercing_debuff_after_shielded_hit(self) -> None:
        battle = create_battle("dragon_rider", "bard")
        rider = primary_hero(battle, 1)
        dragon = summon_by_code(battle, 1, "dragon_mount")
        bard = primary_hero(battle, 2)
        rider.position = Position(3, 3)
        dragon.position = Position(3, 3)
        bard.position = Position(6, 4)
        bard.shields = 1

        action = next(action for action in battle.action_snapshot_for(dragon)["actions"] if action["kind"] == "attack")
        pattern = self.dragon_pattern_hitting(action, bard)

        battle.perform_action({"type": "attack", "unit_id": dragon.unit_id, "cells": pattern})
        resolve_pending_chain(battle)

        self.assertEqual(bard.shields, 0)
        self.assertAlmostEqual(bard.current_hp, 1.0)
        self.assertTrue(bard.has_status("龙击破魔"))
        self.assertEqual(bard.stat("speed"), 1)
        self.assertEqual(bard.stat("defense"), 3)

    def test_ai_generates_cell_payloads_for_dragon_area_attack(self) -> None:
        battle = create_battle("dragon_rider", "bard")
        rider = primary_hero(battle, 1)
        dragon = summon_by_code(battle, 1, "dragon_mount")
        bard = primary_hero(battle, 2)
        rider.position = Position(3, 3)
        dragon.position = Position(3, 3)
        bard.position = Position(6, 4)
        action = next(action for action in battle.action_snapshot_for(dragon)["actions"] if action["kind"] == "attack")

        payloads = attack_payloads_for_action(battle, dragon, action)

        self.assertTrue(any(payload.get("cells") and not payload.get("target_unit_id") for payload in payloads))
        battle.build_queued_action(payloads[0])

    def test_dragon_damage_reduction_caps_skill_area_bonus_but_not_raw_damage(self) -> None:
        battle = create_battle("dragon_rider", "bard")
        dragon = summon_by_code(battle, 1, "dragon_mount")
        bard = primary_hero(battle, 2)

        battle.resolve_damage(
            DamageContext(
                source=bard,
                target=dragon,
                attack_power=5,
                is_skill=True,
                action_name="测试技能",
                area_cell_hits=4,
            )
        )
        self.assertAlmostEqual(dragon.current_hp, 0.75)

        dragon.current_hp = 1.0
        battle.resolve_damage(
            DamageContext(
                source=bard,
                target=dragon,
                attack_power=0,
                is_skill=True,
                action_name="测试固定伤害",
                raw_damage=0.5,
                area_cell_hits=4,
            )
        )
        self.assertAlmostEqual(dragon.current_hp, 0.5)

    def test_dragon_slash_damages_pulls_and_cleans_slow_on_rider_next_turn_start(self) -> None:
        battle = create_battle("dragon_rider", "bard")
        rider = primary_hero(battle, 1)
        dragon = summon_by_code(battle, 1, "dragon_mount")
        bard = primary_hero(battle, 2)
        rider.position = Position(3, 3)
        dragon.position = Position(3, 3)
        bard.position = Position(6, 3)
        bard.max_health = 3
        bard.current_hp = 3
        skill = skill_by_code(rider, "dragon_slash")
        pattern = next(pattern for pattern in skill.patterns(battle, rider) if bard.position in pattern)

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": rider.unit_id,
                "skill_code": "dragon_slash",
                "cells": [cell.to_dict() for cell in pattern],
            }
        )
        resolve_pending_chain(battle)

        self.assertEqual(bard.position, Position(5, 3))
        self.assertAlmostEqual(bard.current_hp, 2.0)
        self.assertTrue(bard.has_status("龙斩链条"))
        self.assertEqual(bard.stat("speed"), 1)

        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})

        self.assertFalse(bard.has_status("龙斩链条"))

    def test_dragon_slash_pull_is_not_limited_by_target_speed(self) -> None:
        battle = create_battle("dragon_rider", "bard")
        rider = primary_hero(battle, 1)
        dragon = summon_by_code(battle, 1, "dragon_mount")
        bard = primary_hero(battle, 2)
        rider.position = Position(3, 3)
        dragon.position = Position(3, 3)
        bard.position = Position(7, 3)
        bard.base_stats.speed = 1
        bard.max_health = 3
        bard.current_hp = 3
        skill = skill_by_code(rider, "dragon_slash")
        pattern = next(pattern for pattern in skill.patterns(battle, rider) if bard.position in pattern)

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": rider.unit_id,
                "skill_code": "dragon_slash",
                "cells": [cell.to_dict() for cell in pattern],
            }
        )
        resolve_pending_chain(battle)

        self.assertEqual(bard.position, Position(5, 3))
        self.assertTrue(any(type(status).__name__ == "DragonSlashSlowStatus" for status in bard.statuses))

    def test_smoke_spray_blocks_attack_and_active_skills_until_rider_next_turn_start(self) -> None:
        battle = create_battle("dragon_rider", "bard")
        rider = primary_hero(battle, 1)
        dragon = summon_by_code(battle, 1, "dragon_mount")
        bard = primary_hero(battle, 2)
        rider.position = Position(2, 2)
        dragon.position = Position(2, 2)
        bard.position = Position(5, 3)
        skill = skill_by_code(rider, "smoke_spray")
        pattern = next(
            pattern
            for pattern in skill.patterns(battle, rider)
            if bard.position in pattern and rider.position not in pattern
        )

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": rider.unit_id,
                "skill_code": "smoke_spray",
                "cells": [cell.to_dict() for cell in pattern],
            }
        )
        resolve_pending_chain(battle)

        self.assertTrue(bard.has_status("喷烟"))
        self.assertTrue(bard.cannot_attack)
        self.assertFalse(bard.cannot_move)

        battle.perform_action({"type": "end_turn"})
        with self.assertRaises(ActionError):
            battle.perform_action({"type": "attack", "unit_id": bard.unit_id, "target_unit_id": rider.unit_id, "x": 2, "y": 2})
        with self.assertRaises(ActionError):
            battle.perform_action({"type": "skill", "unit_id": bard.unit_id, "skill_code": "heal", "target_unit_id": bard.unit_id})

        battle.perform_action({"type": "end_turn"})

        self.assertFalse(bard.has_status("喷烟"))
        self.assertFalse(bard.cannot_attack)
        self.assertFalse(battle.field_effects)

    def test_dragon_rider_gains_mana_for_fielded_allied_mage_heroes_on_own_turn_start(self) -> None:
        battle = create_battle(["dragon_rider", "ellie"], "bard")
        rider = next(unit for unit in battle.hero_units(1) if unit.hero_code == "dragon_rider")
        rider.current_mana = 3

        while battle.current_turn_unit().unit_id != rider.unit_id:
            battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})
        while battle.current_turn_unit().unit_id != rider.unit_id:
            battle.perform_action({"type": "end_turn"})

        self.assertEqual(rider.current_mana, 4)


class ClassicMultiHeroBattleTests(unittest.TestCase):
    def test_classic_multihero_turn_order_interleaves_sorted_side_lists(self) -> None:
        battle = create_battle(
            ["dark_human", "fire_funeral", "bard"],
            ["undead_king_lina", "jade", "doomlight_dragon"],
        )

        turn_order_codes = [battle.get_unit(unit_id).hero_code for unit_id in battle.turn_order_unit_ids]

        self.assertEqual(
            turn_order_codes,
            ["undead_king_lina", "dark_human", "jade", "fire_funeral", "doomlight_dragon", "bard"],
        )
        self.assertEqual(battle.current_turn_unit().hero_code, "undead_king_lina")
        self.assertEqual(battle.active_player, 2)

    def test_destroyed_hero_slot_is_skipped_without_reordering(self) -> None:
        battle = create_battle(
            ["dark_human", "fire_funeral", "bard"],
            ["undead_king_lina", "jade", "doomlight_dragon"],
        )
        bard = next(unit for unit in battle.hero_units(1) if unit.hero_code == "bard")

        battle.end_turn()
        battle.end_turn()
        battle.end_turn()
        battle.end_turn()
        battle.remove_unit(bard)
        battle.end_turn()

        self.assertIn(bard.unit_id, battle.turn_order_unit_ids)
        self.assertEqual(battle.current_turn_unit().hero_code, "undead_king_lina")
        self.assertEqual(battle.round_number, 2)
        battle.end_turn()
        self.assertEqual(battle.current_turn_unit().hero_code, "dark_human")
        self.assertEqual(battle.active_player, 1)

    def test_banished_hero_keeps_slot_and_returns_on_its_turn(self) -> None:
        battle = create_battle(["ellie", "bard"], ["dark_human", "fire_funeral"])
        bard = next(unit for unit in battle.hero_units(1) if unit.hero_code == "bard")
        origin = bard.position

        battle.banish_unit(bard, 0)
        battle.end_turn()

        self.assertEqual(battle.current_turn_unit().unit_id, bard.unit_id)
        self.assertFalse(bard.banished)
        self.assertEqual(bard.position, origin)
        self.assertEqual(battle.to_public_dict()["input_player"], 1)

    def test_mount_only_acts_in_owner_hero_turn_bundle(self) -> None:
        battle = create_battle(["masamune", "bard"], ["ellie"])
        current_bundle_codes = {getattr(unit, "hero_code", "") for unit in battle.current_turn_bundle_units()}

        self.assertEqual(battle.current_turn_unit().hero_code, "masamune")
        self.assertEqual(current_bundle_codes, {"masamune", "motor_horse"})

        battle.end_turn()

        next_bundle_codes = {getattr(unit, "hero_code", "") for unit in battle.current_turn_bundle_units()}
        self.assertEqual(battle.current_turn_unit().hero_code, "ellie")
        self.assertEqual(next_bundle_codes, {"ellie"})


if __name__ == "__main__":
    unittest.main()
