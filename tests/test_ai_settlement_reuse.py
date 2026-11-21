"""Bounded exactness and invalidation checks for independent area outcomes."""
from contextlib import ExitStack
import os
from pathlib import Path
import pickle
import random
import sys
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from wujiang.tactical import prediction, settlement_reuse as reuse, stat_queries
from wujiang.tactical.engine import core
from wujiang.tactical.heroes.registry import create_hero
from wujiang.tactical.heroes.common import MachineGunSkill, positions_to_dict
from wujiang.tactical.heroes.mechanics.jade import CommonMissileSkill
from wujiang.tactical.heroes.mechanics.electronic_teleport import SatelliteCannonSkill
from wujiang.tactical.rooms import ai
from wujiang.tactical.rooms.equipment_effects import EquipmentStatus
from wujiang.tactical.rooms.synergy_effects import SynergyStatus


class UnknownObserver(core.Trait):
    def __init__(self):
        super().__init__('observer','')
    def on_owner_action_declared(self,battle,action_type,payload):
        self.owner.current_mana += 1


class UnknownField(core.BattleFieldEffect):
    def on_before_dead_cleanup(self,battle):
        pass


class HiddenProperty(core.StatusEffect):
    calls=[]
    @property
    def grants_stealth(self):
        self.calls.append(1)
        return False


class SettlementReuseTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.dict(os.environ,{'WUJIANG_AI_EXPECTATION_METHOD':'direct',
            'WUJIANG_AI_SETTLEMENT_REUSE':'1','WUJIANG_AI_NATIVE_ROLLBACK':'1',
            'WUJIANG_AI_DYNAMIC_COMPONENTS':'1','WUJIANG_AI_STAT_VIEWS':'1'}))
        self.stack.enter_context(prediction.prediction_rules(True))
        from wujiang.platform.ai_prediction_native import load_kernel
        self.kernel=load_kernel()
        if self.kernel is None or not hasattr(self.kernel,'make_excluding_field_guard'):
            self.skipTest('optional native guard has not been built')
        reuse._program_proof.cache_clear()
        self.battle=core.Battle(width=10,height=10)
        self.battle.chess_rng=random.Random(300)
        self.battle.fast_ai_simulation=True
        self.units=[]
        for i in range(12):
            unit=create_hero('bard',1 if i%3==0 else 2)
            unit.skills.clear(); unit.traits.clear()
            unit.base_stats=core.Stats(attack=2,defense=4,speed=2,attack_range=3,mana=4)
            unit.current_hp=20
            self.battle.add_unit(unit,core.Position(2+i%4,2+i//4))
            unit.add_status(SynergyStatus('role','勇者',2))
            self.units.append(unit)
        self.actor=self.units[0]
        self.skills=[MachineGunSkill(),CommonMissileSkill(),SatelliteCannonSkill()]
        self.actor.skills[:]=[s.bind(self.actor) for s in self.skills]
        self.actor.current_mana=4
        self.battle.configure_turn_order([u.unit_id for u in self.units])
        self.battle.start_battle()
        self.profile=ai.difficulty_profile('standard')

    def view(self):
        context=self.stack.enter_context(ai.ai_enemy_view(self.battle,1))
        for unit in self.units:
            unit.stat('attack')
        return context

    def payload(self,skill,pattern=None):
        if pattern is None:
            pattern=skill.ai_direct_area_patterns(self.battle,self.actor)[0]
        return {'type':'skill','unit_id':self.actor.unit_id,'skill_code':skill.code,'cells':positions_to_dict(pattern)}

    def optimized(self,skill,payload):
        if skill is self.skills[2]:
            return ai.r23_paid_skill_value(self.battle,self.actor,payload)
        return ai.reviewed_r18_effect_score(self.battle,self.actor,skill,payload,self.profile)

    def original(self,skill,payload):
        with mock.patch.dict(os.environ,{'WUJIANG_AI_SETTLEMENT_REUSE':'0'}):
            return self.optimized(skill,payload)

    def stamp(self):
        from wujiang.tactical.heroes import base
        return pickle.dumps(self.battle,protocol=5),random.getstate(),core._id_counter.peek(),base._unit_counter.peek()

    def test_all_patterns_keep_scores_float_bits_and_parent_state(self):
        before=self.stamp()
        self.view()
        for skill in self.skills:
            for pattern in skill.ai_direct_area_patterns(self.battle,self.actor):
                payload=self.payload(skill,pattern)
                expected=self.original(skill,payload)
                actual=self.optimized(skill,payload)
                self.assertEqual(float(actual).hex(),float(expected).hex(),(skill.code,payload))
        self.assertGreater(prediction._session.get()['statistics'].get('area_reuse_complete_skill_probes_avoided',0),50)
        self.stack.close()
        self.assertEqual(self.stamp(),before)

    def test_shields_dodges_and_multicell_targets_keep_scores(self):
        target=self.units[5]
        target.set_footprint_offsets([(0,0),(0,1)])
        target.shields=2; target.temporary_shields=1; target.dodge_charges=1
        target.add_status(EquipmentStatus('木盾','',''))
        self.view()
        for skill in self.skills:
            payload=self.payload(skill)
            self.assertEqual(float(self.optimized(skill,payload)).hex(),float(self.original(skill,payload)).hex())
        self.assertGreater(prediction._session.get()['statistics'].get('area_reuse_recipient_batches',0),0)

    def test_low_hp_and_lethal_results_use_original_probe(self):
        for unit in self.units:
            unit.current_hp=.001
        self.view()
        for skill in self.skills:
            payload=self.payload(skill)
            expected=self.original(skill,payload)
            self.assertEqual(self.optimized(skill,payload),expected)
        self.assertGreater(prediction._session.get()['statistics'].get('area_reuse_coupled_recipient_fallbacks',0),0)

    def assert_unsupported(self):
        self.view()
        payload=self.payload(self.skills[0])
        before=self.stamp()
        self.assertIsNone(reuse.try_area_score(ai._AI_MOBILITY_EFFECT_SCORING,self.battle,self.actor,payload,'r18',self.skills[0]))
        self.assertEqual(self.stamp(),before)

    def test_unknown_declaration_observer_falls_back(self):
        self.actor.traits.append(UnknownObserver().bind(self.actor))
        self.assert_unsupported()

    def test_unknown_optional_field_hook_falls_back(self):
        self.battle.field_effects.append(UnknownField('unknown'))
        self.assert_unsupported()

    def test_source_lifesteal_and_belt_fall_back(self):
        self.actor.add_status(EquipmentStatus('吸血面具','',''))
        self.assert_unsupported()

    def test_new_stat_modifier_status_falls_back(self):
        self.units[1].add_status(SynergyStatus('race','石人',2))
        self.assert_unsupported()

    def test_half_piercing_header_falls_back(self):
        self.actor.skills[0].all_damage_half_pierce=True
        self.assert_unsupported()

    def test_changed_original_score_function_is_rejected(self):
        from wujiang.tactical.rooms.ai_mobility_effect_scoring import AIMobilityEffectScoring
        with mock.patch.object(AIMobilityEffectScoring,'_r18_damage_unit_value',lambda *args:(999,True)):
            self.assert_unsupported()

    def test_replacing_service_or_inplace_method_code_invalidates_world(self):
        self.view()
        world=reuse.AreaWorld(self.battle,self.actor)
        with mock.patch.object(ai._AI_MOBILITY_EFFECT_SCORING,'_r18_damage_unit_value',lambda *args:(999,True)):
            self.assertFalse(world.same(self.battle,self.actor))
        function=core.Battle.resolve_damage
        old=function.__code__
        try:
            function.__code__=(lambda self,ctx:None).__code__
            self.assertFalse(world.same(self.battle,self.actor))
        finally:
            function.__code__=old

    def test_mutated_battle_or_component_invalidates_world(self):
        self.view()
        world=reuse.AreaWorld(self.battle,self.actor)
        self.assertTrue(world.same(self.battle,self.actor))
        self.units[2].position=core.Position(7,7)
        self.assertFalse(world.same(self.battle,self.actor))
        self.units[2].position=world.before18[self.units[2].unit_id][5]
        self.units[2].statuses[0].tier=1
        self.assertFalse(world.same(self.battle,self.actor))

    def test_invalid_payloads_retain_original_error_scores(self):
        self.view()
        skill=self.skills[0]
        good=self.payload(skill)
        for payload in (good|{'unit_id':'missing'},good|{'type':'attack'},good|{'cells':[{'x':99,'y':99}]},
                        good|{'resources_prepaid':True},good|{'cells':[]}):
            expected=self.original(skill,payload)
            self.assertEqual(self.optimized(skill,payload),expected)

    def test_native_projection_guard_ignores_only_declared_fields(self):
        class Plain: pass
        obj=Plain(); obj.hp=4; obj.marker=object()
        guard=self.kernel.make_excluding_field_guard([(obj,vars(obj),(('marker',obj.marker),),('hp','optional'))])
        self.assertTrue(self.kernel.field_guard_matches(guard,object()))
        obj.hp=1; obj.optional=5
        self.assertTrue(self.kernel.field_guard_matches(guard,object()))
        obj.unexpected=5
        self.assertFalse(self.kernel.field_guard_matches(guard,object()))
        del obj.unexpected
        obj.marker=object()
        self.assertFalse(self.kernel.field_guard_matches(guard,object()))
        with self.assertRaises(ValueError):
            self.kernel.make_excluding_field_guard([(obj,vars(obj),(),('hp','hp'))])

    def test_plain_stat_view_survives_position_change_but_new_modifier_invalidates(self):
        self.view()
        unit=self.units[4]
        unit.stat('attack')
        view=prediction._session.get()['stat_views']['views'][id(unit)][0]
        unit.position=core.Position(8,8)
        self.assertTrue(view.matches())
        unit.add_status(EquipmentStatus('重爪','',''))
        self.assertFalse(view.matches())

    def test_unavailable_skill_preserves_invalid_score(self):
        self.skills[0].cooldown_remaining=2
        self.view()
        payload=self.payload(self.skills[0])
        self.assertEqual(self.original(self.skills[0],payload),-1000)
        self.assertEqual(self.optimized(self.skills[0],payload),-1000)

    def test_target_counter_equipment_falls_back(self):
        self.units[2].add_status(EquipmentStatus('德古拉之爪','',''))
        self.assert_unsupported()

    def test_unknown_descriptor_is_not_invoked_by_reuse(self):
        HiddenProperty.calls.clear()
        self.units[2].add_status(HiddenProperty('custom',''))
        self.assert_unsupported()
        self.assertEqual(HiddenProperty.calls,[])

    def test_missing_native_and_legacy_keep_original_path(self):
        self.view()
        payload=self.payload(self.skills[0])
        with mock.patch('wujiang.platform.ai_prediction_native.load_kernel',return_value=None):
            self.assertIsNone(reuse.try_area_score(ai._AI_MOBILITY_EFFECT_SCORING,self.battle,self.actor,payload,'r18',self.skills[0]))
        with prediction.prediction_rules(False):
            self.assertIsNone(reuse.try_area_score(ai._AI_MOBILITY_EFFECT_SCORING,self.battle,self.actor,payload,'r18',self.skills[0]))

    def test_world_budget_declines_without_rebuilding_every_candidate(self):
        self.view()
        world=reuse._world_for(self.battle,self.actor)
        prediction._session.get()['area_settlements']['worlds']=[world]*4
        self.actor.current_hp-=.1
        with mock.patch.object(reuse,'AreaWorld',side_effect=AssertionError('extra capture')):
            self.assertIsNone(reuse._world_for(self.battle,self.actor))
            self.assertIsNone(reuse._world_for(self.battle,self.actor))

    def test_source_belt_and_messenger_policy_use_original_path(self):
        self.actor.add_status(EquipmentStatus('腰带','',''))
        self.assert_unsupported()

    def test_registry_mutation_invalidates_program_proof(self):
        self.view()
        world=reuse.AreaWorld(self.battle,self.actor)
        reuse._COMPONENTS.add(UnknownObserver)
        try:
            self.assertFalse(world.same(self.battle,self.actor))
        finally:
            reuse._COMPONENTS.remove(UnknownObserver)

    def test_worker_private_sequence_replacement_does_not_disable_reuse(self):
        from wujiang.platform.id_sequence import IdSequence
        from wujiang.tactical.heroes import base
        self.view()
        world=reuse.AreaWorld(self.battle,self.actor)
        with mock.patch.object(core,'_id_counter',IdSequence(9000)), mock.patch.object(base,'_unit_counter',IdSequence(8000)):
            self.assertTrue(world.same(self.battle,self.actor))
            payload=self.payload(self.skills[0])
            expected=self.original(self.skills[0],payload)
            self.assertEqual(self.optimized(self.skills[0],payload),expected)
            self.assertGreater(prediction._session.get()['statistics'].get('area_reuse_complete_skill_probes_avoided',0),0)

    def test_referenced_module_function_replacement_invalidates(self):
        import math
        self.view()
        world=reuse.AreaWorld(self.battle,self.actor)
        with mock.patch.object(math,'fsum',lambda values:999):
            self.assertFalse(world.same(self.battle,self.actor))

    def test_unrelated_engine_method_does_not_disable_probe_at_startup(self):
        with mock.patch.object(core.Battle,'unrelated_test_operation',lambda battle:None,create=True):
            self.view()
            world=reuse.AreaWorld(self.battle,self.actor)
            self.assertTrue(world.same(self.battle,self.actor))
            names={f.__qualname__ for f in reuse.contract_functions()}
            self.assertFalse(any('unrelated_test_operation' in name for name in names))
            payload=self.payload(self.skills[0])
            self.assertEqual(self.original(self.skills[0],payload),self.optimized(self.skills[0],payload))
            self.assertGreater(prediction._session.get()['statistics'].get('area_reuse_complete_skill_probes_avoided',0),0)

    def test_unknown_hook_shape_declines_before_expensive_program_proof(self):
        self.actor.traits.append(UnknownObserver().bind(self.actor))
        self.view()
        with mock.patch.object(reuse,'_program_proof',side_effect=AssertionError('unneeded code proof')):
            self.assertIsNone(reuse.try_area_score(ai._AI_MOBILITY_EFFECT_SCORING,self.battle,self.actor,
                self.payload(self.skills[0]),'r18',self.skills[0]))

    def test_nonlethal_mean_preserves_local_kill_probability_reward(self):
        from wujiang.tactical.direct_expectation import expected_death
        target=self.units[1]
        target.statuses.clear()
        target.base_stats.defense=2
        target.current_hp=.4
        target.add_status(EquipmentStatus('木盾','',''))
        self.view()
        with ai.ai_probe_rollback(self.battle):
            self.battle.resolve_damage(core.DamageContext(source=self.actor,target=target,attack_power=2,is_skill=True,action_name='probability check'))
            self.assertTrue(target.alive)
            self.assertEqual(expected_death(target),.5)
        for skill in self.skills:
            pattern=next(p for p in skill.ai_direct_area_patterns(self.battle,self.actor) if target.position in p)
            payload=self.payload(skill,pattern)
            self.assertEqual(float(self.original(skill,payload)).hex(),float(self.optimized(skill,payload)).hex())
        self.assertGreater(prediction._session.get()['statistics'].get('area_reuse_complete_skill_probes_avoided',0),0)


if __name__=='__main__':
    unittest.main()
