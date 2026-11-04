"""Direct scoring policy: weighted local contributions, no probability replay."""
from contextlib import ExitStack
import os
import pickle
import random
import sys
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wujiang.tactical import prediction
from wujiang.tactical.engine.core import Battle, DamageContext, Position, StatusEffect
from wujiang.tactical.heroes.registry import create_hero
from wujiang.tactical.rooms import ai
from wujiang.tactical.rooms.equipment_effects import EquipmentStatus
from wujiang.tactical.rooms.synergy import SynergyStatus


class GuaranteedRevival(StatusEffect):
    def on_after_damage(self, battle, ctx):
        if ctx.target is self.owner and not self.owner.alive:
            self.owner.alive, self.owner.current_hp = True, 1.0


class DirectExpectationBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.dict(os.environ, {"WUJIANG_AI_EXPECTATION_METHOD": "direct"}))
        self.stack.enter_context(prediction.prediction_rules(True))

    def fixture(self):
        battle = Battle(width=12, height=10)
        actor, target = create_hero("bard", 1), create_hero("bard", 2)
        for unit in (actor, target):
            unit.skills.clear()
            unit.traits.clear()
        battle.add_unit(actor, Position(2, 2))
        battle.add_unit(target, Position(3, 2))
        battle.chess_rng = random.Random(35)
        return battle, actor, target

    def hit(self, battle, actor, target, *, raw=None, power=4, skill=False):
        return battle.resolve_damage(DamageContext(source=actor, target=target, attack_power=power,
                                    raw_damage=raw, is_skill=skill, action_name="直接期望维护", tags={"skill"} if skill else {"attack"}))

    def test_deterministic_numeric_query_does_not_enter_a_probability_or_graph_boundary(self):
        battle, _, _ = self.fixture()
        calls = []
        @prediction.expected_numeric(independent=True)
        def score(world):
            calls.append(True)
            return 37.5
        with mock.patch("wujiang.platform.ai_prediction_native.load_kernel", side_effect=AssertionError("research kernel loaded")), \
                ai.ai_enemy_view(battle, 1):
            with mock.patch.object(prediction, "outcomes", side_effect=AssertionError("probability solver entered")), \
                    mock.patch.object(prediction, "_saved_graph", side_effect=AssertionError("numeric query prepared a graph")):
                self.assertEqual(score(battle), 37.5)
        self.assertEqual(calls, [True])

    def test_dark_dodge_and_heavy_claw_multiply_probabilities_without_rng_or_state_leaks(self):
        battle, actor, target = self.fixture()
        target.add_status(SynergyStatus("attribute", "暗", 2))
        actor.add_status(EquipmentStatus("重爪", "", ""))
        before = pickle.dumps(battle), random.getstate(), battle.chess_rng.getstate()
        observations = []
        with prediction.prediction_observer(observations.append), ai.ai_enemy_view(battle, 1), ai.ai_probe_rollback(battle):
            ctx = self.hit(battle, actor, target, raw=.5)
            self.assertAlmostEqual(ctx.actual_damage, .5 * .8 * .5)
        self.assertEqual((pickle.dumps(battle), random.getstate(), battle.chess_rng.getstate()), before)
        self.assertEqual(observations[0]["statistics"].get("probability_forks", 0), 0)
        self.assertEqual(observations[0]["statistics"].get("graph_captures", 0), 1)
        self.assertEqual(observations[0]["statistics"].get("direct_view_checkpoints", 0), 1)
        self.assertEqual(observations[0]["statistics"].get("unhandled_direct_random_calls", 0), 0)

    def test_wood_shield_averages_after_the_original_nonlinear_damage_table(self):
        battle, actor, target = self.fixture()
        target.add_status(EquipmentStatus("木盾", "", ""))
        target.base_stats.defense = 2
        expected = sum(min(target.current_hp, battle.damage_rule.calculate_damage(power, 2)) for power in (2, 3)) / 2
        with ai.ai_enemy_view(battle, 1), ai.ai_probe_rollback(battle):
            self.assertAlmostEqual(self.hit(battle, actor, target, power=3).actual_damage, expected)

    def test_local_lethal_bonus_uses_probability_instead_of_treating_mean_damage_as_a_kill(self):
        battle, actor, target = self.fixture()
        target.current_hp = .4
        actor.add_status(EquipmentStatus("重爪", "", ""))
        with ai.ai_enemy_view(battle, 1), ai.ai_probe_rollback(battle):
            before = ai.r23_action_state(battle)
            self.hit(battle, actor, target, raw=1)
            self.assertTrue(target.alive)
            self.assertAlmostEqual(ai.r23_action_delta(actor, before), .2 * 100 + .5 * 90)

    def test_nested_probe_does_not_reuse_another_alternatives_death_probability(self):
        battle, actor, target = self.fixture()
        actor.add_status(EquipmentStatus("重爪", "", ""))
        target.current_hp = .4
        with ai.ai_enemy_view(battle, 1):
            for index in range(2):
                with ai.ai_probe_rollback(battle):
                    before = ai.r23_action_state(battle)
                    self.assertEqual(ai.r23_action_delta(actor, before), 0)
                    if index == 0:
                        self.hit(battle, actor, target, raw=1)

    def test_generic_attack_score_weights_the_kill_reward_with_the_coin_probability(self):
        battle, actor, target = self.fixture()
        target.current_hp = .4
        actor.base_stats.attack, target.base_stats.defense = 5, 1
        claw = EquipmentStatus("重爪", "", "")
        payload = {"type": "attack", "unit_id": actor.unit_id, "target_unit_id": target.unit_id}
        profile = ai.difficulty_profile("standard")
        with ai.ai_enemy_view(battle, 1):
            hit = ai.score_attack_payload(battle, actor, payload, profile)
            actor.add_status(claw)
            average = ai.score_attack_payload(battle, actor, payload, profile)
            with mock.patch("wujiang.tactical.direct_expectation.damage_chance",
                    side_effect=lambda ctx, probability: setattr(ctx, "forecast_hit_probability", 0.0)):
                miss = ai.score_attack_payload(battle, actor, payload, profile)
            self.assertAlmostEqual(average, (hit + miss) / 2)

    def test_cap_once_per_turn_uses_remaining_availability_probability(self):
        battle, actor, target = self.fixture()
        target.race = "精灵"
        target.add_status(EquipmentStatus("精灵便帽", "", ""))
        with ai.ai_enemy_view(battle, 1), ai.ai_probe_rollback(battle):
            self.assertAlmostEqual(self.hit(battle, actor, target, raw=.2).actual_damage, .1)
            self.assertAlmostEqual(self.hit(battle, actor, target, raw=.2).actual_damage, .15)

    def test_guaranteed_revival_does_not_receive_a_kill_bonus(self):
        battle, actor, target = self.fixture()
        target.current_hp = 1
        target.add_status(GuaranteedRevival("确定复活"))
        with ai.ai_enemy_view(battle, 1), ai.ai_probe_rollback(battle):
            before = ai.r23_action_state(battle)
            self.hit(battle, actor, target, raw=1)
            self.assertTrue(target.alive)
            self.assertEqual(ai.r23_action_delta(actor, before), 0)

    def test_cap_availability_does_not_consume_on_a_heavy_claw_miss(self):
        battle, actor, target = self.fixture()
        target.race = "精灵"
        actor.add_status(EquipmentStatus("重爪", "", ""))
        target.add_status(EquipmentStatus("精灵便帽", "", ""))
        with ai.ai_enemy_view(battle, 1), ai.ai_probe_rollback(battle):
            self.assertAlmostEqual(self.hit(battle, actor, target, raw=.2).actual_damage, .05)
            self.assertAlmostEqual(self.hit(battle, actor, target, raw=.2).actual_damage, .0625)

    def test_fate_link_retains_its_one_use_limit_in_the_local_model(self):
        battle, actor, target = self.fixture()
        link = EquipmentStatus("命运纽带", "", "")
        target.add_status(link)
        with ai.ai_enemy_view(battle, 1), ai.ai_probe_rollback(battle):
            self.hit(battle, actor, target, raw=2)
            self.assertTrue(target.alive)
            self.assertTrue(link.survival_used)
            self.hit(battle, actor, target, raw=2)
            self.assertFalse(target.alive)

    def test_red_random_pierce_is_half_damage_through_a_shield_without_a_coin(self):
        from wujiang.tactical.heroes.excel_roster import RedRandomPierceTrait
        battle, actor, target = self.fixture()
        actor.traits.append(RedRandomPierceTrait().bind(actor))
        target.shields = 1
        with ai.ai_enemy_view(battle, 1), ai.ai_probe_rollback(battle):
            payload = {"type": "attack", "unit_id": actor.unit_id}
            actor.notify_action_declared(battle, "attack", payload)
            self.assertEqual(payload["red_random_pierce_probability"], .5)
            self.assertAlmostEqual(self.hit(battle, actor, target, raw=.4).actual_damage, .2)

    def test_random_stat_bonus_is_five_weighted_contributions_without_a_choice(self):
        from wujiang.tactical.heroes.excel_roster import AncientRepairSkill
        battle, actor, target = self.fixture()
        target.player_id = actor.player_id
        target.race = "机甲"
        actor.skills.append(AncientRepairSkill().bind(actor))
        skill = actor.skills[-1]
        with ai.ai_enemy_view(battle, 1), ai.ai_probe_rollback(battle):
            names = ("attack", "defense", "speed", "attack_range", "mana")
            before = {name: target.stat(name) for name in names}
            skill.execute(battle, actor, {"target_unit_id": target.unit_id})
            for name in names:
                self.assertAlmostEqual(target.stat(name) - before[name], .2)

    def test_tianyuan_dice_adds_three_and_a_half_points(self):
        from wujiang.tactical.heroes.excel_roster import TianyuanSkill
        battle, actor, _ = self.fixture()
        skill = TianyuanSkill().bind(actor)
        with ai.ai_enemy_view(battle, 1), ai.ai_probe_rollback(battle):
            skill.execute(battle, actor, {})
            self.assertEqual(actor.get_status("叹号计数点").points, 3.5)

    def test_unhandled_random_draw_is_visible_and_never_selects_a_fixed_outcome(self):
        battle, _, _ = self.fixture()
        observations = []
        with self.assertRaisesRegex(RuntimeError, "uncovered random mechanism"):
            with prediction.prediction_observer(observations.append), ai.ai_enemy_view(battle, 1):
                with self.assertRaisesRegex(RuntimeError, "explicit expected contribution"):
                    prediction.random.random()
        self.assertEqual(observations[0]["statistics"]["unhandled_direct_random_calls"], 1)

    def test_dice_multihit_uses_six_numeric_terms_without_replaying_the_battle(self):
        from wujiang.tactical.heroes.fisher_garden_demon import BlackNightShadowSkill
        battle, actor, target = self.fixture()
        target.current_hp, target.base_stats.defense = .6, 2
        skill = BlackNightShadowSkill().bind(actor)
        per_hit = battle.damage_rule.calculate_damage(2, 2)
        expected = sum(min(.6, count * per_hit) for count in range(1, 7)) / 6
        with ai.ai_enemy_view(battle, 1), ai.ai_probe_rollback(battle):
            skill.execute(battle, actor, {})
            self.assertAlmostEqual(.6 - target.current_hp, expected, places=4)

    def test_gravity_uses_four_numeric_area_weights_without_probability_boundaries(self):
        from wujiang.tactical.heroes.excel_roster import GravityFieldSkill
        battle, actor, target = self.fixture()
        skill = GravityFieldSkill().bind(actor)
        center = skill.candidate_centers(battle, actor)[0]
        observations = []
        with prediction.prediction_observer(observations.append), ai.ai_enemy_view(battle, 1), ai.ai_probe_rollback(battle):
            skill.execute(battle, actor, {"x": center.x, "y": center.y})
        self.assertEqual(observations[0]["statistics"].get("probability_forks", 0), 0)
        self.assertEqual(observations[0]["statistics"].get("unhandled_direct_random_calls", 0), 0)

    def test_hidden_enemy_and_clone_view_remain_and_restore(self):
        battle, actor, target = self.fixture()
        target.add_status(StatusEffect("隐身"))
        clone = create_hero("bard", 2)
        clone.is_clone, clone.summoner_id = True, target.unit_id
        battle.add_unit(clone, Position(6, 3))
        before = pickle.dumps(battle)
        with ai.ai_enemy_view(battle, 1):
            self.assertNotIn(target.unit_id, battle.units)
            self.assertIn(clone.unit_id, battle.units)
            self.assertFalse(battle.units[clone.unit_id].is_clone)
        self.assertEqual(pickle.dumps(battle), before)

    def test_enemy_view_restores_nested_reaction_previews_and_original_aliases(self):
        from wujiang.tactical.engine.core import ReactionOption
        battle, actor, _ = self.fixture()
        preview = {"cells": [{"x": 1, "y": 1}]}
        option = ReactionOption(actor.unit_id, "ion_shield", "离子盾", "skill", "passive", 1, "", preview)
        battle.saved_reaction_options = [option]
        battle.shared_reaction_preview = preview
        before = pickle.dumps(battle)
        with ai.ai_enemy_view(battle, 1):
            option.preview["cells"].clear()
            option.preview["target_unit_ids"] = ["temporary"]
        self.assertEqual(pickle.dumps(battle), before)
        self.assertIs(option.preview, preview)
        self.assertIs(battle.shared_reaction_preview, preview)

    def test_reusable_checkpoint_restores_nested_aliases_exceptions_rng_and_reuses(self):
        battle, actor, _ = self.fixture()
        actor.actions_taken_this_turn.append("original")
        alias = actor.actions_taken_this_turn
        before = pickle.dumps(battle), random.getstate()
        observations = []
        with prediction.prediction_observer(observations.append), ai.ai_enemy_view(battle, 1):
            for _ in range(3):
                with self.assertRaisesRegex(ValueError, "stop"):
                    with ai.ai_probe_rollback(battle):
                        actor.current_hp -= .1
                        actor.actions_taken_this_turn.append("outer")
                        with ai.ai_probe_rollback(battle):
                            actor.actions_taken_this_turn.clear()
                            random.random()
                        self.assertEqual(actor.actions_taken_this_turn, ["original", "outer"])
                        raise ValueError("stop")
                self.assertEqual(actor.actions_taken_this_turn, ["original"])
                self.assertIs(actor.actions_taken_this_turn, alias)
        self.assertEqual((pickle.dumps(battle), random.getstate()), before)
        stats = observations[0]["statistics"]
        self.assertGreater(stats.get("direct_checkpoint_reuses", 0), 0)
        self.assertEqual(stats.get("probability_forks", 0), 0)

    def test_component_snapshot_preserves_order_suppression_and_definition_duplicates(self):
        from wujiang.tactical.engine.core import Trait
        battle, actor, _ = self.fixture()
        skill = create_hero("bard", 1).skills[0].bind(actor)
        actor.skills[:] = [skill, skill]
        trait = Trait("native trait").bind(actor)
        actor.traits[:] = [trait]
        extra = create_hero("bard", 1).skills[1].bind(actor)
        actor._triggered_skill_definitions = {"owned": skill, "extra": extra, "same": extra}
        suppress = StatusEffect("suppress")
        suppress.suppresses_innate_traits = True
        with ai.ai_enemy_view(battle, 1):
            self.assertEqual(battle.components_for(actor), list(actor.iter_components()))
            actor.add_status(suppress)
            self.assertEqual(battle.components_for(actor), [skill, skill, suppress, extra, extra])
            actor.statuses.clear()
            self.assertEqual(battle.components_for(actor), [skill, skill, trait, extra, extra])

    def test_component_snapshot_checks_instance_providers_and_changed_class_bindings(self):
        battle, actor, _ = self.fixture()
        status, child = StatusEffect("provider"), StatusEffect("child")
        actor.add_status(status)
        with ai.ai_enemy_view(battle, 1):
            self.assertEqual(battle.components_for(actor), [status])
            status.provided_components = lambda: (child,)
            self.assertEqual(battle.components_for(actor), [status, child])
            del status.provided_components
            with mock.patch.object(type(status), "provided_components", lambda self: (child,)):
                self.assertEqual(battle.components_for(actor), [status, child])
            self.assertEqual(battle.components_for(actor), [status])

    def test_component_snapshot_keeps_projected_traits_and_special_modes(self):
        from wujiang.tactical.engine.core import Trait
        class Provider(Trait):
            def projected_trait_for(self, world, unit):
                return projected if unit.is_summon else None
        battle, actor, target = self.fixture()
        projected = Trait("projection").bind(actor)
        target.traits.append(Provider("provider").bind(target))
        actor.is_summon = True
        with ai.ai_enemy_view(battle, 1):
            self.assertEqual(battle.components_for(actor), [projected])
            battle.special_mode_codes = {"arena"}
            actor.banished = True
            self.assertEqual(battle.components_for(actor), [])
            actor.banished = False
            battle.special_mode_codes = {"real_men"}
            actor.skills[:] = create_hero("bard", 1).skills
            self.assertEqual(battle.components_for(actor), list(actor.iter_components()))

    def test_empty_notification_guard_never_skips_real_or_new_hooks(self):
        battle, actor, _ = self.fixture()
        status = StatusEffect("listener")
        actor.add_status(status)
        with ai.ai_enemy_view(battle, 1):
            battle.sync_linked_units()
            with mock.patch.object(type(status), "sync_linked_state", lambda self, world: setattr(self.owner, "current_mana", .37)):
                battle.sync_linked_units()
                self.assertEqual(actor.current_mana, .37)
            status.on_destroyed_hero_count_changed = lambda world, count: setattr(actor, "current_mana", .41)
            battle.notify_destroyed_hero_count_changed()
            self.assertEqual(actor.current_mana, .41)

    def test_dynamic_runtime_children_keep_original_checkpoint_and_execute_each_probe(self):
        calls = []
        child = StatusEffect("runtime child")
        class Parent(StatusEffect):
            def runtime_children(self):
                calls.append(True)
                return (child,)
        battle, actor, _ = self.fixture()
        actor.add_status(Parent("parent"))
        child.value = 1
        observations = []
        with prediction.prediction_observer(observations.append), ai.ai_enemy_view(battle, 1):
            initial = len(calls)
            for _ in range(2):
                with ai.ai_probe_rollback(battle):
                    child.value = 2
                self.assertEqual(child.value, 1)
            self.assertEqual(len(calls) - initial, 2)
        self.assertEqual(observations[0]["statistics"].get("direct_checkpoint_fallbacks", 0), 2)

    def test_reusable_checkpoint_invalidates_equal_value_replacement_and_restores_deleted_slots(self):
        from wujiang.tactical.engine.core import ReactionOption
        battle, actor, _ = self.fixture()
        preview = {"cells": [{"x": 1, "y": 1}]}
        option = ReactionOption(actor.unit_id, "ion_shield", "离子盾", "skill", "passive", 1, "", preview)
        battle.pending_followup_actions = [option]
        with ai.ai_enemy_view(battle, 1):
            with ai.ai_probe_rollback(battle):
                del option.preview
            self.assertIs(option.preview, preview)
            replacement = ["equal"]
            actor.actions_taken_this_turn = replacement
            with ai.ai_probe_rollback(battle):
                actor.actions_taken_this_turn.append("changed")
            self.assertIs(actor.actions_taken_this_turn, replacement)
            self.assertEqual(replacement, ["equal"])

    def test_missing_optional_dispatch_native_uses_original_probes(self):
        battle, actor, _ = self.fixture()
        observations = []
        with mock.patch("wujiang.platform.ai_prediction_native.load_kernel", return_value=None), \
                prediction.prediction_observer(observations.append), ai.ai_enemy_view(battle, 1):
            with ai.ai_probe_rollback(battle):
                actor.current_hp = .2
        self.assertEqual(actor.current_hp, 1)
        self.assertEqual(observations[0]["statistics"].get("direct_checkpoint_fallbacks", 0), 1)

    def test_server_direct_profile_and_legacy_selector_import_without_starting_server(self):
        import json
        from pathlib import Path
        import subprocess
        import sys
        root = Path(__file__).resolve().parents[1]
        script = (
            "import json,sys,run; "
            "assert run.server_ai_choice(['--ai-scoring','legacy'])=='legacy'; "
            "assert run.server_ai_choice([])=='direct'; "
            "run.configure_server_ai(); "
            "assert 'wujiang.platform.ai_scoring_policy' not in sys.modules; "
            "info=run.server_ai_build_info(); "
            "assert 'wujiang.platform.http.server' not in sys.modules; "
            "print(json.dumps(info['features']))"
        )
        done = subprocess.run([sys.executable, "-c", script], cwd=root, env=os.environ,
                              capture_output=True, text=True, timeout=8)
        self.assertEqual(done.returncode, 0, done.stderr)
        features = json.loads(done.stdout)
        self.assertEqual(features["scoring_rules"], "expected-visible-direct-v2")
        self.assertTrue(features["direct_checkpoint_reuse"])
        self.assertFalse(features["prediction_whole_battle_probability_replay"])
        self.assertIn("-AIScoring legacy", (root / "start_windows_server_legacy.bat").read_text())

    def test_legacy_does_not_enter_the_new_formulas(self):
        battle, actor, target = self.fixture()
        target.add_status(SynergyStatus("attribute", "暗", 2))
        with prediction.prediction_rules(False), mock.patch("wujiang.tactical.direct_expectation.damage_chance",
                side_effect=AssertionError("legacy entered direct formulas")), ai.ai_probe_rollback(battle):
            self.hit(battle, actor, target, raw=.2)

    def test_scalar_and_seen_alias_capture_skip_redundant_dispatch(self):
        from wujiang.platform.ai_prediction_native import load_kernel
        from wujiang.tactical.engine import core
        from wujiang.tactical.direct_checkpoint import DirectSnapshot
        battle, _, _ = self.fixture()
        kernel = load_kernel()
        self.assertIsNotNone(kernel)
        saved = DirectSnapshot(battle, kernel, core)
        alias = [1, 2, {"value": 3}]
        saved.visit(alias)
        with mock.patch.object(prediction._GraphSnapshot, "visit", side_effect=AssertionError("redundant dispatch")):
            for value in (None, True, 5, 2.5, "scalar", b"bytes", alias):
                saved.visit(value)

    def test_capture_class_proof_rechecks_inherited_lookup_changes(self):
        from wujiang.platform.ai_prediction_native import load_kernel
        from wujiang.tactical.engine import core
        from wujiang.tactical.direct_checkpoint import DirectSnapshot, UnsupportedGraph
        battle, _, _ = self.fixture()
        class LocalStatus(StatusEffect):
            pass
        LocalStatus.__module__ = StatusEffect.__module__
        saved = DirectSnapshot(battle, load_kernel(), core)
        saved.visit(LocalStatus("first"))
        with mock.patch.object(StatusEffect, "__getattr__", lambda self, name: None, create=True):
            with self.assertRaises(UnsupportedGraph):
                saved.visit(LocalStatus("second"))

    def test_direct_position_queries_observe_every_changed_row_without_battle_serialization(self):
        from wujiang.tactical.rooms import ai_acceleration as acceleration
        battle, actor, target = self.fixture()
        calls, witnesses = [], []
        @acceleration.cached_position_value
        def position(current, viewer, unit):
            calls.append((unit.unit_id, unit.current_hp, target.position))
            return unit.current_hp + target.position.x
        with ai.ai_enemy_view(battle, actor.player_id), acceleration.decision_memo_scope(), \
                mock.patch.object(acceleration, "_snapshot", side_effect=AssertionError("per-row full serialization")), \
                mock.patch.object(acceleration, "_position_diagnostic_sink", wraps=acceleration._position_diagnostic_sink) as sink:
            sink.get.return_value = witnesses.append
            self.assertEqual(position(battle, actor, actor), 4)
            target.position = Position(5, 2)
            actor.current_hp = .5
            self.assertEqual(position(battle, actor, actor), 5.5)
            self.assertEqual(position(battle, actor, actor), 5.5)
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(witnesses), 3)
        self.assertEqual([row[1] for row in calls], [1, .5, .5])
        self.assertEqual((actor.current_hp, target.position), (1, Position(3, 2)))

    def test_reused_complete_probe_graph_also_reuses_empty_discovery_proof(self):
        from wujiang.platform.ai_prediction_native import load_kernel
        battle, actor, _ = self.fixture()
        observations = []
        kernel = load_kernel()
        with prediction.prediction_observer(observations.append), ai.ai_enemy_view(battle, 1), \
                mock.patch.object(kernel, "empty_component_pass", wraps=kernel.empty_component_pass) as discovery:
            for _ in range(3):
                with ai.ai_probe_rollback(battle):
                    actor.current_hp = .2
                self.assertEqual(actor.current_hp, 1)
            self.assertEqual(discovery.call_count, 1)
            # An equal-valued new list still needs a new proof/capture.
            actor.statuses = list(actor.statuses)
            with ai.ai_probe_rollback(battle):
                actor.current_hp = .3
            self.assertEqual(discovery.call_count, 2)
        stats = observations[0]["statistics"]
        self.assertEqual(stats["direct_checkpoint_discovery_reuses"], 2)
        self.assertEqual(stats["direct_checkpoint_discovery_scans"], 2)

    def test_discovery_proof_reuse_rejects_inherited_runtime_and_in_place_code_changes(self):
        from wujiang.tactical.engine.core import BattleComponent
        for change in ("inherited", "code"):
            with self.subTest(change=change):
                battle, actor, _ = self.fixture()
                actor.add_status(StatusEffect("proof owner"))
                observations = []
                with prediction.prediction_observer(observations.append), ai.ai_enemy_view(battle, 1):
                    with ai.ai_probe_rollback(battle):
                        actor.current_hp = .2
                    def empty_children(self):
                        return ()
                    with ExitStack() as changes:
                        if change == "inherited":
                            changes.enter_context(mock.patch.object(StatusEffect, "runtime_children", empty_children))
                        else:
                            method = BattleComponent.runtime_children
                            changes.callback(setattr, method, "__code__", method.__code__)
                            method.__code__ = empty_children.__code__
                        with ai.ai_probe_rollback(battle):
                            actor.current_hp = .3
                    self.assertEqual(actor.current_hp, 1)
                stats = observations[0]["statistics"]
                self.assertEqual(stats.get("direct_checkpoint_discovery_reuses", 0), 0)
                self.assertEqual(stats["direct_checkpoint_fallbacks"], 1)

    def test_direct_whole_baseline_still_reuses_only_pure_unchanged_batches(self):
        from wujiang.tactical.rooms import ai_acceleration as acceleration
        battle, actor, target = self.fixture()
        # Twelve units exercises the retained batch cache, including an enemy
        # move that must invalidate the complete initial position baseline.
        for i in range(10):
            unit = create_hero("bard", 1 + i % 2)
            unit.skills.clear()
            unit.traits.clear()
            battle.add_unit(unit, Position(1 + i, 6))
        with ai.ai_enemy_view(battle, actor.player_id), acceleration.decision_memo_scope(), \
                mock.patch.object(ai, "reviewed_r17_position_value", wraps=ai.reviewed_r17_position_value) as query:
            units = list(battle.all_units())
            first = ai.reviewed_effect_baseline(battle, actor, units)
            self.assertEqual(query.call_count, 12)
            again = ai.reviewed_effect_baseline(battle, actor, units)
            self.assertEqual(first, again)
            self.assertIsNot(first, again)
            self.assertEqual(query.call_count, 12)
            target.position = Position(8, 2)
            ai.reviewed_effect_baseline(battle, actor, units)
            self.assertEqual(query.call_count, 24)
            first.clear()
            self.assertEqual(len(again), 12)

    def test_legacy_position_cache_keeps_original_pure_and_impure_behavior(self):
        from wujiang.tactical.rooms import ai_acceleration as acceleration
        battle, actor, target = self.fixture()
        calls = []
        @acceleration.cached_position_value
        def position(current, viewer, unit):
            calls.append(unit.current_hp)
            return unit.current_hp
        with prediction.prediction_rules(False), acceleration.decision_memo_scope():
            self.assertEqual(position(battle, actor, target), 1)
            self.assertEqual(position(battle, actor, target), 1)
            self.assertEqual(calls, [1])
            target.current_hp = .5
            self.assertEqual(position(battle, actor, target), .5)
            self.assertEqual(calls, [1, .5])
        @acceleration.cached_position_value
        def impure(current, viewer, unit):
            unit.current_mana += 1
            return unit.current_mana
        with prediction.prediction_rules(False), acceleration.decision_memo_scope():
            initial = target.current_mana
            self.assertEqual(impure(battle, actor, target), initial + 1)
            self.assertEqual(impure(battle, actor, target), initial + 2)


    def test_placement_batch_matches_all_cells_and_body_shapes_without_state_or_rng_changes(self):
        from wujiang.tactical.placement_queries import legal_placement_batch
        from wujiang.tactical.rooms.ai_acceleration import _id_state
        battle, actor, target = self.fixture()
        cells = [Position(x, y) for y in range(-1, 11) for x in range(-1, 13)]
        cells = [*reversed(cells), cells[3], cells[3]]
        battle.blocked_cells.update({(1, 1), (4, 2), (7, 6)})
        with ai.ai_enemy_view(battle, 1):
            for shape in (((0, 0),), ((0, 0), (1, 0), (0, 1), (1, 1)),
                          ((-1, 0), (0, -1), (0, 0), (1, 0)), ()):
                with self.subTest(shape=shape):
                    actor.footprint_offsets = list(shape)
                    target.set_footprint_offsets(((0, 0), (1, 0)))
                    before = pickle.dumps(battle), random.getstate(), battle.chess_rng.getstate(), _id_state()
                    expected = [cell for cell in cells if battle.can_place_unit(actor, cell, ignore=actor, mover=actor)]
                    actual = legal_placement_batch(battle, actor, cells)
                    self.assertIsNotNone(actual)
                    self.assertEqual(actual, expected)
                    self.assertTrue(all(a is b for a, b in zip(actual, expected)))
                    self.assertEqual((pickle.dumps(battle), random.getstate(), battle.chess_rng.getstate(), _id_state()), before)

    def test_placement_batch_preserves_mount_terrain_attachment_and_enemy_overlap_rules(self):
        from wujiang.tactical.placement_queries import legal_placement_batch
        configurations = ({}, {"alive": False}, {"banished": True}, {"position": None},
                          {"attached_to_unit_id": "source"}, {"standable_terrain": True},
                          {"is_mount": True, "mount_owner_id": "actor"},
                          {"mounted_on_unit_id": "actor"}, {"allow_enemy_destination_overlap": True})
        for config in configurations:
            with self.subTest(config=config):
                battle, actor, target = self.fixture()
                with ai.ai_enemy_view(battle, 1):
                    for name, value in config.items():
                        setattr(target, name, actor.unit_id if value == "actor" else value)
                    for actor_overlap, actor_terrain in ((False, False), (True, False), (False, True)):
                        actor.allow_enemy_destination_overlap = actor_overlap
                        actor.standable_terrain = actor_terrain
                        cells = [Position(x, y) for y in range(5) for x in range(7)]
                        expected = [cell for cell in cells if battle.can_place_unit(actor, cell, ignore=actor, mover=actor)]
                        self.assertEqual(legal_placement_batch(battle, actor, cells), expected)

    def test_placement_batch_rebuilds_after_movement_summons_removal_and_body_changes(self):
        from wujiang.tactical.placement_queries import legal_placement_batch
        battle, actor, target = self.fixture()
        cells = [Position(x, y) for y in range(7) for x in range(8)]
        with ai.ai_enemy_view(battle, 1):
            first = legal_placement_batch(battle, actor, cells)
            target.position = Position(6, 4)
            target.set_footprint_offsets(((0, 0), (1, 0), (0, 1)))
            extra = create_hero("bard", 2)
            battle.add_unit(extra, Position(3, 2))
            for alive in (True, False):
                extra.alive = alive
                expected = [cell for cell in cells if battle.can_place_unit(actor, cell, ignore=actor, mover=actor)]
                self.assertEqual(legal_placement_batch(battle, actor, cells), expected)
            battle.units.pop(extra.unit_id)
            target.position = None
            current = legal_placement_batch(battle, actor, cells)
            self.assertIn(Position(3, 2), current)
            self.assertNotIn(Position(3, 2), first)
            first.clear()
            self.assertTrue(legal_placement_batch(battle, actor, cells))

    def test_placement_batch_does_not_call_unknown_methods_descriptors_or_truth_hooks(self):
        from wujiang.tactical.placement_queries import legal_placement_batch
        from wujiang.tactical.engine import core
        battle, actor, _ = self.fixture()
        cells = [Position(4, 2)]
        with ai.ai_enemy_view(battle, 1):
            for owner, name, replacement in (
                    (core.Battle, "can_place_unit", mock.Mock(return_value=True)),
                    (type(actor), "footprint_cells_at", mock.Mock(return_value=[])),
                    (type(actor), "standable_terrain", property(lambda unit: self.fail("descriptor executed"))),
                    (type(actor), "__bool__", lambda unit: self.fail("truth hook executed"))):
                with self.subTest(name=name), mock.patch.object(owner, name, replacement, create=True):
                    self.assertIsNone(legal_placement_batch(battle, actor, cells))
                    if isinstance(replacement, mock.Mock):
                        replacement.assert_not_called()

    def test_placement_batch_rejects_changed_code_defaults_and_geometry_function(self):
        from wujiang.tactical.placement_queries import legal_placement_batch
        from wujiang.tactical.engine import core
        battle, actor, _ = self.fixture()
        with ai.ai_enemy_view(battle, 1):
            cells = [Position(4, 2)]
            self.assertIsNotNone(legal_placement_batch(battle, actor, cells))
            original = core.Battle.can_place_unit.__kwdefaults__["ignore_units"]
            try:
                core.Battle.can_place_unit.__kwdefaults__["ignore_units"] = True
                self.assertIsNone(legal_placement_batch(battle, actor, cells))
            finally:
                core.Battle.can_place_unit.__kwdefaults__["ignore_units"] = original
            method = core.body_coordinates.__wrapped__
            code = method.__code__
            def replaced(x, y, offsets):
                return ()
            try:
                method.__code__ = replaced.__code__
                self.assertIsNone(legal_placement_batch(battle, actor, cells))
            finally:
                method.__code__ = code
            self.assertIsNotNone(legal_placement_batch(battle, actor, cells))

    def test_placement_batch_rejects_custom_values_without_comparing_or_reading_them(self):
        from wujiang.tactical.placement_queries import legal_placement_batch
        from wujiang.tactical.engine import core
        class Opaque:
            def __eq__(self, other):
                raise AssertionError("custom equality")
            @property
            def x(self):
                raise AssertionError("custom coordinate getter")
        battle, actor, _ = self.fixture()
        with ai.ai_enemy_view(battle, 1):
            self.assertIsNone(legal_placement_batch(battle, actor, [Opaque()]))
            original = core.Battle.can_place_unit.__kwdefaults__["ignore_units"]
            try:
                core.Battle.can_place_unit.__kwdefaults__["ignore_units"] = Opaque()
                self.assertIsNone(legal_placement_batch(battle, actor, [Position(4, 2)]))
            finally:
                core.Battle.can_place_unit.__kwdefaults__["ignore_units"] = original

    def test_placement_batch_is_disabled_for_live_legacy_or_explicit_fallback(self):
        from wujiang.tactical.placement_queries import legal_placement_batch
        battle, actor, _ = self.fixture()
        cells = [Position(4, 2)]
        self.assertIsNone(legal_placement_batch(battle, actor, cells))
        with ai.ai_enemy_view(battle, 1), mock.patch.dict(os.environ, {"WUJIANG_AI_DIRECT_PLACEMENT_BATCHES": "0"}):
            self.assertIsNone(legal_placement_batch(battle, actor, cells))
        with prediction.prediction_rules(False), ai.ai_enemy_view(battle, 1):
            self.assertIsNone(legal_placement_batch(battle, actor, cells))

    def test_actual_swap_endpoints_and_restoration_match_original_with_large_bodies_and_rider(self):
        from wujiang.tactical.heroes.excel_roster import gladiator_swap_destinations
        from wujiang.tactical.rooms.ai_acceleration import _id_state
        battle, actor, enemy = self.fixture()
        ally = create_hero("bard", 1)
        battle.add_unit(ally, Position(7, 6))
        rider = create_hero("bard", 1)
        battle.add_unit(rider, Position(8, 6))
        ally.is_mount, ally.ridden_by_unit_id = True, rider.unit_id
        rider.mounted_on_unit_id = ally.unit_id
        actor.set_footprint_offsets(((0, 0), (1, 0), (0, 1), (1, 1)))
        enemy.position = Position(4, 4)
        with ai.ai_enemy_view(battle, 1):
            before = pickle.dumps(battle), random.getstate(), _id_state()
            with mock.patch.dict(os.environ, {"WUJIANG_AI_DIRECT_PLACEMENT_BATCHES": "0"}):
                expected = gladiator_swap_destinations(battle, actor, ally, via_skill=False)
            actual = gladiator_swap_destinations(battle, actor, ally, via_skill=False)
            self.assertIsNotNone(expected)
            self.assertEqual(actual, expected)
            self.assertEqual((pickle.dumps(battle), random.getstate(), _id_state()), before)

    def test_placement_mask_reuses_this_decisions_geometry_and_invalidates_changed_inputs(self):
        from wujiang.tactical.placement_queries import legal_placement_batch
        battle, actor, target = self.fixture()
        cells = [Position(x, y) for y in range(battle.height) for x in range(battle.width)]
        observations = []
        with prediction.prediction_observer(observations.append), ai.ai_enemy_view(battle, 1):
            first = legal_placement_batch(battle, actor, cells)
            self.assertEqual(legal_placement_batch(battle, actor, list(reversed(cells))), list(reversed(first)))
            for change in (lambda: setattr(actor, "allow_enemy_destination_overlap", True),
                           lambda: target.set_footprint_offsets(((0, 0), (1, 0))),
                           lambda: setattr(target, "position", Position(6, 3)),
                           lambda: battle.blocked_cells.add((8, 4))):
                change()
                expected = [cell for cell in cells if battle.can_place_unit(actor, cell, ignore=actor, mover=actor)]
                self.assertEqual(legal_placement_batch(battle, actor, cells), expected)
            self.assertGreaterEqual(prediction._session.get()["statistics"]["direct_placement_mask_hits"], 1)
        self.assertIsNone(prediction._session.get())

    def test_placement_type_proof_detects_new_inherited_descriptor_without_invoking_it(self):
        from wujiang.tactical.placement_queries import legal_placement_batch
        from wujiang.tactical.engine.core import Unit
        battle, actor, _ = self.fixture()
        with ai.ai_enemy_view(battle, 1):
            cells = [Position(4, 2)]
            self.assertIsNotNone(legal_placement_batch(battle, actor, cells))
            descriptor = property(lambda unit: self.fail("new descriptor invoked"))
            with mock.patch.object(Unit, "standable_terrain", descriptor, create=True):
                self.assertIsNone(legal_placement_batch(battle, actor, cells))
            self.assertIsNotNone(legal_placement_batch(battle, actor, cells))

    def test_placement_masks_and_type_proofs_are_bounded_and_optional_native_is_not_required(self):
        from wujiang.tactical.placement_queries import legal_placement_batch
        battle, actor, _ = self.fixture()
        cells = [Position(x, y) for y in range(battle.height) for x in range(battle.width)]
        with ai.ai_enemy_view(battle, 1):
            with mock.patch("wujiang.platform.ai_prediction_native.load_kernel", return_value=None):
                for i in range(130):
                    battle.blocked_cells = {(40 + i, 40)}
                    self.assertIsNotNone(legal_placement_batch(battle, actor, cells))
            session = prediction._session.get()
            self.assertLessEqual(len(session["placement_masks"]), 128)
            self.assertLessEqual(session["placement_mask_bytes"], 2 * 1024 * 1024)
            self.assertLessEqual(len(session["placement_type_proofs"]), 256)
            expected = [cell for cell in cells if battle.can_place_unit(actor, cell, ignore=actor, mover=actor)]
            self.assertEqual(legal_placement_batch(battle, actor, cells), expected)


if __name__ == "__main__":
    unittest.main()
