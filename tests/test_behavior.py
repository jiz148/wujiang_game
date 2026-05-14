from __future__ import annotations

import json
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    import quickjs
except ImportError:  # pragma: no cover - optional dependency guard
    quickjs = None


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wujiang.engine.core import ActionError, DamageContext, Position, StatusEffect  # noqa: E402
from wujiang.heroes.registry import create_battle, create_hero  # noqa: E402
from wujiang.web.multiplayer import GameRoom, ROOMS, battle_state_for_viewer  # noqa: E402
from wujiang.web.server import SESSION, WujiangHandler, configure_public_base_url  # noqa: E402


def primary_hero(battle, player_id: int):
    return next(unit for unit in battle.player_units(player_id) if not unit.is_summon)


def summon_by_code(battle, player_id: int, hero_code: str):
    return next(
        unit
        for unit in battle.player_units(player_id)
        if unit.is_summon and getattr(unit, "hero_code", "") == hero_code
    )


def skill_by_code(unit, skill_code: str):
    return unit.get_skill(skill_code)


def resolve_pending_chain(battle) -> None:
    while battle.pending_chain is not None:
        battle.perform_action({"type": "chain_skip"})


def end_turns(battle, count: int) -> None:
    for _ in range(count):
        battle.perform_action({"type": "end_turn"})


class RoomBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        configure_public_base_url(None)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), WujiangHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)

    def setUp(self) -> None:
        ROOMS._rooms.clear()  # type: ignore[attr-defined]
        SESSION.battle = None

    def api_get(self, path: str, *, params: dict[str, str] | None = None) -> dict:
        query = f"?{urlencode(params)}" if params else ""
        with urlopen(f"http://127.0.0.1:{self.port}{path}{query}") as response:
            return json.loads(response.read().decode("utf-8"))

    def api_post(self, path: str, payload: dict) -> dict:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_scenario_created_room_is_visible_to_public_lobby_queries(self) -> None:
        # Given an empty public lobby
        lobby_before = self.api_get("/api/rooms")
        self.assertEqual(lobby_before["rooms"], [])

        # When a host creates a room
        created = self.api_post("/api/rooms/create", {"player_name": "Alice"})
        room_id = created["room"]["room_id"]

        # Then another viewer can discover that room from the public room APIs
        lobby_after = self.api_get("/api/rooms")
        hero_index = self.api_get("/api/heroes")

        self.assertEqual(len(lobby_after["rooms"]), 1)
        self.assertEqual(lobby_after["rooms"][0]["room_id"], room_id)
        self.assertEqual(lobby_after["rooms"][0]["occupied_seat_count"], 1)
        self.assertEqual(lobby_after["rooms"][0]["status"], "lobby")
        self.assertEqual(hero_index["rooms"][0]["room_id"], room_id)

    def test_scenario_anonymous_viewer_can_open_room_state_before_joining(self) -> None:
        # Given a host has created a room
        created = self.api_post("/api/rooms/create", {"player_name": "Alice"})
        room_id = created["room"]["room_id"]

        # When another viewer opens the room by room id without a player token
        room_state = self.api_get("/api/rooms/state", params={"room_id": room_id})

        # Then the viewer can still see the room and current seat state
        self.assertEqual(room_state["room"]["room_id"], room_id)
        self.assertIsNone(room_state["room"]["viewer_player_id"])
        self.assertEqual(room_state["room"]["status"], "lobby")
        self.assertTrue(room_state["room"]["seats"][0]["occupied"])
        self.assertFalse(room_state["room"]["seats"][1]["occupied"])

    def test_scenario_room_flows_from_lobby_to_battle_through_public_http_api(self) -> None:
        # Given two players create and join the same room
        created = self.api_post("/api/rooms/create", {"player_name": "Alice"})
        room_id = created["room"]["room_id"]
        host_token = created["player_token"]
        joined = self.api_post("/api/rooms/join", {"room_id": room_id, "player_name": "Bob"})
        guest_token = joined["player_token"]

        # When both sides choose heroes and the host starts the battle
        self.api_post(
            "/api/rooms/select-hero",
            {"room_id": room_id, "player_token": host_token, "hero_code": "dark_human", "delta": 1},
        )
        self.api_post(
            "/api/rooms/select-hero",
            {"room_id": room_id, "player_token": host_token, "hero_code": "fire_funeral", "delta": 1},
        )
        self.api_post(
            "/api/rooms/select-hero",
            {"room_id": room_id, "player_token": guest_token, "hero_code": "undead_king_lina", "delta": 1},
        )
        started = self.api_post("/api/rooms/start", {"room_id": room_id, "player_token": host_token})

        # Then the room enters battle and exposes the current acting unit bundle
        self.assertEqual(started["room"]["status"], "battle")
        self.assertEqual(started["battle"]["input_player"], 2)
        self.assertEqual(started["battle"]["active_turn_unit_name"], "不死王利娜")
        self.assertEqual(len(started["battle"]["active_units"]), 0)

        guest_view = self.api_get("/api/rooms/state", params={"room_id": room_id, "player_token": guest_token})
        self.assertEqual(len(guest_view["battle"]["active_units"]), 1)
        self.assertEqual(guest_view["battle"]["active_units"][0]["name"], "不死王利娜")


    def test_scenario_ai_room_seat_takes_the_opening_turn_before_returning_input_to_the_human(self) -> None:
        # Given a room with one human seat and one AI seat
        created = self.api_post("/api/rooms/create", {"player_name": "Alice"})
        room_id = created["room"]["room_id"]
        host_token = created["player_token"]
        self.api_post(
            "/api/rooms/set-seat-controller",
            {"room_id": room_id, "player_token": host_token, "seat_id": 2, "controller_type": "ai"},
        )
        self.api_post(
            "/api/rooms/select-hero",
            {"room_id": room_id, "player_token": host_token, "hero_code": "bard", "delta": 1},
        )
        self.api_post(
            "/api/rooms/select-hero",
            {"room_id": room_id, "player_token": host_token, "seat_id": 2, "hero_code": "elite_soldier", "delta": 1},
        )

        # When the host starts the battle
        started = self.api_post("/api/rooms/start", {"room_id": room_id, "player_token": host_token})

        # Then the AI has already taken its first turn and input is back on the human side
        self.assertEqual(started["room"]["status"], "battle")
        self.assertEqual(started["battle"]["input_player"], 1)
        self.assertIsNotNone(started["battle"]["pending_chain"])
        self.assertEqual(started["battle"]["pending_chain"]["queued_action"]["actor_id"], started["battle"]["turn_order_unit_ids"][0])

    def test_scenario_random_room_assigns_n_heroes_per_side_with_classic_turn_rules(self) -> None:
        # Given a random-mode room with both players joined
        created = self.api_post("/api/rooms/create", {"player_name": "Alice", "mode": "random"})
        room_id = created["room"]["room_id"]
        host_token = created["player_token"]
        self.api_post("/api/rooms/join", {"room_id": room_id, "player_name": "Bob"})

        # When the host sets n and starts with a deterministic random roster
        configured = self.api_post(
            "/api/rooms/set-random-roster-size",
            {"room_id": room_id, "player_token": host_token, "random_roster_size": 3},
        )
        roster1 = ["doomlight_dragon", "bard", "dark_human"]
        roster2 = ["rock_god", "elite_soldier", "ellie"]
        with mock.patch("wujiang.web.multiplayer.random_room_hero_codes", return_value=(roster1, roster2)):
            with mock.patch("wujiang.heroes.registry.random.choice", side_effect=lambda seq: seq[-1]):
                started = self.api_post("/api/rooms/start", {"room_id": room_id, "player_token": host_token})

        # Then both sides receive n heroes and use the classic multi-hero board and turn-order rules
        self.assertEqual(configured["room"]["random_roster_size"], 3)
        self.assertEqual(started["room"]["status"], "battle")
        self.assertEqual(started["room"]["random_roster_size"], 3)
        self.assertEqual(started["room"]["seats"][0]["hero_total_count"], 3)
        self.assertEqual(started["room"]["seats"][1]["hero_total_count"], 3)
        self.assertEqual(
            len([unit for unit in started["battle"]["units"] if unit["player_id"] == 1 and not unit.get("is_summon")]),
            3,
        )
        self.assertEqual(
            len([unit for unit in started["battle"]["units"] if unit["player_id"] == 2 and not unit.get("is_summon")]),
            3,
        )

        expected_battle = create_battle(roster1, roster2)
        self.assertEqual(started["battle"]["board"]["width"], expected_battle.width)
        self.assertEqual(started["battle"]["board"]["height"], expected_battle.height)
        room = ROOMS.get_room(room_id)
        self.assertEqual(
            [room.battle.get_unit(unit_id).hero_code for unit_id in room.battle.turn_order_unit_ids],
            [expected_battle.get_unit(unit_id).hero_code for unit_id in expected_battle.turn_order_unit_ids],
        )

    def test_scenario_random_room_never_starts_with_duplicate_heroes_on_field(self) -> None:
        created = self.api_post("/api/rooms/create", {"player_name": "Alice", "mode": "random"})
        room_id = created["room"]["room_id"]
        host_token = created["player_token"]
        self.api_post("/api/rooms/join", {"room_id": room_id, "player_name": "Bob"})
        self.api_post(
            "/api/rooms/set-random-roster-size",
            {"room_id": room_id, "player_token": host_token, "random_roster_size": 1},
        )

        started = self.api_post("/api/rooms/start", {"room_id": room_id, "player_token": host_token})
        hero_names = [
            unit["name"]
            for unit in started["battle"]["units"]
            if not unit.get("is_summon")
        ]

        self.assertEqual(len(hero_names), 2)
        self.assertEqual(len(set(hero_names)), 2)

    def test_scenario_multi_seat_room_can_start_with_team_merge_and_random_quotas(self) -> None:
        created = self.api_post("/api/rooms/create", {"player_name": "Alice", "mode": "random"})
        room_id = created["room"]["room_id"]
        host_token = created["player_token"]
        self.api_post("/api/rooms/join", {"room_id": room_id, "player_name": "Bob"})

        self.api_post("/api/rooms/set-seat-count", {"room_id": room_id, "player_token": host_token, "seat_count": 4})
        self.api_post(
            "/api/rooms/set-seat-controller",
            {"room_id": room_id, "player_token": host_token, "seat_id": 3, "controller_type": "ai"},
        )
        self.api_post(
            "/api/rooms/set-seat-controller",
            {"room_id": room_id, "player_token": host_token, "seat_id": 4, "controller_type": "ai"},
        )
        self.api_post("/api/rooms/set-seat-team", {"room_id": room_id, "player_token": host_token, "seat_id": 3, "team_id": 1})
        self.api_post("/api/rooms/set-seat-team", {"room_id": room_id, "player_token": host_token, "seat_id": 4, "team_id": 2})
        self.api_post(
            "/api/rooms/set-random-roster-size",
            {"room_id": room_id, "player_token": host_token, "random_roster_size": 2},
        )
        self.api_post("/api/rooms/set-seat-random-quota", {"room_id": room_id, "player_token": host_token, "seat_id": 1, "quota": 1})
        self.api_post("/api/rooms/set-seat-random-quota", {"room_id": room_id, "player_token": host_token, "seat_id": 2, "quota": 1})
        self.api_post("/api/rooms/set-seat-random-quota", {"room_id": room_id, "player_token": host_token, "seat_id": 3, "quota": 1})
        self.api_post("/api/rooms/set-seat-random-quota", {"room_id": room_id, "player_token": host_token, "seat_id": 4, "quota": 1})

        roster1 = ["bard", "dark_human"]
        roster2 = ["doomlight_dragon", "elite_soldier"]
        with mock.patch("wujiang.web.multiplayer.random_room_hero_codes", return_value=(roster1, roster2)):
            started = self.api_post("/api/rooms/start", {"room_id": room_id, "player_token": host_token})

        self.assertEqual(started["room"]["seat_count"], 4)
        self.assertEqual(started["room"]["ai_seat_count"], 2)
        self.assertEqual(started["room"]["seats"][0]["hero_total_count"], 1)
        self.assertEqual(started["room"]["seats"][2]["hero_total_count"], 1)
        self.assertEqual(started["room"]["seats"][1]["hero_total_count"], 1)
        self.assertEqual(started["room"]["seats"][3]["hero_total_count"], 1)
        self.assertEqual(
            len([unit for unit in started["battle"]["units"] if unit["player_id"] == 1 and not unit.get("is_summon")]),
            2,
        )
        self.assertEqual(
            len([unit for unit in started["battle"]["units"] if unit["player_id"] == 2 and not unit.get("is_summon")]),
            2,
        )


    def test_scenario_ai_only_room_can_run_with_pause_step_and_replay_http_api(self) -> None:
        created = self.api_post("/api/rooms/create", {"player_name": "Alice"})
        room_id = created["room"]["room_id"]
        host_token = created["player_token"]

        self.api_post("/api/rooms/set-seat-count", {"room_id": room_id, "player_token": host_token, "seat_count": 4})
        self.api_post(
            "/api/rooms/set-seat-controller",
            {"room_id": room_id, "player_token": host_token, "seat_id": 2, "controller_type": "ai"},
        )
        self.api_post(
            "/api/rooms/set-seat-controller",
            {"room_id": room_id, "player_token": host_token, "seat_id": 3, "controller_type": "ai"},
        )
        self.api_post(
            "/api/rooms/set-seat-controller",
            {"room_id": room_id, "player_token": host_token, "seat_id": 4, "controller_type": "ai"},
        )
        self.api_post("/api/rooms/set-seat-team", {"room_id": room_id, "player_token": host_token, "seat_id": 2, "team_id": 2})
        self.api_post("/api/rooms/set-seat-team", {"room_id": room_id, "player_token": host_token, "seat_id": 3, "team_id": 1})
        self.api_post("/api/rooms/set-seat-team", {"room_id": room_id, "player_token": host_token, "seat_id": 4, "team_id": 2})
        self.api_post(
            "/api/rooms/select-hero",
            {"room_id": room_id, "player_token": host_token, "seat_id": 3, "hero_code": "bard", "delta": 1},
        )
        self.api_post(
            "/api/rooms/select-hero",
            {"room_id": room_id, "player_token": host_token, "seat_id": 2, "hero_code": "elite_soldier", "delta": 1},
        )

        started = self.api_post("/api/rooms/start", {"room_id": room_id, "player_token": host_token})
        self.assertTrue(started["room"]["simulation"]["enabled"])
        self.assertTrue(started["room"]["replay"]["available"])

        room = ROOMS.get_room(room_id)
        room.simulation_last_advanced_at = 0
        running = self.api_get("/api/rooms/state", params={"room_id": room_id, "player_token": host_token})
        self.assertGreater(running["room"]["simulation"]["live_step_index"], 0)

        paused = self.api_post(
            "/api/rooms/simulation-control",
            {"room_id": room_id, "player_token": host_token, "action": "pause"},
        )
        self.assertTrue(paused["room"]["simulation"]["paused"])
        paused_step = paused["room"]["simulation"]["live_step_index"]

        stepped = self.api_post(
            "/api/rooms/simulation-control",
            {"room_id": room_id, "player_token": host_token, "action": "step"},
        )
        self.assertGreaterEqual(stepped["room"]["simulation"]["live_step_index"], paused_step)

        replay = self.api_get(
            "/api/rooms/replay",
            params={"room_id": room_id, "player_token": host_token, "step_index": 0},
        )
        self.assertEqual(replay["replay"]["step_index"], 0)
        self.assertIn("board", replay["battle"])


class CombatBehaviorTests(unittest.TestCase):
    def test_scenario_classic_multihero_turn_order_stays_fixed_and_skips_destroyed_slots(self) -> None:
        # Given a classic multi-hero battle with both rosters sorted once at battle start
        battle = create_battle(
            ["dark_human", "fire_funeral", "bard"],
            ["undead_king_lina", "jade", "doomlight_dragon"],
        )
        bard = next(unit for unit in battle.hero_units(1) if unit.hero_code == "bard")

        # Then the round order alternates between the already-sorted side lists
        turn_order_codes = [battle.get_unit(unit_id).hero_code for unit_id in battle.turn_order_unit_ids]
        self.assertEqual(
            turn_order_codes,
            ["undead_king_lina", "dark_human", "jade", "fire_funeral", "doomlight_dragon", "bard"],
        )

        # When the bard slot comes later in the ring but the bard has already been destroyed
        end_turns(battle, 4)
        battle.remove_unit(bard)
        battle.perform_action({"type": "end_turn"})

        # Then the fixed slot is skipped without reordering the rest of the ring
        self.assertIn(bard.unit_id, battle.turn_order_unit_ids)
        self.assertEqual(battle.current_turn_unit().hero_code, "undead_king_lina")
        self.assertEqual(battle.round_number, 2)

    def test_scenario_timeout_rule_scales_with_opening_hero_count(self) -> None:
        # Given a four-hero battle with no damage or kills happening
        battle = create_battle(["bard", "ellie"], ["dark_human", "elite_soldier"])
        self.assertEqual(battle.initial_hero_count, 4)
        self.assertEqual(battle.turn_timeout_limit, 80)

        # When 79 hero turns have completed
        end_turns(battle, 79)

        # Then the battle is still unresolved
        self.assertIsNone(battle.winner)

        # And the 80th completed hero turn forces a random winner
        with mock.patch("wujiang.engine.core.random.choice", return_value=1):
            battle.perform_action({"type": "end_turn"})

        self.assertEqual(battle.winner, 1)
        self.assertIn("80 个武将回合上限", battle.logs[-1])

    def test_scenario_stealth_blocks_enemy_direct_targeting_but_friendly_support_still_works(self) -> None:
        # Given a stealthed Dark Human with a friendly Bard nearby
        battle = create_battle("dark_human", "elite_soldier")
        dark = primary_hero(battle, 1)
        soldier = primary_hero(battle, 2)
        bard = create_hero("bard", 1)
        battle.add_unit(bard, Position(4, 4))
        dark.position = Position(5, 4)
        soldier.position = Position(7, 7)
        dark.current_hp = 0.5

        # When Dark Human enters stealth and Bard uses a friendly targeted heal
        battle.perform_action({"type": "skill", "unit_id": dark.unit_id, "skill_code": "stealth"})
        battle.perform_action({"type": "skill", "unit_id": bard.unit_id, "skill_code": "heal", "target_unit_id": dark.unit_id})

        # Then the friendly support still lands on the stealthed ally
        self.assertGreater(dark.current_hp, 0.5)

        # And enemy direct targeting still fails on the following turn
        battle.perform_action({"type": "end_turn"})
        with self.assertRaises(ActionError):
            battle.perform_action({"type": "attack", "unit_id": soldier.unit_id, "target_unit_id": dark.unit_id})

    def test_scenario_bard_chant_adds_mana_points_without_refilling_mana(self) -> None:
        # Given a Bard with mana below cap
        battle = create_battle("bard", "dark_human")
        bard = primary_hero(battle, 1)
        enemy = primary_hero(battle, 2)
        bard.position = Position(4, 4)
        enemy.position = Position(7, 7)
        bard.current_mana = 3

        # When Bard uses Chant on self
        battle.perform_action({"type": "skill", "unit_id": bard.unit_id, "skill_code": "chant", "target_unit_id": bard.unit_id})

        # Then mana points increase but mana itself does not
        self.assertEqual(bard.mana_points, 2.0)
        self.assertEqual(bard.current_mana, 3)

    def test_scenario_ellie_experiment_kills_after_three_target_rounds_not_three_global_turns(self) -> None:
        # Given Ellie buffs a faster ally with Experiment
        battle = create_battle(["dark_human", "ellie"], ["bard"])
        dark = next(unit for unit in battle.hero_units(1) if unit.hero_code == "dark_human")
        ellie = next(unit for unit in battle.hero_units(1) if unit.hero_code == "ellie")
        bard = primary_hero(battle, 2)
        dark.position = Position(5, 4)
        ellie.position = Position(4, 4)
        bard.position = Position(7, 7)

        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})
        battle.perform_action(
            {"type": "skill", "unit_id": ellie.unit_id, "skill_code": "experiment", "target_unit_id": dark.unit_id}
        )

        # When three global turns pass after the cast
        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})

        # Then the buffed target is still alive because only one of its own rounds has ended
        self.assertTrue(dark.alive)

        # And it dies only after its third own turn ends
        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})
        self.assertTrue(dark.alive)
        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})
        self.assertFalse(dark.alive)

    def test_scenario_ellie_crystal_ball_lasts_four_own_rounds_not_four_global_turns(self) -> None:
        # Given Ellie reaches her turn in a multi-hero ring
        battle = create_battle(["dark_human", "ellie"], ["bard"])
        ellie = next(unit for unit in battle.hero_units(1) if unit.hero_code == "ellie")
        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})

        # When Ellie uses Crystal Ball
        battle.perform_action({"type": "skill", "unit_id": ellie.unit_id, "skill_code": "crystal_ball"})

        # Then two unrelated global turns do not consume the effect
        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})
        self.assertEqual(ellie.get_status("水晶球").duration, 4)

        # And it only drops when Ellie's own next round begins
        battle.perform_action({"type": "end_turn"})
        self.assertEqual(ellie.get_status("水晶球").duration, 3)

    def test_scenario_great_holy_light_damages_only_enemy_normal_movement(self) -> None:
        # Given Bard has created Great Holy Light
        battle = create_battle("bard", "dark_human")
        bard = primary_hero(battle, 1)
        dark = primary_hero(battle, 2)

        battle.perform_action({"type": "skill", "unit_id": bard.unit_id, "skill_code": "great_holy_light"})
        battle.perform_action({"type": "end_turn"})

        # When the enemy finishes a normal move inside the field
        hp_before = dark.current_hp
        battle.perform_action({"type": "move", "unit_id": dark.unit_id, "x": 5, "y": 4})

        # Then the field deals damage
        self.assertLess(dark.current_hp, hp_before)

    def test_scenario_great_holy_light_does_not_damage_skill_movement(self) -> None:
        # Given Bard has created Great Holy Light
        battle = create_battle("bard", "dark_human")
        bard = primary_hero(battle, 1)
        dark = primary_hero(battle, 2)
        bard.position = Position(4, 4)
        dark.position = Position(0, 4)

        battle.perform_action({"type": "skill", "unit_id": bard.unit_id, "skill_code": "great_holy_light"})
        battle.perform_action({"type": "end_turn"})

        # When the enemy enters the area using a movement skill instead of a normal move
        hp_before = dark.current_hp
        battle.perform_action({"type": "skill", "unit_id": dark.unit_id, "skill_code": "fly_leap", "x": 3, "y": 4})

        # Then the field does not damage that movement
        self.assertEqual(dark.current_hp, hp_before)

    def test_scenario_element_hunter_clone_can_act_but_cannot_attack_or_use_skills_and_expires(self) -> None:
        # Given Element Hunter uses Earth Walker
        battle = create_battle("element_hunter", "bard")
        hunter = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        original_position = hunter.position

        battle.perform_action({"type": "skill", "unit_id": hunter.unit_id, "skill_code": "earth_walker", "x": 2, "y": 4})

        # Then the clone keeps the old cell and can still take turn actions this turn
        clones = [unit for unit in battle.all_units() if unit.is_clone]
        self.assertEqual(len(clones), 1)
        clone = clones[0]
        self.assertEqual(hunter.position, Position(2, 4))
        self.assertEqual(clone.position, original_position)
        self.assertTrue(clone.turn_ready)
        self.assertTrue(clone.can_take_turn_actions(battle))

        # But it cannot attack or use skills
        self.assertTrue(clone.cannot_attack)
        self.assertTrue(clone.cannot_use_skills)
        self.assertEqual(clone.skills, [])
        bard.position = Position(1, 5)
        with self.assertRaises(ActionError):
            battle.perform_action({"type": "attack", "unit_id": clone.unit_id, "target_unit_id": bard.unit_id})

        # And it expires before Element Hunter's next own turn begins
        battle.perform_action({"type": "end_turn"})
        self.assertTrue(any(unit.unit_id == clone.unit_id for unit in battle.all_units()))
        battle.perform_action({"type": "end_turn"})
        self.assertFalse(any(unit.unit_id == clone.unit_id for unit in battle.all_units()))

    def test_scenario_rock_absorb_allows_stealthed_targets_to_chain_protection_per_target(self) -> None:
        # Given Rock God hits a stealthed Dark Human with Rock Absorb
        battle = create_battle("rock_god", "dark_human")
        rock = primary_hero(battle, 1)
        dark = primary_hero(battle, 2)
        rock.position = Position(3, 3)
        dark.position = Position(6, 3)
        dark.add_status(StatusEffect("隐身"))

        battle.perform_action(
            {
                "type": "skill",
                "unit_id": rock.unit_id,
                "skill_code": "rock_absorb",
                "stat_name": "attack",
                "cells": [{"x": 2, "y": 3}],
            }
        )

        # Then the stealthed target still gets a protection reaction window
        self.assertIsNotNone(battle.pending_chain)
        options = battle.to_public_dict()["pending_chain"]["options_by_unit"].get(dark.unit_id, [])
        self.assertIn("protection", {option["action_code"] for option in options})

        # When Dark Human reacts with Protection
        battle.perform_action({"type": "chain_react", "unit_id": dark.unit_id, "action_code": "protection"})

        # Then that target's absorption is blocked without granting Rock God the extra body cell
        self.assertEqual(dark.stat("attack"), 3)
        self.assertEqual(rock.stat("attack"), 3)
        self.assertNotIn(Position(2, 3), battle.unit_cells(rock))
        self.assertEqual(dark.shields, 1)

    def test_scenario_doomlight_dragon_apocalypse_allows_n_one_above_one_hp(self) -> None:
        # Given Doomlight Dragon has slightly more than 1 hp
        battle = create_battle("doomlight_dragon", "bard")
        dragon = primary_hero(battle, 1)
        bard = primary_hero(battle, 2)
        dragon.position = Position(0, 0)
        bard.position = Position(3, 3)
        dragon.current_hp = 1.25

        # When the player chooses Apocalypse with n = 1
        battle.perform_action(
            {
                "type": "skill",
                "unit_id": dragon.unit_id,
                "skill_code": "apocalypse",
                "n": 1,
                "cells": [{"x": 3, "y": 3}],
            }
        )
        resolve_pending_chain(battle)

        # Then the skill is legal, costs 1 hp, and still hits the target cell
        self.assertAlmostEqual(dragon.current_hp, 0.25)
        self.assertLess(bard.current_hp, 1.0)

    def test_scenario_masamune_redirects_threats_to_mount_and_only_gains_block_counter_after_dismount(self) -> None:
        # Given Masamune starts the battle mounted
        battle = create_battle("fire_funeral", "masamune")
        fire = primary_hero(battle, 1)
        masamune = primary_hero(battle, 2)
        mount = summon_by_code(battle, 2, "motor_horse")
        fire.position = Position(4, 4)
        mount.position = Position(5, 4)
        masamune.position = Position(5, 4)

        self.assertIs(battle.mounted_unit_for(masamune), mount)

        # When the enemy attacks Masamune while he is mounted
        battle.perform_action({"type": "attack", "unit_id": fire.unit_id, "target_unit_id": masamune.unit_id, "x": 5, "y": 4})

        # Then the mounted version does not offer block/counter and the mount takes the hit
        self.assertIsNotNone(battle.pending_chain)
        while battle.pending_chain is not None and battle.pending_chain.current_unit_id() != masamune.unit_id:
            battle.perform_action({"type": "chain_skip"})
        mounted_codes = {option.action_code for option in battle.pending_chain.options_by_unit.get(masamune.unit_id, [])}
        self.assertNotIn("block", mounted_codes)
        self.assertNotIn("counter", mounted_codes)
        battle.perform_action({"type": "chain_skip"})
        self.assertAlmostEqual(masamune.current_hp, 1.0)
        self.assertLess(mount.current_hp, 1.0)

        # When Masamune is no longer mounted
        battle = create_battle("fire_funeral", "masamune")
        fire = primary_hero(battle, 1)
        masamune = primary_hero(battle, 2)
        mount = summon_by_code(battle, 2, "motor_horse")
        fire.position = Position(4, 4)
        mount.position = Position(1, 1)
        masamune.position = Position(5, 4)
        battle.clear_mounted_state(masamune)

        battle.perform_action(
            {"type": "attack", "unit_id": fire.unit_id, "target_unit_id": masamune.unit_id, "x": 5, "y": 4}
        )

        # Then the unmounted version does offer block and counter
        while battle.pending_chain is not None and battle.pending_chain.current_unit_id() != masamune.unit_id:
            battle.perform_action({"type": "chain_skip"})
        unmounted_codes = {option.action_code for option in battle.pending_chain.options_by_unit.get(masamune.unit_id, [])}
        self.assertIn("block", unmounted_codes)
        self.assertIn("counter", unmounted_codes)

    def test_scenario_jade_stance_only_protects_other_allies_for_the_next_enemy_turn(self) -> None:
        # Given Jade prepares Stance around an ally
        battle = create_battle("jade", "fire_funeral")
        jade = primary_hero(battle, 1)
        ally = create_hero("bard", 1)
        enemy = primary_hero(battle, 2)
        battle.add_unit(ally, Position(4, 4))
        jade.position = Position(3, 4)
        enemy.position = Position(6, 4)

        battle.perform_action({"type": "skill", "unit_id": jade.unit_id, "skill_code": "stance"})
        battle.perform_action({"type": "end_turn"})

        # When the next enemy turn tries to damage the ally and Jade
        ally_ctx = battle.resolve_damage(
            DamageContext(source=enemy, target=ally, attack_power=5, is_skill=False, action_name="test attack")
        )
        jade_ctx = battle.resolve_damage(
            DamageContext(source=enemy, target=jade, attack_power=5, is_skill=False, action_name="test attack")
        )

        # Then Stance protects the ally, but not Jade herself
        self.assertTrue(ally_ctx.cancelled)
        self.assertAlmostEqual(ally.current_hp, 1.0)
        self.assertFalse(jade_ctx.cancelled)
        self.assertLess(jade.current_hp, 1.0)

        # And the field expires once that enemy turn is over
        battle.perform_action({"type": "end_turn"})
        self.assertFalse(any(effect.name == "立场" for effect in battle.field_effects))

    def test_scenario_jade_quantum_shield_uses_three_casts_then_locks_the_next_own_cycle(self) -> None:
        # Given Jade has a threatened ally during a usable Quantum Shield cycle
        battle = create_battle("jade", "bard")
        jade = primary_hero(battle, 1)
        ally = create_hero("dark_human", 1)
        enemy = primary_hero(battle, 2)
        battle.add_unit(ally, Position(4, 4))
        jade.position = Position(4, 3)
        enemy.position = Position(6, 3)
        skill = skill_by_code(jade, "quantum_shield")
        threatened_cells = [Position(4, 3), Position(4, 4)]
        queued = battle.build_skill_effect_action(
            actor=enemy,
            display_name="test area damage",
            effect_code="area_damage",
            payload={"cells": [cell.to_dict() for cell in threatened_cells], "attack_power": 4, "tags": ["skill"]},
            target_cells=threatened_cells,
            speed=1,
        )
        reaction_payload = {"target_unit_ids": [jade.unit_id, ally.unit_id]}

        battle.perform_action({"type": "end_turn"})

        # When Jade spends all three casts in the current usable cycle
        for expected_uses in range(1, 4):
            ok, reason = skill.can_react_with_payload(battle, jade, queued, reaction_payload)
            self.assertTrue(ok, reason)
            skill.prepay_resources(battle, jade, reaction_payload)
            skill.react(battle, jade, reaction_payload, queued)
            self.assertEqual(skill.uses_this_turn, expected_uses)
            battle.expire_chain_temporary_statuses()

        # Then the next own cycle is unavailable
        ok, _ = skill.can_react_with_payload(battle, jade, queued, reaction_payload)
        self.assertFalse(ok)
        self.assertTrue(skill.cooldown_pending)

        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})
        ok, reason = skill.can_react_with_payload(battle, jade, queued, reaction_payload)
        self.assertFalse(ok)
        self.assertEqual(reason, "技能冷却中。")

        # And the following own cycle becomes usable again
        battle.perform_action({"type": "end_turn"})
        battle.perform_action({"type": "end_turn"})
        ok, reason = skill.can_react_with_payload(battle, jade, queued, reaction_payload)
        self.assertTrue(ok, reason)

    def test_scenario_public_battle_visual_events_cover_attacks_skills_and_shield_blocks(self) -> None:
        # Given Bard threatens a shielded Dark Human
        battle = create_battle("bard", "dark_human")
        bard = primary_hero(battle, 1)
        dark = primary_hero(battle, 2)
        bard.position = Position(4, 4)
        dark.position = Position(5, 4)
        dark.shields = 1

        # When Bard attacks into the shield
        battle.perform_action({"type": "attack", "unit_id": bard.unit_id, "target_unit_id": dark.unit_id})

        # Then the public state exposes both the outgoing attack and the defensive block event
        public_after_attack = battle.to_public_dict()
        attack_events = [event for event in public_after_attack["visual_events"] if event["kind"] == "attack"]
        defense_events = [event for event in public_after_attack["visual_events"] if event["kind"] == "defense"]
        self.assertTrue(attack_events)
        self.assertEqual(attack_events[-1]["actor_id"], bard.unit_id)
        self.assertIn(dark.unit_id, attack_events[-1]["target_unit_ids"])
        self.assertTrue(defense_events)
        self.assertEqual(defense_events[-1]["defense_reason"], "shield")
        self.assertIn(dark.unit_id, defense_events[-1]["target_unit_ids"])

        # And when Bard follows up with a skill cast
        battle.perform_action({"type": "skill", "unit_id": bard.unit_id, "skill_code": "great_holy_light"})

        # Then the skill cast is also present in the visual event feed
        public_after_skill = battle.to_public_dict()
        skill_events = [
            event
            for event in public_after_skill["visual_events"]
            if event["kind"] == "skill" and event["action_code"] == "great_holy_light"
        ]
        self.assertTrue(skill_events)

    def test_scenario_stealthed_enemy_visual_events_are_filtered_from_the_opponent_view(self) -> None:
        # Given Dark Human becomes stealthed on its own turn
        battle = create_battle("dark_human", "bard")
        dark = primary_hero(battle, 1)
        dark.position = Position(4, 4)
        battle.perform_action({"type": "skill", "unit_id": dark.unit_id, "skill_code": "stealth"})

        # When the opposing viewer reads the public battle state
        enemy_view = battle_state_for_viewer(battle, 2)

        # Then the stealthed unit's visual event does not leak through the event feed
        self.assertFalse(enemy_view["visual_events"])

    def test_scenario_n_instant_skill_is_exposed_to_the_waiting_player_and_room_allows_it(self) -> None:
        # Given a room where player 2 controls N during player 1's turn
        room = GameRoom("n-skill")
        _, host_token = room.create_host("p1")
        _, guest_token = room.join("p2")
        room.select_hero(host_token, "dark_human", 1)
        room.select_hero(guest_token, "n", 1)
        room.start_battle(host_token)
        battle = room.battle
        assert battle is not None
        attacker = primary_hero(battle, 1)
        caster = primary_hero(battle, 2)
        attacker.position = Position(5, 4)
        caster.position = Position(4, 4)
        caster.mana_points = 2

        # When player 2 reads the viewer state while waiting
        viewer_state = room.serialize_state(guest_token)["battle"]

        # Then that viewer still gets an interactable instant-skill unit bundle
        self.assertEqual(viewer_state["input_player"], 2)
        self.assertEqual([entry["unit_id"] for entry in viewer_state["active_units"]], [caster.unit_id])
        actions = viewer_state["active_units"][0]["actions"]["actions"]
        magnetic_wave = next(action for action in actions if action["code"] == "magnetic_wave")
        self.assertTrue(magnetic_wave["available"])
        self.assertEqual(magnetic_wave["timing"], "instant")

        # And when player 2 uses Magnetic Wave through the room permission layer
        room.perform_action(
            guest_token,
            {
                "type": "skill",
                "unit_id": caster.unit_id,
                "skill_code": "magnetic_wave",
                "cells": [
                    {"x": 4, "y": 3},
                    {"x": 4, "y": 4},
                    {"x": 4, "y": 5},
                    {"x": 5, "y": 3},
                    {"x": 5, "y": 4},
                    {"x": 5, "y": 5},
                    {"x": 6, "y": 3},
                    {"x": 6, "y": 4},
                    {"x": 6, "y": 5},
                ],
            },
        )

        # Then the current actor loses the rest of this turn
        self.assertFalse(attacker.turn_ready)
        self.assertAlmostEqual(caster.mana_points, 0.0)


@unittest.skipIf(quickjs is None, "quickjs is required for frontend behavior checks")
class FrontendBehaviorTests(unittest.TestCase):
    def test_scenario_room_directory_renders_and_primary_join_button_starts_join_flow(self) -> None:
        app_source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        ctx = quickjs.Context()
        ctx.eval(
            """
            function createClassList() {
              return {
                add() {},
                remove() {},
                toggle() {},
                contains() { return false; },
              };
            }

            function createElement(tagName, id) {
              const element = {
                tagName: String(tagName || "div").toUpperCase(),
                id: id || "",
                children: [],
                listeners: {},
                className: "",
                value: "",
                disabled: false,
                dataset: {},
                style: {},
                _textContent: "",
                _innerHTML: "",
                classList: createClassList(),
                append(...nodes) {
                  this.children.push(...nodes);
                },
                appendChild(node) {
                  this.children.push(node);
                  return node;
                },
                addEventListener(type, handler) {
                  if (!this.listeners[type]) this.listeners[type] = [];
                  this.listeners[type].push(handler);
                },
                querySelector() {
                  return null;
                },
                querySelectorAll() {
                  return [];
                },
                replaceWith() {},
                focus() {},
                set innerHTML(value) {
                  this._innerHTML = String(value);
                },
                get innerHTML() {
                  return this._innerHTML;
                },
                set textContent(value) {
                  this._textContent = String(value);
                },
                get textContent() {
                  return this._textContent;
                },
              };
              return element;
            }

            const document = {
              elements: {},
              listeners: {},
              body: createElement("body", "body"),
              getElementById(id) {
                if (!this.elements[id]) this.elements[id] = createElement("div", id);
                return this.elements[id];
              },
              createElement(tagName) {
                return createElement(tagName);
              },
              querySelector() {
                return null;
              },
              querySelectorAll() {
                return [];
              },
              addEventListener(type, handler) {
                if (!this.listeners[type]) this.listeners[type] = [];
                this.listeners[type].push(handler);
              },
            };
            document.body.classList = createClassList();

            const storageFactory = () => ({
              _store: {},
              getItem(key) {
                return Object.prototype.hasOwnProperty.call(this._store, key) ? this._store[key] : null;
              },
              setItem(key, value) {
                this._store[key] = String(value);
              },
              removeItem(key) {
                delete this._store[key];
              },
            });

            const window = {
              location: { href: "http://example.test/", search: "", hash: "" },
              listeners: {},
              addEventListener(type, handler) {
                if (!this.listeners[type]) this.listeners[type] = [];
                this.listeners[type].push(handler);
              },
              requestAnimationFrame(callback) { return callback(); },
              cancelAnimationFrame() {},
              setInterval() { return 1; },
              clearInterval() {},
            };

            const history = { replaceState() {} };
            const localStorage = storageFactory();
            const sessionStorage = storageFactory();
            """
        )
        ctx.eval(app_source)
        ctx.eval(
            """
            canReclaimSeatByName = function () { return false; };
            loadStoredIdentity = function () { return { token: "", name: "" }; };
            joinListedRoom = function (roomId) { globalThis.joinRoomCalledWith = roomId; };
            globalThis.joinRoomCalledWith = "";
            state.profileReady = true;
            state.rooms = [
              {
                room_id: "AB12CD",
                can_join: true,
                status: "lobby",
                occupied_seat_count: 1,
                seat_count: 2,
                mode: "classic",
                mode_name: "标准选将",
                is_full: false,
                seats: [
                  { player_id: 1, occupied: true, name: "Alice", hero_total_count: 1, hero_summary: "吟游诗人 × 1" },
                  { player_id: 2, occupied: false, name: "", hero_total_count: 0, hero_summary: null },
                ],
              },
            ];
            renderRoomListActive();
            """
        )

        self.assertEqual(ctx.eval("document.elements['room-list'].children.length"), 1)

        ctx.eval("document.elements['room-list'].children[0].children[0].children[0].listeners.click[0]();")
        self.assertEqual(ctx.eval("globalThis.joinRoomCalledWith"), "AB12CD")

    def test_scenario_random_room_roster_size_control_reflects_state_and_wires_host_change(self) -> None:
        app_source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        ctx = quickjs.Context()
        ctx.eval(
            """
            function createClassList() {
              return {
                add() {},
                remove() {},
                toggle() {},
                contains() { return false; },
              };
            }

            function createElement(tagName, id) {
              const element = {
                tagName: String(tagName || "div").toUpperCase(),
                id: id || "",
                children: [],
                listeners: {},
                className: "",
                value: "",
                disabled: false,
                dataset: {},
                style: {},
                _textContent: "",
                _innerHTML: "",
                classList: createClassList(),
                append(...nodes) {
                  this.children.push(...nodes);
                },
                appendChild(node) {
                  this.children.push(node);
                  return node;
                },
                addEventListener(type, handler) {
                  if (!this.listeners[type]) this.listeners[type] = [];
                  this.listeners[type].push(handler);
                },
                querySelector() {
                  return null;
                },
                querySelectorAll() {
                  return [];
                },
                replaceWith() {},
                focus() {},
                set innerHTML(value) {
                  this._innerHTML = String(value);
                },
                get innerHTML() {
                  return this._innerHTML;
                },
                set textContent(value) {
                  this._textContent = String(value);
                },
                get textContent() {
                  return this._textContent;
                },
              };
              return element;
            }

            const document = {
              elements: {},
              listeners: {},
              body: createElement("body", "body"),
              getElementById(id) {
                if (!this.elements[id]) this.elements[id] = createElement("div", id);
                return this.elements[id];
              },
              createElement(tagName) {
                return createElement(tagName);
              },
              querySelector() {
                return null;
              },
              querySelectorAll() {
                return [];
              },
              addEventListener(type, handler) {
                if (!this.listeners[type]) this.listeners[type] = [];
                this.listeners[type].push(handler);
              },
            };
            document.body.classList = createClassList();

            const storageFactory = () => ({
              _store: {},
              getItem(key) {
                return Object.prototype.hasOwnProperty.call(this._store, key) ? this._store[key] : null;
              },
              setItem(key, value) {
                this._store[key] = String(value);
              },
              removeItem(key) {
                delete this._store[key];
              },
            });

            const window = {
              location: { href: "http://example.test/?room=AB12CD", search: "?room=AB12CD", hash: "#draft" },
              listeners: {},
              addEventListener(type, handler) {
                if (!this.listeners[type]) this.listeners[type] = [];
                this.listeners[type].push(handler);
              },
              requestAnimationFrame(callback) { return callback(); },
              cancelAnimationFrame() {},
              setInterval() { return 1; },
              clearInterval() {},
            };

            const history = { replaceState() {} };
            const localStorage = storageFactory();
            const sessionStorage = storageFactory();
            """
        )
        ctx.eval(app_source)
        ctx.eval(
            """
            globalThis.randomRosterSizeCall = "";
            setRandomRosterSize = function (value) { globalThis.randomRosterSizeCall = String(value); };
            state.profileReady = true;
            state.playerToken = "host-token";
            state.room = {
              room_id: "AB12CD",
              status: "lobby",
              mode: "random",
              mode_name: "随机选人",
              random_roster_size: 4,
              viewer_player_id: 1,
              viewer_name: "Alice",
              viewer_is_host: true,
              can_start: false,
              can_rematch: false,
              is_full: true,
              invite_url: "http://example.test/?room=AB12CD",
              invite_path: "/?room=AB12CD",
              available_modes: [
                { code: "classic", name: "标准选将", description: "" },
                { code: "random", name: "随机选人", description: "" }
              ],
              seats: [
                { player_id: 1, occupied: true, name: "Alice", hero_total_count: 0, hero_summary: null, is_host: true },
                { player_id: 2, occupied: true, name: "Bob", hero_total_count: 0, hero_summary: null, is_host: false }
              ]
            };
            bindEvents();
            applyRandomRoomPanelState();
            globalThis.randomRosterInputValue = document.elements["random-roster-size-input"].value;
            globalThis.randomRosterNoteText = document.elements["random-roster-size-note"].textContent;
            document.activeElement = document.elements["random-roster-size-input"];
            document.elements["random-roster-size-input"].value = "5";
            document.elements["random-roster-size-input"].listeners.input[0]({ target: document.elements["random-roster-size-input"] });
            applyRandomRoomPanelState();
            globalThis.randomRosterEditingValue = document.elements["random-roster-size-input"].value;
            document.elements["random-roster-size-input"].value = "5";
            document.elements["random-roster-size-input"].listeners.change[0]({ target: document.elements["random-roster-size-input"] });
            """
        )

        self.assertEqual(ctx.eval("globalThis.randomRosterInputValue"), "4")
        self.assertIn("4", ctx.eval("globalThis.randomRosterNoteText"))
        self.assertIn("不重复", ctx.eval("globalThis.randomRosterNoteText"))
        self.assertEqual(ctx.eval("globalThis.randomRosterEditingValue"), "5")
        self.assertEqual(ctx.eval("globalThis.randomRosterSizeCall"), "5")

    def test_scenario_multi_seat_room_panel_renders_four_seats_and_ai_configuration_state(self) -> None:
        app_source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        ctx = quickjs.Context()
        ctx.eval(
            """
            function createClassList() {
              return {
                add() {},
                remove() {},
                toggle() {},
                contains() { return false; },
              };
            }

            function createElement(tagName, id) {
              const element = {
                tagName: String(tagName || "div").toUpperCase(),
                id: id || "",
                children: [],
                listeners: {},
                className: "",
                value: "",
                disabled: false,
                dataset: {},
                style: {},
                _textContent: "",
                _innerHTML: "",
                classList: createClassList(),
                append(...nodes) {
                  this.children.push(...nodes);
                },
                appendChild(node) {
                  this.children.push(node);
                  return node;
                },
                addEventListener(type, handler) {
                  if (!this.listeners[type]) this.listeners[type] = [];
                  this.listeners[type].push(handler);
                },
                querySelector() {
                  return null;
                },
                querySelectorAll() {
                  return [];
                },
                replaceWith() {},
                focus() {},
                set innerHTML(value) {
                  this._innerHTML = String(value);
                },
                get innerHTML() {
                  return this._innerHTML;
                },
                set textContent(value) {
                  this._textContent = String(value);
                },
                get textContent() {
                  return this._textContent;
                },
              };
              return element;
            }

            const document = {
              elements: {},
              listeners: {},
              body: createElement("body", "body"),
              getElementById(id) {
                if (!this.elements[id]) this.elements[id] = createElement("div", id);
                return this.elements[id];
              },
              createElement(tagName) {
                return createElement(tagName);
              },
              querySelector() {
                return null;
              },
              querySelectorAll() {
                return [];
              },
              addEventListener(type, handler) {
                if (!this.listeners[type]) this.listeners[type] = [];
                this.listeners[type].push(handler);
              },
            };
            document.body.classList = createClassList();

            const storageFactory = () => ({
              _store: {},
              getItem(key) {
                return Object.prototype.hasOwnProperty.call(this._store, key) ? this._store[key] : null;
              },
              setItem(key, value) {
                this._store[key] = String(value);
              },
              removeItem(key) {
                delete this._store[key];
              },
            });

            const window = {
              location: { href: "http://example.test/?room=AB12CD", search: "?room=AB12CD", hash: "#draft" },
              listeners: {},
              addEventListener(type, handler) {
                if (!this.listeners[type]) this.listeners[type] = [];
                this.listeners[type].push(handler);
              },
              requestAnimationFrame(callback) { return callback(); },
              cancelAnimationFrame() {},
              setInterval() { return 1; },
              clearInterval() {},
            };

            const history = { replaceState() {} };
            const localStorage = storageFactory();
            const sessionStorage = storageFactory();
            """
        )
        ctx.eval(app_source)
        ctx.eval(
            """
            canReclaimSeatByName = function () { return false; };
            state.profileReady = true;
            state.playerToken = "host-token";
            state.room = {
              room_id: "AB12CD",
              status: "lobby",
              mode: "random",
              mode_name: "随机选人",
              random_roster_size: 2,
              viewer_player_id: 1,
              viewer_team_id: 1,
              viewer_name: "Alice",
              viewer_is_host: true,
              can_start: false,
              can_rematch: false,
              is_full: true,
              seat_count: 4,
              seat_count_min: 2,
              seat_count_max: 6,
              start_blocker: "红队的随机武将配额之和必须等于 n = 2。",
              invite_url: "http://example.test/?room=AB12CD",
              invite_path: "/?room=AB12CD",
              available_modes: [
                { code: "classic", name: "标准选将", description: "" },
                { code: "random", name: "随机选人", description: "" }
              ],
              seats: [
                { player_id: 1, occupied: true, is_human: true, is_ai: false, controller_type: "human", team_id: 1, team_name: "红队", name: "Alice", hero_total_count: 0, hero_summary: null, random_quota: 2, is_host: true },
                { player_id: 2, occupied: true, is_human: true, is_ai: false, controller_type: "human", team_id: 2, team_name: "蓝队", name: "Bob", hero_total_count: 0, hero_summary: null, random_quota: 2, is_host: false },
                { player_id: 3, occupied: true, is_human: false, is_ai: true, controller_type: "ai", team_id: 1, team_name: "红队", name: "AI 3", hero_total_count: 0, hero_summary: null, random_quota: 0, is_host: false },
                { player_id: 4, occupied: false, is_human: false, is_ai: false, controller_type: "open", team_id: 2, team_name: "蓝队", name: null, hero_total_count: 0, hero_summary: null, random_quota: 0, is_host: false }
              ]
            };
            renderRoomPanels();
            globalThis.seatCardCount = document.elements["seat-cards"].children.length;
            globalThis.seatCountValue = document.elements["room-seat-count-input"].value;
            globalThis.viewerSeatLabel = document.elements["viewer-seat-label"].textContent;
            globalThis.roomMessageText = document.elements["room-message"].textContent;
            globalThis.thirdSeatHtml = document.elements["seat-cards"].children[2].innerHTML;
            """
        )

        self.assertEqual(ctx.eval("globalThis.seatCardCount"), 4)
        self.assertEqual(ctx.eval("globalThis.seatCountValue"), "4")
        self.assertEqual(ctx.eval("globalThis.viewerSeatLabel"), "席位 1")
        self.assertIn("随机武将配额", ctx.eval("globalThis.roomMessageText"))
        self.assertIn("AI", ctx.eval("globalThis.thirdSeatHtml"))
        self.assertIn("红队", ctx.eval("globalThis.thirdSeatHtml"))

    def test_scenario_render_header_shows_dynamic_next_turn_summary(self) -> None:
        app_source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        ctx = quickjs.Context()
        ctx.eval(
            """
            function createClassList() {
              return {
                add() {},
                remove() {},
                toggle() {},
                contains() { return false; },
              };
            }

            function createElement(tagName, id) {
              const element = {
                tagName: String(tagName || "div").toUpperCase(),
                id: id || "",
                children: [],
                listeners: {},
                className: "",
                value: "",
                disabled: false,
                dataset: {},
                style: {},
                _textContent: "",
                _innerHTML: "",
                classList: createClassList(),
                append(...nodes) {
                  this.children.push(...nodes);
                },
                appendChild(node) {
                  this.children.push(node);
                  return node;
                },
                addEventListener(type, handler) {
                  if (!this.listeners[type]) this.listeners[type] = [];
                  this.listeners[type].push(handler);
                },
                querySelector() {
                  return null;
                },
                querySelectorAll() {
                  return [];
                },
                replaceWith() {},
                focus() {},
                set innerHTML(value) {
                  this._innerHTML = String(value);
                },
                get innerHTML() {
                  return this._innerHTML;
                },
                set textContent(value) {
                  this._textContent = String(value);
                },
                get textContent() {
                  return this._textContent;
                },
              };
              return element;
            }

            const document = {
              elements: {},
              listeners: {},
              body: createElement("body", "body"),
              getElementById(id) {
                if (!this.elements[id]) this.elements[id] = createElement("div", id);
                return this.elements[id];
              },
              createElement(tagName) {
                return createElement(tagName);
              },
              querySelector() {
                return null;
              },
              querySelectorAll() {
                return [];
              },
              addEventListener(type, handler) {
                if (!this.listeners[type]) this.listeners[type] = [];
                this.listeners[type].push(handler);
              },
            };
            document.body.classList = createClassList();

            const storageFactory = () => ({
              _store: {},
              getItem(key) {
                return Object.prototype.hasOwnProperty.call(this._store, key) ? this._store[key] : null;
              },
              setItem(key, value) {
                this._store[key] = String(value);
              },
              removeItem(key) {
                delete this._store[key];
              },
            });

            const window = {
              location: { href: "http://example.test/?room=AB12CD", search: "?room=AB12CD", hash: "#battle" },
              listeners: {},
              addEventListener(type, handler) {
                if (!this.listeners[type]) this.listeners[type] = [];
                this.listeners[type].push(handler);
              },
              requestAnimationFrame(callback) { return callback(); },
              cancelAnimationFrame() {},
              setInterval() { return 1; },
              clearInterval() {},
            };

            const history = { replaceState() {} };
            const localStorage = storageFactory();
            const sessionStorage = storageFactory();
            """
        )
        ctx.eval(app_source)
        ctx.eval(
            """
            state.room = {
              room_id: "AB12CD",
              status: "battle",
              mode: "classic",
              mode_name: "标准选将",
              viewer_player_id: 1,
              viewer_name: "Alice",
              viewer_is_host: true,
              can_start: false,
              can_rematch: false,
              is_full: true,
              seats: [],
            };
            state.battle = {
              winner: null,
              pending_chain: null,
              pending_respawn: null,
              round_number: 3,
              input_player: 1,
              active_turn_unit_name: "艾莉",
              next_turn_unit_name: "吟游诗人",
              next_turn_player_id: 2,
            };
            renderHeader();
            globalThis.turnPillText = document.elements["turn-pill"].textContent;
            globalThis.topbarSublineText = document.elements["topbar-subline"].textContent;
            globalThis.boardCaptionText = document.elements["board-caption"].textContent;
            """
        )

        self.assertIn("艾莉", ctx.eval("globalThis.turnPillText"))
        self.assertIn("下回合：玩家 2 的 吟游诗人。", ctx.eval("globalThis.topbarSublineText"))
        self.assertIn("下回合：玩家 2 的 吟游诗人。", ctx.eval("globalThis.boardCaptionText"))

    def test_scenario_board_zoom_and_stage_scroll_reposition_board_overlays(self) -> None:
        app_source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        ctx = quickjs.Context()
        ctx.eval(
            """
            function createClassList() {
              return {
                add() {},
                remove() {},
                toggle() {},
                contains() { return false; },
              };
            }

            function createElement(tagName, id) {
              const element = {
                tagName: String(tagName || "div").toUpperCase(),
                id: id || "",
                children: [],
                listeners: {},
                className: "",
                value: "",
                disabled: false,
                dataset: {},
                style: {},
                _textContent: "",
                _innerHTML: "",
                classList: createClassList(),
                append(...nodes) {
                  this.children.push(...nodes);
                },
                appendChild(node) {
                  this.children.push(node);
                  return node;
                },
                addEventListener(type, handler) {
                  if (!this.listeners[type]) this.listeners[type] = [];
                  this.listeners[type].push(handler);
                },
                querySelector() {
                  return null;
                },
                querySelectorAll() {
                  return [];
                },
                replaceWith() {},
                focus() {},
                set innerHTML(value) {
                  this._innerHTML = String(value);
                },
                get innerHTML() {
                  return this._innerHTML;
                },
                set textContent(value) {
                  this._textContent = String(value);
                },
                get textContent() {
                  return this._textContent;
                },
              };
              return element;
            }

            const document = {
              elements: {},
              listeners: {},
              body: createElement("body", "body"),
              getElementById(id) {
                if (!this.elements[id]) this.elements[id] = createElement("div", id);
                return this.elements[id];
              },
              createElement(tagName) {
                return createElement(tagName);
              },
              querySelector() {
                return null;
              },
              querySelectorAll() {
                return [];
              },
              addEventListener(type, handler) {
                if (!this.listeners[type]) this.listeners[type] = [];
                this.listeners[type].push(handler);
              },
            };
            document.body.classList = createClassList();

            const storageFactory = () => ({
              _store: {},
              getItem(key) {
                return Object.prototype.hasOwnProperty.call(this._store, key) ? this._store[key] : null;
              },
              setItem(key, value) {
                this._store[key] = String(value);
              },
              removeItem(key) {
                delete this._store[key];
              },
            });

            const window = {
              location: { href: "http://example.test/", search: "", hash: "" },
              listeners: {},
              addEventListener(type, handler) {
                if (!this.listeners[type]) this.listeners[type] = [];
                this.listeners[type].push(handler);
              },
              requestAnimationFrame(callback) { return callback(); },
              cancelAnimationFrame() {},
              setInterval() { return 1; },
              clearInterval() {},
            };

            const history = { replaceState() {} };
            const localStorage = storageFactory();
            const sessionStorage = storageFactory();
            """
        )
        ctx.eval(app_source)
        ctx.eval(
            """
            globalThis.actionWheelRenderCount = 0;
            globalThis.boardAlertRenderCount = 0;
            renderActionWheel = function () { globalThis.actionWheelRenderCount += 1; };
            renderBoardAlert = function () { globalThis.boardAlertRenderCount += 1; };
            renderBoard = function () {};
            renderBoardZoomControls = function () {};
            state.battle = { board: { width: 10, height: 10 } };
            bindEvents();
            document.elements["board-stage"].listeners.scroll[0]();
            adjustBoardZoom(0.15);
            window.listeners.resize[0]();
            """
        )

        self.assertEqual(ctx.eval("globalThis.actionWheelRenderCount"), 3)
        self.assertEqual(ctx.eval("globalThis.boardAlertRenderCount"), 3)

    def test_scenario_action_wheel_buttons_stay_inside_stage_near_board_edge(self) -> None:
        app_source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        ctx = quickjs.Context()
        ctx.eval(
            """
            function createClassList() {
              return {
                add() {},
                remove() {},
                toggle() {},
                contains() { return false; },
              };
            }

            function createElement(tagName, id) {
              const element = {
                tagName: String(tagName || "div").toUpperCase(),
                id: id || "",
                children: [],
                listeners: {},
                className: "",
                value: "",
                disabled: false,
                dataset: {},
                style: {},
                _textContent: "",
                _innerHTML: "",
                classList: createClassList(),
                append(...nodes) {
                  this.children.push(...nodes);
                },
                appendChild(node) {
                  this.children.push(node);
                  return node;
                },
                addEventListener(type, handler) {
                  if (!this.listeners[type]) this.listeners[type] = [];
                  this.listeners[type].push(handler);
                },
                querySelector() {
                  return null;
                },
                querySelectorAll() {
                  return [];
                },
                replaceWith() {},
                focus() {},
                set innerHTML(value) {
                  this._innerHTML = String(value);
                  this.children = [];
                },
                get innerHTML() {
                  return this._innerHTML;
                },
                set textContent(value) {
                  this._textContent = String(value);
                },
                get textContent() {
                  return this._textContent;
                },
              };
              return element;
            }

            const document = {
              elements: {},
              listeners: {},
              body: createElement("body", "body"),
              getElementById(id) {
                if (!this.elements[id]) this.elements[id] = createElement("div", id);
                return this.elements[id];
              },
              createElement(tagName) {
                return createElement(tagName);
              },
              querySelector() {
                return null;
              },
              querySelectorAll() {
                return [];
              },
              addEventListener(type, handler) {
                if (!this.listeners[type]) this.listeners[type] = [];
                this.listeners[type].push(handler);
              },
            };
            document.body.classList = createClassList();

            const storageFactory = () => ({
              _store: {},
              getItem(key) {
                return Object.prototype.hasOwnProperty.call(this._store, key) ? this._store[key] : null;
              },
              setItem(key, value) {
                this._store[key] = String(value);
              },
              removeItem(key) {
                delete this._store[key];
              },
            });

            const window = {
              location: { href: "http://example.test/", search: "", hash: "" },
              listeners: {},
              addEventListener(type, handler) {
                if (!this.listeners[type]) this.listeners[type] = [];
                this.listeners[type].push(handler);
              },
              requestAnimationFrame(callback) { return callback(); },
              cancelAnimationFrame() {},
              setInterval() { return 1; },
              clearInterval() {},
            };

            const history = { replaceState() {} };
            const localStorage = storageFactory();
            const sessionStorage = storageFactory();
            """
        )
        ctx.eval(app_source)
        ctx.eval(
            """
            const topEdgeCell = createElement("div", "cell-2-1");
            topEdgeCell.dataset = { x: "2", y: "1" };
            topEdgeCell.getBoundingClientRect = function () {
              return { left: 150, top: 8, right: 210, bottom: 68, width: 60, height: 60 };
            };
            const boardStage = document.getElementById("board-stage");
            document.querySelectorAll = function (selector) {
              if (selector === ".cell") return [topEdgeCell];
              return [];
            };
            boardStage.getBoundingClientRect = function () {
              return { left: 0, top: 0, right: 360, bottom: 260, width: 360, height: 260 };
            };
            state.screen = "battle";
            state.room = { viewer_player_id: 1 };
            state.selectedUnitId = "u1";
            state.battle = {
              input_player: 1,
              units: [
                {
                  id: "u1",
                  player_id: 1,
                  position: { x: 2, y: 1 },
                  occupied_cells: [{ x: 2, y: 1 }],
                }
              ],
              active_units: [
                {
                  unit_id: "u1",
                  actions: {
                    actions: [
                      { code: "move", kind: "move", timing: "active", available: true, name: "ç§»åŠ¨" },
                      { code: "attack", kind: "attack", timing: "active", available: true, name: "æ”»å‡»" },
                      { code: "machine_gun", kind: "skill", timing: "active", available: true, name: "æœºæžª", action_name: "æœºæžª" },
                    ]
                  },
                  reactions: { actions: [] },
                }
              ],
            };
            renderActionWheel();
            const buttons = document.elements["action-wheel"].children;
            globalThis.actionWheelButtonCount = buttons.length;
            globalThis.minActionLeft = Infinity;
            globalThis.minActionTop = Infinity;
            globalThis.maxActionRight = -Infinity;
            globalThis.maxActionBottom = -Infinity;
            for (let index = 0; index < buttons.length; index += 1) {
              const button = buttons[index];
              const left = Number.parseFloat(button.style.left || "0");
              const top = Number.parseFloat(button.style.top || "0");
              globalThis.minActionLeft = Math.min(globalThis.minActionLeft, left);
              globalThis.minActionTop = Math.min(globalThis.minActionTop, top);
              globalThis.maxActionRight = Math.max(globalThis.maxActionRight, left + 84);
              globalThis.maxActionBottom = Math.max(globalThis.maxActionBottom, top + 46);
            }
            """
        )

        self.assertEqual(ctx.eval("globalThis.actionWheelButtonCount"), 3)
        self.assertGreaterEqual(ctx.eval("globalThis.minActionLeft"), 0)
        self.assertGreaterEqual(ctx.eval("globalThis.minActionTop"), 0)
        self.assertLessEqual(ctx.eval("globalThis.maxActionRight"), 360)
        self.assertLessEqual(ctx.eval("globalThis.maxActionBottom"), 260)

    def test_scenario_new_visual_events_create_board_vfx_nodes(self) -> None:
        app_source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        ctx = quickjs.Context()
        ctx.eval(
            """
            function createClassList() {
              return {
                add() {},
                remove() {},
                toggle() {},
                contains() { return false; },
              };
            }

            function createElement(tagName, id) {
              const element = {
                tagName: String(tagName || "div").toUpperCase(),
                id: id || "",
                children: [],
                listeners: {},
                className: "",
                value: "",
                disabled: false,
                dataset: {},
                style: {
                  setProperty(name, value) {
                    this[name] = String(value);
                  },
                },
                _textContent: "",
                _innerHTML: "",
                classList: createClassList(),
                append(...nodes) {
                  this.children.push(...nodes);
                },
                appendChild(node) {
                  this.children.push(node);
                  return node;
                },
                addEventListener(type, handler) {
                  if (!this.listeners[type]) this.listeners[type] = [];
                  this.listeners[type].push(handler);
                },
                querySelector() {
                  return null;
                },
                querySelectorAll() {
                  return [];
                },
                replaceWith() {},
                remove() {
                  this.removed = true;
                },
                focus() {},
                getBoundingClientRect() {
                  return { left: 0, top: 0, width: 320, height: 320, right: 320, bottom: 320 };
                },
                set innerHTML(value) {
                  this._innerHTML = String(value);
                  this.children = [];
                },
                get innerHTML() {
                  return this._innerHTML;
                },
                set textContent(value) {
                  this._textContent = String(value);
                },
                get textContent() {
                  return this._textContent;
                },
              };
              return element;
            }

            const document = {
              elements: {},
              listeners: {},
              body: createElement("body", "body"),
              getElementById(id) {
                if (!this.elements[id]) this.elements[id] = createElement("div", id);
                return this.elements[id];
              },
              createElement(tagName) {
                return createElement(tagName);
              },
              querySelector() {
                return null;
              },
              querySelectorAll() {
                return [];
              },
              addEventListener(type, handler) {
                if (!this.listeners[type]) this.listeners[type] = [];
                this.listeners[type].push(handler);
              },
            };
            document.body.classList = createClassList();

            const storageFactory = () => ({
              _store: {},
              getItem(key) {
                return Object.prototype.hasOwnProperty.call(this._store, key) ? this._store[key] : null;
              },
              setItem(key, value) {
                this._store[key] = String(value);
              },
              removeItem(key) {
                delete this._store[key];
              },
            });

            const window = {
              location: { href: "http://example.test/", search: "", hash: "" },
              listeners: {},
              addEventListener(type, handler) {
                if (!this.listeners[type]) this.listeners[type] = [];
                this.listeners[type].push(handler);
              },
              requestAnimationFrame(callback) { return callback(); },
              cancelAnimationFrame() {},
              setInterval() { return 1; },
              clearInterval() {},
              setTimeout() { return 1; },
              clearTimeout() {},
            };

            const history = { replaceState() {} };
            const localStorage = storageFactory();
            const sessionStorage = storageFactory();
            function URLSearchParams(search) {
              this._params = {};
              const raw = String(search || "").replace(/^\\?/, "");
              if (raw) {
                raw.split("&").forEach((entry) => {
                  if (!entry) return;
                  const parts = entry.split("=");
                  this._params[decodeURIComponent(parts[0])] = decodeURIComponent(parts[1] || "");
                });
              }
            }
            URLSearchParams.prototype.get = function (key) {
              return Object.prototype.hasOwnProperty.call(this._params, key) ? this._params[key] : null;
            };
            URLSearchParams.prototype.set = function (key, value) {
              this._params[key] = String(value);
            };
            URLSearchParams.prototype.delete = function (key) {
              delete this._params[key];
            };
            URLSearchParams.prototype.toString = function () {
              return Object.keys(this._params).map((key) => `${key}=${this._params[key]}`).join("&");
            };
            function URL(href) {
              this.href = String(href || "http://example.test/");
              this.hash = "";
              this.searchParams = new URLSearchParams("");
            }
            """
        )
        ctx.eval(app_source)
        ctx.eval(
            """
            cellCenterPoint = function (cell) {
              if (!cell) return null;
              return { x: Number(cell.x) * 40 + 20, y: Number(cell.y) * 40 + 20 };
            };
            unitCenterPoint = function () {
              return { x: 60, y: 60 };
            };
            state.screen = "battle";
            state.room = { viewer_player_id: 1 };
            applyRoomPayload({
              heroes: [],
              room: { viewer_player_id: 1, room_id: "ROOM01" },
              battle: {
                board: { width: 8, height: 8 },
                units: [],
                field_effects: [],
                pending_chain: null,
                pending_respawn: null,
                logs: [],
                visual_events: [
                  {
                    id: 1,
                    kind: "attack",
                    display_name: "普攻",
                    actor_id: "u1",
                    actor_player_id: 1,
                    action_type: "attack",
                    action_code: "attack",
                    target_unit_ids: [],
                    target_cells: [{ x: 4, y: 4 }],
                    source_cell: { x: 3, y: 4 },
                    defense_reason: "",
                    metadata: {},
                  },
                ],
              },
            });
            globalThis.initialVfxCount = state.activeBattleVfx.length;
            applyRoomPayload({
              heroes: [],
              room: { viewer_player_id: 1, room_id: "ROOM01" },
              battle: {
                board: { width: 8, height: 8 },
                units: [],
                field_effects: [],
                pending_chain: null,
                pending_respawn: null,
                logs: [],
                visual_events: [
                  {
                    id: 1,
                    kind: "attack",
                    display_name: "普攻",
                    actor_id: "u1",
                    actor_player_id: 1,
                    action_type: "attack",
                    action_code: "attack",
                    target_unit_ids: [],
                    target_cells: [{ x: 4, y: 4 }],
                    source_cell: { x: 3, y: 4 },
                    defense_reason: "",
                    metadata: {},
                  },
                  {
                    id: 2,
                    kind: "skill",
                    display_name: "大圣光",
                    actor_id: "u2",
                    actor_player_id: 2,
                    action_type: "skill",
                    action_code: "great_holy_light",
                    target_unit_ids: [],
                    target_cells: [{ x: 4, y: 4 }, { x: 4, y: 5 }],
                    source_cell: { x: 2, y: 4 },
                    defense_reason: "",
                    metadata: {},
                  },
                ],
              },
            });
            renderBattleVfx();
            globalThis.afterVfxCount = state.activeBattleVfx.length;
            globalThis.renderedNodeCount = document.elements["battle-vfx"].children.length;
            """
        )

        self.assertEqual(ctx.eval("globalThis.initialVfxCount"), 0)
        self.assertEqual(ctx.eval("globalThis.afterVfxCount"), 1)
        self.assertGreater(ctx.eval("globalThis.renderedNodeCount"), 0)

    def test_scenario_replay_toolbar_reflects_live_and_replay_state(self) -> None:
        app_source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        ctx = quickjs.Context()
        ctx.eval(
            """
            function createClassList(owner) {
              return {
                add(name) {
                  if (!owner.className.includes(name)) owner.className = `${owner.className} ${name}`.trim();
                },
                remove(name) {
                  owner.className = owner.className
                    .split(/\\s+/)
                    .filter((token) => token && token !== name)
                    .join(" ");
                },
                toggle(name, force) {
                  const shouldAdd = force === undefined ? !this.contains(name) : Boolean(force);
                  if (shouldAdd) this.add(name);
                  else this.remove(name);
                },
                contains(name) {
                  return owner.className.split(/\\s+/).includes(name);
                },
              };
            }

            function createElement(tagName, id) {
              const element = {
                tagName: String(tagName || "div").toUpperCase(),
                id: id || "",
                children: [],
                listeners: {},
                className: "",
                value: "",
                checked: false,
                disabled: false,
                dataset: {},
                style: {},
                _textContent: "",
                _innerHTML: "",
                append(...nodes) {
                  this.children.push(...nodes);
                },
                appendChild(node) {
                  this.children.push(node);
                  return node;
                },
                addEventListener(type, handler) {
                  if (!this.listeners[type]) this.listeners[type] = [];
                  this.listeners[type].push(handler);
                },
                querySelector() { return null; },
                querySelectorAll() { return []; },
                replaceWith() {},
                focus() {},
                set innerHTML(value) { this._innerHTML = String(value); },
                get innerHTML() { return this._innerHTML; },
                set textContent(value) { this._textContent = String(value); },
                get textContent() { return this._textContent; },
              };
              element.classList = createClassList(element);
              return element;
            }

            const document = {
              elements: {},
              listeners: {},
              body: createElement("body", "body"),
              getElementById(id) {
                if (!this.elements[id]) this.elements[id] = createElement("div", id);
                return this.elements[id];
              },
              createElement(tagName) { return createElement(tagName); },
              querySelector() { return null; },
              querySelectorAll() { return []; },
              addEventListener(type, handler) {
                if (!this.listeners[type]) this.listeners[type] = [];
                this.listeners[type].push(handler);
              },
            };
            document.body.classList = createClassList(document.body);

            const storageFactory = () => ({
              _store: {},
              getItem(key) { return Object.prototype.hasOwnProperty.call(this._store, key) ? this._store[key] : null; },
              setItem(key, value) { this._store[key] = String(value); },
              removeItem(key) { delete this._store[key]; },
            });

            const window = {
              location: { href: "http://example.test/?room=ROOM01", search: "?room=ROOM01", hash: "#battle" },
              listeners: {},
              addEventListener(type, handler) {
                if (!this.listeners[type]) this.listeners[type] = [];
                this.listeners[type].push(handler);
              },
              requestAnimationFrame(callback) { return callback(); },
              cancelAnimationFrame() {},
              setInterval() { return 1; },
              clearInterval() {},
            };
            const history = { replaceState() {} };
            const localStorage = storageFactory();
            const sessionStorage = storageFactory();
            """
        )
        ctx.eval(app_source)
        ctx.eval(
            """
            [
              "replay-toolbar",
              "replay-step-back",
              "replay-pause",
              "replay-live",
              "replay-step-forward",
              "replay-speed",
              "replay-omniscient",
              "replay-timeline",
              "replay-status",
            ].forEach((id) => document.getElementById(id));
            state.room = {
              room_id: "ROOM01",
              viewer_is_host: true,
              replay: { available: true, last_step_index: 6, can_use_omniscient: true },
              simulation: { enabled: true, paused: true, speed: 2, can_control: true, live_step_index: 4 },
            };
            state.battle = { winner: null, board: { width: 8, height: 8 } };
            state.replayMode = true;
            state.replayStepIndex = 2;
            state.replayOmniscient = true;
            renderReplayToolbar();
            globalThis.toolbarHidden = document.elements["replay-toolbar"].classList.contains("hidden");
            globalThis.pauseText = document.elements["replay-pause"].textContent;
            globalThis.timelineValue = document.elements["replay-timeline"].value;
            globalThis.omniscientChecked = document.elements["replay-omniscient"].checked;
            globalThis.liveDisabled = document.elements["replay-live"].disabled;
            """
        )

        self.assertFalse(ctx.eval("globalThis.toolbarHidden"))
        self.assertEqual(ctx.eval("globalThis.pauseText"), "继续")
        self.assertEqual(ctx.eval("globalThis.timelineValue"), "2")
        self.assertTrue(ctx.eval("globalThis.omniscientChecked"))
        self.assertFalse(ctx.eval("globalThis.liveDisabled"))


if __name__ == "__main__":
    unittest.main()
