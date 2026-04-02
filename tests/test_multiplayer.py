from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wujiang.web.multiplayer import RoomError, RoomRegistry  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
