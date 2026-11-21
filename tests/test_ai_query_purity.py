"""Bounded correctness checks for readonly, selective position baselines."""
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'src'), str(Path(__file__).resolve().parent)]
import test_ai_stat_queries as fixtures
from wujiang.tactical import prediction
from wujiang.tactical.component_providers import readonly_component_fields
from wujiang.tactical.engine import core
from wujiang.tactical.heroes.common import StatModifierStatus
from wujiang.tactical.query_purity import position_queries_readonly
from wujiang.tactical.rooms import ai


class ChildGrant(core.StatusEffect):
    def __init__(self):
        super().__init__('readonly child', '')
        self.child = StatModifierStatus('child bonus', speed_delta=1)

    @readonly_component_fields('child')
    def provided_components(self):
        return (self.child,)


class UnknownProvider(core.StatusEffect):
    calls = 0

    def provided_components(self):
        type(self).calls += 1
        return ()


class UnknownRange(core.StatusEffect):
    calls = 0

    def modify_targeting_range(self, value):
        type(self).calls += 1
        return value


class FatalHit(fixtures.SmallHit):
    def execute(self, battle, actor, payload):
        battle.resolve_damage(core.DamageContext(source=actor, target=battle.get_unit(payload['target_unit_id']),
            attack_power=999, is_skill=True, action_name=self.name))


class ReadonlyPositionQueryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.DirectStatQueryTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.battle, self.actor = self.fixture.battle, self.fixture.actor

    def ready(self):
        self.actor.stat('attack')

    def grant(self):
        grant = ChildGrant()
        grant.child.bind(self.actor)
        self.actor.add_status(grant)
        return grant

    def test_nonempty_provider_can_be_readonly_without_enabling_value_cache(self):
        self.grant()
        with ai.ai_enemy_view(self.battle, 1):
            self.ready()
            self.assertTrue(position_queries_readonly(self.battle.all_units()))
            self.assertEqual(prediction._session.get()['stat_views']['views'].get(id(self.actor), ()), ())

    def test_recreated_containers_and_changed_scalar_values_are_rechecked(self):
        grant = self.grant()
        with ai.ai_enemy_view(self.battle, 1):
            self.ready()
            self.assertTrue(position_queries_readonly(self.battle.all_units()))
            self.actor.statuses = list(self.actor.statuses)
            grant.child.speed_delta = 3
            self.assertTrue(position_queries_readonly(self.battle.all_units()))
            self.assertEqual(self.actor.stat('speed'), self.actor._stat_without_query_cache('speed'))
            grant.child.speed_delta = object()
            self.assertFalse(position_queries_readonly(self.battle.all_units()))

    def test_changed_provider_and_function_defaults_decline(self):
        self.grant()
        with ai.ai_enemy_view(self.battle, 1):
            self.ready()
            self.assertTrue(position_queries_readonly(self.battle.all_units()))
            function = ChildGrant.provided_components
            with mock.patch.object(function, '__defaults__', (None,)):
                self.assertFalse(position_queries_readonly(self.battle.all_units()))
        with ai.ai_enemy_view(self.battle, 1):
            self.ready()
            with mock.patch.object(ChildGrant, 'provided_components', lambda self: ()):
                self.assertFalse(position_queries_readonly(self.battle.all_units()))

    def test_replaced_child_with_unknown_modifier_declines(self):
        grant = self.grant()
        with ai.ai_enemy_view(self.battle, 1):
            self.ready()
            self.assertTrue(position_queries_readonly(self.battle.all_units()))
            grant.child = fixtures.CountingStat().bind(self.actor)
            self.assertFalse(position_queries_readonly(self.battle.all_units()))
            self.assertEqual(grant.child.calls, 0)
            grant.child = None
            self.assertFalse(position_queries_readonly(self.battle.all_units()))

    def test_unknown_provider_is_not_executed_by_inspection(self):
        with ai.ai_enemy_view(self.battle, 1):
            self.ready()
            provider = UnknownProvider('unknown', '').bind(self.actor)
            self.actor.statuses.append(provider)
            UnknownProvider.calls = 0
            self.assertFalse(position_queries_readonly(self.battle.all_units()))
            self.assertEqual(UnknownProvider.calls, 0)

    def test_unknown_nonstat_query_hook_declines_without_calling_it(self):
        with ai.ai_enemy_view(self.battle, 1):
            self.ready()
            self.actor.statuses.append(UnknownRange('unknown range', '').bind(self.actor))
            UnknownRange.calls = 0
            self.assertFalse(position_queries_readonly(self.battle.all_units()))
            self.assertEqual(UnknownRange.calls, 0)

    def test_unknown_property_is_not_evaluated(self):
        calls = []
        class Getter(core.StatusEffect):
            @property
            def suppresses_innate_traits(self):
                calls.append(1)
                return False
        with ai.ai_enemy_view(self.battle, 1):
            self.ready()
            self.actor.statuses.append(Getter('getter', '').bind(self.actor))
            self.assertFalse(position_queries_readonly(self.battle.all_units()))
            self.assertFalse(calls)

    def test_unknown_unit_resource_descriptor_is_not_evaluated(self):
        calls = []
        class Getter:
            def __get__(self, instance, owner):
                calls.append(1)
                return 1
        with ai.ai_enemy_view(self.battle, 1):
            self.ready()
            with mock.patch.object(type(self.actor), 'current_hp', Getter(), create=True):
                self.assertFalse(position_queries_readonly(self.battle.all_units()))
            self.assertFalse(calls)

    def test_failed_qualification_does_not_repeat_for_later_payloads(self):
        with ai.ai_enemy_view(self.battle, 1):
            self.ready()
            unknown = UnknownRange('unknown', '').bind(self.actor)
            self.actor.statuses.append(unknown)
            self.assertFalse(position_queries_readonly(self.battle.all_units()))
            self.actor.statuses.remove(unknown)
            self.assertFalse(position_queries_readonly(self.battle.all_units()))

    def test_nonmoving_effect_with_nonempty_provider_matches_original(self):
        self.grant()
        stats = self.fixture.compare_effect(fixtures.SmallHit())
        self.assertEqual(stats.get('unused_position_baselines_avoided', 0), 1)

    def test_actual_displacement_prices_only_moved_unit_with_identical_score(self):
        self.grant()
        stats = self.fixture.compare_effect(fixtures.SmallMove())
        self.assertEqual(stats.get('partial_position_baseline_units', 0), 1)

    def test_death_position_removal_keeps_original_position_penalty(self):
        self.grant()
        stats = self.fixture.compare_effect(FatalHit())
        self.assertEqual(stats.get('partial_position_baseline_units', 0), 1)


if __name__ == '__main__':
    unittest.main()
