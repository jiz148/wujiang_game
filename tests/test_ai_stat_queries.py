"""Short exactness checks for decision-local stat and unused-baseline work."""
from contextlib import ExitStack
import os
from pathlib import Path
from types import SimpleNamespace
import pickle
import random
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from wujiang.tactical import prediction, stat_queries
from wujiang.tactical.engine import core
from wujiang.tactical.heroes.registry import create_hero
from wujiang.tactical.rooms import ai
from wujiang.tactical.rooms.equipment_effects import EquipmentStatus
from wujiang.tactical.rooms.synergy_effects import SynergyStatus, SynergyPierceSkill
from wujiang.tactical.direct_queries import empty_hook_pass


class CountingStat(core.StatusEffect):
    def __init__(self):
        super().__init__('uncertified', '')
        self.calls = 0

    def modify_stat(self, name, value):
        if name == 'attack':
            self.calls += 1
            return value + self.calls
        return value


class MagicProvider(core.Trait):
    def __init__(self):
        super().__init__('aura', '')

    def grants_magic_immunity_to(self, battle, unit):
        return True


class SmallHit(core.Skill):
    def __init__(self):
        super().__init__('stat_test_hit', 'hit', '', target_mode='unit')

    def execute(self, battle, actor, payload):
        battle.resolve_damage(core.DamageContext(source=actor, target=battle.get_unit(payload['target_unit_id']),
            attack_power=2, is_skill=True, action_name=self.name))


class SmallMove(SmallHit):
    def execute(self, battle, actor, payload):
        battle.get_unit(payload['target_unit_id']).position = core.Position(4, 3)


class CountedMove(SmallMove):
    calls = []

    def ai_direct_effect_score(self, battle, actor, payload):
        type(self).calls.append(1)
        return None


class DirectStatQueryTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.dict(os.environ, {'WUJIANG_AI_EXPECTATION_METHOD': 'direct',
            'WUJIANG_AI_STAT_VIEWS': '1', 'WUJIANG_AI_LAZY_POSITION_BASELINE': '1'}))
        self.stack.enter_context(prediction.prediction_rules(True))
        from wujiang.platform.ai_prediction_native import load_kernel
        if load_kernel() is None or not hasattr(load_kernel(), 'make_field_guard'):
            self.skipTest('Native stat guard not built')
        self.battle = core.Battle(width=10, height=10)
        self.battle.chess_rng = random.Random(204)
        self.actor, self.enemy = create_hero('bard', 1), create_hero('bard', 2)
        for unit in (self.actor, self.enemy):
            unit.base_stats = core.Stats(**{name: getattr(unit.base_stats, name) for name in stat_queries._STATS})
            unit.skills.clear()
            unit.traits.clear()
        self.battle.add_unit(self.actor, core.Position(2, 2))
        self.battle.add_unit(self.enemy, core.Position(3, 2))
        self.battle.configure_turn_order([self.actor.unit_id, self.enemy.unit_id])
        self.battle.start_battle()

    def check_stats(self, unit=None):
        unit = unit or self.actor
        for name in ('attack', 'defense', 'speed', 'attack_range', 'mana'):
            self.assertEqual(float(unit.stat(name)).hex(), float(unit._stat_without_query_cache(name)).hex())

    def test_cache_has_real_hits_without_mutating_battle_rng_or_ids(self):
        from wujiang.tactical.rooms.ai_acceleration import _id_state
        before = pickle.dumps(self.battle), random.getstate(), _id_state()
        events = []
        with prediction.prediction_observer(events.append), ai.ai_enemy_view(self.battle, 1):
            self.check_stats()
            self.check_stats()
            self.assertGreater(prediction._session.get()['statistics'].get('stat_view_hits', 0), 0)
        self.assertEqual((pickle.dumps(self.battle), random.getstate(), _id_state()), before)

    def test_base_slots_and_minima_invalidate_cached_values(self):
        with ai.ai_enemy_view(self.battle, 1):
            self.check_stats()
            self.actor.base_stats = core.Stats(7, 3, 5, 4, 2)
            self.check_stats()
            self.actor.base_stats.attack = 8
            self.actor.stat_minimums['defense'] = 9
            self.check_stats()

    def test_equipment_owner_fields_code_and_bonus_invalidate(self):
        status = EquipmentStatus('自动臂', '', '')
        self.actor.add_status(status)
        with ai.ai_enemy_view(self.battle, 1):
            self.check_stats()
            self.actor.race = '机甲'
            self.check_stats()
            status.code = '腰带'
            status.bonus_defense = 3.5
            self.check_stats()
            status.bonus_defense = 5.25
            self.check_stats()
            status.code = '锈剑'
            self.actor.role = '剑士'
            self.check_stats()

    def test_synergy_tier_and_membership_invalidate(self):
        status = SynergyStatus('role', '骑士', 1)
        self.actor.add_status(status)
        with ai.ai_enemy_view(self.battle, 1):
            self.check_stats()
            status.tier = 2
            self.check_stats()
            self.actor.statuses.remove(status)
            self.check_stats()
            self.actor.statuses.append(status)
            self.check_stats()

    def test_unknown_modifier_executes_once_per_read(self):
        status = CountingStat()
        self.actor.add_status(status)
        with ai.ai_enemy_view(self.battle, 1):
            base = self.actor.base_stats.attack
            self.assertEqual(self.actor.stat('attack'), base + 1)
            self.assertEqual(self.actor.stat('attack'), base + 2)
            self.assertEqual(status.calls, 2)

    def test_unknown_getter_is_not_evaluated_by_the_guard(self):
        calls = []
        class Getter(core.StatusEffect):
            @property
            def modify_stat(self):
                calls.append(1)
                return lambda name, value: value
        self.actor.add_status(Getter('getter', ''))
        with ai.ai_enemy_view(self.battle, 1):
            self.actor.stat('attack')
            self.assertEqual(len(calls), 1)

    def test_field_modifier_and_trait_modifier_use_original_path(self):
        class Field(core.BattleFieldEffect):
            def modify_unit_stat(self, battle, unit, name, value):
                return value + 2
        class Receiver(core.Trait):
            def modify_received_stat_change(self, status, name, before, after):
                return after + 1
        self.battle.field_effects.append(Field('stat-field'))
        self.actor.traits.append(Receiver('receiver', '').bind(self.actor))
        with ai.ai_enemy_view(self.battle, 1):
            self.check_stats()

    def test_instance_and_in_place_method_replacements_are_not_reused(self):
        with ai.ai_enemy_view(self.battle, 1):
            self.check_stats()
            self.actor._stat_without_query_cache = lambda name: 18.5
            self.assertEqual(self.actor.stat('attack'), 18.5)
            del self.actor._stat_without_query_cache
            original = core.Unit._stat_without_query_cache
            old = original.__code__
            try:
                original.__code__ = (lambda self, name: 21.5).__code__
                self.assertEqual(self.actor.stat('attack'), 21.5)
            finally:
                original.__code__ = old

    def test_namespace_replacement_invalidates(self):
        with ai.ai_enemy_view(self.battle, 1):
            self.check_stats()
            self.actor.__dict__ = dict(vars(self.actor))
            self.actor.stat_minimums = {'attack': 11}
            self.check_stats()

    def test_dynamic_child_and_suppression_invalidate_components(self):
        status = SynergyStatus('attribute', '雷', 2)
        self.actor.add_status(status)
        with ai.ai_enemy_view(self.battle, 1):
            self.check_stats()
            cached = stat_queries.cached_components(self.actor)
            self.assertIsNotNone(cached)
            cached.clear()
            self.assertIn(status, stat_queries.cached_components(self.actor))
            status.pierce = SynergyPierceSkill().bind(self.actor)
            self.assertIsNone(stat_queries.cached_components(self.actor))
            self.check_stats()

    def test_stat_batch_is_immutable_and_rechecks_changes(self):
        with ai.ai_enemy_view(self.battle, 1):
            self.check_stats()
            units = self.battle.all_units()
            values = stat_queries.stat_values_for(units)
            self.assertIsNotNone(values)
            with self.assertRaises(TypeError):
                values[id(self.actor)]['attack'] = 900
            self.actor.base_stats.attack += 1
            current = stat_queries.stat_values_for(units)
            self.assertEqual(current[id(self.actor)]['attack'], self.actor._stat_without_query_cache('attack'))
            state = prediction._session.get()['stat_views']
            self.assertLessEqual(state.get('batch_bytes', 0), 8*1024*1024)
            self.assertLessEqual(state['bytes'], 8*1024*1024)

    def test_custom_shield_read_retains_resource_then_stat_order(self):
        status = EquipmentStatus('腰带', '', '')
        self.actor.add_status(status)
        with ai.ai_enemy_view(self.battle, 1):
            self.check_stats()
            calls = []
            def shield_read():
                calls.append(1)
                status.bonus_defense += 1
                return 0
            self.actor.total_shields = shield_read
            state = ai.r23_action_state(self.battle)
            self.assertEqual(len(calls), 1)
            self.assertEqual(state[self.actor.unit_id][-1]['defense'], self.actor.base_stats.defense + 1)

    def test_numeric_operator_override_cannot_enter_stat_batch(self):
        class CustomFloat(float):
            pass
        with ai.ai_enemy_view(self.battle, 1):
            self.check_stats()
            self.actor.current_hp = CustomFloat(self.actor.current_hp)
            self.assertIsNone(stat_queries.stat_values_for(self.battle.all_units()))

    def test_changed_unit_value_query_retains_per_unit_stat_interleaving(self):
        status = EquipmentStatus('腰带', '', '')
        self.enemy.add_status(status)
        with ai.ai_enemy_view(self.battle, 1):
            self.check_stats()
            before = ai.r23_action_state(self.battle)
            def changed(unit):
                status.bonus_defense += 1
                return 1.0
            with mock.patch.object(ai, 'hostile_unit_value', changed):
                self.assertEqual(ai.r23_action_delta(self.actor, before), -30.0)

    def test_custom_scoring_service_keeps_its_original_read_order(self):
        from wujiang.tactical.rooms.ai_late_scoring import AILateTacticalScoring
        status = EquipmentStatus('腰带', '', '')
        self.enemy.add_status(status)
        with ai.ai_enemy_view(self.battle, 1):
            self.check_stats()
            before = ai.r23_action_state(self.battle)
            def changed(unit):
                status.bonus_defense += 1
                return 1.0
            scorer = AILateTacticalScoring(SimpleNamespace(hostile_unit_value=changed))
            self.assertEqual(scorer.r23_action_delta(self.actor, before), -30.0)

    def test_unrelated_hp_mana_changes_keep_stat_values_exact(self):
        with ai.ai_enemy_view(self.battle, 1):
            self.check_stats()
            self.actor.current_hp -= .125
            self.actor.current_mana += .5
            self.check_stats()
            self.assertGreater(prediction._session.get()['statistics'].get('stat_view_hits', 0), 0)

    def test_in_place_annotated_modifier_edit_invalidates_its_certificate(self):
        status = EquipmentStatus('腰带', '', '')
        self.actor.add_status(status)
        with ai.ai_enemy_view(self.battle, 1):
            self.check_stats()
            function, old = EquipmentStatus.modify_stat, EquipmentStatus.modify_stat.__code__
            try:
                function.__code__ = (lambda self, name, value: value + 7).__code__
                self.assertEqual(self.actor.stat('defense'), self.actor.base_stats.defense + 7)
            finally:
                function.__code__ = old

    def test_new_dependency_descriptor_is_not_called_by_cache_validation(self):
        calls = []
        status = EquipmentStatus('腰带', '', '')
        self.actor.add_status(status)
        with ai.ai_enemy_view(self.battle, 1):
            self.check_stats()
            with mock.patch.object(EquipmentStatus, 'bonus_defense', property(lambda self: calls.append(1) or 4), create=True):
                self.assertEqual(self.actor.stat('defense'), self.actor.base_stats.defense + 4)
                self.assertEqual(len(calls), 1)

    def test_trait_suppression_updates_eager_snapshots(self):
        trait = core.Trait('ordinary', '').bind(self.actor)
        self.actor.traits.append(trait)
        class Suppression(core.StatusEffect):
            suppresses_innate_traits = True
        with ai.ai_enemy_view(self.battle, 1):
            self.check_stats()
            self.assertIn(trait, stat_queries.cached_components(self.actor))
            self.actor.statuses.append(Suppression('suppression', '').bind(self.actor))
            self.check_stats()
            self.assertNotIn(trait, self.battle.components_for(self.actor))

    def test_real_projected_modifier_keeps_original_stat_evaluator(self):
        class Projector(core.Trait):
            def projected_trait_for(self, battle, target):
                return core.Trait('projected', '').bind(target)
        with ai.ai_enemy_view(self.battle, 1):
            self.actor.is_summon = True
            self.check_stats()
            self.enemy.traits.append(Projector('source', '').bind(self.enemy))
            self.assertIsNone(stat_queries.cached_components(self.actor))
            self.check_stats()
            self.assertTrue(any(part.name == 'projected' for part in self.battle.components_for(self.actor)))

    def test_per_unit_cache_variants_remain_bounded(self):
        status = EquipmentStatus('腰带', '', '')
        self.actor.add_status(status)
        with ai.ai_enemy_view(self.battle, 1):
            for number in range(12):
                status.bonus_defense = number
                self.check_stats()
                stat_queries.stat_values_for(self.battle.all_units())
            state = prediction._session.get()['stat_views']
            self.assertLessEqual(len(state['views'][id(self.actor)]), 3)
            self.assertTrue(all(len(variants) <= 2 for variants in state['batches'].values()))

    def test_missing_native_module_uses_original(self):
        with ai.ai_enemy_view(self.battle, 1), mock.patch('wujiang.platform.ai_prediction_native.load_kernel', return_value=None):
            prediction._session.get().pop('stat_views', None)
            self.check_stats()
            self.assertIs(prediction._session.get()['stat_views'], False)

    def test_magic_provider_added_during_stat_reuse_is_observed(self):
        with ai.ai_enemy_view(self.battle, 1):
            self.check_stats()
            self.assertFalse(self.actor.magic_immunity)
            self.enemy.traits.append(MagicProvider().bind(self.enemy))
            self.assertTrue(self.actor.magic_immunity)

    def test_instance_all_units_override_keeps_original_immunity_query(self):
        provider = create_hero('bard', 2)
        provider.traits = [MagicProvider().bind(provider)]
        provider.position = core.Position(5, 5)
        with ai.ai_enemy_view(self.battle, 1):
            self.check_stats()
            self.assertFalse(self.actor.magic_immunity)
            self.battle.all_units = lambda: [provider]
            self.assertTrue(self.actor.magic_immunity)

    def test_empty_damage_notification_proof_rejects_new_hook(self):
        with ai.ai_enemy_view(self.battle, 1):
            self.assertTrue(empty_hook_pass(self.battle, 'on_battle_damage'))
            status = core.StatusEffect('observer', '').bind(self.enemy)
            status.on_battle_damage = lambda battle, ctx: None
            self.enemy.statuses.append(status)
            self.assertFalse(empty_hook_pass(self.battle, 'on_battle_damage'))

    def compare_effect(self, skill):
        from wujiang.tactical.rooms.ai_acceleration import _id_state
        self.actor.skills = [skill.bind(self.actor)]
        payload = {'type': 'skill', 'unit_id': self.actor.unit_id, 'skill_code': skill.code, 'target_unit_id': self.enemy.unit_id}
        before = pickle.dumps(self.battle), random.getstate(), _id_state()
        results, events = [], []
        for flag in ('0', '1'):
            with mock.patch.dict(os.environ, {'WUJIANG_AI_LAZY_POSITION_BASELINE': flag}), prediction.prediction_observer(events.append), ai.ai_enemy_view(self.battle, 1):
                self.check_stats()
                self.check_stats(self.enemy)
                results.append(ai.reviewed_r18_effect_score(self.battle, self.actor, skill, payload, ai.difficulty_profile('standard')))
            self.assertEqual((pickle.dumps(self.battle), random.getstate(), _id_state()), before)
        self.assertEqual(float(results[0]).hex(), float(results[1]).hex())
        return events[-1]['statistics']

    def test_nonmoving_effect_avoids_unused_position_queries_exactly(self):
        stats = self.compare_effect(SmallHit())
        self.assertEqual(stats.get('unused_position_baselines_avoided', 0), 1)

    def test_actual_displacement_restores_then_uses_original_position_scorer(self):
        stats = self.compare_effect(SmallMove())
        self.assertEqual(stats.get('position_baseline_displacement_fallbacks', 0), 1)

    def test_replaced_position_query_keeps_original_behavior(self):
        original = ai.reviewed_effect_baseline
        def changed(battle, actor, units, *, include_pending=False):
            actor.current_mana -= 1
            return original(battle, actor, units, include_pending=include_pending)
        with mock.patch.object(ai, 'reviewed_effect_baseline', changed):
            stats = self.compare_effect(SmallHit())
        self.assertEqual(stats.get('position_baseline_unreviewed_query_fallbacks', 0), 1)
        self.assertEqual(stats.get('unused_position_baselines_avoided', 0), 0)

    def test_owned_effect_query_runs_once_when_displacement_falls_back(self):
        CountedMove.calls.clear()
        self.compare_effect(CountedMove())
        self.assertEqual(len(CountedMove.calls), 2)  # one original call, one optimized call


if __name__ == '__main__':
    unittest.main()
