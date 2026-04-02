from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wujiang.engine.core import ActionError, Position  # noqa: E402
from wujiang.heroes.first_five import GreatFireFuneralField  # noqa: E402
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
        self.assertTrue(target.cannot_move)

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
        self.assertTrue(bard.cannot_move)

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

    def test_protection_chain_consumes_one_temp_shield_then_leaves_one(self) -> None:
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

        self.assertEqual(bard.temporary_shields, 1)

    def test_evasion_moves_two_cells_and_attack_hits_original_cell(self) -> None:
        battle = create_battle("ellie", "dark_human")
        ellie = battle.player_units(1)[0]
        dark = battle.player_units(2)[0]
        ellie.position = Position(4, 4)
        dark.position = Position(5, 4)

        battle.perform_action({"type": "attack", "unit_id": ellie.unit_id, "target_unit_id": dark.unit_id})

        self.assertIsNotNone(battle.pending_chain)
        battle.perform_action(
            {"type": "chain_react", "unit_id": dark.unit_id, "action_code": "evasion", "x": 7, "y": 4}
        )

        self.assertEqual(dark.position, Position(7, 4))
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
            {"type": "chain_react", "unit_id": dark.unit_id, "action_code": "evasion", "x": 0, "y": 2}
        )

        self.assertEqual(dark.position, Position(0, 2))
        self.assertEqual(dark.current_hp, 1.0)

    def test_evasion_requires_exactly_two_cells(self) -> None:
        battle = create_battle("ellie", "dark_human")
        ellie = battle.player_units(1)[0]
        dark = battle.player_units(2)[0]
        ellie.position = Position(4, 4)
        dark.position = Position(5, 4)

        battle.perform_action({"type": "attack", "unit_id": ellie.unit_id, "target_unit_id": dark.unit_id})

        self.assertIsNotNone(battle.pending_chain)
        with self.assertRaises(ActionError):
            battle.perform_action(
                {"type": "chain_react", "unit_id": dark.unit_id, "action_code": "evasion", "x": 6, "y": 4}
            )

    def test_evasion_can_pass_through_unit_while_moving_two_cells(self) -> None:
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
            {"type": "chain_react", "unit_id": dark.unit_id, "action_code": "evasion", "x": 7, "y": 4}
        )

        self.assertEqual(dark.position, Position(7, 4))
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
        self.assertEqual(bard.temporary_shields, 1)
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

    def test_magic_wall_can_chain_against_break_magic_even_with_existing_shield(self) -> None:
        battle = create_battle("dark_human", "ellie")
        dark = battle.player_units(1)[0]
        ellie = battle.player_units(2)[0]
        dark.position = Position(4, 4)
        ellie.position = Position(5, 4)
        ellie.max_health = 5.0
        ellie.current_hp = 5.0
        ellie.shields = 1

        battle.perform_action(
            {"type": "skill", "unit_id": dark.unit_id, "skill_code": "paralyzing_glove", "target_unit_id": ellie.unit_id}
        )

        self.assertIsNotNone(battle.pending_chain)
        battle.perform_action(
            {
                "type": "chain_react",
                "unit_id": ellie.unit_id,
                "action_code": "magic_wall",
                "target_unit_id": ellie.unit_id,
            }
        )

        self.assertEqual(ellie.current_mana, 4.0)
        self.assertEqual(ellie.current_hp, 4.0)
        self.assertEqual(ellie.shields, 1)
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

        battle.perform_action({"type": "attack", "unit_id": ellie.unit_id, "target_unit_id": fire.unit_id})

        self.assertIsNotNone(battle.pending_chain)
        battle.perform_action({"type": "chain_react", "unit_id": fire.unit_id, "action_code": "knockback"})

        self.assertEqual(ellie.position, Position(3, 4))
        self.assertEqual(fire.current_hp, 1.0)
        self.assertEqual(fire.shields, 0)

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

    def test_pierce_hits_two_cells_without_break_magic(self) -> None:
        battle = create_battle("fire_funeral", "bard")
        fire = battle.player_units(1)[0]
        bard = battle.player_units(2)[0]
        soldier = create_hero("elite_soldier", 2)
        fire.position = Position(4, 4)
        bard.position = Position(5, 4)
        bard.shields = 1
        battle.add_unit(soldier, Position(6, 4))

        battle.perform_action({"type": "skill", "unit_id": fire.unit_id, "skill_code": "pierce", "x": 6, "y": 4})

        self.assertIsNotNone(battle.pending_chain)
        battle.perform_action({"type": "chain_skip"})
        self.assertEqual(bard.shields, 0)
        self.assertEqual(bard.current_hp, 1.0)
        self.assertFalse(soldier.alive)

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
        hp_before = enemy.current_hp

        battle.perform_action({"type": "end_turn"})
        self.assertEqual(enemy.current_hp, hp_before)

        battle.perform_action({"type": "end_turn"})
        self.assertFalse(enemy.alive)

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

        battle.perform_action({"type": "skill", "unit_id": dark.unit_id, "skill_code": "fly_leap", "x": 5, "y": 4})

        self.assertEqual(dark.position, Position(5, 4))
        self.assertIsNone(battle.pending_chain)

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


if __name__ == "__main__":
    unittest.main()
