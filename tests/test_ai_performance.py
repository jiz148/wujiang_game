"""Focused correctness boundaries for dense direct-AI acceleration."""
from contextlib import ExitStack
import os
from pathlib import Path
import pickle
import random
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from wujiang.tactical import prediction
from wujiang.tactical.direct_queries import component_snapshot, empty_hook_pass
from wujiang.tactical.engine import core
from wujiang.tactical.engine.core import Position
from wujiang.tactical.heroes.registry import create_hero
from wujiang.tactical.heroes.mechanics.electronic_teleport import SatelliteCannonSkill
from wujiang.tactical.rooms import ai
from wujiang.tactical.rooms.synergy_effects import SynergyStatus, SynergyPierceSkill


class DirectAIPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.dict(os.environ, {'WUJIANG_AI_EXPECTATION_METHOD': 'direct',
            'WUJIANG_AI_DYNAMIC_COMPONENTS': '1', 'WUJIANG_AI_PROJECTION_QUERIES': '1'}))
        self.stack.enter_context(prediction.prediction_rules(True))
        self.battle = core.Battle(width=10, height=10)
        self.battle.chess_rng = random.Random(197)
        self.actor, self.enemy = create_hero('bard', 1), create_hero('bard', 2)
        for unit in (self.actor, self.enemy):
            unit.skills.clear()
            unit.traits.clear()
        self.battle.add_unit(self.actor, core.Position(2, 2))
        self.battle.add_unit(self.enemy, core.Position(3, 2))

    def require_native(self):
        from wujiang.platform.ai_prediction_native import load_kernel
        if load_kernel() is None:
            self.skipTest('Optional native query module is not built')

    def test_missing_native_module_uses_complete_original_rollback(self):
        self.actor.add_status(SynergyStatus('attribute', '雷', 2))
        before = pickle.dumps(self.battle), random.getstate()
        with mock.patch('wujiang.platform.ai_prediction_native.load_kernel', return_value=None), ai.ai_enemy_view(self.battle, 1):
            self.assertIsNone(component_snapshot(self.battle, self.actor))
            with ai.ai_probe_rollback(self.battle):
                self.actor.current_hp = .1
        self.assertEqual((pickle.dumps(self.battle), random.getstate()), before)

    def test_empty_dynamic_provider_uses_original_components_and_reusable_checkpoint(self):
        self.require_native()
        status = SynergyStatus('attribute', '雷', 2)
        self.actor.add_status(status)
        before = pickle.dumps(self.battle), random.getstate(), self.battle.chess_rng.getstate()
        events = []
        with prediction.prediction_observer(events.append), ai.ai_enemy_view(self.battle, 1):
            self.assertEqual(component_snapshot(self.battle, self.actor), list(self.actor.iter_components()))
            for _ in range(2):
                with ai.ai_probe_rollback(self.battle):
                    status.extra_moves = 3
                    self.actor.current_hp = .1
                self.assertEqual(status.extra_moves, 0)
        self.assertEqual((pickle.dumps(self.battle), random.getstate(), self.battle.chess_rng.getstate()), before)
        self.assertGreaterEqual(events[0]['statistics'].get('direct_checkpoint_reuses', 0), 1)

    def test_nonempty_dynamic_skill_keeps_original_discovery_and_child_rollback(self):
        self.require_native()
        status = SynergyStatus('role', '剑士', 2)
        self.actor.add_status(status)
        child = status.pierce
        before = pickle.dumps(self.battle)
        with ai.ai_enemy_view(self.battle, 1):
            self.assertIsNone(component_snapshot(self.battle, self.actor))
            self.assertIn(child, list(self.actor.iter_components()))
            with ai.ai_probe_rollback(self.battle):
                child.uses_this_turn = 8
                status.extra_attacks = 9
            self.assertEqual(child.uses_this_turn, 0)
            self.assertEqual(status.extra_attacks, 0)
        self.assertEqual(pickle.dumps(self.battle), before)

    def test_provider_child_added_after_empty_capture_invalidates_the_branch(self):
        self.require_native()
        status = SynergyStatus('attribute', '雷', 2)
        self.actor.add_status(status)
        with ai.ai_enemy_view(self.battle, 1):
            with ai.ai_probe_rollback(self.battle):
                status.extra_moves = 1
            status.pierce = SynergyPierceSkill().bind(self.actor)
            self.assertIsNone(component_snapshot(self.battle, self.actor))
            with ai.ai_probe_rollback(self.battle):
                status.pierce.uses_this_turn = 7
            self.assertEqual(status.pierce.uses_this_turn, 0)

    def test_provider_replacement_or_in_place_code_change_is_not_certified(self):
        self.require_native()
        status = SynergyStatus('attribute', '雷', 2)
        self.actor.add_status(status)
        with ai.ai_enemy_view(self.battle, 1):
            self.assertIsNotNone(component_snapshot(self.battle, self.actor))
            original = SynergyStatus.provided_components
            old_code = original.__code__
            try:
                original.__code__ = (lambda self: ()).__code__
                self.assertIsNone(component_snapshot(self.battle, self.actor))
            finally:
                original.__code__ = old_code
        with mock.patch.object(SynergyStatus, 'provided_components', lambda self: ()):
            with ai.ai_enemy_view(self.battle, 1):
                self.assertIsNone(component_snapshot(self.battle, self.actor))

    def test_unknown_provider_field_descriptor_is_rejected_without_calling_it(self):
        self.require_native()
        self.actor.add_status(SynergyStatus('attribute', '雷', 2))
        with ai.ai_enemy_view(self.battle, 1):
            self.assertIsNotNone(component_snapshot(self.battle, self.actor))
            def forbidden(_):
                raise AssertionError('descriptor evaluated')
            with mock.patch.object(SynergyStatus, 'pierce', property(forbidden), create=True):
                self.assertIsNone(component_snapshot(self.battle, self.actor))

    def test_disabling_conditional_provider_acceleration_keeps_original_discovery(self):
        self.require_native()
        self.actor.add_status(SynergyStatus('attribute', '雷', 2))
        with mock.patch.dict(os.environ, {'WUJIANG_AI_DYNAMIC_COMPONENTS': '0'}), ai.ai_enemy_view(self.battle, 1):
            self.assertIsNone(component_snapshot(self.battle, self.actor))
            self.assertEqual(len(list(self.actor.iter_components())), 1)

    def test_projection_empty_proof_does_not_require_an_empty_skill_provider(self):
        self.require_native()
        self.actor.is_summon = True
        self.actor.add_status(SynergyStatus('role', '剑士', 2))
        events = []
        with prediction.prediction_observer(events.append), ai.ai_enemy_view(self.battle, 1):
            self.assertEqual(list(self.actor.projected_traits()), [])
        self.assertGreater(events[0]['statistics'].get('direct_empty_projection_passes', 0), 0)

    def test_new_real_projection_and_suppression_take_the_original_path(self):
        self.require_native()
        class Projection(core.Trait):
            def projected_trait_for(inner, battle, target):
                return inner if target is self.actor else None
        trait = Projection('真实投射', '')
        self.actor.is_summon = True
        with ai.ai_enemy_view(self.battle, 1):
            self.assertTrue(empty_hook_pass(self.battle, 'projection'))
            self.enemy.traits.append(trait.bind(self.enemy))
            self.assertFalse(empty_hook_pass(self.battle, 'projection'))
            self.assertEqual(list(self.actor.projected_traits()), [trait])
            suppress = core.StatusEffect('封特性', '')
            suppress.suppresses_innate_traits = True
            self.enemy.add_status(suppress)
            self.assertEqual(list(self.actor.projected_traits()), [])

    def test_projection_method_code_change_is_not_skipped(self):
        self.require_native()
        trait = core.Trait('空投射', '')
        self.enemy.traits.append(trait.bind(self.enemy))
        self.actor.is_clone = True
        with ai.ai_enemy_view(self.battle, 1):
            self.assertEqual(list(self.actor.projected_traits()), [])
            original = core.BattleComponent.projected_trait_for
            old_code = original.__code__
            try:
                original.__code__ = (lambda self, battle, target: self).__code__
                self.assertFalse(empty_hook_pass(self.battle, 'projection'))
                self.assertEqual(list(self.actor.projected_traits()), [trait])
            finally:
                original.__code__ = old_code


class FixedAreaGeometryTests(unittest.TestCase):
    def test_patterns_and_selected_cells_match_original_order_at_clipped_edges(self):
        skill = SatelliteCannonSkill()
        for width, height in ((1, 1), (2, 5), (10, 10), (14, 9)):
            battle = core.Battle(width=width, height=height)
            with mock.patch.dict(os.environ, {'WUJIANG_AI_FIXED_GEOMETRY': '0'}):
                expected = skill.patterns(battle)
            with mock.patch.dict(os.environ, {'WUJIANG_AI_FIXED_GEOMETRY': '1'}):
                self.assertEqual(skill.patterns(battle), expected)
                for row in expected:
                    payload = {'cells': [dict(x=p.x, y=p.y) for p in reversed(row)]}
                    self.assertEqual(skill.chosen_cells(battle, payload), row)

    def test_returned_cells_and_lists_are_fresh_and_malformed_shapes_still_fail(self):
        skill, battle = SatelliteCannonSkill(), core.Battle(width=10, height=10)
        first, second = skill.patterns(battle), skill.patterns(battle)
        self.assertIsNot(first, second)
        self.assertIsNot(first[0][0], second[0][0])
        first[0].clear()
        self.assertTrue(skill.patterns(battle)[0])
        for cells in ([], [{'x': 0, 'y': 0}, {'x': 3, 'y': 3}], [{'x': -1, 'y': 0}]):
            with self.assertRaises(core.ActionError):
                skill.chosen_cells(battle, {'cells': cells})

    def test_custom_pattern_override_and_queued_cells_keep_their_contract(self):
        skill, battle = SatelliteCannonSkill(), core.Battle(width=10, height=10)
        custom = [core.Position(7, 7), core.Position(9, 9)]
        skill.patterns = lambda _: [custom]
        payload = {'cells': [dict(x=p.x, y=p.y) for p in custom]}
        self.assertEqual(skill.chosen_cells(battle, payload), custom)
        self.assertEqual(skill.chosen_cells(battle, {'queued_resolution': True, 'satellite_cells': [{'x': 4, 'y': 5}]}),
                         [core.Position(4, 5)])

    def test_geometry_cache_is_bounded_and_large_or_unusual_boards_decline(self):
        from wujiang.tactical.engine.geometry import clipped_rectangle_coordinates, _clipped_rectangles
        for size in range(3, 25):
            self.assertIsNotNone(clipped_rectangle_coordinates(size, 4, 3, 3))
        self.assertLessEqual(_clipped_rectangles.cache_info().currsize, 16)
        for dimensions in ((1000, 1000), (True, 5), (0, 5)):
            self.assertIsNone(clipped_rectangle_coordinates(*dimensions, 3, 3))

    def test_replaced_or_changed_pattern_method_keeps_its_custom_shape(self):
        skill, battle = SatelliteCannonSkill(), core.Battle(width=10, height=10)
        expected = [core.Position(7, 7), core.Position(9, 9)]
        payload = {'cells': [{'x': 7, 'y': 7}, {'x': 9, 'y': 9}]}
        with mock.patch.object(SatelliteCannonSkill, 'patterns', lambda self, world: [expected]):
            self.assertEqual(skill.chosen_cells(battle, payload), expected)
        original, old_code = SatelliteCannonSkill.patterns, SatelliteCannonSkill.patterns.__code__
        try:
            original.__code__ = (lambda self, world: [[Position(7, 7), Position(9, 9)]]).__code__
            self.assertEqual(skill.chosen_cells(battle, payload), expected)
        finally:
            original.__code__ = old_code


class DensePublicationTests(unittest.TestCase):
    def test_current_four_dense_boards_publish_without_touching_battles_rng_or_ids(self):
        from types import SimpleNamespace
        from wujiang.tactical.rooms.multiplayer import GameRoom
        from wujiang.tactical.rooms.autochess import AutoChessState, ChessMatch, hero_catalog
        from wujiang.tactical.rooms.synergy import synergy_counts, SynergyField
        from wujiang.tactical.rooms.equipment_effects import EquipmentStatus
        from wujiang.tactical.rooms.autochess_frames import publish_match
        from wujiang.tactical.rooms.ai_acceleration import _id_state
        room = GameRoom('FASTFRAME', mode='autochess', seat_count=8)
        room.autochess = chess = AutoChessState(8, seed=197)
        room.status = chess.phase = 'battle'
        chess.round_number = 12
        for index, seat in room.seats.items():
            seat.set_ai()
            seat.token = 'fast-viewer-' + str(index)
        catalog = hero_catalog()
        for board in range(4):
            battle = core.Battle(width=10, height=10)
            battle.chess_rng = random.Random(197 + board)
            codes = ('excel_r221', 'excel_r224', 'excel_r225', 'excel_r223', 'excel_r305', 'excel_r222') if board % 2 == 0 else ('excel_r239', 'excel_r238', 'excel_r240', 'excel_r242', 'excel_r243', 'excel_r244')
            for side in (1, 2):
                for index in range(12):
                    unit = create_hero(codes[index % len(codes)], side)
                    cell = next(core.Position(x, y) for y in range(10)
                                for x in (range(4) if side == 1 else range(6, 10))
                                if battle.can_place_unit(unit, core.Position(x, y)))
                    battle.add_unit(unit, cell)
                    if board == 2:
                        unit.add_status(EquipmentStatus('木盾', '', ''))
            entries = {side: [row for row in synergy_counts([
                SimpleNamespace(code=unit.hero_code) for unit in battle.player_units(side)], catalog) if row['tier']]
                for side in (1, 2)}
            field = SynergyField(entries)
            battle.add_field_effect(field)
            field.apply_initial(battle)
            runner = GameRoom('FAST' + str(board), seat_count=2)
            runner.battle = battle
            chess.matches.append(ChessMatch(board * 2 + 1, board * 2 + 2, runner=runner))
        for viewer in (1, 2):
            room.serialize_state('fast-viewer-' + str(viewer), advance_chess=False)
        battles = [m.runner.battle for m in chess.matches]
        self.assertTrue(all(len(b.units) >= 24 for b in battles))
        before = [pickle.dumps(b) for b in battles], random.getstate(), _id_state()
        for match in chess.matches:
            publish_match(room, chess, match)
        for viewer in (1, 2):
            payload = room.serialize_state('fast-viewer-' + str(viewer), advance_chess=False)
            self.assertEqual(len(payload['room']['autochess']['matches']), 4)
        self.assertEqual(([pickle.dumps(b) for b in battles], random.getstate(), _id_state()), before)


if __name__ == '__main__':
    unittest.main()
