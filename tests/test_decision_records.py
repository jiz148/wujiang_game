import unittest

from wujiang.tactical.rooms.imitation.decisions import decisions_from_replay


def _unit(**overrides):
    unit = {
        "id": "hero-1",
        "hero_code": "excel_r001",
        "player_id": 1,
        "alive": True,
        "banished": False,
        "position": {"x": 1, "y": 1},
        "hp": 8,
        "max_hp": 10,
        "mana": 3,
        "max_mana": 4,
        "stats": {"attack_range": 2},
        "skills": [{
            "code": "pierce",
            "name": "穿刺",
            "timing": "active",
            "target_mode": "cell",
            "passive": False,
            "mana_cost": 1,
            "cooldown_remaining": 0,
            "uses_this_battle": 0,
        }],
    }
    unit.update(overrides)
    return unit


class DecisionRecordTests(unittest.TestCase):
    def test_visual_event_names_the_skill_and_checks_the_legal_list(self) -> None:
        actor = _unit()
        enemy = _unit(id="hero-2", player_id=2, position={"x": 4, "y": 1}, hp=5, skills=[])
        before_actor = actor
        after_actor = _unit(mana=2, skills=[{**actor["skills"][0], "uses_this_battle": 1}])
        replay = {
            "steps": [
                {
                    "reason": "battle_start",
                    "omniscient_battle": {
                        "round_number": 1,
                        "active_turn_unit_id": "hero-1",
                        "units": [before_actor, enemy],
                        "visual_events": [],
                    },
                    "seat_views": {
                        "1": {
                            "active_units": [{
                                "unit_id": "hero-1",
                                "actions": {"actions": [
                                    {"kind": "move", "code": "move", "available": True},
                                    {"kind": "skill", "code": "pierce", "available": True},
                                    {"kind": "skill", "code": "stillness", "available": False},
                                ]},
                            }],
                        },
                    },
                },
                {
                    "reason": "ai_turn",
                    "omniscient_battle": {
                        "round_number": 1,
                        "active_turn_unit_id": "hero-1",
                        "units": [after_actor, enemy],
                        "visual_events": [{
                            "id": 1,
                            "kind": "skill",
                            "action_type": "skill",
                            "action_code": "pierce",
                            "actor_id": "hero-1",
                            "target_unit_ids": ["hero-2"],
                            "target_cells": [{"x": 4, "y": 1}],
                        }],
                    },
                },
            ],
        }
        rows = decisions_from_replay(replay, source_name="sample.json")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["verb"], "skill:pierce")
        self.assertTrue(row["on_skill_card"])
        self.assertTrue(row["chosen_was_legal"])
        self.assertEqual(row["legal_verbs"], ["move", "skill:pierce"])
        self.assertEqual(row["target_cells"], [{"x": 4, "y": 1}])
        self.assertIn("pierce", row["situation"]["ready_skills"])

    def test_skill_use_on_the_card_labels_a_replay_without_events(self) -> None:
        actor = _unit()
        after = _unit(skills=[{**actor["skills"][0], "uses_this_battle": 1}])
        replay = {
            "steps": [
                {"reason": "battle_start", "omniscient_battle": {"units": [actor], "visual_events": [], "active_turn_unit_id": "hero-1"}},
                {"reason": "ai_turn", "omniscient_battle": {"units": [after], "visual_events": [], "active_turn_unit_id": "hero-1"}},
            ],
        }
        rows = decisions_from_replay(replay)
        self.assertEqual(rows[0]["verb"], "skill:pierce")
        self.assertEqual(rows[0]["label_source"], "board")
        self.assertTrue(rows[0]["on_skill_card"])
