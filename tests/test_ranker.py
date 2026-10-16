import unittest

from wujiang.tactical.rooms.imitation.ranker import LegalRanker
from wujiang.tactical.rooms.imitation.visible import INPUT_DIM, pair_features, visible_situation


def _situation(mana: float, ready: list[str]) -> dict:
    return visible_situation(
        {
            "round_number": 1,
            "units": [
                {
                    "id": "self",
                    "player_id": 1,
                    "alive": True,
                    "hero_code": "excel_r120",
                    "position": {"x": 1, "y": 1},
                    "hp": 10,
                    "max_hp": 10,
                    "mana": mana,
                    "max_mana": 4,
                    "stats": {"attack": 6, "defense": 4, "speed": 0, "attack_range": 1},
                    "skills": [{
                        "code": "pierce",
                        "timing": "active",
                        "passive": False,
                        "mana_cost": 1,
                        "cooldown_remaining": 0,
                    }],
                    "traits": [{"name": "以魔代步", "description": "每格消耗 1 魔"}],
                    "statuses": [],
                },
                {
                    "id": "foe",
                    "player_id": 2,
                    "alive": True,
                    "position": {"x": 2, "y": 1},
                    "hp": 3,
                    "max_hp": 10,
                    "mana": 0,
                    "max_mana": 1,
                    "stats": {"attack": 1, "defense": 1, "speed": 1, "attack_range": 1},
                    "statuses": [],
                },
            ],
        },
        {
            "id": "self",
            "player_id": 1,
            "alive": True,
            "hero_code": "excel_r120",
            "position": {"x": 1, "y": 1},
            "hp": 10,
            "max_hp": 10,
            "mana": mana,
            "max_mana": 4,
            "stats": {"attack": 6, "defense": 4, "speed": 0, "attack_range": 1},
            "skills": [{
                "code": "pierce",
                "timing": "active",
                "passive": False,
                "mana_cost": 1,
                "cooldown_remaining": 0,
            }],
            "traits": [{"name": "以魔代步", "description": "每格消耗 1 魔"}],
            "statuses": [],
        },
    )


class VisibleSituationTests(unittest.TestCase):
    def test_enemy_stealth_is_left_out_of_the_visible_board(self) -> None:
        situation = visible_situation(
            {
                "units": [
                    {"id": "self", "player_id": 1, "alive": True, "position": {"x": 0, "y": 0}, "hp": 1, "max_hp": 1, "stats": {}},
                    {"id": "friend", "player_id": 1, "alive": True, "position": {"x": 1, "y": 0}, "hp": 1, "max_hp": 1, "stats": {}, "statuses": [{"name": "隐身"}]},
                    {"id": "hidden", "player_id": 2, "alive": True, "position": {"x": 3, "y": 0}, "hp": 1, "max_hp": 1, "stats": {}, "statuses": [{"name": "隐身"}]},
                    {"id": "seen", "player_id": 2, "alive": True, "position": {"x": 4, "y": 0}, "hp": 1, "max_hp": 1, "stats": {}},
                ],
            },
            {"id": "self", "player_id": 1, "alive": True, "position": {"x": 0, "y": 0}, "hp": 1, "max_hp": 1, "stats": {}, "skills": [], "traits": [], "statuses": []},
        )
        distances = [item["distance"] for item in situation["enemies"]]
        self.assertEqual(situation["ally_count"], 1)
        self.assertEqual(situation["enemy_count"], 1)
        self.assertEqual(distances, [4])

    def test_paid_move_is_marked_on_the_actor(self) -> None:
        situation = _situation(4, ["pierce"])
        self.assertEqual(situation["actor"]["move_mana_per_cell"], 1.0)
        self.assertEqual(len(pair_features(situation, {"verb": "move", "mana_cost": 1, "enemy_targets": 0, "cells": 3})), INPUT_DIM)


class LegalRankerTests(unittest.TestCase):
    def test_ranker_learns_to_prefer_the_ready_skill(self) -> None:
        ready = _situation(4, ["pierce"])
        empty = _situation(0, [])
        move = {"verb": "move", "mana_cost": 1, "enemy_targets": 0, "cells": 4}
        skill = {"verb": "skill:pierce", "mana_cost": 1, "enemy_targets": 1, "cells": 1}
        groups = []
        for _ in range(30):
            groups.append((ready, [move, skill], 1))
            groups.append((empty, [move, skill], 0))
        model = LegalRanker()
        model.fit(groups, epochs=25, lr=0.08, skill_weight=1.0)
        self.assertEqual(int(model.score_candidates(ready, [move, skill]).argmax()), 1)
        self.assertEqual(int(model.score_candidates(empty, [move, skill]).argmax()), 0)
