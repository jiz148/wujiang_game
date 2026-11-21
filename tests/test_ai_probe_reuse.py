"""Exact complete-state reuse, mutation invalidation and clean bypasses."""
import os
from unittest import mock
import unittest
from test_ai_settlement_reuse import SettlementReuseTests,UnknownObserver
from wujiang.tactical import prediction,probe_reuse
from wujiang.tactical.engine import core
from wujiang.tactical.rooms import ai
from wujiang.tactical.rooms.ai_damage_probes import DamageProbeService
from wujiang.tactical.rooms.equipment_effects import EquipmentStatus


class AttackProbeReuseTests(unittest.TestCase):
    def setUp(self):
        self.f=SettlementReuseTests()
        self.f.setUp()
        self.addCleanup(self.f.stack.close)
        self.battle,self.actor,self.target=self.f.battle,self.f.actor,self.f.units[1]
        self.f.stamp()  # pickle may initialize CPython's class __slotnames__ cache
        self.f.view()
        token=probe_reuse._POSITION.set(True)
        self.addCleanup(probe_reuse._POSITION.reset,token)

    def probe(self,actor=None,target=None,payload=None,power=2):
        return DamageProbeService.probe_attack_damage_impact(self.battle,actor or self.actor,target or self.target,
            {} if payload is None else payload,attack_power=power)

    def stamp(self,value):
        return type(value.damage),float(value.damage).hex(),getattr(value.damage,'kill_probability',None),value.changed_target

    def check_original(self):
        actual=self.probe()
        with mock.patch.dict(os.environ,{'WUJIANG_AI_ATTACK_PROBE_REUSE':'0'}):
            expected=self.probe()
        self.assertEqual(self.stamp(actual),self.stamp(expected))

    def hits(self):
        return prediction._session.get()['statistics'].get('attack_probe_value_hits',0)

    def test_repeated_original_results_are_exact_and_fresh(self):
        before=self.f.stamp()
        first=self.probe()
        value=self.stamp(first)
        first.damage=999
        second=self.probe()
        self.assertEqual(self.stamp(second),value)
        self.assertIsNot(first,second)
        self.assertEqual(self.hits(),1)
        self.assertEqual(self.f.stamp(),before)

    def test_resources_stats_and_positions_invalidate(self):
        self.probe();self.probe()
        for mutation in (lambda:setattr(self.target,'shields',2),lambda:setattr(self.target,'current_hp',.2),
                         lambda:setattr(self.target.base_stats,'defense',6),
                         lambda:setattr(self.target,'position',core.Position(9,9))):
            old=self.hits();mutation();self.check_original();self.assertEqual(self.hits(),old)

    def test_new_unknown_hook_cannot_reuse_old_results(self):
        self.probe();self.probe()
        self.target.traits.append(UnknownObserver().bind(self.target))
        old=self.hits();self.check_original();self.probe()
        self.assertEqual(self.hits(),old)

    def test_explicit_payload_uses_original(self):
        for _ in range(3):
            self.probe(payload={'ignore_shield':True})
        self.assertEqual(self.hits(),0)

    def test_outside_multi_unit_position_scope_is_bypassed(self):
        token=probe_reuse._POSITION.set(False)
        try:
            self.probe();self.probe()
        finally:
            probe_reuse._POSITION.reset(token)
        self.assertEqual(self.hits(),0)

    def test_changed_original_method_does_not_return_stale_value(self):
        self.probe();self.probe()
        original=DamageProbeService._probe_attack_damage_impact_original
        calls=[]
        def counted(*args,**kwargs):
            calls.append(1)
            return original(*args,**kwargs)
        old=self.hits()
        with mock.patch.object(DamageProbeService,'_probe_attack_damage_impact_original',counted):
            self.probe();self.probe()
        self.assertEqual(len(calls),2)
        self.assertEqual(self.hits(),old)

    def test_missing_native_checkpoint_clears_last_reference(self):
        with mock.patch('wujiang.platform.ai_prediction_native.load_kernel',return_value=None):
            self.check_original();self.probe()
        self.assertEqual(self.hits(),0)
        self.assertIsNone(prediction._session.get().get('last_direct_checkpoint'))

    def test_local_kill_probability_is_retained(self):
        self.target.base_stats.defense=2
        self.target.current_hp=.4
        self.target.statuses.clear()
        self.target.add_status(EquipmentStatus('木盾','',''))
        first=self.probe();second=self.probe()
        self.assertEqual(self.stamp(first),self.stamp(second))
        self.assertEqual(second.damage.kill_probability,.5)
        self.assertEqual(self.hits(),1)

    def test_same_unit_id_on_a_different_object_is_not_a_hit(self):
        import copy
        self.probe();self.probe()
        other=copy.copy(self.target)
        old=self.hits()
        self.probe(target=other)
        self.assertEqual(self.hits(),old)

    def test_cache_budget_stops_new_captures(self):
        self.probe()
        state=prediction._session.get()['original_attack_probes']
        state['count']=2048
        self.probe(target=self.f.units[3])
        self.assertEqual(state['count'],2048)

    def test_signed_zero_and_integer_keys_remain_distinct(self):
        self.probe(power=0.0);self.probe(power=-0.0);self.probe(power=0)
        self.assertEqual(self.hits(),0)


if __name__=='__main__':
    unittest.main()
