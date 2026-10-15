import unittest

from wujiang.tactical.rooms.imitation.dataset import FEATURE_NAMES, action_label, situation_features


class ImitationDatasetTests(unittest.TestCase):
    def test_features_follow_the_named_layout(self) -> None:
        actor = {
            "id": "hero-1",
            "player_id": 1,
            "alive": True,
            "position": {"x": 1, "y": 1},
            "hp": 1,
            "max_hp": 2,
            "mana": 1,
            "max_mana": 4,
            "stats": {"attack": 3, "defense": 1, "speed": 2, "attack_range": 1, "max_mana": 4},
            "statuses": [],
            "move_used": False,
            "attacks_used": 0,
            "performed_active_skill": False,
        }
        enemy = {**actor, "id": "hero-2", "player_id": 2, "position": {"x": 4, "y": 1}, "hp": 1, "max_hp": 1}
        features = situation_features(actor, [actor, enemy], {"round_number": 2})
        self.assertEqual(len(features), len(FEATURE_NAMES))
        self.assertEqual(features[FEATURE_NAMES.index("nearest_enemy_distance")], 3)

    def test_a_position_change_is_a_move(self) -> None:
        before = {"position": {"x": 1, "y": 1}, "attacks_used": 0, "performed_active_skill": False}
        after = {"position": {"x": 2, "y": 1}, "attacks_used": 0, "performed_active_skill": False}
        self.assertEqual(action_label(before, after, ["某人 移动了。"]), "move")

    def test_ending_the_turn_is_not_called_a_move(self) -> None:
        before = {"position": {"x": 1, "y": 1}, "attacks_used": 0, "performed_active_skill": False}
        self.assertEqual(action_label(before, None, ["某人 结束了自己的回合。"]), "end_turn")


if __name__ == "__main__":
    unittest.main()
