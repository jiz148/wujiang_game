import unittest

from wujiang.tactical.rooms.imitation.dataset import FEATURE_COUNT, LABELS, examples_from_replay, situation_vector
from wujiang.tactical.rooms.imitation.service import summarize


class ImitationDatasetTests(unittest.TestCase):
    def test_situation_vector_has_a_fixed_length(self) -> None:
        actor = {
            "id": "hero-1",
            "player_id": 1,
            "alive": True,
            "position": {"x": 1, "y": 1},
            "hp": 1,
            "max_hp": 2,
            "mana": 1,
            "max_mana": 4,
            "stats": {"attack": 3, "defense": 1, "speed": 2, "attack_range": 1},
            "statuses": [],
        }
        enemy = {**actor, "id": "hero-2", "player_id": 2, "position": {"x": 4, "y": 1}, "hp": 1, "max_hp": 1}
        features = situation_vector({"units": [actor, enemy], "round_number": 2, "active_turn_unit_id": "hero-1"}, "hero-1")
        self.assertEqual(len(features), FEATURE_COUNT)
        self.assertIsNotNone(features)

    def test_a_position_change_becomes_a_move_example(self) -> None:
        actor = {
            "id": "hero-1",
            "player_id": 1,
            "alive": True,
            "position": {"x": 1, "y": 1},
            "hp": 1,
            "max_hp": 1,
            "mana": 1,
            "max_mana": 1,
            "stats": {"attack": 1, "defense": 1, "speed": 1, "attack_range": 1},
            "statuses": [],
        }
        moved = {**actor, "position": {"x": 2, "y": 1}}
        rows = examples_from_replay({
            "steps": [
                {"reason": "battle_start", "spectator_battle": {"units": [actor], "active_turn_unit_id": "hero-1", "logs": []}},
                {"reason": "ai_turn", "spectator_battle": {"units": [moved], "active_turn_unit_id": "hero-1", "logs": ["某人 移动了。"]}},
            ],
        })
        self.assertEqual(rows, [(rows[0][0], "move")])
        self.assertIn("move", LABELS)

    def test_summary_counts_labels(self) -> None:
        report = summarize([("a.json", [0.0] * FEATURE_COUNT, "move"), ("b.json", [0.0] * FEATURE_COUNT, "end")])
        self.assertEqual(report["replays"], 2)
        self.assertEqual(report["counts"]["move"], 1)
        self.assertEqual(report["counts"]["end"], 1)


if __name__ == "__main__":
    unittest.main()
