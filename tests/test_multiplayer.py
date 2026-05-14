from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wujiang.engine.core import Position  # noqa: E402
from wujiang.heroes.registry import create_battle  # noqa: E402
from wujiang.web.multiplayer import RoomError, RoomRegistry, random_room_hero_codes  # noqa: E402
from wujiang.web.server import normalize_public_base_url  # noqa: E402


class MultiplayerRoomTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = RoomRegistry()

    def test_random_mode_samples_unique_heroes_across_both_sides(self) -> None:
        player1_roster, player2_roster = random_room_hero_codes(3)

        self.assertEqual(len(player1_roster), 3)
        self.assertEqual(len(player2_roster), 3)
        self.assertEqual(len(set(player1_roster + player2_roster)), 6)

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

    def test_open_lobby_same_name_still_claims_open_second_seat(self) -> None:
        room, _, host_token = self.registry.create_room("Alice")

        guest_player_id, guest_token = room.join("Alice")
        host_view = room.serialize_state(host_token)
        guest_view = room.serialize_state(guest_token)

        self.assertEqual(guest_player_id, 2)
        self.assertNotEqual(host_token, guest_token)
        self.assertEqual(host_view["room"]["viewer_player_id"], 1)
        self.assertEqual(guest_view["room"]["viewer_player_id"], 2)

    def test_room_registry_lists_multihero_room_summaries(self) -> None:
        room, _, host_token = self.registry.create_room("Alice")
        room.join("Bob")
        room.select_hero(host_token, "ellie", 2)

        rooms = self.registry.list_rooms(base_url="http://example.test")

        self.assertEqual(len(rooms), 1)
        summary = rooms[0]
        self.assertEqual(summary["room_id"], room.room_id)
        self.assertEqual(summary["status"], "lobby")
        self.assertFalse(summary["can_join"])
        self.assertEqual(summary["occupied_seat_count"], 2)
        self.assertEqual(summary["invite_url"], f"http://example.test/?room={room.room_id}")
        self.assertEqual(summary["seats"][0]["name"], "Alice")
        self.assertEqual(summary["seats"][0]["hero_counts"], {"ellie": 2})
        self.assertEqual(summary["seats"][0]["hero_summary"], "艾莉 × 2")
        self.assertEqual(summary["seats"][1]["name"], "Bob")

    def test_classic_room_supports_duplicate_multi_selection(self) -> None:
        room, _, host_token = self.registry.create_room("Alice")
        room.join("Bob")

        room.select_hero(host_token, "ellie", 1)
        room.select_hero(host_token, "ellie", 1)
        room.select_hero(host_token, "bard", 1)
        room.select_hero(host_token, "ellie", -1)

        seat = room.serialize_state(host_token)["room"]["seats"][0]
        self.assertEqual(seat["hero_counts"], {"bard": 1, "ellie": 1})
        self.assertEqual(seat["hero_total_count"], 2)
        self.assertEqual(seat["hero_summary"], "吟游诗人 × 1 / 艾莉 × 1")
        self.assertIsNone(seat["hero_code"])

    def test_room_cannot_start_until_both_players_select_at_least_one_hero(self) -> None:
        room, _, host_token = self.registry.create_room("Alice")
        _, guest_token = room.join("Bob")
        room.select_hero(host_token, "ellie", 2)

        with self.assertRaises(RoomError):
            room.start_battle(guest_token)

        room.select_hero(guest_token, "bard")
        room.start_battle(host_token)

        self.assertEqual(room.status, "battle")
        self.assertIsNotNone(room.battle)
        self.assertEqual(len(room.battle.hero_units(1)), 2)
        self.assertEqual(len(room.battle.hero_units(2)), 1)
        self.assertEqual(room.battle.width, 10)
        self.assertEqual(room.battle.height, 10)

    def test_host_can_switch_room_mode_in_lobby_and_clear_existing_rosters(self) -> None:
        room, _, host_token = self.registry.create_room("Alice")
        _, guest_token = room.join("Bob")
        room.select_hero(host_token, "ellie", 2)
        room.select_hero(guest_token, "bard", 1)

        room.set_mode(host_token, "random")

        room_state = room.serialize_state(host_token)["room"]
        self.assertEqual(room_state["mode"], "random")
        self.assertEqual(room_state["mode_name"], "随机选人")
        self.assertEqual(room_state["seats"][0]["hero_counts"], {})
        self.assertEqual(room_state["seats"][1]["hero_counts"], {})
        self.assertTrue(room_state["can_start"])

    def test_random_mode_does_not_allow_manual_hero_selection(self) -> None:
        room, _, host_token = self.registry.create_room("Alice", mode="random")

        with self.assertRaises(RoomError):
            room.select_hero(host_token, "ellie")

    def test_host_can_set_random_mode_roster_size_in_lobby(self) -> None:
        room, _, host_token = self.registry.create_room("Alice", mode="random")
        room.join("Bob")

        room.set_random_roster_size(host_token, 3)

        room_state = room.serialize_state(host_token)["room"]
        self.assertEqual(room.random_roster_size, 3)
        self.assertEqual(room_state["random_roster_size"], 3)

    def test_random_mode_roster_size_cannot_exceed_unique_hero_pool(self) -> None:
        room, _, host_token = self.registry.create_room("Alice", mode="random")
        room.join("Bob")

        with self.assertRaises(RoomError) as exc:
            room.set_random_roster_size(host_token, 999)

        self.assertIn("不会出现重复武将", str(exc.exception))

    def test_random_mode_start_assigns_n_random_heroes_per_side(self) -> None:
        room, _, host_token = self.registry.create_room("Alice", mode="random")
        room.join("Bob")
        room.set_random_roster_size(host_token, 3)

        roster1 = ["bard", "dark_human", "fire_funeral"]
        roster2 = ["doomlight_dragon", "elite_soldier", "ellie"]
        with mock.patch("wujiang.web.multiplayer.random_room_hero_codes", return_value=(roster1, roster2)):
            room.start_battle(host_token)

        self.assertEqual(room.status, "battle")
        self.assertEqual(room.seats[1].hero_total_count, 3)
        self.assertEqual(room.seats[2].hero_total_count, 3)
        self.assertEqual(room.seats[1].expanded_roster(), sorted(roster1))
        self.assertEqual(room.seats[2].expanded_roster(), sorted(roster2))
        self.assertEqual(len(room.battle.hero_units(1)), 3)
        self.assertEqual(len(room.battle.hero_units(2)), 3)

        expected_battle = create_battle(roster1, roster2)
        self.assertEqual(room.battle.width, expected_battle.width)
        self.assertEqual(room.battle.height, expected_battle.height)
        self.assertEqual(
            [room.battle.get_unit(unit_id).hero_code for unit_id in room.battle.turn_order_unit_ids],
            [expected_battle.get_unit(unit_id).hero_code for unit_id in expected_battle.turn_order_unit_ids],
        )

    def test_host_can_expand_room_and_configure_extra_ai_seats(self) -> None:
        room, _, host_token = self.registry.create_room("Alice")
        room.join("Bob")

        room.set_seat_count(host_token, 4)
        room.set_seat_controller(host_token, 3, "ai")
        room.set_seat_controller(host_token, 4, "ai")
        room.set_seat_team(host_token, 3, 1)
        room.set_seat_team(host_token, 4, 2)

        room_state = room.serialize_state(host_token)["room"]
        self.assertEqual(room_state["seat_count"], 4)
        self.assertEqual(room_state["occupied_seat_count"], 4)
        self.assertEqual(room_state["ai_seat_count"], 2)
        self.assertEqual(room_state["seats"][2]["controller_type"], "ai")
        self.assertEqual(room_state["seats"][2]["team_id"], 1)
        self.assertEqual(room_state["seats"][3]["controller_type"], "ai")
        self.assertEqual(room_state["seats"][3]["team_id"], 2)

    def test_multi_seat_classic_room_enforces_seat_owned_control(self) -> None:
        room, _, host_token = self.registry.create_room("Alice")
        _, guest_token = room.join("Bob")
        room.set_seat_count(host_token, 4)
        room.set_seat_controller(host_token, 3, "ai")
        room.set_seat_controller(host_token, 4, "ai")
        room.set_seat_team(host_token, 3, 1)
        room.set_seat_team(host_token, 4, 2)
        room.select_hero(host_token, "dark_human")
        room.select_hero(guest_token, "fire_funeral")
        room.select_hero(host_token, "bard", seat_id=3)
        room.select_hero(host_token, "ellie", seat_id=4)

        room.start_battle(host_token)

        host_view = room.serialize_state(host_token)
        self.assertEqual(host_view["room"]["viewer_player_id"], 1)
        self.assertEqual(host_view["room"]["viewer_team_id"], 1)
        self.assertEqual(host_view["battle"]["input_player"], 1)
        self.assertEqual(len(host_view["battle"]["active_units"]), 1)
        self.assertEqual(host_view["battle"]["active_units"][0]["name"], "E。暗人")

        allied_ai_unit = next(
            unit
            for unit in room.battle.player_units(1)
            if getattr(unit, "owner_seat_id", None) == 3 and not unit.is_summon
        )
        with self.assertRaises(RoomError):
            room.perform_action(
                host_token,
                {"type": "skill", "unit_id": allied_ai_unit.unit_id, "skill_code": "heal"},
            )

    def test_multi_seat_random_room_uses_per_seat_quotas(self) -> None:
        room, _, host_token = self.registry.create_room("Alice", mode="random")
        room.join("Bob")
        room.set_seat_count(host_token, 4)
        room.set_seat_controller(host_token, 3, "ai")
        room.set_seat_controller(host_token, 4, "ai")
        room.set_seat_team(host_token, 3, 1)
        room.set_seat_team(host_token, 4, 2)
        room.set_random_roster_size(host_token, 2)
        room.set_random_quota(host_token, 1, 1)
        room.set_random_quota(host_token, 3, 1)
        room.set_random_quota(host_token, 2, 1)
        room.set_random_quota(host_token, 4, 1)

        roster1 = ["bard", "dark_human"]
        roster2 = ["doomlight_dragon", "elite_soldier"]
        with mock.patch("wujiang.web.multiplayer.random_room_hero_codes", return_value=(roster1, roster2)):
            with mock.patch.object(room, "_resolve_ai_until_human_input"):
                room.start_battle(host_token)

        self.assertEqual(room.seats[1].expanded_roster(), ["bard"])
        self.assertEqual(room.seats[3].expanded_roster(), ["dark_human"])
        self.assertEqual(room.seats[2].expanded_roster(), ["doomlight_dragon"])
        self.assertEqual(room.seats[4].expanded_roster(), ["elite_soldier"])
        self.assertEqual(
            sorted(
                (unit.hero_code, getattr(unit, "owner_seat_id", None))
                for unit in room.battle.hero_units(1)
            ),
            [("bard", 1), ("dark_human", 3)],
        )
        self.assertEqual(
            sorted(
                (unit.hero_code, getattr(unit, "owner_seat_id", None))
                for unit in room.battle.hero_units(2)
            ),
            [("doomlight_dragon", 2), ("elite_soldier", 4)],
        )

    def test_ai_seat_automatically_plays_its_active_turn(self) -> None:
        room, _, host_token = self.registry.create_room("Alice")
        room.set_seat_controller(host_token, 2, "ai")
        room.select_hero(host_token, "bard")
        room.select_hero(host_token, "elite_soldier", seat_id=2)

        room.start_battle(host_token)

        self.assertEqual(room.current_input_player_id(), 1)
        self.assertEqual(room.battle.active_player, 2)
        self.assertIsNotNone(room.battle.pending_chain)
        self.assertEqual(room.battle.get_unit(room.battle.pending_chain.queued_action.actor_id).hero_code, "elite_soldier")

    def test_ai_seat_uses_protection_reaction_instead_of_skipping(self) -> None:
        room, _, host_token = self.registry.create_room("Alice")
        room.set_seat_controller(host_token, 2, "ai")
        room.select_hero(host_token, "elite_soldier")
        room.select_hero(host_token, "bard", seat_id=2)

        room.start_battle(host_token)

        soldier = next(unit for unit in room.battle.hero_units(1) if unit.hero_code == "elite_soldier")
        bard = next(unit for unit in room.battle.hero_units(2) if unit.hero_code == "bard")
        room.perform_action(host_token, {"type": "attack", "unit_id": soldier.unit_id, "target_unit_id": bard.unit_id})

        self.assertEqual(bard.current_hp, 1.0)
        self.assertGreaterEqual(bard.total_shields(), 1)

    def test_ai_seat_can_fire_instant_skill_during_enemy_turn(self) -> None:
        room, _, host_token = self.registry.create_room("Alice")
        room.set_seat_controller(host_token, 2, "ai")
        room.select_hero(host_token, "dark_human")
        room.select_hero(host_token, "n", seat_id=2)

        room.start_battle(host_token)

        dark = next(unit for unit in room.battle.hero_units(1) if unit.hero_code == "dark_human")
        caster = next(unit for unit in room.battle.hero_units(2) if unit.hero_code == "n")
        dark.position = Position(3, 4)
        caster.position = Position(5, 4)
        caster.mana_points = 2

        room.perform_action(host_token, {"type": "move", "unit_id": dark.unit_id, "x": 4, "y": 4})

        self.assertFalse(dark.turn_ready)
        self.assertIn("磁力波", "".join(room.battle.logs))

    def test_random_mode_opening_player_uses_tiebreaker_stats(self) -> None:
        room, _, host_token = self.registry.create_room("Alice", mode="random")
        room.join("Bob")

        with mock.patch(
            "wujiang.web.multiplayer.random_room_hero_codes",
            return_value=(["fire_funeral"], ["elite_soldier"]),
        ):
            room.start_battle(host_token)

        self.assertEqual(room.battle.active_player, 1)

    def test_active_units_only_show_current_turn_bundle_in_classic_multihero_mode(self) -> None:
        room, _, host_token = self.registry.create_room("Alice")
        _, guest_token = room.join("Bob")
        room.select_hero(host_token, "dark_human")
        room.select_hero(host_token, "fire_funeral")
        room.select_hero(guest_token, "undead_king_lina")
        room.select_hero(guest_token, "jade")
        room.start_battle(host_token)

        host_view = room.serialize_state(host_token)
        guest_view = room.serialize_state(guest_token)

        self.assertEqual(host_view["battle"]["active_turn_unit_name"], "不死王利娜")
        self.assertEqual(host_view["battle"]["input_player"], 2)
        self.assertEqual(host_view["battle"]["active_units"], [])
        self.assertEqual(len(guest_view["battle"]["active_units"]), 1)
        self.assertEqual(guest_view["battle"]["active_units"][0]["name"], "不死王利娜")

    def test_only_current_input_player_can_submit_actions(self) -> None:
        room, _, host_token = self.registry.create_room("Alice")
        _, guest_token = room.join("Bob")
        room.select_hero(host_token, "ellie")
        room.select_hero(host_token, "bard")
        room.select_hero(guest_token, "dark_human")
        room.start_battle(host_token)

        opening_input = room.current_input_player_id()
        current_token = host_token if opening_input == 1 else guest_token
        waiting_token = guest_token if current_token == host_token else host_token

        with self.assertRaises(RoomError):
            room.perform_action(waiting_token, {"type": "end_turn"})

        room.perform_action(current_token, {"type": "end_turn"})
        next_input = room.current_input_player_id()
        next_view = room.serialize_state(host_token if next_input == 1 else guest_token)

        self.assertNotEqual(next_input, opening_input)
        self.assertEqual(next_view["battle"]["input_player"], next_input)
        self.assertTrue(next_view["battle"]["active_units"])

    def test_player_can_reclaim_running_battle_seat_by_name_after_token_loss(self) -> None:
        room, _, host_token = self.registry.create_room("Alice")
        _, guest_token = room.join("Bob")
        room.select_hero(host_token, "dark_human")
        room.select_hero(guest_token, "bard")
        room.start_battle(host_token)
        room.perform_action(host_token, {"type": "end_turn"})

        with self.assertRaises(RoomError):
            room.join("Charlie")

        rejoined_player_id, rejoined_token = room.join("Bob")
        guest_view = room.serialize_state(rejoined_token)

        self.assertEqual(rejoined_player_id, 2)
        self.assertEqual(rejoined_token, guest_token)
        self.assertEqual(guest_view["room"]["viewer_player_id"], 2)
        self.assertEqual(guest_view["battle"]["input_player"], 2)
        self.assertTrue(guest_view["battle"]["active_units"])

    def test_spectator_view_hides_action_bundles(self) -> None:
        room, _, host_token = self.registry.create_room("Alice")
        room.join("Bob")
        room.select_hero(host_token, "ellie")
        room.select_hero(host_token, "bard")
        room.select_hero(room.seats[2].token, "dark_human")
        room.start_battle(host_token)

        spectator_view = room.serialize_state(None)
        host_view = room.serialize_state(host_token)

        self.assertIsNone(spectator_view["room"]["viewer_player_id"])
        self.assertEqual(spectator_view["battle"]["active_units"], [])
        self.assertTrue(host_view["battle"]["active_units"] or host_view["battle"]["input_player"] != 1)

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
        if clone.unit_id in host_active_units:
            self.assertEqual(host_active_units[clone.unit_id], "元素猎人（分身）")
        self.assertEqual(guest_units[clone.unit_id], "元素猎人")

    def test_finished_room_can_restart_lobby_and_clear_rosters(self) -> None:
        room, _, host_token = self.registry.create_room("Alice")
        _, guest_token = room.join("Bob")
        room.select_hero(host_token, "ellie", 2)
        room.select_hero(guest_token, "bard")
        room.start_battle(host_token)
        room.status = "finished"

        room.restart_lobby(guest_token)
        room_state = room.serialize_state(host_token)["room"]

        self.assertEqual(room.status, "lobby")
        self.assertIsNone(room.battle)
        self.assertFalse(room_state["can_start"])
        self.assertFalse(room_state["can_rematch"])
        self.assertEqual(room_state["seats"][0]["hero_counts"], {})
        self.assertEqual(room_state["seats"][1]["hero_counts"], {})

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

    def test_classic_room_can_start_with_human_spectator_seat_and_ai_only_rosters(self) -> None:
        room, _, host_token = self.registry.create_room("Alice")
        room.set_seat_count(host_token, 4)
        room.set_seat_controller(host_token, 2, "ai")
        room.set_seat_controller(host_token, 3, "ai")
        room.set_seat_controller(host_token, 4, "ai")
        room.set_seat_team(host_token, 2, 2)
        room.set_seat_team(host_token, 3, 1)
        room.set_seat_team(host_token, 4, 2)
        room.select_hero(host_token, "bard", seat_id=3)
        room.select_hero(host_token, "elite_soldier", seat_id=2)

        room.start_battle(host_token)

        room_state = room.serialize_state(host_token)["room"]
        self.assertEqual(room.status, "battle")
        self.assertTrue(room_state["simulation"]["enabled"])
        self.assertTrue(room_state["replay"]["available"])
        self.assertEqual(room_state["seats"][0]["hero_total_count"], 0)

    def test_ai_only_room_progresses_incrementally_and_records_replay_steps(self) -> None:
        room, _, host_token = self.registry.create_room("Alice")
        room.set_seat_count(host_token, 4)
        room.set_seat_controller(host_token, 2, "ai")
        room.set_seat_controller(host_token, 3, "ai")
        room.set_seat_controller(host_token, 4, "ai")
        room.set_seat_team(host_token, 2, 2)
        room.set_seat_team(host_token, 3, 1)
        room.set_seat_team(host_token, 4, 2)
        room.select_hero(host_token, "bard", seat_id=3)
        room.select_hero(host_token, "elite_soldier", seat_id=2)
        room.start_battle(host_token)

        initial_steps = room.replay.step_count
        room.simulation_last_advanced_at = time.time() - 5
        room.serialize_state(host_token)
        advanced_steps = room.replay.step_count

        self.assertGreater(advanced_steps, initial_steps)
        room.control_simulation(host_token, "pause")
        paused_step = room.replay.last_index
        room.control_simulation(host_token, "step")
        self.assertGreaterEqual(room.replay.last_index, paused_step)
        self.assertTrue(room.simulation_paused)

    def test_replay_endpoint_uses_spectator_view_before_finish_and_allows_omniscient_after_finish(self) -> None:
        room, _, host_token = self.registry.create_room("Alice")
        room.set_seat_count(host_token, 4)
        room.set_seat_controller(host_token, 2, "ai")
        room.set_seat_controller(host_token, 3, "ai")
        room.set_seat_controller(host_token, 4, "ai")
        room.set_seat_team(host_token, 2, 2)
        room.set_seat_team(host_token, 3, 1)
        room.set_seat_team(host_token, 4, 2)
        room.select_hero(host_token, "bard", seat_id=3)
        room.select_hero(host_token, "elite_soldier", seat_id=2)
        room.start_battle(host_token)

        spectator_replay = room.serialize_replay_step(None, step_index=0, omniscient=True)
        self.assertFalse(spectator_replay["replay"]["omniscient"])
        self.assertEqual(spectator_replay["battle"]["active_units"], [])

        room.surrender(host_token)
        finished_replay = room.serialize_replay_step(None, step_index=room.replay.last_index, omniscient=True)
        self.assertTrue(finished_replay["replay"]["omniscient"])

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
