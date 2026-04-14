from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wujiang.engine.core import ActionError, DamageContext, Position, StatusEffect  # noqa: E402
from wujiang.heroes.first_five import GreatFireFuneralField  # noqa: E402
from wujiang.heroes.next_five import SandstormWeatherEffect  # noqa: E402
from wujiang.heroes.registry import create_battle, create_hero  # noqa: E402


class BattleSmokeTests(unittest.TestCase):
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

        battle.perform_action(
            {"type": "skill", "unit_id": bard.unit_id, "skill_code": "chant", "target_unit_id": enemy.unit_id}
        )

        self.assertEqual(enemy.mana_points, 0.0)

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

    def test_stealthed_unit_can_share_a_cell_but_cannot_act_until_separated(self) -> None:
        battle = create_battle("dark_human", "bard")
        dark = battle.player_units(1)[0]
        enemy = battle.player_units(2)[0]
        ally = create_hero("bard", 1)
        dark.position = Position(5, 4)
        enemy.position = Position(5, 5)
        battle.add_unit(ally, Position(4, 4))

        battle.perform_action({"type": "skill", "unit_id": dark.unit_id, "skill_code": "stealth"})
        battle.perform_action(
            {
                "type": "move",
                "unit_id": dark.unit_id,
                "x": 4,
                "y": 4,
                "path": [{"x": 4, "y": 4}],
            }
        )

        self.assertEqual(dark.position, ally.position)
        with self.assertRaises(ActionError):
            battle.perform_action({"type": "attack", "unit_id": dark.unit_id, "target_unit_id": enemy.unit_id})
        with self.assertRaises(ActionError):
            battle.perform_action(
                {"type": "skill", "unit_id": dark.unit_id, "skill_code": "paralyzing_glove", "target_unit_id": enemy.unit_id}
            )

    def test_visible_unit_can_move_onto_a_stealthed_ally_cell(self) -> None:
        battle = create_battle("dark_human", "bard")
        dark = battle.player_units(1)[0]
        enemy = battle.player_units(2)[0]
        ally = create_hero("bard", 1)
        dark.position = Position(5, 4)
        enemy.position = Position(7, 7)
        battle.add_unit(ally, Position(4, 4))

        battle.perform_action({"type": "skill", "unit_id": dark.unit_id, "skill_code": "stealth"})
        battle.perform_action(
            {
                "type": "move",
                "unit_id": ally.unit_id,
                "x": 5,
                "y": 4,
                "path": [{"x": 5, "y": 4}],
            }
        )

        self.assertEqual(ally.position, dark.position)

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

        battle.perform_action(
            {"type": "skill", "unit_id": dark.unit_id, "skill_code": "paralyzing_glove", "target_unit_id": bard.unit_id}
        )

        self.assertIsNotNone(battle.pending_chain)
        battle.perform_action({"type": "chain_react", "unit_id": bard.unit_id, "action_code": "protection"})

        self.assertEqual(bard.current_hp, 4.5)
        self.assertEqual(bard.temporary_shields, 0)
        self.assertTrue(bard.cannot_move)

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
        self.assertTrue(ellie.cannot_move)

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
        self.assertTrue(summon.cannot_move)

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
        self.assertIn("不能移动", pending_chain["queued_action_effect_summary"])
        self.assertIn("艾莉", pending_chain["queued_action_effect_summary"])

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
        if battle.pending_chain is not None:
            battle.perform_action({"type": "chain_skip"})

        self.assertTrue(battle.has_weather("沙尘"))
        self.assertEqual(battle.to_public_dict()["field_effects"][0]["weather_name"], "沙尘")

    def test_lina_recovers_naturally_on_own_turn_start_during_sandstorm(self) -> None:
        battle = create_battle("bard", "undead_king_lina")
        lina = battle.player_units(2)[0]
        lina.current_hp = 0.5
        lina.current_mana = 4
        battle.add_field_effect(SandstormWeatherEffect(duration=3))

        battle.perform_action({"type": "end_turn"})

        self.assertAlmostEqual(lina.current_hp, 0.75)
        self.assertAlmostEqual(lina.current_mana, 4.25)

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

    def test_large_footprint_move_blocks_overlap_except_stealth(self) -> None:
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

        bard.add_status(StatusEffect("隐身"))
        battle.perform_action(
            {
                "type": "move",
                "unit_id": lina.unit_id,
                "x": 2,
                "y": 4,
                "path": [{"x": 2, "y": 4}],
            }
        )

        self.assertEqual(lina.position, Position(2, 4))
        self.assertIn(bard, battle.units_at(Position(3, 4)))

    def test_rock_god_has_local_sandstorm_area(self) -> None:
        battle = create_battle("rock_god", "bard")
        rock = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        rock.position = Position(1, 4)
        bard.position = Position(7, 0)

        self.assertTrue(any(effect.name == "岩神沙尘" for effect in battle.field_effects))
        self.assertTrue(battle.cell_has_weather("沙尘", Position(5, 4)))
        self.assertFalse(battle.unit_in_weather("沙尘", bard))

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


if __name__ == "__main__":
    unittest.main()
