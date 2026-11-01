"""New scoring semantics. Written for maintenance; performance entry skips tests."""
import pickle
import random
import unittest
from unittest import mock

from wujiang.tactical.prediction import (random as prediction_random, expected_numeric,
    prediction_session, prediction_rules, prediction_observer, outcomes, sum_distributions,
    attack_damage_distribution)
from wujiang.platform.id_sequence import IdSequence, isolated_ids
from wujiang.tactical.engine.core import Battle, Position, StatusEffect
from wujiang.tactical.heroes.registry import create_hero
from wujiang.tactical.rooms import ai
from wujiang.tactical.rooms.equipment_effects import EquipmentStatus


class CrossRecipientDamageObserver(StatusEffect):
    def on_battle_damage(self, battle, ctx):
        if ctx.target.unit_id == self.first_unit_id:
            target = battle.units.get(self.second_unit_id)
            if target is not None:
                target.current_hp = .5


class UnreviewedHealObserver(StatusEffect):
    def on_after_heal(self, battle, ctx):
        pass


class UnreviewedLinkedSync(StatusEffect):
    def sync_linked_state(self, battle):
        pass


class FullAIPredictionBehaviorTests(unittest.TestCase):
    def setUp(self):
        # Historical exact-enumeration policy remains an explicit research
        # reference. Production-default direct scoring has its own scenarios.
        import os
        patch = mock.patch.dict(os.environ, {"WUJIANG_AI_EXPECTATION_METHOD": "enumerate"})
        patch.start()
        self.addCleanup(patch.stop)

    def fixture(self):
        battle = Battle(width=10, height=10)
        actor, enemy = create_hero("bard", 1), create_hero("bard", 2)
        battle.add_unit(actor, Position(1, 1))
        battle.add_unit(enemy, Position(5, 5))
        battle.chess_rng = random.Random(97)
        return battle, actor, enemy

    def test_coin_expectation_weights_final_kill_utility(self):
        battle, _, _ = self.fixture()
        @expected_numeric
        def score(battle):
            damage = 1.0 if prediction_random.random() < .5 else 0.0
            return damage * 100 + (90 if damage >= 1 else 0)
        with ai.ai_enemy_view(battle, 1):
            self.assertEqual(score(battle), 95.0)

    def test_dice_enumerates_six_outcomes_and_threshold(self):
        battle, _, _ = self.fixture()
        with ai.ai_enemy_view(battle, 1):
            distribution = outcomes(battle, lambda: prediction_random.randint(1, 6))
            self.assertAlmostEqual(sum(v * p for v, p in distribution), 3.5)
            self.assertAlmostEqual(sum(p for v, p in distribution if v > 4), 1 / 3)

    def test_independent_coins_keep_threshold_probability(self):
        battle, _, _ = self.fixture()
        @expected_numeric
        def score(battle):
            first = prediction_random.random() < .5
            second = prediction_random.random() < .5
            return 100.0 if first and second else 0.0
        with ai.ai_enemy_view(battle, 1):
            self.assertEqual(score(battle), 25.0)

    def test_action_selection_compares_expectations_without_outcome_oracle(self):
        battle, _, _ = self.fixture()
        @expected_numeric(independent=True)
        def alternative(battle, name):
            return 100.0 if prediction_random.random() < .5 else 0.0
        @expected_numeric
        def best(battle):
            return max(alternative(battle, "first"), alternative(battle, "second"))
        with ai.ai_enemy_view(battle, 1):
            self.assertEqual(best(battle), 50.0)

    def test_repeated_comparison_of_one_uniform_preserves_correlation(self):
        battle, _, _ = self.fixture()
        @expected_numeric
        def score(battle):
            draw = prediction_random.random()
            return 10.0 if draw < .5 else 20.0 if draw < .75 else 0.0
        with ai.ai_enemy_view(battle, 1):
            self.assertEqual(score(battle), 10.0)

    def test_closed_payload_is_restored_for_every_probability_branch(self):
        battle, _, _ = self.fixture()
        payload = {"paid": False}
        def callback():
            paid = payload["paid"]
            payload["paid"] = True
            return 100.0 if prediction_random.random() < .5 and not paid else 0.0
        with ai.ai_enemy_view(battle, 1):
            values = outcomes(battle, callback)
            self.assertEqual(sum(value * probability for value, probability in values), 50.0)
            self.assertEqual(payload, {"paid": False})

    def test_prediction_restores_shared_container_identity(self):
        battle, _, _ = self.fixture()
        shared = []
        battle.first_alias = battle.second_alias = shared
        @expected_numeric
        def score(battle):
            battle.first_alias.append(1)
            battle.second_alias = [2]
            return 1.0 if prediction_random.random() < .5 else 3.0
        with ai.ai_enemy_view(battle, 1):
            self.assertEqual(score(battle), 2.0)
            self.assertIs(battle.first_alias, shared)
            self.assertIs(battle.second_alias, shared)
            self.assertEqual(shared, [])
        self.assertIs(battle.first_alias, shared)
        self.assertIs(battle.second_alias, shared)

    def test_prediction_does_not_read_live_coin_or_consume_rng_or_ids(self):
        battle, actor, _ = self.fixture()
        from wujiang.tactical.rooms.ai_acceleration import _id_state
        from wujiang.tactical.engine import core
        before = pickle.dumps(battle), random.getstate(), battle.chess_rng.getstate(), _id_state()
        @expected_numeric
        def score(battle):
            actor.current_hp = .125
            next(core._id_counter)
            return 10.0 if prediction_random.random() < .5 else -2.0
        with mock.patch("random.random", side_effect=AssertionError("live random read")):
            with ai.ai_enemy_view(battle, 1):
                self.assertEqual(score(battle), 4.0)
        self.assertEqual((pickle.dumps(battle), random.getstate(), battle.chess_rng.getstate(), _id_state()), before)

    def test_prediction_exception_restores_view_and_battle(self):
        battle, actor, enemy = self.fixture()
        enemy.add_status(StatusEffect("隐身"))
        before = pickle.dumps(battle)
        with self.assertRaisesRegex(ValueError, "failure"):
            with ai.ai_enemy_view(battle, 1):
                actor.current_hp = 0
                raise ValueError("failure")
        self.assertIs(battle.units[enemy.unit_id], enemy)
        self.assertEqual(pickle.dumps(battle), before)

    def test_enemy_stealth_is_empty_in_prediction_and_real_entity_restores(self):
        battle, actor, enemy = self.fixture()
        enemy.add_status(StatusEffect("隐身"))
        actor.add_status(StatusEffect("隐身"))
        with ai.ai_enemy_view(battle, 1):
            self.assertNotIn(enemy.unit_id, battle.units)
            self.assertIsNone(battle.unit_at(enemy.position))
            self.assertIn(actor.unit_id, battle.units)
        self.assertIs(battle.units[enemy.unit_id], enemy)

    def test_omitting_stealth_does_not_create_a_false_prediction_victory(self):
        battle, _, enemy = self.fixture()
        enemy.add_status(StatusEffect("隐身"))
        with ai.ai_enemy_view(battle, 1):
            battle.check_win_condition()
            self.assertIsNone(battle.winner)
        self.assertIsNone(battle.winner)

    def test_unknown_enemy_position_does_not_change_movement_scores(self):
        battle, actor, enemy = self.fixture()
        hidden = create_hero("bard", 2)
        battle.add_unit(hidden, Position(2, 1))
        hidden.add_status(StatusEffect("隐身"))
        profile = ai.difficulty_profile("standard")
        results = []
        for cell in (Position(2, 1), Position(9, 9)):
            hidden.position = cell
            with ai.ai_enemy_view(battle, 1):
                results.append(ai.score_move_destination(battle, actor, Position(2, 2), "aggressive", profile))
        self.assertEqual(results[0], results[1])

    def test_visible_clone_is_valued_like_source_without_clone_fragility(self):
        battle, actor, source = self.fixture()
        clone = create_hero("bard", 2)
        clone.is_clone = clone.is_summon = True
        clone.summoner_id = source.unit_id
        clone.controller_hero_id = source.unit_id
        clone.current_hp = .125
        source.current_hp = .75
        battle.add_unit(clone, Position(5, 6))
        with ai.ai_enemy_view(battle, 1):
            perceived = battle.units[clone.unit_id]
            self.assertFalse(perceived.is_clone)
            self.assertEqual(perceived.current_hp, source.current_hp)
            self.assertEqual(ai.hostile_unit_value(perceived), ai.hostile_unit_value(source))
            self.assertIsNot(perceived, clone)
        self.assertIs(battle.units[clone.unit_id], clone)
        self.assertTrue(clone.is_clone)
        self.assertEqual(clone.current_hp, .125)

    def test_wood_shield_distribution_retains_both_results_and_rng(self):
        battle, actor, target = self.fixture()
        target.position = Position(2, 1)
        target.base_stats.defense = 4
        target.add_status(EquipmentStatus("木盾", "无", "近战1/2概率减1"))
        before = battle.chess_rng.getstate()
        with ai.ai_enemy_view(battle, 1):
            values = attack_damage_distribution(battle, actor, target, {}, attack_power=2)
            self.assertAlmostEqual(sum(p for _, p in values), 1.0)
            self.assertEqual(len(values), 2)
            self.assertTrue(all(probability == .5 for _, probability in values))
        self.assertEqual(battle.chess_rng.getstate(), before)

    def test_expected_distribution_cache_never_stores_one_branch_sample(self):
        battle, _, _ = self.fixture()
        observed = []
        @expected_numeric
        def score(battle):
            return 20.0 if prediction_random.random() < .25 else 0.0
        with prediction_observer(observed.append), ai.ai_enemy_view(battle, 1):
            first, second = score(battle), score(battle)
        self.assertEqual(first, 5.0)
        self.assertEqual(second, first)
        self.assertGreater(observed[0]["statistics"].get("distribution_cache_hits", 0), 0)

    def test_changed_known_state_invalidates_expected_result(self):
        battle, actor, _ = self.fixture()
        @expected_numeric
        def score(battle):
            return actor.stat("attack") * (2.0 if prediction_random.random() < .5 else 0.0)
        with ai.ai_enemy_view(battle, 1):
            first = score(battle)
            actor.base_stats.attack += 1
            second = score(battle)
            self.assertEqual(second, first + 1.0)

    def test_real_area_damage_still_affects_stealthed_enemy(self):
        from wujiang.tactical.engine.core import DamageContext
        battle, actor, target = self.fixture()
        target.add_status(StatusEffect("隐身"))
        old_hp = target.current_hp
        battle.resolve_damage(DamageContext(source=actor, target=target, attack_power=0,
            raw_damage=.25, is_skill=True, from_field_effect=True, action_name="area"))
        self.assertLess(target.current_hp, old_hp)

    def test_visible_clone_of_hidden_source_stays_visible_in_prediction(self):
        battle, _, source = self.fixture()
        source.add_status(StatusEffect("隐身"))
        clone = create_hero("bard", 2)
        clone.is_clone = clone.is_summon = True
        clone.controller_hero_id = source.unit_id
        battle.add_unit(clone, Position(5, 6))
        with ai.ai_enemy_view(battle, 1):
            self.assertNotIn(source.unit_id, battle.units)
            self.assertFalse(battle.units[clone.unit_id].is_stealthed())
            self.assertFalse(battle.units[clone.unit_id].is_clone)

    def test_cached_expected_query_cannot_cross_player_views(self):
        battle, _, enemy = self.fixture()
        enemy.add_status(StatusEffect("隐身"))
        @expected_numeric
        def count(battle):
            return float(len(battle.units))
        with ai.ai_enemy_view(battle, 1):
            first = count(battle)
        with ai.ai_enemy_view(battle, 2):
            second = count(battle)
        self.assertEqual(first, 1.0)
        self.assertEqual(second, 2.0)

    def test_public_incoming_effect_can_be_defended_without_hidden_position(self):
        from wujiang.tactical.engine.core import QueuedAction
        battle, actor, enemy = self.fixture()
        enemy.add_status(StatusEffect("隐身"))
        queued = QueuedAction(action_type="attack", actor_id=enemy.unit_id, display_name="attack", speed=1,
            payload={"declared_source_attack": 3}, target_unit_ids=[actor.unit_id], hostile=True)
        results = []
        for cell in (Position(2, 1), Position(9, 9)):
            enemy.position = cell
            with ai.ai_enemy_view(battle, 1):
                results.append(ai.score_reaction_payload(battle, actor, queued,
                    {"action_code": "protection", "mana_cost": 0}, {}, ai.difficulty_profile("standard")))
        self.assertEqual(results[0], results[1])
        self.assertGreater(results[0], 0)

    def test_live_checkpoint_keeps_runners_omitted_by_normal_chess_persistence(self):
        from types import SimpleNamespace
        from wujiang.tactical.rooms.autochess import AutoChessState, ChessMatch
        from wujiang.tools.ai_baseline import dump_live_checkpoint, load_live_checkpoint
        battle, _, _ = self.fixture()
        chess = AutoChessState.__new__(AutoChessState)
        chess.matches = [ChessMatch(1, 2, runner=SimpleNamespace(battle=battle))]
        data = dump_live_checkpoint(chess, 0, random.getstate(), (1, 2))
        restored, board, rng, ids = load_live_checkpoint(data)
        self.assertIsNotNone(restored.matches[0].runner.battle)
        self.assertEqual(board, 0)
        self.assertEqual(ids, (1, 2))

    def test_baseline_capture_intercepts_actual_room_turn_planning(self):
        from types import SimpleNamespace
        from wujiang.tactical.rooms import ai_router, multiplayer
        from wujiang.tools.ai_baseline import capture_room_choosers, KINDS

        battle, actor, enemy = self.fixture()
        battle.configure_turn_order([actor.unit_id, enemy.unit_id])
        battle.start_battle()
        room = multiplayer.GameRoom("BASELN", seat_count=2)
        room.battle = battle
        payload = {"type": "end_turn"}
        captured = {name: mock.Mock(name=name, return_value=None) for name in KINDS}
        captured["choose_turn_bundle_action"].return_value = (payload, actor)
        original_router = {name: getattr(ai_router, name) for name in KINDS}
        original_room = {name: getattr(multiplayer, name) for name in KINDS}
        with mock.patch.object(room, "_current_prompt_seat", return_value=SimpleNamespace(
                is_ai_controlled=True, ai_difficulty_override=None)), \
                mock.patch.object(room, "_choose_ai_dragon_return_payload", return_value=None), \
                mock.patch.object(room, "_choose_ai_instant_payload", return_value=None), \
                capture_room_choosers(captured.__getitem__):
            planned = room._next_ai_planned_action()
        self.assertEqual(planned, (payload, "ai_turn", actor))
        captured["choose_turn_bundle_action"].assert_called_once()
        self.assertIs(captured["choose_turn_bundle_action"].call_args.args[0], battle)
        for name in KINDS:
            self.assertIs(getattr(ai_router, name), original_router[name])
            self.assertIs(getattr(multiplayer, name), original_room[name])

    def test_baseline_capture_intercepts_damage_prompt_and_restores_after_error(self):
        from types import SimpleNamespace
        from wujiang.tactical.rooms import ai_router, multiplayer
        from wujiang.tools.ai_baseline import capture_room_choosers, KINDS

        battle, actor, _ = self.fixture()
        battle.pending_damage_choice = {"unit_id": actor.unit_id}
        room = multiplayer.GameRoom("BASELN", seat_count=2)
        room.battle = battle
        captured = {name: mock.Mock(name=name) for name in KINDS}
        captured["choose_damage_choice_action"].side_effect = RuntimeError("capture failed")
        original_router = {name: getattr(ai_router, name) for name in KINDS}
        original_room = {name: getattr(multiplayer, name) for name in KINDS}
        with mock.patch.object(room, "_current_prompt_seat", return_value=SimpleNamespace(
                is_ai_controlled=True)), self.assertRaisesRegex(RuntimeError, "capture failed"):
            with capture_room_choosers(captured.__getitem__):
                room._next_ai_planned_action()
        captured["choose_damage_choice_action"].assert_called_once_with(battle)
        for name in KINDS:
            self.assertIs(getattr(ai_router, name), original_router[name])
            self.assertIs(getattr(multiplayer, name), original_room[name])

    def test_private_id_scope_is_nested_and_never_rewinds_real_sequence(self):
        sequence = IdSequence(5)
        with isolated_ids():
            self.assertEqual(next(sequence), 5)
            with isolated_ids():
                self.assertEqual(next(sequence), 6)
            self.assertEqual(next(sequence), 6)
        self.assertEqual(next(sequence), 5)

    def test_distribution_convolution_merges_equal_sums(self):
        result = dict(sum_distributions(((0, .5), (1, .5)), ((0, .5), (1, .5))))
        self.assertEqual(result, {0: .25, 1: .5, 2: .25})

    def test_real_rng_adapter_delegates_without_adding_draws(self):
        state = random.getstate()
        try:
            random.seed(41)
            expected = random.random(), random.randint(1, 6), random.choice((2, 4, 6))
            random.seed(41)
            with prediction_rules(False):
                actual = prediction_random.random(), prediction_random.randint(1, 6), prediction_random.choice((2, 4, 6))
            self.assertEqual(actual, expected)
        finally:
            random.setstate(state)

    def test_deterministic_queries_skip_probability_cache_keys_and_see_new_state(self):
        battle, actor, _ = self.fixture()
        @expected_numeric
        def score(battle):
            return actor.current_hp * 10
        with mock.patch("wujiang.tactical.prediction._memo_key", side_effect=AssertionError("unneeded full key")):
            with ai.ai_enemy_view(battle, 1):
                first = score(battle)
                actor.current_hp -= .25
                self.assertEqual(score(battle), first - 2.5)

    def test_graph_snapshot_reuses_only_unchanged_original_references(self):
        from wujiang.tactical.prediction import prediction_rollback
        battle, actor, _ = self.fixture()
        original = ["same"]
        actor.probe_values = original
        observations = []
        with prediction_observer(observations.append), ai.ai_enemy_view(battle, 1):
            for _ in range(2):
                with prediction_rollback(battle):
                    actor.probe_values.append("temporary")
                self.assertIs(actor.probe_values, original)
                self.assertEqual(original, ["same"])
            replacement = ["same"]
            actor.probe_values = replacement
            with prediction_rollback(battle):
                actor.probe_values.append("different identity")
            self.assertIs(actor.probe_values, replacement)
            self.assertEqual(replacement, ["same"])
        self.assertIs(actor.probe_values, original)
        self.assertGreater(observations[0]["statistics"].get("graph_snapshot_reuses", 0), 0)

    def test_explicit_heavy_deterministic_query_retains_complete_result_cache(self):
        battle, actor, _ = self.fixture()
        observations = []
        @expected_numeric(eager_cache=True)
        def score(battle):
            return actor.current_hp * 10
        with prediction_observer(observations.append), ai.ai_enemy_view(battle, 1):
            self.assertEqual(score(battle), score(battle))
        self.assertGreater(observations[0]["statistics"].get("distribution_cache_hits", 0), 0)

    def test_graph_snapshot_respects_dictionary_order_and_nested_shared_mutations(self):
        from wujiang.tactical.prediction import prediction_rollback
        battle, actor, _ = self.fixture()
        shared = []
        actor.probe_values = {"a": shared, "b": shared}
        with ai.ai_enemy_view(battle, 1):
            with prediction_rollback(battle):
                shared.append(1)
            actor.probe_values = {"b": shared, "a": shared}
            with prediction_rollback(battle):
                actor.probe_values.clear()
                shared.append(2)
            self.assertEqual(list(actor.probe_values), ["b", "a"])
            self.assertIs(actor.probe_values["a"], actor.probe_values["b"])
            self.assertEqual(shared, [])

    def test_graph_snapshot_restores_slots_and_ordinary_dataclass_fields(self):
        from dataclasses import dataclass
        from wujiang.tactical.prediction import prediction_rollback
        @dataclass(slots=True)
        class Slotted:
            values: list
        @dataclass
        class Ordinary:
            values: list
        battle, _, _ = self.fixture()
        shared = []
        battle.slotted, battle.ordinary = Slotted(shared), Ordinary(shared)
        with prediction_session(1):
            for _ in range(2):
                with prediction_rollback(battle):
                    battle.slotted.values.append(1)
                    battle.slotted.values = [2]
                    battle.ordinary.values = [3]
                self.assertIs(battle.slotted.values, shared)
                self.assertIs(battle.ordinary.values, shared)
                self.assertEqual(shared, [])

    def test_graph_snapshot_reuse_keeps_runtime_child_discovery_and_restoration(self):
        from wujiang.tactical.prediction import prediction_rollback
        calls = []
        child = StatusEffect("runtime child")
        class Dynamic(StatusEffect):
            def runtime_children(self):
                calls.append("discovered")
                return [child]
        battle, actor, _ = self.fixture()
        actor.add_status(Dynamic("dynamic"))
        with prediction_session(1):
            for _ in range(2):
                with prediction_rollback(battle):
                    child.name = "temporary"
                self.assertEqual(child.name, "runtime child")
        self.assertEqual(calls, ["discovered", "discovered"])

    def test_joint_vector_preserves_nonlinear_leaf_utility_and_shared_outcome(self):
        from wujiang.tactical.prediction import expected_vector
        battle, _, _ = self.fixture()
        def utilities():
            hit = prediction_random.random() < .5
            return (190.0 if hit else 0.0, -190.0 if hit else 0.0)
        with ai.ai_enemy_view(battle, 1):
            first = expected_vector(battle, utilities, cache_request=("joint",))
            second = expected_vector(battle, utilities, cache_request=("joint",))
        self.assertEqual(first, (95.0, -95.0))
        self.assertEqual(second, first)

    def test_queued_losses_share_one_complete_action_distribution_for_both_targets(self):
        from wujiang.tactical.engine.core import QueuedAction
        battle, actor, target = self.fixture()
        for unit in (actor, target):
            unit.skills.clear()
            unit.traits.clear()
        actor.current_hp = target.current_hp = .5
        queued = QueuedAction(action_type="attack", actor_id=actor.unit_id, display_name="joint", speed=1,
                              payload={}, target_unit_ids=[actor.unit_id, target.unit_id])
        def resolve(current_battle, action):
            for unit in (actor, target):
                if prediction_random.random() < .5:
                    unit.current_hp = 0
                    unit.alive = False
        with mock.patch.object(Battle, "resolve_queued_action", autospec=True, side_effect=resolve) as resolver:
            with ai.ai_enemy_view(battle, 1):
                first = ai.r21_queued_loss(battle, actor, queued, observed_targets=[actor, target])
                calls = resolver.call_count
                second = ai.r21_queued_loss(battle, target, queued, observed_targets=[actor, target])
                self.assertEqual(resolver.call_count, calls)
        self.assertEqual(first, 70.0)  # E[50 HP utility + 90 death utility].
        self.assertEqual(second, first)
        self.assertTrue(actor.alive and target.alive)
        self.assertEqual(actor.current_hp, .5)

    def test_focused_repair_selection_covers_four_boards_worst_reactions_and_dense_tail(self):
        from wujiang.tools.ai_baseline import focused_requests
        index = [{"request": i, "step": i, "board": (i - 1) % 4 + 1, "units": 24} for i in range(1, 201)]
        previous = {68: [{"seconds": 509}], 76: [{"seconds": 568}]}
        selected = focused_requests(index, previous)
        self.assertEqual({row["board"] for row in selected}, {1, 2, 3, 4})
        self.assertTrue({68, 76, 117, 137, 196} <= {row["request"] for row in selected})
        self.assertLessEqual(len(selected), 12)

    def test_joint_losses_fall_back_when_additional_observation_mutates_state(self):
        from wujiang.tactical.engine.core import QueuedAction, Unit
        battle, actor, target = self.fixture()
        for unit in (actor, target):
            unit.skills.clear()
            unit.traits.clear()
        observations = []
        queued = QueuedAction(action_type="attack", actor_id=actor.unit_id, display_name="guard", speed=1,
                              payload={}, target_unit_ids=[target.unit_id])
        original_stat = Unit.stat
        def impure_stat(unit, name):
            unit.probe_reads = getattr(unit, "probe_reads", 0) + 1
            return original_stat(unit, name)
        def resolve(current_battle, action):
            target.current_hp -= .25
        with mock.patch.object(Unit, "stat", impure_stat), \
                mock.patch.object(Battle, "resolve_queued_action", resolve), \
                prediction_observer(observations.append), ai.ai_enemy_view(battle, 1):
            value = ai.r21_queued_loss(battle, target, queued, observed_targets=[actor, target])
        self.assertEqual(value, 25.0)
        self.assertFalse(hasattr(target, "probe_reads"))
        self.assertEqual(observations[0]["statistics"].get("joint_utility_impure_observer_fallbacks"), 1)

    def test_joint_losses_fall_back_to_scalar_tree_for_stochastic_getters(self):
        from wujiang.tactical.engine.core import QueuedAction, Unit
        battle, actor, target = self.fixture()
        for unit in (actor, target):
            unit.skills.clear()
            unit.traits.clear()
        queued = QueuedAction(action_type="attack", actor_id=actor.unit_id, display_name="guard", speed=1,
                              payload={}, target_unit_ids=[target.unit_id])
        original_stat = Unit.stat
        def stochastic_stat(unit, name):
            return original_stat(unit, name) + (1 if prediction_random.random() < .5 else 0)
        def resolve(current_battle, action):
            target.current_hp -= .25
        with mock.patch.object(Unit, "stat", stochastic_stat), \
                mock.patch.object(Battle, "resolve_queued_action", resolve), ai.ai_enemy_view(battle, 1):
            value = ai.r21_queued_loss(battle, target, queued, observed_targets=[actor, target])
        self.assertEqual(value, 25.0 + (24.0 + 30.0 + 14.0) / 4)

    def test_joint_final_utility_marginals_keep_scalar_float_accumulation(self):
        import math
        from wujiang.tactical.prediction import expected_vector
        battle, _, _ = self.fixture()
        table = ((1e16, 1.0), (1.0, 2.0), (-1e16, 3.0))
        def vector():
            return table[prediction_random.choice((0, 1, 2))]
        with ai.ai_enemy_view(battle, 1):
            actual = expected_vector(battle, vector, cache_request=("float_order", table))
            expected = tuple(math.fsum(value * weight for value, weight in outcomes(
                battle, lambda: vector()[index])) for index in range(2))
        self.assertEqual(actual, expected)

    def test_joint_losses_fall_back_for_id_consuming_getters_without_live_id_changes(self):
        from wujiang.tactical.engine import core
        from wujiang.tactical.engine.core import QueuedAction, Unit
        from wujiang.tactical.rooms.ai_acceleration import _id_state
        battle, actor, target = self.fixture()
        for unit in (actor, target):
            unit.skills.clear()
            unit.traits.clear()
        queued = QueuedAction(action_type="attack", actor_id=actor.unit_id, display_name="guard", speed=1,
                              payload={}, target_unit_ids=[target.unit_id])
        original_stat = Unit.stat
        def allocating_stat(unit, name):
            next(core._id_counter)
            return original_stat(unit, name)
        def resolve(current_battle, action):
            target.current_hp -= .25
        ids = _id_state()
        observations = []
        with mock.patch.object(Unit, "stat", allocating_stat), \
                mock.patch.object(Battle, "resolve_queued_action", resolve), \
                prediction_observer(observations.append), ai.ai_enemy_view(battle, 1):
            value = ai.r21_queued_loss(battle, target, queued, observed_targets=[actor, target])
        self.assertEqual(value, 25.0)
        self.assertEqual(_id_state(), ids)
        self.assertEqual(observations[0]["statistics"].get("joint_utility_impure_observer_fallbacks"), 1)

    def test_historical_expected_comparison_labels_incomplete_prior_repeats(self):
        from wujiang.tools.ai_baseline import historical_expected_comparison
        previous = {1: [{"seconds": value, "choice": {}} for value in (2.0, 4.0)]}
        current = [[{"request": 1, "seconds": 1.0, "choice": {}}] for _ in range(3)]
        result = historical_expected_comparison(previous, current)
        self.assertEqual(result["requests"][0]["prior_repeats"], 2)
        self.assertEqual(result["requests"][0]["current_repeats"], 3)
        self.assertAlmostEqual(result["time_reduction_percent"], 200 / 3)

    def test_selected_requests_do_not_claim_an_incomplete_four_board_sweep(self):
        from wujiang.tools.ai_baseline import summarize
        rows = [{"step": 1, "board": 1, "active_boards": 4, "kind": "choose_turn_bundle_action", "seconds": 1.0}]
        self.assertIsNone(summarize([rows])["four_board_sweep_max"])

    def test_repair_coordinator_prioritizes_dense_series_and_continues_after_hell_timeout(self):
        import json
        import os
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from wujiang.tools import ai_baseline
        visited = []
        def stage(command, log, *, timeout, environment):
            if "--child" not in command:
                return
            fixture = command[command.index("--child") + 1]
            directory = Path(command[command.index("--output-dir") + 1])
            visited.append(fixture)
            if fixture == "hell-heavy":
                raise TimeoutError("fixture timeout")
            ai_baseline.write(directory / "result.json", {"status": "completed", "build": {"id": "test"},
                "time_reduction_percent": 0.0, "timing": {}, "new_observation_changes": {}})
        with TemporaryDirectory() as temporary, mock.patch.dict(os.environ):
            root = Path(temporary)
            with mock.patch.object(ai_baseline, "ROOT", root), \
                    mock.patch("wujiang.platform.ai_build.build_info", return_value={"id": "test"}), \
                    mock.patch("wujiang.platform.ai_prediction_native.native_status", return_value={"available": True}), \
                    mock.patch("wujiang.tools.ai_validation.validation_environment", return_value={}), \
                    mock.patch("wujiang.tools.ai_validation._run_stage", side_effect=stage):
                result = ai_baseline.main(["--output-dir", str(root / "result"), "--repair-check"])
            summary = json.loads((root / "result/summary.json").read_text(encoding="utf-8"))
        self.assertEqual(result, 1)
        self.assertEqual(visited[:2], ["electronic-heavy", "hell-heavy"])
        self.assertEqual(summary["fixtures"][0]["status"], "completed")
        self.assertEqual(summary["fixtures"][1]["status"], "timeout")
        self.assertFalse(summary["baseline_established"])

    def test_complete_state_key_preserves_world_argument_aliases_and_reuses_serialization(self):
        from copy import deepcopy
        from wujiang.tactical import prediction
        battle, _, _ = self.fixture()
        battle.key_probe = {"values": []}
        with ai.ai_enemy_view(battle, 1):
            saved = prediction._saved_graph(battle)
            self.assertTrue(saved.complete_observation)
            first = prediction._memo_key(battle, ("probe", battle.key_probe))
            self.assertEqual(first, prediction._memo_key(battle, ("probe", battle.key_probe)))
            self.assertNotEqual(first, prediction._memo_key(battle, ("probe", deepcopy(battle.key_probe))))
            state = saved.serialized_query_state
            self.assertIs(saved.serialized_query_state, state)
            self.assertGreater(saved.serialized_query_bytes, len(state[1]))
            self.assertLessEqual(prediction._session.get()["graph_cache_bytes"], 8 * 1024 * 1024)

    def test_complete_state_key_tracks_frozen_position_rng_and_protocol_changes(self):
        from wujiang.tactical import prediction
        from wujiang.tactical.prediction_keys import graph_observation_stamp
        from wujiang.tactical.engine.core import Unit
        battle, actor, _ = self.fixture()
        with ai.ai_enemy_view(battle, 1):
            saved = prediction._saved_graph(battle)
            first = prediction._memo_key(battle, ("probe",))
            battle.chess_rng.gauss_next = .25
            self.assertNotEqual(first, prediction._memo_key(battle, ("probe",)))
            first = prediction._memo_key(battle, ("probe",))
            x = actor.position.x
            try:
                object.__setattr__(actor.position, "x", x + 1)
                self.assertNotEqual(first, prediction._memo_key(battle, ("probe",)))
            finally:
                object.__setattr__(actor.position, "x", x)
            with mock.patch.object(Unit, "__getstate__", lambda unit: dict(vars(unit))):
                self.assertIsNone(graph_observation_stamp(saved))

    def test_complete_state_key_rejects_foreign_serialization_protocol(self):
        from wujiang.tactical import prediction
        battle, _, _ = self.fixture()
        class Opaque:
            def __getstate__(self): return {"value": 1}
        battle.opaque = Opaque()
        with ai.ai_enemy_view(battle, 1):
            self.assertFalse(prediction._saved_graph(battle).complete_observation)

    def test_runtime_discovery_rebinding_invalidates_a_warm_static_plan(self):
        from wujiang.tactical import prediction
        from wujiang.tactical.prediction_runtime import static_runtime_scan
        battle, actor, _ = self.fixture()
        status = StatusEffect("runtime")
        actor.statuses.append(status)
        calls = []
        with ai.ai_enemy_view(battle, 1):
            saved = prediction._saved_graph(battle)
            self.assertTrue(static_runtime_scan(saved, prediction._session.get()))
            with mock.patch.object(StatusEffect, "runtime_children", lambda self: calls.append(self) or ()):
                self.assertFalse(static_runtime_scan(saved, prediction._session.get()))
                from wujiang.tactical.prediction_inline import can_inline
                self.assertFalse(can_inline(ai.score_skill_payload.__wrapped__, battle,
                    (actor, {"code": "test"}, {}, ai.difficulty_profile("standard")), {"instant_only": False},
                    prediction._session.get()))
                with prediction.prediction_rollback(battle): pass
                with prediction.prediction_rollback(battle): pass
        self.assertEqual(calls, [status, status])

    def test_runtime_discovery_refuses_a_descriptor_that_changes_enumeration_reads(self):
        from wujiang.tactical import prediction
        from wujiang.tactical.prediction_runtime import static_runtime_scan
        battle, actor, _ = self.fixture()
        status = StatusEffect("runtime")
        actor.statuses.append(status)
        with ai.ai_enemy_view(battle, 1):
            saved = prediction._saved_graph(battle)
            self.assertTrue(static_runtime_scan(saved, prediction._session.get()))
            with mock.patch.object(StatusEffect, "suppresses_innate_traits", property(lambda status: False), create=True):
                self.assertFalse(static_runtime_scan(saved, prediction._session.get()))
                fresh = prediction._GraphSnapshot(battle)
                fresh.visit(battle)
                self.assertFalse(static_runtime_scan(fresh, prediction._session.get()))

    def test_inline_parent_keeps_child_expectations_before_nonlinear_comparison(self):
        import os
        from wujiang.tactical import prediction
        battle, actor, _ = self.fixture()
        before = pickle.dumps(battle)
        @expected_numeric(independent=True)
        def child(battle, *args, **kwargs):
            actor.current_hp = .125
            return 100.0 if prediction_random.random() < .5 else 0.0
        @expected_numeric(independent=True)
        def adjustment(battle, *args, **kwargs):
            return 5.0 if prediction_random.random() < .5 else -1000.0
        values, observations = [], []
        for flag in ("0", "1"):
            with mock.patch.dict(os.environ, {"WUJIANG_AI_PREPARATION_BATCH": flag}), \
                    mock.patch.object(ai, "_score_skill_payload", child), \
                    mock.patch.object(ai, "nova_paid_action_adjustment", adjustment), \
                    prediction_observer(observations.append), ai.ai_enemy_view(battle, 1):
                prediction._saved_graph(battle)
                values.append(ai.score_skill_payload(battle, actor, {"code": "test"}, {},
                                                    ai.difficulty_profile("standard"), instant_only=False))
        self.assertEqual(values, [-447.5, -447.5])
        self.assertGreater(observations[1]["statistics"].get("certified_inline_queries:score_skill_payload", 0), 0)
        self.assertEqual(pickle.dumps(battle), before)

    def test_inline_plain_input_certificate_rejects_cycles_custom_keys_and_changed_code(self):
        from types import FunctionType
        from wujiang.tactical import prediction, prediction_inline as inline
        battle, actor, _ = self.fixture()
        cyclic = []; cyclic.append(cyclic)
        class Key:
            def __hash__(self): return 1
        with ai.ai_enemy_view(battle, 1):
            session = prediction._session.get()
            prediction._saved_graph(battle)
            self.assertFalse(inline._plain(cyclic, session))
            self.assertFalse(inline._plain({Key(): 1}, session))
            raw = ai.score_skill_payload.__wrapped__
            changed = FunctionType(raw.__code__.replace(co_consts=raw.__code__.co_consts + ("changed",)), raw.__globals__)
            self.assertFalse(inline.can_inline(changed, battle, (actor, {"code": "test"}, {}, ai.difficulty_profile("standard")),
                                               {"instant_only": False}, session))
        self.assertIsNone(inline._dispatch_tree("missing-ai-source.py"))

    def test_native_input_skip_keeps_custom_key_hash_protocol(self):
        from collections import deque
        kernel = self.prediction_kernel()
        calls = []
        class Key:
            def __hash__(self): calls.append("hash"); return 3
        key = Key(); mapping = {key: []}
        def callback(): return mapping
        rows, _ = kernel.capture_inputs(callback, deque)
        calls.clear(); kernel.restore_inputs(rows, deque, False); expected = calls[:]
        calls.clear(); kernel.restore_inputs(rows, deque, True)
        self.assertEqual(calls, expected)
        self.assertGreater(len(calls), 0)

    def test_native_graph_restores_deleted_slots_original_namespaces_and_property_setters(self):
        from collections import deque
        kernel = self.prediction_kernel()
        class Slots:
            __slots__ = ("value",)
        obj = Slots(); old = []; obj.value = old
        slots = [(obj, (("value", old),))]
        self.assertTrue(kernel.graph_matches([], slots, [], deque))
        del obj.value; kernel.restore_slots(slots)
        self.assertIs(obj.value, old)
        class Namespace: pass
        item = Namespace(); original = item.__dict__; item.__dict__ = {"new": 1}
        kernel.restore_namespaces([(item, original)])
        self.assertIs(item.__dict__, original)
        calls = []
        class Property:
            @property
            def value(self): return old
            @value.setter
            def value(self, value): calls.append(value)
        kernel.restore_slots([(Property(), (("value", old),))])
        self.assertEqual(calls, [old])

    def test_native_type_versions_reject_inherited_and_descriptor_rebinding(self):
        kernel = self.prediction_kernel()
        class Base:
            def hook(self): pass
        class Child(Base): pass
        Child.hook
        versions = kernel.type_versions((Child,))
        if versions is None:
            self.skipTest("Runtime did not assign a type lookup tag")
        self.assertTrue(kernel.type_versions_match((Child,), versions))
        Base.hook = staticmethod(Base.hook)
        self.assertFalse(kernel.type_versions_match((Child,), versions))

    def prediction_kernel(self):
        import os
        from wujiang.platform.ai_prediction_native import load_kernel
        kernel = load_kernel()
        if kernel is None:
            if os.environ.get("WUJIANG_AI_REQUIRE_PREDICTION_NATIVE") == "1":
                self.fail("Required expectation preparation kernel missing")
            self.skipTest("Optional expectation preparation kernel not built")
        return kernel

    def test_legacy_view_never_loads_expectation_kernel_or_argument_copier(self):
        battle, _, _ = self.fixture()
        with prediction_rules(False), \
                mock.patch("wujiang.platform.ai_prediction_native.load_kernel", side_effect=AssertionError("legacy load")), \
                mock.patch("wujiang.tactical.prediction._copy_arguments", side_effect=AssertionError("legacy copy")):
            @expected_numeric
            def score(battle, payload):
                return payload["value"]
            with ai.ai_enemy_view(battle, 1):
                self.assertEqual(score(battle, {"value": 7}), 7)

    def test_required_expectation_kernel_cannot_silently_fall_back(self):
        import os
        with prediction_rules(True), \
                mock.patch.dict(os.environ, WUJIANG_AI_PREDICTION_NATIVE="1", WUJIANG_AI_REQUIRE_PREDICTION_NATIVE="1"), \
                mock.patch("wujiang.platform.ai_prediction_native.load_kernel", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "required"):
                with prediction_session(1):
                    self.fail("Missing kernel must reject a mislabeled measurement")

    def test_native_input_capture_matches_python_for_cycles_aliases_and_function_closures(self):
        from collections import deque
        from wujiang.tactical import prediction
        kernel = self.prediction_kernel()
        shared = []
        mapping = {"left": shared, "right": shared}
        queue = deque([shared])
        values = {"old"}
        cycle = [mapping, queue, values]
        cycle.append(cycle)
        def callback():
            return cycle
        native_rows, _ = kernel.capture_inputs(callback, deque)
        saved_native = [(id(obj), type(saved), tuple(saved.items()) if type(saved) is dict else tuple(saved))
                        for obj, saved in native_rows]
        # Exercise the actual fallback, rather than a second test-only walker.
        with mock.patch.object(prediction, "_session") as session:
            session.get.return_value = None
            restore_python = prediction._replay_inputs(callback)
        closed = dict(zip(restore_python.__code__.co_freevars,
                          (cell.cell_contents for cell in restore_python.__closure__)))
        python_rows = closed["snapshots"]
        self.assertEqual([id(obj) for obj, _ in python_rows], [id(obj) for obj, _ in native_rows])
        for (_, old), (_, new) in zip(python_rows, native_rows):
            self.assertIs(type(old), type(new))
            if type(old) is set:
                self.assertEqual({id(item) for item in old}, {id(item) for item in new})
            elif old and type(old[0]) is tuple and len(old[0]) == 2:
                self.assertEqual([(id(key), id(value)) for key, value in old],
                                 [(id(key), id(value)) for key, value in new])
            else:
                self.assertEqual([id(item) for item in old], [id(item) for item in new])
        shared.append("temporary")
        mapping.clear()
        queue.append("extra")
        values.add("new")
        cycle.pop()
        restore_python()
        self.assertIs(mapping["left"], shared)
        self.assertIs(mapping["right"], shared)
        self.assertIs(cycle[-1], cycle)
        shared.append("again")
        mapping["extra"] = []
        queue.clear()
        values.clear()
        kernel.restore_inputs(native_rows, deque)
        self.assertEqual(shared, [])
        self.assertEqual(list(mapping), ["left", "right"])
        self.assertIs(queue[0], shared)
        self.assertEqual(values, {"old"})
        rows_again, _ = kernel.capture_inputs(callback, deque)
        self.assertEqual([id(obj) for obj, _ in rows_again], [row[0] for row in saved_native])

    def test_native_input_restore_preserves_dictionary_key_hash_calls_and_order(self):
        from collections import deque
        from wujiang.tactical import prediction
        kernel = self.prediction_kernel()
        events = []
        class Key:
            def __init__(self, name): self.name = name
            def __hash__(self):
                events.append(self.name)
                return hash(self.name)
        first, second = Key("first"), Key("second")
        mapping = {first: [], second: []}
        def callback(): return mapping
        rows, _ = kernel.capture_inputs(callback, deque)
        restore_python = prediction._replay_inputs(callback)
        events.clear()
        restore_python()
        expected = events.copy()
        events.clear()
        kernel.restore_inputs(rows, deque)
        self.assertEqual(events, expected)
        self.assertEqual(list(mapping), [first, second])

    def test_native_input_capture_ignores_custom_container_protocols_like_python(self):
        from collections import deque
        kernel = self.prediction_kernel()
        class Custom(list):
            def __iter__(self): raise AssertionError("custom input must not be expanded")
        custom = Custom([1])
        def callback(): return custom
        rows, _ = kernel.capture_inputs(callback, deque)
        self.assertEqual(rows, [])

    def test_native_graph_checks_pointer_identity_dictionary_order_and_deque_order(self):
        from collections import deque
        from wujiang.tactical.prediction import _GraphSnapshot
        kernel = self.prediction_kernel()
        left, right = ["same"], ["same"]
        mapping, queue = {"a": left, "b": right}, deque([left, right])
        rows = [(mapping, tuple(mapping.items()), _GraphSnapshot.identities(mapping)),
                (queue, tuple(queue), _GraphSnapshot.identities(queue))]
        self.assertTrue(kernel.containers_match(rows, deque))
        mapping["a"] = ["same"]
        self.assertFalse(kernel.containers_match(rows, deque))
        kernel.restore_graph(rows, deque)
        self.assertIs(mapping["a"], left)
        mapping["a"] = mapping.pop("a")
        self.assertFalse(kernel.containers_match(rows, deque))
        kernel.restore_graph(rows, deque)
        self.assertEqual(list(mapping), ["a", "b"])
        queue.reverse()
        self.assertFalse(kernel.containers_match(rows, deque))
        kernel.restore_graph(rows, deque)
        self.assertIs(queue[0], left)

    def test_native_graph_validation_and_unchanged_restore_do_not_call_key_equality_or_hash(self):
        from collections import deque
        from wujiang.tactical.prediction import _GraphSnapshot
        kernel = self.prediction_kernel()
        class Key:
            armed = False
            def __hash__(self):
                if self.armed: raise AssertionError("identity check hashed key")
                return 1
            def __eq__(self, other): raise AssertionError("identity check compared key")
        key = Key()
        mapping = {key: []}
        rows = [(mapping, tuple(mapping.items()), _GraphSnapshot.identities(mapping))]
        key.armed = True
        self.assertTrue(kernel.containers_match(rows, deque))
        kernel.restore_graph(rows, deque)

    def test_native_graph_sets_compare_object_identity_even_for_equal_values(self):
        from collections import deque
        from wujiang.tactical.prediction import _GraphSnapshot
        kernel = self.prediction_kernel()
        first = tuple(["equal"])
        second = tuple(["equal"])
        self.assertIsNot(first, second)
        values = {first}
        rows = [(values, tuple(values), _GraphSnapshot.identities(values))]
        values.clear()
        values.add(second)
        self.assertFalse(kernel.containers_match(rows, deque))
        kernel.restore_graph(rows, deque)
        self.assertIs(next(iter(values)), first)

    def test_native_preparation_preserves_joint_probability_tree_and_live_state(self):
        import os
        from wujiang.tactical.rooms.ai_acceleration import _id_state
        self.prediction_kernel()
        results = []
        for enabled in ("0", "1"):
            battle, actor, _ = self.fixture()
            payload = {"hits": []}
            initial_hp, initial_ids = actor.current_hp, _id_state()
            rng = random.getstate(), battle.chess_rng.getstate()
            observations = []
            def callback():
                payload["hits"].append(1)
                draw = prediction_random.random()
                if draw < .5:
                    actor.current_hp -= .25
                if draw < .75:
                    actor.current_hp -= .25
                return (initial_hp - actor.current_hp) * 100 + (90 if actor.current_hp <= 0 else 0)
            with mock.patch.dict(os.environ, WUJIANG_AI_PREDICTION_NATIVE=enabled, WUJIANG_AI_REQUIRE_PREDICTION_NATIVE="0"), \
                    prediction_rules(True), prediction_observer(observations.append), prediction_session(1):
                result = outcomes(battle, callback)
            self.assertEqual(payload, {"hits": []})
            self.assertEqual(actor.current_hp, initial_hp)
            self.assertEqual(_id_state(), initial_ids)
            self.assertEqual((random.getstate(), battle.chess_rng.getstate()), rng)
            stats = observations[0]["statistics"]
            self.assertEqual(stats["native_preparation_sessions"], int(enabled))
            if enabled == "1":
                self.assertGreater(stats.get("native_input_captures", 0), 0)
                self.assertGreater(stats.get("native_graph_restores", 0), 0)
            results.append((result, stats["probability_leaves"], stats["probability_forks"], stats["probability_reads"]))
        self.assertEqual(results[0], results[1])

    def test_argument_copier_retains_independent_nested_container_copies_and_custom_fallback(self):
        from collections import deque
        from wujiang.tactical.engine import core
        from wujiang.tactical.prediction import _copy_arguments
        if core._NATIVE_ROLLBACK_COPY is None:
            self.skipTest("Existing native container copier missing")
        shared, owner = [1], object()
        class Custom(dict):
            def items(self): return [("custom", [owner])]
        value = ({"a": shared, "b": shared, "owner": owner}, deque([shared]), Custom())
        expected = core._rollback_copy_value(value)
        with mock.patch.object(core, "_ROLLBACK_NATIVE_ENABLED", True):
            actual = _copy_arguments(value)
        self.assertEqual(actual, expected)
        self.assertIsNot(actual[0]["a"], actual[0]["b"])
        self.assertIs(actual[0]["owner"], owner)
        self.assertIs(actual[2]["custom"][0], owner)

    def test_argument_copier_respects_missing_or_disabled_native_backend(self):
        from wujiang.tactical.engine import core
        from wujiang.tactical.prediction import _copy_arguments
        for enabled, native in ((False, mock.Mock(side_effect=AssertionError("disabled"))), (True, None)):
            with mock.patch.object(core, "_ROLLBACK_NATIVE_ENABLED", enabled), \
                    mock.patch.object(core, "_NATIVE_ROLLBACK_COPY", native):
                value = {"values": [1]}
                result = _copy_arguments(value)
                self.assertEqual(result, value)
                self.assertIsNot(result["values"], value["values"])

    def test_native_preparation_restores_before_callback_exception_escapes(self):
        import os
        self.prediction_kernel()
        battle, actor, _ = self.fixture()
        payload, hp = {"values": []}, actor.current_hp
        def callback():
            payload["values"].append("temporary")
            actor.current_hp = 0
            raise ValueError("failed prediction")
        with prediction_rules(True), mock.patch.dict(os.environ, WUJIANG_AI_PREDICTION_NATIVE="1"), prediction_session(1):
            with self.assertRaisesRegex(ValueError, "failed prediction"):
                outcomes(battle, callback)
        self.assertEqual(payload, {"values": []})
        self.assertEqual(actor.current_hp, hp)

    def test_legacy_build_metadata_does_not_probe_research_native_artifact(self):
        from wujiang.platform.ai_build import build_info
        with prediction_rules(False), mock.patch("wujiang.platform.ai_prediction_native.native_status",
                                                 side_effect=AssertionError("legacy artifact probe")):
            info = build_info()["features"]
        self.assertFalse(info["prediction_preparation_native_requested"])
        self.assertFalse(info["prediction_preparation_artifact"]["available"])

    def test_native_kernel_errors_are_visible_instead_of_returning_a_partial_score(self):
        import os
        fake = mock.Mock()
        fake.capture_inputs.side_effect = ValueError("invalid input checkpoint")
        battle, _, _ = self.fixture()
        with prediction_rules(True), mock.patch.dict(os.environ, WUJIANG_AI_PREDICTION_NATIVE="1"), \
                mock.patch("wujiang.platform.ai_prediction_native.load_kernel", return_value=fake), prediction_session(1):
            with self.assertRaisesRegex(ValueError, "invalid input checkpoint"):
                outcomes(battle, lambda: 42)

    def test_latest_prediction_reference_requires_same_checkpoint_hash_and_keeps_partial_repeat_count(self):
        import json
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from wujiang.tools import ai_baseline
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            current = root / "reports/full-ai-baseline-004/standard"
            current.mkdir(parents=True)
            for stamp, sha, count in (("001", "same", 3), ("002", "same", 2), ("003", "different", 3)):
                folder = root / "reports" / ("full-ai-baseline-" + stamp)
                (folder / "standard").mkdir(parents=True)
                ai_baseline.write(folder / "summary.json", {"build": {"id": stamp}, "status": "running"})
                records = [{"request": 1, "mode": "new", "sha256": sha, "seconds": 1, "choice": {}}] * count
                (folder / "standard/decisions.jsonl").write_text(
                    "\n".join(json.dumps(row) for row in records), encoding="utf-8")
            with mock.patch.object(ai_baseline, "ROOT", root):
                rows = ai_baseline.latest_prediction_reference(current, [{"request": 1, "sha256": "same"}])
        self.assertEqual(len(rows[1]), 2)
        self.assertEqual(rows[1][0]["reference_build"], "002")

    def test_historical_prediction_comparison_rejects_same_request_number_with_different_input(self):
        from wujiang.tools.ai_baseline import historical_expected_comparison
        old = {1: [{"sha256": "old", "seconds": 10, "choice": {}}]}
        new = [[{"request": 1, "sha256": "new", "seconds": 1, "choice": {}}]]
        result = historical_expected_comparison(old, new)
        self.assertEqual(result["requests"], [])
        self.assertIsNone(result["time_reduction_percent"])

    def area_fixture(self, count=4, *, codes=None):
        from wujiang.tactical.engine.core import QueuedAction
        from wujiang.tactical.rooms.synergy import SynergyStatus
        battle = Battle(width=12, height=10)
        source = create_hero("bard", 1)
        source.skills.clear()
        source.traits.clear()
        battle.add_unit(source, Position(4, 5))
        targets = []
        for index, code in enumerate(codes or ["bard"] * count):
            target = create_hero(code, 2)
            if codes is None:
                target.skills.clear()
                target.traits.clear()
            battle.add_unit(target, Position(5 + index % 4, 5 + index // 4))
            target.add_status(SynergyStatus("attribute", "暗", 2))
            targets.append(target)
        battle.chess_rng = random.Random(139)
        queued = QueuedAction(action_type="skill_effect", actor_id=source.unit_id,
                              display_name="期望分解维护场景", speed=1,
                              payload={"effect_code": "area_damage", "raw_damage": 1.0,
                                       "cells": [unit.position.to_dict() for unit in targets]},
                              target_unit_ids=[unit.unit_id for unit in targets])
        return battle, source, targets, queued

    def area_scores(self, battle, source, targets, queued, *, factors):
        import os
        from wujiang.tactical.rooms.ai_acceleration import _id_state
        observations = []
        before = pickle.dumps(battle), random.getstate(), battle.chess_rng.getstate(), _id_state()
        with mock.patch.dict(os.environ, {"WUJIANG_AI_EXPECTATION_FACTORS": str(int(factors))}), \
                prediction_observer(observations.append), ai.ai_enemy_view(battle, source.player_id):
            result = [ai.r21_queued_loss(battle, target, queued, observed_targets=targets) for target in targets]
        self.assertEqual((pickle.dumps(battle), random.getstate(), battle.chess_rng.getstate(), _id_state()), before)
        return result, observations[0]["statistics"]

    def test_area_factors_preserve_final_kill_utilities_and_remove_cartesian_tree(self):
        battle, source, targets, queued = self.area_fixture(8)
        reference, old = self.area_scores(battle, source, targets, queued, factors=False)
        actual, new = self.area_scores(battle, source, targets, queued, factors=True)
        for left, right in zip(actual, reference):
            self.assertAlmostEqual(left, right)
            self.assertAlmostEqual(left, .8 * 190)  # Death bonus is inside the expectation.
        self.assertEqual(new["maximum_probability_depth"], 1)
        self.assertEqual(old["maximum_probability_depth"], 8)
        self.assertEqual(new["factorized_recipient_utilities"], 8)
        self.assertLess(new["probability_forks"], old["probability_forks"] / 10)

    def test_area_factors_keep_survival_probability_correlated_with_kill_bonus(self):
        battle, source, targets, queued = self.area_fixture(2)
        targets[0].add_status(EquipmentStatus("命运纽带", "", ""))
        reference, _ = self.area_scores(battle, source, targets, queued, factors=False)
        actual, stats = self.area_scores(battle, source, targets, queued, factors=True)
        for left, right in zip(actual, reference):
            self.assertAlmostEqual(left, right)
        self.assertAlmostEqual(actual[0], .8 * (190 * 5 / 6 + 93.75 / 6))
        self.assertGreater(stats.get("factorized_utility_queries", 0), 0)

    def test_area_factors_preserve_local_status_expiry_and_defense_utility(self):
        from wujiang.tactical.engine.core import TemporaryDefenseStatus
        battle, source, targets, queued = self.area_fixture(2)
        status = TemporaryDefenseStatus("保护", 3, "")
        status.expires_after_action_token = battle._next_action_resolution_token
        targets[0].add_status(status)
        queued.payload["raw_damage"] = .25
        reference, _ = self.area_scores(battle, source, targets, queued, factors=False)
        actual, stats = self.area_scores(battle, source, targets, queued, factors=True)
        for left, right in zip(actual, reference):
            self.assertAlmostEqual(left, right)
        self.assertAlmostEqual(actual[0], 3 * 30 + .8 * 25)
        self.assertGreater(stats.get("factorized_utility_queries", 0), 0)

    def test_area_factors_preserve_probe_deaths_and_deferred_reactions(self):
        from wujiang.tactical.heroes.excel_roster import HellWarGodStatus
        battle, source, targets, queued = self.area_fixture(codes=["excel_r246", "excel_r244", "excel_r247"])
        targets[0].add_status(HellWarGodStatus())
        reference, _ = self.area_scores(battle, source, targets, queued, factors=False)
        actual, stats = self.area_scores(battle, source, targets, queued, factors=True)
        for left, right in zip(actual, reference):
            self.assertAlmostEqual(left, right)
        self.assertGreater(stats.get("factorized_utility_queries", 0), 0)
        self.assertFalse(battle.pending_followup_actions)
        self.assertFalse(battle.hell_rebirth_unit_ids)

    def test_scalar_area_observer_uses_only_its_local_outcomes(self):
        battle, source, targets, queued = self.area_fixture(8)
        observations = []
        with prediction_observer(observations.append), ai.ai_enemy_view(battle, 1):
            value = ai.r21_queued_loss(battle, targets[-1], queued)
        self.assertAlmostEqual(value, .8 * 190)
        self.assertEqual(observations[0]["statistics"]["maximum_probability_depth"], 1)
        self.assertEqual(observations[0]["statistics"]["factorized_recipient_utilities"], 1)

    def test_area_marginal_cache_reuses_overlap_without_averaging_intermediate_damage(self):
        battle, source, targets, queued = self.area_fixture(3)
        observations = []
        with prediction_observer(observations.append), ai.ai_enemy_view(battle, 1):
            first = ai.r21_queued_loss(battle, targets[0], queued, observed_targets=targets[:2])
            second = ai.r21_queued_loss(battle, targets[1], queued, observed_targets=targets[1:])
        self.assertAlmostEqual(first, .8 * 190)
        self.assertAlmostEqual(second, first)
        stats = observations[0]["statistics"]
        self.assertEqual(stats["factorized_recipient_utilities"], 4)
        self.assertGreater(stats.get("distribution_cache_hits", 0), 0)

    def test_area_factors_reject_cross_unit_damage_observers(self):
        battle, source, targets, queued = self.area_fixture(2)
        observer = CrossRecipientDamageObserver("跨目标影响")
        observer.first_unit_id, observer.second_unit_id = (unit.unit_id for unit in targets)
        source.add_status(observer)
        reference, _ = self.area_scores(battle, source, targets, queued, factors=False)
        actual, stats = self.area_scores(battle, source, targets, queued, factors=True)
        self.assertEqual(actual, reference)
        self.assertGreater(stats.get("factorization_rejected_callback", 0) + stats.get("factorization_unsafe_inspection", 0), 0)
        self.assertEqual(stats.get("factorized_utility_queries", 0), 0)

    def test_area_factors_reject_source_lifesteal_and_target_counters(self):
        for on_source, code in ((True, "吸血面具"), (False, "德古拉之爪")):
            with self.subTest(code=code):
                battle, source, targets, queued = self.area_fixture(2)
                (source if on_source else targets[0]).add_status(EquipmentStatus(code, "", ""))
                reference, _ = self.area_scores(battle, source, targets, queued, factors=False)
                actual, stats = self.area_scores(battle, source, targets, queued, factors=True)
                self.assertEqual(actual, reference)
                self.assertEqual(stats.get("factorized_utility_queries", 0), 0)

    def test_area_factors_reject_source_inside_damage_area(self):
        battle, source, targets, queued = self.area_fixture(2)
        queued.payload["cells"].append(source.position.to_dict())
        reference, _ = self.area_scores(battle, source, targets, queued, factors=False)
        actual, stats = self.area_scores(battle, source, targets, queued, factors=True)
        self.assertEqual(actual, reference)
        self.assertGreater(stats.get("factorization_rejected_source_in_area", 0), 0)

    def test_unexpected_nested_damage_aborts_projection_before_joint_fallback(self):
        import wujiang.tactical.prediction_factors as factors
        battle, source, targets, queued = self.area_fixture(2)
        targets[0].add_status(EquipmentStatus("德古拉之爪", "", ""))
        reference, _ = self.area_scores(battle, source, targets, queued, factors=False)
        # Deliberately bypass the contract to exercise its second defense.
        with mock.patch.object(factors, "_certified", return_value=True):
            actual, stats = self.area_scores(battle, source, targets, queued, factors=True)
        self.assertEqual(actual, reference)
        self.assertGreater(stats.get("factorization_runtime_fallbacks", 0), 0)
        self.assertEqual(stats.get("factorized_utility_queries", 0), 0)

    def test_area_factors_reject_changed_optional_choice_and_restore_ids(self):
        from wujiang.tactical.engine import core
        from wujiang.tactical.rooms.ai_acceleration import _id_state
        battle, source, targets, queued = self.area_fixture(2)
        ids = _id_state()
        with mock.patch.object(core.Battle, "optional_position_choice", lambda self, *args, **kwargs: None):
            actual, stats = self.area_scores(battle, source, targets, queued, factors=True)
        self.assertGreater(stats.get("factorization_rejected_changed_engine", 0), 0)
        self.assertEqual(stats.get("factorized_utility_queries", 0), 0)
        self.assertEqual(_id_state(), ids)

    def test_audited_engine_alias_keeps_original_identity_and_factor_support(self):
        from wujiang.tactical import prediction_factors as factors
        self.assertIs(Battle._canonical_basic_attack_preview, Battle.basic_attack_preview_for_payload)
        support = factors.engine_factor_support()
        self.assertTrue(all(support.values()), support)

    def test_audited_empty_trait_class_and_reviewed_hierarchies_are_supported(self):
        from wujiang.tactical import prediction_factors as factors
        from wujiang.tactical.engine.core import Trait
        self.assertTrue(factors._class_methods_original(Trait))
        support = factors.engine_factor_support()
        self.assertIn("wujiang.tactical.engine.core.Trait", support)
        self.assertIn("wujiang.tactical.heroes.mechanics.hell_rebirth.HellRebirthTrait", support)
        self.assertTrue(all(support.values()), support)

    def test_added_method_on_audited_empty_trait_class_is_rejected(self):
        from wujiang.tactical import prediction_factors as factors
        from wujiang.tactical.engine.core import Trait
        with mock.patch.object(Trait, "unreviewed_hook", lambda self: None, create=True):
            self.assertFalse(factors._class_methods_original(Trait))
            self.assertFalse(factors.engine_factor_support()["wujiang.tactical.engine.core.Trait"])

    def test_rebound_engine_alias_is_rejected_even_when_its_function_is_original(self):
        battle, source, targets, queued = self.area_fixture(2)
        with mock.patch.object(Battle, "_canonical_basic_attack_preview", Battle.current_turn_unit):
            actual, stats = self.area_scores(battle, source, targets, queued, factors=True)
        self.assertGreater(stats.get("factorization_rejected_changed_engine", 0), 0)
        self.assertEqual(stats.get("factorized_utility_queries", 0), 0)
        for value in actual:
            self.assertAlmostEqual(value, .8 * 190)

    def test_unreviewed_new_alias_is_rejected(self):
        battle, source, targets, queued = self.area_fixture(2)
        with mock.patch.object(Battle, "_unreviewed_preview_alias", Battle.basic_attack_preview_for_payload, create=True):
            _, stats = self.area_scores(battle, source, targets, queued, factors=True)
        self.assertGreater(stats.get("factorization_rejected_changed_engine", 0), 0)
        self.assertEqual(stats.get("factorized_utility_queries", 0), 0)

    def test_failed_factor_certificate_is_not_repeated_for_each_joint_probability_leaf(self):
        battle, source, targets, queued = self.area_fixture(8)
        with mock.patch.object(Battle, "optional_position_choice", lambda self, *args, **kwargs: None):
            actual, stats = self.area_scores(battle, source, targets, queued, factors=True)
        self.assertEqual(stats["factorization_rejected_changed_engine"], 1)
        self.assertEqual(stats["maximum_probability_depth"], 8)
        for value in actual:
            self.assertAlmostEqual(value, .8 * 190)

    def test_area_factors_never_replace_legacy_queries(self):
        import wujiang.tactical.prediction_factors as factors
        battle, source, targets, queued = self.area_fixture(2)
        with prediction_rules(False), mock.patch.object(factors, "expected_area_utilities",
                side_effect=AssertionError("legacy entered expectation specialization")):
            ai.r21_queued_loss(battle, targets[0], queued, observed_targets=targets)

    def focused_tool_fixture(self, root):
        import gzip
        import hashlib
        import json
        from types import SimpleNamespace
        source = root / "saved"
        source.mkdir()
        battle, actor, _ = self.fixture()
        data = pickle.dumps((battle, ([actor], "standard"), {}, random.getstate(), (100, 100)))
        (source / "one.pkl.gz").write_bytes(gzip.compress(data))
        (source / "checkpoints.json").write_text(json.dumps([{
            "request": 1, "step": 1, "board": 1, "units": 24, "actor": "bard",
            "kind": "choose_turn_bundle_action", "checkpoint": "one.pkl.gz",
            "sha256": hashlib.sha256(data).hexdigest()}]), encoding="utf-8")
        chess = SimpleNamespace(matches=[SimpleNamespace(runner=SimpleNamespace(
            battle=SimpleNamespace(units=dict.fromkeys(range(24))))) for _ in range(4)])
        return source, chess

    def settled_score(self, battle, source, queued, *, factors):
        import math
        import os
        from wujiang.tactical.rooms.ai_acceleration import _id_state
        observations = []
        before = pickle.dumps(battle), random.getstate(), battle.chess_rng.getstate(), _id_state()
        with mock.patch.dict(os.environ, {"WUJIANG_AI_EXPECTATION_FACTORS": str(int(factors))}), \
                prediction_observer(observations.append), ai.ai_enemy_view(battle, source.player_id), \
                ai.ai_probe_rollback(battle):
            state = ai.r23_action_state(battle)
            # Keep the actual prepaid resource loss inside the final utility.
            source.current_mana -= .5
            battle.pending_followup_actions.append(queued)
            if factors:
                value = ai._r23_expected_settled_value(battle, source, state)
            else:
                def full_chain():
                    while battle.pending_followup_actions:
                        battle.resolve_queued_action(battle.pending_followup_actions.popleft())
                    return ai.r23_action_delta(source, state) - 8.0
                value = math.fsum(result * probability for result, probability in outcomes(battle, full_chain))
        self.assertEqual((pickle.dumps(battle), random.getstate(), battle.chess_rng.getstate(), _id_state()), before)
        return value, observations[0]["statistics"]

    def test_settled_area_keeps_prepaid_cost_and_final_death_utility_without_cartesian_tree(self):
        battle, source, targets, queued = self.area_fixture(8)
        expected, old = self.settled_score(battle, source, queued, factors=False)
        actual, new = self.settled_score(battle, source, queued, factors=True)
        self.assertAlmostEqual(actual, expected)
        self.assertAlmostEqual(actual, 8 * .8 * 190 - .5 * 18 * 1.3 - 8)
        self.assertEqual(old["maximum_probability_depth"], 8)
        self.assertEqual(new["maximum_probability_depth"], 1)
        self.assertEqual(new["settled_factorized_utilities"], 9)
        self.assertLess(new["probability_forks"], old["probability_forks"] / 10)

    def test_settled_area_keeps_death_sweep_and_dead_source_lifesteal_in_one_group(self):
        from wujiang.tactical.rooms.synergy import SynergyStatus
        battle, source, targets, queued = self.area_fixture(codes=["excel_r246", "excel_r244", "excel_r247"])
        source.add_status(SynergyStatus("attribute", "暗", 2))
        targets[0].add_status(EquipmentStatus("吸血面具", "", ""))
        expected, _ = self.settled_score(battle, source, queued, factors=False)
        actual, stats = self.settled_score(battle, source, queued, factors=True)
        self.assertAlmostEqual(actual, expected)
        self.assertEqual(stats["settled_factorized_groups_size:2"], 1)
        self.assertEqual(stats["settled_factorized_groups_size:1"], 2)

    def test_settled_area_joins_transitive_death_sweeps_and_multiple_hits(self):
        from wujiang.tactical.heroes.excel_roster import HellWarGodRebirthTrait
        battle, source, targets, queued = self.area_fixture(codes=["excel_r246", "excel_r244"])
        source.traits.append(HellWarGodRebirthTrait().bind(source))
        targets[1].position = Position(4, 4)
        queued.payload["cells"] = [unit.position.to_dict() for unit in targets]
        expected, _ = self.settled_score(battle, source, queued, factors=False)
        actual, stats = self.settled_score(battle, source, queued, factors=True)
        self.assertAlmostEqual(actual, expected)
        self.assertEqual(stats["settled_factorized_groups_size:3"], 1)

    def test_settled_area_preserves_friendly_and_self_damage(self):
        battle, source, targets, queued = self.area_fixture(3)
        targets[0].player_id = source.player_id
        queued.payload["cells"].append(source.position.to_dict())
        expected, _ = self.settled_score(battle, source, queued, factors=False)
        actual, stats = self.settled_score(battle, source, queued, factors=True)
        self.assertAlmostEqual(actual, expected)
        self.assertGreater(stats["settled_factorized_queries"], 0)

    def test_settled_area_falls_back_for_absolute_action_token_expiry(self):
        from wujiang.tactical.engine.core import TemporaryDefenseStatus
        battle, source, targets, queued = self.area_fixture(2)
        status = TemporaryDefenseStatus("保护", 1, "")
        status.expires_after_action_token = battle._next_action_resolution_token
        targets[0].add_status(status)
        expected, _ = self.settled_score(battle, source, queued, factors=False)
        actual, stats = self.settled_score(battle, source, queued, factors=True)
        self.assertAlmostEqual(actual, expected)
        self.assertEqual(stats["factorization_rejected_settled_action_expiry"], 1)
        self.assertEqual(stats.get("settled_factorized_queries", 0), 0)

    def test_settled_area_rejects_unreviewed_heal_observers(self):
        battle, source, targets, queued = self.area_fixture(codes=["excel_r246", "excel_r244"])
        targets[0].add_status(EquipmentStatus("吸血面具", "", ""))
        source.add_status(UnreviewedHealObserver("未审计恢复"))
        _, stats = self.settled_score(battle, source, queued, factors=True)
        self.assertEqual(stats.get("factorization_rejected_callback", 0)
                         + stats.get("settled_factorization_unsafe_inspection", 0), 1)
        self.assertEqual(stats.get("settled_factorized_queries", 0), 0)

    def test_paid_skill_integration_matches_full_expected_replay_and_keeps_legacy_separate(self):
        import os
        from wujiang.tactical.heroes.excel_roster import HellDragonFireSkill
        battle, source, targets, _ = self.area_fixture(4)
        source.skills.append(HellDragonFireSkill().bind(source))
        battle.active_player, battle._exclusive_turn_unit_id, source.turn_ready = source.player_id, source.unit_id, True
        payload = {"skill_code": "hell_dragon_fire", "cells": [Position(x, y).to_dict()
                   for y in range(5, 10) for x in range(5, 10)]}
        values, observations = [], []
        before = pickle.dumps(battle), random.getstate()
        for enabled in ("0", "1"):
            with mock.patch.dict(os.environ, {"WUJIANG_AI_EXPECTATION_FACTORS": enabled}), \
                    prediction_observer(observations.append), ai.ai_enemy_view(battle, source.player_id):
                values.append(ai.r23_paid_skill_value(battle, source, payload))
        self.assertGreater(values[0], -1000)
        self.assertAlmostEqual(*values)
        self.assertGreater(observations[-1]["statistics"]["settled_factorized_queries"], 0)
        self.assertEqual((pickle.dumps(battle), random.getstate()), before)
        with prediction_rules(False), mock.patch.object(ai, "_r23_expected_settled_value",
                side_effect=AssertionError("legacy entered expected chain solver")):
            ai.r23_paid_skill_value(battle, source, payload)
        from wujiang.tactical.engine.core import BattleFieldEffect
        battle.field_effects.append(BattleFieldEffect("维护场地"))
        with ai.ai_enemy_view(battle, source.player_id), mock.patch.object(ai, "_r23_expected_settled_value",
                side_effect=AssertionError("unsupported field entered chain specialization")):
            ai.r23_paid_skill_value(battle, source, payload)

    def defense_fixture(self, code, count=3):
        from wujiang.tactical.heroes.excel_roster import (InfiniteArmorSkill, InfiniteRobeSkill,
                                                        FlyingDragonOrbSkill, HellDragonFireSkill)
        from wujiang.tactical.rooms.synergy import SynergyStatus
        battle, source, enemies, _ = self.area_fixture(count)
        source.current_hp = .75
        source.base_stats.defense = 0
        source.add_status(SynergyStatus("attribute", "暗", 5))
        kind = {"infinite_armor": InfiniteArmorSkill, "infinite_robe": InfiniteRobeSkill,
                "flying_dragon_orb": FlyingDragonOrbSkill}[code]
        source.skills.append(kind().bind(source))
        for enemy in enemies:
            enemy.base_stats.attack = 5
            enemy.base_stats.attack_range = 9
            if code == "infinite_robe":
                enemy.skills.append(HellDragonFireSkill().bind(enemy))
        return battle, source, enemies

    def defense_score(self, battle, source, code, *, factors):
        import os
        from wujiang.tactical.rooms.ai_acceleration import _id_state
        observations = []
        before = pickle.dumps(battle), random.getstate(), battle.chess_rng.getstate(), _id_state()
        with mock.patch.dict(os.environ, {"WUJIANG_AI_EXPECTATION_FACTORS": str(int(factors))}), \
                prediction_observer(observations.append), ai.ai_enemy_view(battle, source.player_id):
            value = ai.red_preparation_score(battle, source, code, ai.difficulty_profile("standard"))
        self.assertEqual((pickle.dumps(battle), random.getstate(), battle.chess_rng.getstate(), _id_state()), before)
        return value, observations[0]["statistics"]

    def test_defense_convolution_matches_full_tree_for_armor_robe_and_orb(self):
        for code in ("infinite_armor", "infinite_robe", "flying_dragon_orb"):
            with self.subTest(code=code):
                battle, source, _ = self.defense_fixture(code)
                expected, _ = self.defense_score(battle, source, code, factors=False)
                actual, stats = self.defense_score(battle, source, code, factors=True)
                self.assertAlmostEqual(actual, expected)
                self.assertEqual(stats["convolved_defense_queries"], 1)

    def test_defense_convolution_preserves_nonlinear_lethal_threshold(self):
        battle, source, _ = self.defense_fixture("infinite_armor", 6)
        expected, old = self.defense_score(battle, source, "infinite_armor", factors=False)
        actual, new = self.defense_score(battle, source, "infinite_armor", factors=True)
        self.assertAlmostEqual(actual, expected)
        self.assertAlmostEqual(actual, 6 * .75 * .5 * 130 + 90 * (1 - .5 ** 6) - 35)
        self.assertEqual(old["maximum_probability_depth"], 6)
        self.assertEqual(new["maximum_probability_depth"], 1)

    def test_defense_convolution_keeps_repeated_attack_count_as_original_multiplier(self):
        battle, source, enemies = self.defense_fixture("infinite_armor", 2)
        enemies[0].base_attack_actions_per_turn = 3
        expected, _ = self.defense_score(battle, source, "infinite_armor", factors=False)
        actual, _ = self.defense_score(battle, source, "infinite_armor", factors=True)
        self.assertAlmostEqual(actual, expected)
        self.assertAlmostEqual(actual, (3 + 1) * .75 * .5 * 130 + 90 * .75 - 35)

    def test_reviewed_physical_immunity_statuses_do_not_block_skill_factorization(self):
        from wujiang.tactical.heroes.excel_roster import FlyingDragonOrbStatus, PhysicalImmunityStatus
        battle, source, targets, queued = self.area_fixture(2)
        targets[0].add_status(FlyingDragonOrbStatus())
        targets[1].add_status(PhysicalImmunityStatus())
        expected, _ = self.settled_score(battle, source, queued, factors=False)
        actual, stats = self.settled_score(battle, source, queued, factors=True)
        self.assertAlmostEqual(actual, expected)
        self.assertEqual(stats["settled_factorized_queries"], 1)

    def test_settled_area_rejects_an_unscored_possible_chain_intermediary(self):
        from wujiang.tactical.prediction_factors import expected_settled_area_utilities
        battle, source, targets, queued = self.area_fixture(2)
        observations = []
        with prediction_observer(observations.append), ai.ai_enemy_view(battle, source.player_id), \
                ai.ai_probe_rollback(battle):
            battle.pending_followup_actions.append(queued)
            value = expected_settled_area_utilities(battle, targets, queued,
                lambda group: tuple(0.0 for _ in group), utility_key=("maintenance",))
        self.assertIsNone(value)
        self.assertEqual(observations[0]["statistics"]["factorization_rejected_settled_recipient_set"], 1)

    def test_focused_performance_tool_never_constructs_new_policy_live_fixture(self):
        import tempfile
        from pathlib import Path
        from wujiang.tools import ai_baseline
        from wujiang.tactical.prediction import rules_enabled
        policies = []
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, chess = self.focused_tool_fixture(root)
            def fixture(*args, **kwargs):
                policies.append(rules_enabled())
                return chess
            with mock.patch("wujiang.tools.ai_equivalence.create_late_chess_fixture", fixture), \
                    mock.patch("wujiang.tools.ai_equivalence.late_chess_composition", return_value={}), \
                    mock.patch("wujiang.tactical.rooms.ai_router.choose_turn_bundle_action", return_value=None), \
                    mock.patch.object(ai_baseline, "latest_prediction_reference", return_value={}):
                report = ai_baseline.run_fixture(root / "out", "standard", 17, 2, 1,
                                                 replay_from=source, focused=True)
        self.assertEqual(policies, [False, False])
        self.assertEqual(report["status"], "completed")
        self.assertTrue(report["timing_completed"])
        self.assertFalse(report["baseline_established"])
        self.assertEqual(report["live_new_baseline"]["steps"], 0)

    def test_completed_timings_survive_interruption_in_separate_profile_stage(self):
        import json
        import tempfile
        from pathlib import Path
        from wujiang.tools import ai_baseline
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, chess = self.focused_tool_fixture(root)
            with mock.patch("wujiang.tools.ai_equivalence.create_late_chess_fixture", return_value=chess), \
                    mock.patch("wujiang.tools.ai_equivalence.late_chess_composition", return_value={}), \
                    mock.patch("wujiang.tactical.rooms.ai_router.choose_turn_bundle_action", return_value=None), \
                    mock.patch.object(ai_baseline, "latest_prediction_reference", return_value={}), \
                    mock.patch.object(ai_baseline.cProfile, "Profile") as profiler:
                profiler.return_value.runcall.side_effect = KeyboardInterrupt()
                with self.assertRaises(KeyboardInterrupt):
                    ai_baseline.run_fixture(root / "out", "standard", 17, 2, 1,
                                            replay_from=source, focused=True)
            report = json.loads((root / "out/result.json").read_text(encoding="utf-8"))
        self.assertTrue(report["timing_completed"])
        self.assertIn("time_reduction_percent", report)
        self.assertEqual(report["timing_progress"]["new"]["completed_requests"], 1)

    def test_prebuilt_query_contracts_match_source_and_reject_a_changed_artifact(self):
        from wujiang.tactical import prediction_contracts as contracts, prediction_factors as factors
        from pathlib import Path
        contracts.load_contracts.cache_clear()
        self.assertIsNotNone(contracts.load_contracts())
        factors._source_code.cache_clear()
        with mock.patch("builtins.compile", side_effect=AssertionError("unexpected runtime source compile")):
            self.assertTrue(factors._original(Battle.resolve_damage))
            self.assertIn("Trait", factors._source_code("wujiang.tactical.engine.core"))
        contracts.load_contracts.cache_clear()
        with mock.patch.object(Path, "read_bytes", return_value=b"{}"):
            self.assertIsNone(contracts.load_contracts())
        contracts.load_contracts.cache_clear()
        factors._source_code.cache_clear()
        self.assertIsNotNone(contracts.load_contracts())

    def test_self_restoring_query_shares_checkpoint_without_changing_expectation_or_state(self):
        import os
        from wujiang.tactical import prediction
        battle, source, enemy = self.fixture()
        before = pickle.dumps(battle), random.getstate()
        values, observations = [], []
        def operation():
            enemy.current_hp = .25 if prediction_random.random() < .3 else .75
        for enabled in ("0", "1"):
            with mock.patch.dict(os.environ, {"WUJIANG_AI_STRUCTURAL_BATCH": enabled}), \
                    prediction_observer(observations.append), ai.ai_enemy_view(battle, 1):
                prediction._saved_graph(battle)
                values.append(ai.reviewed_r16_damage_result(battle, source, operation))
            self.assertEqual((pickle.dumps(battle), random.getstate()), before)
        self.assertEqual(*values)
        self.assertEqual(observations[0]["statistics"].get("shared_inner_checkpoints", 0), 0)
        self.assertGreater(observations[1]["statistics"]["shared_inner_checkpoints"], 0)

    def test_shared_inner_checkpoint_restores_before_an_exception_escapes(self):
        from wujiang.tactical import prediction
        battle, source, enemy = self.fixture()
        before = pickle.dumps(battle), random.getstate()
        def operation():
            enemy.current_hp = .125
            battle.chess_rng.random()
            raise RuntimeError("maintenance probe failure")
        observations = []
        with prediction_observer(observations.append), ai.ai_enemy_view(battle, 1):
            prediction._saved_graph(battle)
            with self.assertRaisesRegex(RuntimeError, "maintenance probe failure"):
                ai.reviewed_r16_damage_result(battle, source, operation)
        self.assertGreater(observations[0]["statistics"]["shared_inner_checkpoints"], 0)
        self.assertEqual((pickle.dumps(battle), random.getstate()), before)

    def test_shared_checkpoint_keeps_outer_guard_for_an_unknown_argument_copier(self):
        from wujiang.tactical import prediction
        from wujiang.tactical.engine import core
        battle, source, _ = self.fixture()
        before = pickle.dumps(battle)
        original = core._rollback_copy_value
        def custom_copy(value):
            battle.custom_copy_seen = True
            return original(value)
        observations = []
        with prediction_observer(observations.append), ai.ai_enemy_view(battle, 1):
            prediction._saved_graph(battle)
            with mock.patch.object(core, "_rollback_copy_value", custom_copy):
                ai.reviewed_r16_damage_result(battle, source, lambda: None)
        self.assertEqual(observations[0]["statistics"].get("shared_inner_checkpoints", 0), 0)
        self.assertEqual(pickle.dumps(battle), before)

    def test_settled_projection_combines_unaffected_units_and_keeps_prepaid_source_cost(self):
        import os
        battle, source, targets, queued = self.area_fixture(6)
        queued.payload["cells"] = [unit.position.to_dict() for unit in targets[:2]]
        expected, _ = self.settled_score(battle, source, queued, factors=False)
        results = []
        for enabled in ("0", "1"):
            with mock.patch.dict(os.environ, {"WUJIANG_AI_STRUCTURAL_BATCH": enabled}):
                results.append(self.settled_score(battle, source, queued, factors=True))
        self.assertAlmostEqual(results[0][0], expected)
        self.assertAlmostEqual(results[1][0], expected)
        self.assertEqual(results[0][1]["settled_factorized_groups"], 7)
        self.assertEqual(results[1][1]["settled_factorized_groups"], 3)
        self.assertEqual(results[1][1]["settled_unaffected_groups_shared"], 5)
        self.assertGreater(results[1][1]["empty_linked_sync_passes_shared"], 0)

    def test_settled_projection_rejects_custom_linked_sync_and_changed_context_constructor(self):
        from wujiang.tactical.engine import core
        battle, source, targets, queued = self.area_fixture(2)
        expected, _ = self.settled_score(battle, source, queued, factors=False)
        constructor = core.DamageContext.__init__
        def changed_constructor(ctx, *args, **kwargs):
            constructor(ctx, *args, **kwargs)
        with mock.patch.object(core.DamageContext, "__init__", changed_constructor):
            actual, stats = self.settled_score(battle, source, queued, factors=True)
        self.assertAlmostEqual(actual, expected)
        self.assertEqual(stats.get("empty_linked_sync_passes_shared", 0), 0)
        targets[0].add_status(UnreviewedLinkedSync("自定义同步"))
        expected, _ = self.settled_score(battle, source, queued, factors=False)
        actual, stats = self.settled_score(battle, source, queued, factors=True)
        self.assertAlmostEqual(actual, expected)
        self.assertEqual(stats.get("settled_factorized_queries", 0), 0)

    def test_native_observation_plan_invalidates_changed_function_code_and_tracks_rng_and_positions(self):
        from wujiang.tactical import prediction
        from wujiang.tactical.prediction_keys import graph_observation_stamp
        from wujiang.tactical.engine.core import Unit
        battle, actor, _ = self.fixture()
        with ai.ai_enemy_view(battle, 1):
            saved = prediction._saved_graph(battle)
            stamp = graph_observation_stamp(saved)
            self.assertIsNotNone(saved.observation_protocol_plan)
            code = Unit.__getstate__.__code__
            try:
                Unit.__getstate__.__code__ = (lambda unit: {}).__code__
                self.assertIsNone(graph_observation_stamp(saved))
            finally:
                Unit.__getstate__.__code__ = code
            self.assertEqual(stamp, graph_observation_stamp(saved))
            battle.chess_rng.gauss_next = .125
            self.assertNotEqual(stamp, graph_observation_stamp(saved))
            battle.chess_rng.gauss_next = None
            x = actor.position.x
            try:
                object.__setattr__(actor.position, "x", x + 1)
                self.assertNotEqual(stamp, graph_observation_stamp(saved))
            finally:
                object.__setattr__(actor.position, "x", x)

    def test_metadata_filter_schema_invalidates_class_changes_and_dynamic_imports(self):
        from wujiang.tactical import prediction, prediction_inline, prediction_structure
        from wujiang.tactical.engine.core import Unit
        from wujiang.tactical.heroes.mechanics import wuchang
        battle, actor, _ = self.fixture()
        function = ai.nova_paid_action_adjustment.__wrapped__
        with ai.ai_enemy_view(battle, 1):
            prediction._saved_graph(battle)
            session = prediction._session.get()
            self.assertTrue(prediction_inline.can_inline(function, battle, (actor, "missile", {}), {}, session))
            with mock.patch.object(Unit, "hero_code", property(lambda unit: "excel_r305"), create=True):
                self.assertFalse(prediction_inline.can_inline(function, battle, (actor, "missile", {}), {}, session))
            imports = ((wuchang.__name__, ("WuchangMistField",)),)
            self.assertTrue(prediction_structure.inert_imports(ai.wuchang_action_success_probability.__wrapped__, imports))
            with mock.patch.object(wuchang, "__getattr__", lambda name: None, create=True):
                self.assertFalse(prediction_structure.inert_imports(ai.wuchang_action_success_probability.__wrapped__, imports))

    def test_shared_action_argument_preserves_payload_copy_and_custom_container_fallback(self):
        from wujiang.tactical import prediction
        from wujiang.tactical.engine import core
        from wujiang.tactical.prediction_structure import copy_scoring_arguments
        battle, actor, _ = self.fixture()
        payload = {"cells": [{"x": 1, "y": 2}]}
        action = {"code": "missile", "payload": payload}
        function = ai.skill_payload_has_effective_enemy_impact.__wrapped__
        with mock.patch.dict("os.environ", {"WUJIANG_AI_EXPECTATION_METHOD": "enumerate"}), ai.ai_enemy_view(battle, 1):
            copied = copy_scoring_arguments(function, (actor, action, payload), core._rollback_copy_value)
            self.assertIs(copied[1], action)
            self.assertIsNot(copied[2], payload)
            self.assertIsNot(copied[2]["cells"], payload["cells"])
            with mock.patch.object(ai, "skill_from_ai_action", return_value=None):
                copied_with_replaced_resolver = copy_scoring_arguments(
                    function, (actor, action, payload), core._rollback_copy_value)
                self.assertIsNot(copied_with_replaced_resolver[1], action)
            class CustomDict(dict):
                pass
            custom = CustomDict(action)
            sentinel = object()
            with mock.patch.object(prediction, "_copy_arguments", return_value=sentinel) as fallback:
                self.assertIs(copy_scoring_arguments(function, (actor, custom, payload), lambda value: value), sentinel)
                fallback.assert_called_once()
            for function, action in (
                    (ai.skill_payload_has_effective_enemy_impact.__wrapped__, {"code": "heaven_punishment"}),
                    (ai.skill_payload_requires_enemy_impact.__wrapped__, {"code": "unknown", "target_mode": "cell"})):
                with mock.patch.object(prediction, "_copy_arguments", return_value=sentinel) as fallback:
                    self.assertIs(copy_scoring_arguments(function, (actor, action, payload), lambda value: value), sentinel)
                    fallback.assert_called_once()

    def test_certificate_storage_is_charged_to_the_graph_memory_limit(self):
        from wujiang.tactical import prediction
        from wujiang.tactical.prediction_inline import _schema
        battle, _, _ = self.fixture()
        with ai.ai_enemy_view(battle, 1):
            saved = prediction._saved_graph(battle)
            session = prediction._session.get()
            before = session["graph_cache_bytes"]
            _schema(saved, "maintenance", session, (Battle.all_units,), lambda: True)
            self.assertGreater(session["graph_cache_bytes"], before)
            self.assertLessEqual(session["graph_cache_bytes"], 8 * 1024 * 1024)
            size = saved.bytes
            self.assertFalse(prediction.reserve_graph_metadata(saved, "oversized", 8 * 1024 * 1024))
            self.assertEqual(saved.bytes, size)

    def test_focused_tool_reports_unavailable_factor_bindings_before_timing_any_ai(self):
        import json
        import tempfile
        from pathlib import Path
        from wujiang.tools import ai_baseline
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, chess = self.focused_tool_fixture(root)
            with mock.patch("wujiang.tools.ai_equivalence.create_late_chess_fixture", return_value=chess), \
                    mock.patch("wujiang.tools.ai_equivalence.late_chess_composition", return_value={}), \
                    mock.patch("wujiang.tactical.rooms.ai_router.choose_turn_bundle_action") as chooser, \
                    mock.patch.object(ai_baseline, "latest_prediction_reference", return_value={}), \
                    mock.patch("wujiang.tactical.prediction_factors.engine_factor_support", return_value={"Battle": False}):
                with self.assertRaisesRegex(ValueError, "no timing was started"):
                    ai_baseline.run_fixture(root / "out", "standard", 17, 2, 1,
                                            replay_from=source, focused=True)
                chooser.assert_not_called()
            report = json.loads((root / "out/result.json").read_text(encoding="utf-8"))
        self.assertEqual(report["prediction_factor_engine_support"], {"Battle": False})
        self.assertEqual(report["status"], "error")
        self.assertNotIn("timing", report)


if __name__ == "__main__":
    unittest.main()
