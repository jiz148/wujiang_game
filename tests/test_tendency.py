from __future__ import annotations

import random
import unittest

from types import SimpleNamespace

from wujiang.tactical.rooms.tendency.decide import SAFE_CELL_SKILLS, _skill_payload, defense_is_hopeless, pick_intent
from wujiang.tactical.rooms.tendency.heroes import INTENTS, POLICIES, ellie_weights, elite_soldier_weights
from wujiang.tactical.rooms.tendency.log import SCHEMA
from wujiang.tactical.rooms.tendency.situation import Situation


def _situation(**overrides) -> Situation:
    payload = dict(
        hero_code="ellie",
        hp_ratio=1.0,
        mana=5.0,
        attack=2.0,
        attack_range=1,
        enemies_alive=1,
        allies_alive=1,
        nearest_enemy_distance=4,
        weakest_enemy_hp_ratio=1.0,
        lowest_ally_hp_ratio=1.0,
        outnumbered=False,
        stealthed=False,
        can_attack_enemy=True,
        enemy_skill_codes=("curse",),
        ally_skill_codes=(),
        self_skill_codes=("crystal_ball",),
    )
    payload.update(overrides)
    return Situation(**payload)


class TendencyDecisionTests(unittest.TestCase):
    def test_clear_gap_stays_on_the_leading_intent(self) -> None:
        weights = {name: 0.1 for name in INTENTS}
        weights["survive"] = 3.0
        intent, sampled = pick_intent(weights, random.Random(1))
        self.assertEqual(intent, "survive")
        self.assertFalse(sampled)

    def test_close_weights_can_vary(self) -> None:
        weights = {name: 1.0 for name in INTENTS}
        seen = {pick_intent(weights, random.Random(seed))[0] for seed in range(30)}
        self.assertGreater(len(seen), 1)

    def test_ellie_hurt_and_cornered_leans_survive(self) -> None:
        calm = ellie_weights(_situation())
        hurt = ellie_weights(_situation(hp_ratio=0.2, nearest_enemy_distance=1))
        self.assertGreater(hurt["survive"], calm["survive"])

    def test_elite_soldier_backs_off_when_someone_is_close(self) -> None:
        far = elite_soldier_weights(_situation(hero_code="elite_soldier", attack_range=14, nearest_enemy_distance=8))
        close = elite_soldier_weights(_situation(hero_code="elite_soldier", attack_range=14, nearest_enemy_distance=1))
        self.assertGreater(close["survive"] + close["spacing"], far["survive"] + far["spacing"])

    def test_log_schema_marks_intent_as_the_training_label(self) -> None:
        self.assertEqual(SCHEMA, 1)

    def test_a_wall_of_defense_is_not_a_basic_attack_target(self) -> None:
        self.assertTrue(defense_is_hopeless(4, 99))
        self.assertFalse(defense_is_hopeless(4, 6))

    def test_first_twenty_implemented_heroes_have_tendencies(self) -> None:
        expected = [
            "ellie", "dark_human", "fire_funeral", "elite_soldier", "bard",
            "element_hunter", "undead_king_lina", "rock_god", "doomlight_dragon", "masamune",
            "jade", "n", "blood_eater", "li", "chanter",
            "erasure_apostle", "dragon_rider", "soul_wraith", "excel_r020", "excel_r021",
        ]
        self.assertEqual(len(expected), 20)
        for code in expected:
            self.assertIn(code, POLICIES)

    def test_every_implemented_excel_hero_has_a_tendency(self) -> None:
        from wujiang.tactical.heroes.excel_roster import IMPLEMENTED_EXCEL_HERO_CODES

        missing = sorted(set(IMPLEMENTED_EXCEL_HERO_CODES) - set(POLICIES))
        self.assertEqual(missing, [])

    def test_skill_payload_uses_the_pattern_nearer_the_enemy(self) -> None:
        enemy = SimpleNamespace(alive=True, banished=False, player_id=2, position=SimpleNamespace(x=4, y=4))
        actor = SimpleNamespace(unit_id="hero", player_id=1)
        battle = SimpleNamespace(units={"enemy": enemy})
        far = [{"x": 0, "y": 0}, {"x": 1, "y": 0}]
        near = [{"x": 3, "y": 4}, {"x": 4, "y": 4}]
        action = {
            "code": "pierce",
            "target_mode": "cell",
            "preview": {"selection": {"patterns": [far, near]}},
        }
        payload = _skill_payload(battle, actor, action)
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["cells"], near)
        self.assertEqual(payload["declared_area_cells"], near)
        self.assertEqual(payload["cells"], payload["declared_area_cells"])
        self.assertEqual((payload["x"], payload["y"]), (4, 4))
        self.assertEqual(payload["type"], "skill")
        self.assertEqual(payload["unit_id"], "hero")
        self.assertEqual(payload["skill_code"], "pierce")

    def test_skill_payload_keeps_the_late_pattern_that_covers_the_enemy(self) -> None:
        enemy = SimpleNamespace(alive=True, banished=False, player_id=2, position=SimpleNamespace(x=4, y=4))
        actor = SimpleNamespace(unit_id="hero", player_id=1)
        battle = SimpleNamespace(units={"enemy": enemy})
        missed = [[{"x": index, "y": 0}] for index in range(12)]
        covering = [{"x": 4, "y": 4}]
        patterns = missed + [covering]
        self.assertEqual(len(patterns), 13)
        action = {
            "code": "zone",
            "target_mode": "cell",
            "preview": {"selection": {"patterns": patterns}},
        }
        payload = _skill_payload(battle, actor, action)
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["cells"], covering)
        self.assertEqual(payload["declared_area_cells"], covering)
        self.assertEqual((payload["x"], payload["y"]), (4, 4))
        self.assertEqual(payload["skill_code"], "zone")

    def test_skill_payload_uses_the_choice_nearer_the_enemy(self) -> None:
        enemy = SimpleNamespace(alive=True, banished=False, player_id=2, position=SimpleNamespace(x=0, y=4))
        actor = SimpleNamespace(unit_id="hero", player_id=1)
        battle = SimpleNamespace(units={"enemy": enemy})
        east = [{"x": 5, "y": 0}]
        north = [{"x": 0, "y": 3}]
        action = {
            "code": "deadly_bow",
            "target_mode": "cell",
            "preview": {
                "selection": {
                    "mode": "choice_pattern",
                    "choices": [
                        {"code": "east", "patterns": [east]},
                        {"code": "north", "patterns": [north]},
                    ],
                },
            },
        }
        payload = _skill_payload(battle, actor, action)
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["choice_code"], "north")
        self.assertEqual(payload["cells"], north)
        self.assertEqual(payload["declared_area_cells"], north)
        self.assertEqual((payload["x"], payload["y"]), (0, 3))
        self.assertEqual(payload["type"], "skill")
        self.assertEqual(payload["unit_id"], "hero")
        self.assertEqual(payload["skill_code"], "deadly_bow")

    def test_skill_payload_casts_a_bare_cell_outside_the_dash_list(self) -> None:
        enemy = SimpleNamespace(alive=True, banished=False, player_id=2, position=SimpleNamespace(x=4, y=4))
        actor = SimpleNamespace(unit_id="hero", player_id=1)
        battle = SimpleNamespace(units={"enemy": enemy})
        code = "waypoint"
        self.assertNotIn(code, SAFE_CELL_SKILLS)
        action = {
            "code": code,
            "target_mode": "cell",
            "preview": {"cells": [{"x": 0, "y": 0}, {"x": 4, "y": 3}]},
        }
        payload = _skill_payload(battle, actor, action)
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual((payload["x"], payload["y"]), (4, 3))
        self.assertEqual(payload["skill_code"], code)
        self.assertEqual(payload["type"], "skill")
        self.assertEqual(payload["unit_id"], "hero")

    def test_skill_payload_refuses_an_undrawn_pattern(self) -> None:
        enemy = SimpleNamespace(alive=True, banished=False, player_id=2, position=SimpleNamespace(x=1, y=1))
        actor = SimpleNamespace(unit_id="hero", player_id=1)
        battle = SimpleNamespace(units={"enemy": enemy})
        for selection in ({"mode": "pattern_cells"}, {"mode": "pattern_cells", "patterns": []}):
            action = {
                "code": "leap",
                "target_mode": "cell",
                "preview": {"cells": [{"x": 1, "y": 1}, {"x": 2, "y": 2}], "selection": selection},
            }
            self.assertIsNone(_skill_payload(battle, actor, action))

    def test_skill_payload_fills_a_listed_direction_toward_the_enemy(self) -> None:
        enemy = SimpleNamespace(alive=True, banished=False, player_id=2, position=SimpleNamespace(x=0, y=4))
        actor = SimpleNamespace(unit_id="hero", player_id=1, position=SimpleNamespace(x=0, y=0))
        battle = SimpleNamespace(units={"enemy": enemy})
        action = {
            "code": "push",
            "target_mode": "cell",
            "direction_mode": "required",
            "preview": {
                "cells": [{"x": 3, "y": 0}],
                "selection": {"directions": [{"dx": 1, "dy": 0}, {"dx": 0, "dy": 1}]},
            },
        }
        payload = _skill_payload(battle, actor, action)
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["direction"], {"dx": 0, "dy": 1})
        self.assertEqual(payload["choice_code"], {"dx": 0, "dy": 1})
        self.assertEqual((payload["x"], payload["y"]), (3, 0))

    def test_skill_payload_skips_when_a_required_direction_is_not_listed(self) -> None:
        enemy = SimpleNamespace(alive=True, banished=False, player_id=2, position=SimpleNamespace(x=1, y=1))
        actor = SimpleNamespace(unit_id="hero", player_id=1, position=SimpleNamespace(x=0, y=0))
        battle = SimpleNamespace(units={"enemy": enemy})
        action = {
            "code": "leap",
            "target_mode": "cell",
            "direction_mode": "required",
            "preview": {"cells": [{"x": 1, "y": 1}]},
        }
        self.assertIsNone(_skill_payload(battle, actor, action))

    def test_excel_r222_full_health_does_not_lead_with_pressure(self) -> None:
        from wujiang.tactical.rooms.tendency.heroes import weights_for

        weights = weights_for(
            Situation(
                hero_code="excel_r222",
                hp_ratio=1,
                mana=0,
                attack=1,
                attack_range=1,
                enemies_alive=1,
                allies_alive=1,
                nearest_enemy_distance=6,
                weakest_enemy_hp_ratio=None,
                lowest_ally_hp_ratio=None,
                outnumbered=False,
                stealthed=False,
                can_attack_enemy=False,
                enemy_skill_codes=(),
                ally_skill_codes=(),
                self_skill_codes=(),
            )
        )
        top = max(weights, key=lambda name: weights[name])
        self.assertNotEqual(top, "pressure")

    def test_excel_r118_full_health_leads_with_spacing(self) -> None:
        from wujiang.tactical.heroes.registry import create_hero
        from wujiang.tactical.rooms.tendency.heroes import weights_for

        attack = float(create_hero("excel_r118", 1).base_stats.attack)
        weights = weights_for(
            Situation(
                hero_code="excel_r118",
                hp_ratio=1,
                mana=0,
                attack=attack,
                attack_range=1,
                enemies_alive=1,
                allies_alive=1,
                nearest_enemy_distance=6,
                weakest_enemy_hp_ratio=None,
                lowest_ally_hp_ratio=None,
                outnumbered=False,
                stealthed=False,
                can_attack_enemy=False,
                enemy_skill_codes=(),
                ally_skill_codes=(),
                self_skill_codes=(),
            )
        )
        top = max(weights, key=lambda name: weights[name])
        self.assertEqual(top, "spacing")
