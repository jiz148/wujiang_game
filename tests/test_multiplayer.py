from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wujiang.web.multiplayer import RoomError, RoomRegistry  # noqa: E402
from wujiang.web.server import normalize_public_base_url  # noqa: E402


class MultiplayerRoomTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = RoomRegistry()

    def test_create_and_join_room_assign_distinct_player_seats(self) -> None:
        room, host_player_id, host_token = self.registry.create_room("Alice")
        guest_player_id, guest_token = room.join("Bob")

        self.assertEqual(host_player_id, 1)
        self.assertEqual(guest_player_id, 2)

        host_view = room.serialize_state(host_token, base_url="http://example.test")
        guest_view = room.serialize_state(guest_token, base_url="http://example.test")

        self.assertEqual(host_view["room"]["viewer_player_id"], 1)
        self.assertEqual(guest_view["room"]["viewer_player_id"], 2)
        self.assertEqual(host_view["room"]["invite_url"], f"http://example.test/?room={room.room_id}")

    def test_room_registry_lists_public_room_summaries(self) -> None:
        room, _, host_token = self.registry.create_room("Alice")
        room.join("Bob")
        room.select_hero(host_token, "ellie")

        rooms = self.registry.list_rooms(base_url="http://example.test")

        self.assertEqual(len(rooms), 1)
        summary = rooms[0]
        self.assertEqual(summary["room_id"], room.room_id)
        self.assertEqual(summary["status"], "lobby")
        self.assertFalse(summary["can_join"])
        self.assertEqual(summary["occupied_seat_count"], 2)
        self.assertEqual(summary["invite_url"], f"http://example.test/?room={room.room_id}")
        self.assertEqual(summary["seats"][0]["name"], "Alice")
        self.assertEqual(summary["seats"][1]["name"], "Bob")

    def test_room_cannot_start_until_both_players_select_heroes(self) -> None:
        room, _, host_token = self.registry.create_room("Alice")
        _, guest_token = room.join("Bob")
        room.select_hero(host_token, "ellie")

        with self.assertRaises(RoomError):
            room.start_battle(guest_token)

        room.select_hero(guest_token, "bard")
        room.start_battle(host_token)

        self.assertEqual(room.status, "battle")
        self.assertIsNotNone(room.battle)

    def test_host_can_switch_room_mode_in_lobby_and_clear_existing_choices(self) -> None:
        room, _, host_token = self.registry.create_room("Alice")
        _, guest_token = room.join("Bob")
        room.select_hero(host_token, "ellie")
        room.select_hero(guest_token, "bard")

        room.set_mode(host_token, "random")

        room_state = room.serialize_state(host_token)["room"]
        self.assertEqual(room_state["mode"], "random")
        self.assertEqual(room_state["mode_name"], "随机选人")
        self.assertIsNone(room_state["seats"][0]["hero_code"])
        self.assertIsNone(room_state["seats"][1]["hero_code"])
        self.assertTrue(room_state["can_start"])

    def test_random_mode_does_not_allow_manual_hero_selection(self) -> None:
        room, _, host_token = self.registry.create_room("Alice", mode="random")

        with self.assertRaises(RoomError):
            room.select_hero(host_token, "ellie")

    def test_random_mode_start_assigns_random_heroes_and_uses_larger_board(self) -> None:
        room, _, host_token = self.registry.create_room("Alice", mode="random")
        room.join("Bob")

        with mock.patch("wujiang.web.multiplayer.random_room_hero_codes", return_value=("bard", "dark_human")):
            room.start_battle(host_token)

        self.assertEqual(room.status, "battle")
        self.assertEqual(room.seats[1].hero_code, "bard")
        self.assertEqual(room.seats[2].hero_code, "dark_human")
        self.assertEqual(room.battle.width, 10)
        self.assertEqual(room.battle.height, 10)
        player1 = room.battle.player_units(1)[0]
        player2 = room.battle.player_units(2)[0]
        self.assertIn(player1.position.x, {1, 2})
        self.assertIn(player2.position.x, {7, 8})
        self.assertEqual(room.battle.active_player, 2)

    def test_random_mode_opening_player_uses_tiebreaker_stats(self) -> None:
        room, _, host_token = self.registry.create_room("Alice", mode="random")
        room.join("Bob")

        with mock.patch("wujiang.web.multiplayer.random_room_hero_codes", return_value=("fire_funeral", "elite_soldier")):
            room.start_battle(host_token)

        self.assertEqual(room.battle.active_player, 1)

    def test_only_current_input_player_can_submit_actions(self) -> None:
        room, _, host_token = self.registry.create_room("Alice")
        _, guest_token = room.join("Bob")
        room.select_hero(host_token, "ellie")
        room.select_hero(guest_token, "bard")
        room.start_battle(host_token)

        with self.assertRaises(RoomError):
            room.perform_action(guest_token, {"type": "end_turn"})

        room.perform_action(host_token, {"type": "end_turn"})
        guest_view = room.serialize_state(guest_token)

        self.assertEqual(guest_view["battle"]["input_player"], 2)
        self.assertTrue(guest_view["battle"]["active_units"])

    def test_spectator_view_hides_action_bundles(self) -> None:
        room, _, host_token = self.registry.create_room("Alice")
        _, guest_token = room.join("Bob")
        room.select_hero(host_token, "ellie")
        room.select_hero(guest_token, "bard")
        room.start_battle(host_token)

        spectator_view = room.serialize_state(None)
        host_view = room.serialize_state(host_token)

        self.assertEqual(spectator_view["room"]["viewer_player_id"], None)
        self.assertEqual(spectator_view["battle"]["active_units"], [])
        self.assertTrue(host_view["battle"]["active_units"])

    def test_enemy_stealthed_units_are_hidden_from_viewer_state(self) -> None:
        room, _, host_token = self.registry.create_room("Alice")
        _, guest_token = room.join("Bob")
        room.select_hero(host_token, "dark_human")
        room.select_hero(guest_token, "bard")
        room.start_battle(host_token)

        room.perform_action(host_token, {"type": "skill", "unit_id": room.battle.player_units(1)[0].unit_id, "skill_code": "stealth"})

        host_view = room.serialize_state(host_token)
        guest_view = room.serialize_state(guest_token)

        self.assertEqual(len(host_view["battle"]["units"]), 2)
        self.assertEqual(len(guest_view["battle"]["units"]), 1)
        self.assertEqual(guest_view["battle"]["units"][0]["player_id"], 2)

    def test_clone_label_is_only_visible_to_owner_view(self) -> None:
        room, _, host_token = self.registry.create_room("Alice")
        _, guest_token = room.join("Bob")
        room.select_hero(host_token, "element_hunter")
        room.select_hero(guest_token, "bard")
        room.start_battle(host_token)
        hunter = room.battle.player_units(1)[0]

        room.perform_action(host_token, {"type": "skill", "unit_id": hunter.unit_id, "skill_code": "earth_walker", "x": 2, "y": 4})

        clone = next(unit for unit in room.battle.all_units() if unit.is_clone)
        host_view = room.serialize_state(host_token)
        guest_view = room.serialize_state(guest_token)
        host_units = {unit["id"]: unit["name"] for unit in host_view["battle"]["units"]}
        guest_units = {unit["id"]: unit["name"] for unit in guest_view["battle"]["units"]}
        host_active_units = {unit["unit_id"]: unit["name"] for unit in host_view["battle"]["active_units"]}

        self.assertEqual(host_units[clone.unit_id], "元素猎人（分身）")
        self.assertEqual(host_active_units[clone.unit_id], "元素猎人（分身）")
        self.assertEqual(guest_units[clone.unit_id], "元素猎人")

    def test_finished_room_can_restart_lobby_and_clear_hero_choices(self) -> None:
        room, _, host_token = self.registry.create_room("Alice")
        _, guest_token = room.join("Bob")
        room.select_hero(host_token, "ellie")
        room.select_hero(guest_token, "bard")
        room.start_battle(host_token)
        room.status = "finished"

        room.restart_lobby(guest_token)
        room_state = room.serialize_state(host_token)["room"]

        self.assertEqual(room.status, "lobby")
        self.assertIsNone(room.battle)
        self.assertFalse(room_state["can_start"])
        self.assertFalse(room_state["can_rematch"])
        self.assertIsNone(room_state["seats"][0]["hero_code"])
        self.assertIsNone(room_state["seats"][1]["hero_code"])

    def test_host_can_delete_room_but_guest_cannot(self) -> None:
        room, _, host_token = self.registry.create_room("Alice")
        _, guest_token = room.join("Bob")

        with self.assertRaises(RoomError):
            self.registry.delete_room(room.room_id, guest_token)

        self.registry.delete_room(room.room_id, host_token)

        with self.assertRaises(RoomError):
            self.registry.get_room(room.room_id)

    def test_guest_can_leave_lobby_and_free_their_seat(self) -> None:
        room, _, host_token = self.registry.create_room("Alice")
        _, guest_token = room.join("Bob")

        deleted, leaving_player_id = self.registry.leave_room(room.room_id, guest_token)
        room_state = room.serialize_state(host_token)["room"]

        self.assertFalse(deleted)
        self.assertEqual(leaving_player_id, 2)
        self.assertFalse(room_state["is_full"])
        self.assertFalse(room_state["seats"][1]["occupied"])
        self.assertIsNone(room_state["seats"][1]["name"])

    def test_host_leave_transfers_host_to_remaining_player(self) -> None:
        room, _, host_token = self.registry.create_room("Alice")
        _, guest_token = room.join("Bob")

        deleted, leaving_player_id = self.registry.leave_room(room.room_id, host_token)
        guest_view = room.serialize_state(guest_token)["room"]

        self.assertFalse(deleted)
        self.assertEqual(leaving_player_id, 1)
        self.assertEqual(guest_view["host_player_id"], 2)
        self.assertTrue(guest_view["viewer_is_host"])

    def test_last_player_leave_removes_room(self) -> None:
        room, _, host_token = self.registry.create_room("Alice")

        deleted, leaving_player_id = self.registry.leave_room(room.room_id, host_token)

        self.assertTrue(deleted)
        self.assertEqual(leaving_player_id, 1)
        with self.assertRaises(RoomError):
            self.registry.get_room(room.room_id)

    def test_player_cannot_leave_while_battle_is_running(self) -> None:
        room, _, host_token = self.registry.create_room("Alice")
        _, guest_token = room.join("Bob")
        room.select_hero(host_token, "ellie")
        room.select_hero(guest_token, "bard")
        room.start_battle(host_token)

        with self.assertRaises(RoomError):
            self.registry.leave_room(room.room_id, guest_token)

    def test_player_can_surrender_and_finish_battle(self) -> None:
        room, _, host_token = self.registry.create_room("Alice")
        _, guest_token = room.join("Bob")
        room.select_hero(host_token, "ellie")
        room.select_hero(guest_token, "bard")
        room.start_battle(host_token)

        room.surrender(guest_token)

        self.assertEqual(room.status, "finished")
        self.assertIsNotNone(room.battle)
        self.assertEqual(room.battle.winner, 1)
        self.assertIn("投降", room.battle.logs[-1])
 
    def test_normalize_public_base_url_accepts_bare_host_and_trailing_slash(self) -> None:
        self.assertEqual(
            normalize_public_base_url("203.0.113.10:8000/"),
            "http://203.0.113.10:8000",
        )

    def test_normalize_public_base_url_rejects_non_root_urls(self) -> None:
        with self.assertRaises(ValueError):
            normalize_public_base_url("https://example.com/wujiang")


if __name__ == "__main__":
    unittest.main()
