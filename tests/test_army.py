from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wujiang.tactical.engine.army import is_army_soldier, parse_army_slot
from wujiang.tactical.heroes.registry import RoomBattleEntry, create_battle, create_hero, create_room_battle
from wujiang.tactical.rooms.multiplayer import RoomRegistry


class ArmyTurnTests(unittest.TestCase):
    def test_default_soldier_stats(self) -> None:
        infantry = create_hero("strategy_infantry", 1)
        archer = create_hero("strategy_archer", 1)
        cavalry = create_hero("strategy_cavalry", 1)

        self.assertTrue(is_army_soldier(infantry))
        self.assertEqual(int(infantry.stat("attack")), 2)
        self.assertEqual(int(infantry.stat("defense")), 2)
        self.assertEqual(int(infantry.stat("speed")), 2)
        self.assertEqual(int(infantry.stat("attack_range")), 1)
        self.assertEqual(int(archer.stat("attack_range")), 3)
        self.assertEqual(int(cavalry.stat("speed")), 4)

    def test_soldiers_are_not_in_hero_turn_order(self) -> None:
        battle = create_battle(["ellie", "strategy_infantry"], ["bard", "strategy_archer"])
        soldier_ids = {unit.unit_id for unit in battle.all_units() if is_army_soldier(unit)}
        hero_ids = {unit.unit_id for unit in battle.all_units() if not is_army_soldier(unit)}

        self.assertTrue(soldier_ids)
        self.assertTrue(set(battle.turn_order_unit_ids) >= hero_ids)
        self.assertTrue(soldier_ids.isdisjoint(battle.turn_order_unit_ids))
        self.assertEqual(
            [parse_army_slot(slot) for slot in battle.turn_order_unit_ids if parse_army_slot(slot)],
            [1, 2],
        )
        self.assertFalse(battle.is_army_turn())

    def test_fifty_vs_fifty_spawn_does_not_move_on_start(self) -> None:
        left = [RoomBattleEntry("strategy_infantry", 1, 1) for _ in range(50)]
        right = [RoomBattleEntry("strategy_infantry", 2, 2) for _ in range(50)]
        battle = create_room_battle(left, right, board_width=24, board_height=16)
        self.assertEqual(len(battle.units), 100)
        self.assertTrue(battle.is_army_turn())

    def test_advance_moves_then_hold_stays(self) -> None:
        battle = create_battle(["ellie", "strategy_infantry"], ["bard", "strategy_infantry"])
        soldier = next(unit for unit in battle.player_units(1) if is_army_soldier(unit))
        start = soldier.position
        battle.set_army_order(1, "advance", "E")
        battle.set_army_order(2, "hold", "W")

        defender = next(unit for unit in battle.player_units(2) if is_army_soldier(unit))
        hold_start = defender.position
        safety = 0
        while soldier.position.x == start.x and battle.winner is None and safety < 12:
            battle.end_turn()
            safety += 1

        self.assertGreater(soldier.position.x, start.x)
        self.assertEqual((defender.position.x, defender.position.y), (hold_start.x, hold_start.y))

    def test_player_cannot_control_soldiers(self) -> None:
        from wujiang.tactical.engine.core import ActionError

        battle = create_battle(["ellie", "strategy_infantry"], ["bard"])
        soldier = next(unit for unit in battle.player_units(1) if is_army_soldier(unit))
        with self.assertRaises(ActionError):
            battle.perform_action({
                "type": "move",
                "unit_id": soldier.unit_id,
                "x": soldier.position.x + 1,
                "y": soldier.position.y,
            })

    def test_army_attack_hits_adjacent_enemy(self) -> None:
        from wujiang.tactical.engine.core import Battle, Position

        battle = Battle(width=8, height=8)
        attacker = create_hero("strategy_infantry", 1)
        victim = create_hero("strategy_infantry", 2)
        battle.add_unit(attacker, Position(2, 3))
        battle.add_unit(victim, Position(3, 3))
        battle.configure_turn_order([])
        battle.start_battle()
        battle.set_army_order(1, "hold", "E")
        battle.set_army_order(2, "hold", "W")
        hp_before = victim.current_hp
        battle.end_turn()
        self.assertLess(victim.current_hp, hp_before)

    def test_encounter_room_can_add_soldiers(self) -> None:
        registry = RoomRegistry()
        room, _player_id, token = registry.create_room("Alice")
        room.set_roster(token, ["ellie"])
        room.set_army_composition(token, {"infantry": 2, "archer": 1, "cavalry": 0})
        room.set_army_order(token, "retreat", "W")

        public = room.serialize_state(token)
        self.assertEqual(public["room"]["seats"][0]["army_counts"]["infantry"], 2)
        self.assertEqual(public["room"]["seats"][0]["army_total_count"], 3)
        self.assertEqual(public["room"]["army_orders"][1]["infantry"]["order"], "retreat")
        self.assertEqual(public["room"]["army_orders"][1]["infantry"]["direction"], "W")
        self.assertEqual(public["room"]["army_orders"][1]["archer"]["order"], "retreat")

        room.set_army_order(token, "hold", "E", kind="archer")
        public = room.serialize_state(token)
        self.assertEqual(public["room"]["army_orders"][1]["infantry"]["order"], "retreat")
        self.assertEqual(public["room"]["army_orders"][1]["archer"]["order"], "hold")

        entries = room._battle_entries_for_team(1)
        self.assertEqual(
            [entry.hero_code for entry in entries],
            ["ellie", "strategy_infantry", "strategy_infantry", "strategy_archer"],
        )

    def test_kind_orders_move_only_that_kind(self) -> None:
        battle = create_battle(["ellie", "strategy_infantry", "strategy_archer"], ["bard"])
        infantry = next(unit for unit in battle.player_units(1) if unit.hero_code == "strategy_infantry")
        archer = next(unit for unit in battle.player_units(1) if unit.hero_code == "strategy_archer")
        infantry_start = infantry.position
        archer_start = (archer.position.x, archer.position.y)
        battle.set_army_order(1, "advance", "E", kind="infantry")
        battle.set_army_order(1, "hold", "E", kind="archer")

        safety = 0
        while infantry.position.x == infantry_start.x and battle.winner is None and safety < 12:
            battle.end_turn()
            safety += 1

        self.assertGreater(infantry.position.x, infantry_start.x)
        self.assertEqual((archer.position.x, archer.position.y), archer_start)

    def test_heroes_spawn_in_front_of_soldiers(self) -> None:
        battle = create_battle(["ellie", "strategy_infantry"], ["bard", "strategy_infantry"])
        p1_hero = next(unit for unit in battle.player_units(1) if not is_army_soldier(unit))
        p1_soldier = next(unit for unit in battle.player_units(1) if is_army_soldier(unit))
        p2_hero = next(unit for unit in battle.player_units(2) if not is_army_soldier(unit))
        p2_soldier = next(unit for unit in battle.player_units(2) if is_army_soldier(unit))

        self.assertGreater(p1_hero.position.x, p1_soldier.position.x)
        self.assertLess(p2_hero.position.x, p2_soldier.position.x)

    def test_step_stride_moves_one_cell_and_records_trace(self) -> None:
        battle = create_battle(["ellie", "strategy_cavalry"], ["bard"])
        cavalry = next(unit for unit in battle.player_units(1) if unit.hero_code == "strategy_cavalry")
        start = cavalry.position
        battle.set_army_order(1, "advance", "E", kind="cavalry", stride="step")

        safety = 0
        while cavalry.position.x == start.x and battle.winner is None and safety < 12:
            battle.end_turn()
            safety += 1

        self.assertEqual(cavalry.position.x, start.x + 1)
        traces = battle.to_public_dict()["army"]["move_traces"]
        self.assertTrue(any(item["unit_id"] == cavalry.unit_id and len(item["path"]) == 2 for item in traces))
        self.assertEqual(battle.army_orders[1]["cavalry"]["stride"], "step")

    def test_retreat_moves_opposite_of_facing(self) -> None:
        from wujiang.tactical.engine.core import Battle, Position

        battle = Battle(width=10, height=8)
        soldier = create_hero("strategy_infantry", 1)
        dummy = create_hero("strategy_infantry", 2)
        battle.add_unit(soldier, Position(4, 3))
        battle.add_unit(dummy, Position(9, 3))
        battle.configure_turn_order([])
        battle.start_battle()
        battle.set_army_order(1, "retreat", "E", kind="infantry")
        battle.set_army_order(2, "hold", "W")
        start_x = soldier.position.x
        battle.end_turn()
        self.assertLess(soldier.position.x, start_x)

    def test_full_speed_trace_steps_one_cell_at_a_time(self) -> None:
        battle = create_battle(["ellie", "strategy_infantry"], ["bard"])
        infantry = next(unit for unit in battle.player_units(1) if unit.hero_code == "strategy_infantry")
        battle.set_army_order(1, "advance", "E", kind="infantry", stride="full")
        safety = 0
        while not battle.to_public_dict()["army"]["move_traces"] and battle.winner is None and safety < 12:
            battle.end_turn()
            safety += 1
        traces = [
            item
            for item in battle.to_public_dict()["army"]["move_traces"]
            if item["unit_id"] == infantry.unit_id
        ]
        self.assertTrue(traces)
        path = traces[0]["path"]
        self.assertGreaterEqual(len(path), 3)
        for prev, nxt in zip(path, path[1:]):
            self.assertEqual(max(abs(nxt["x"] - prev["x"]), abs(nxt["y"] - prev["y"])), 1)

    def test_arrow_tower_has_two_hit_points(self) -> None:
        tower = create_hero("strategy_arrow_tower", 2)
        self.assertEqual(tower.max_health, 2.0)
        self.assertEqual(tower.current_hp, 2.0)

    def test_arrow_tower_ignores_infantry_but_fires_off_axis(self) -> None:
        from wujiang.tactical.engine.core import Battle, Position

        blocked = Battle(width=12, height=8)
        infantry = create_hero("strategy_infantry", 1)
        tower = create_hero("strategy_arrow_tower", 2)
        dummy = create_hero("strategy_infantry", 2)
        blocked.add_unit(infantry, Position(4, 3))
        blocked.add_unit(tower, Position(5, 3))
        blocked.add_unit(dummy, Position(10, 7))
        blocked.configure_turn_order([])
        blocked.start_battle()
        blocked.set_army_order(1, "hold", "E")
        blocked.set_army_order(2, "hold", "W")
        tower_hp = tower.current_hp
        blocked.end_turn()
        self.assertEqual(tower.current_hp, tower_hp)

        battle = Battle(width=12, height=8)
        victim = create_hero("strategy_infantry", 1)
        battery = create_hero("strategy_arrow_tower", 2)
        keeper = create_hero("strategy_infantry", 2)
        battle.add_unit(victim, Position(7, 4))
        battle.add_unit(battery, Position(4, 3))
        battle.add_unit(keeper, Position(11, 7))
        battle.configure_turn_order([])
        battle.start_battle()
        battle.set_army_order(1, "hold", "E")
        battle.set_army_order(2, "hold", "W")
        victim_hp = victim.current_hp
        battle.end_turn()
        battle.end_turn()
        self.assertLess(victim.current_hp, victim_hp)
        bolt_events = [
            event
            for event in battle.visual_events
            if event.metadata.get("vfx_style") == "bolt"
        ]
        self.assertTrue(bolt_events)
        self.assertEqual(bolt_events[-1].action_code, "siege_bolt")
        self.assertTrue(bolt_events[-1].target_cells)
        self.assertIsNotNone(bolt_events[-1].source_cell)
        self.assertTrue(bolt_events[-1].metadata.get("impact_cell"))

    def test_cannon_reloads_then_damages_arrow_tower(self) -> None:
        from wujiang.tactical.engine.army import army_public_state
        from wujiang.tactical.engine.core import Battle, Position

        battle = Battle(width=16, height=10)
        cannon = create_hero("strategy_cannon", 1)
        tower = create_hero("strategy_arrow_tower", 2)
        keeper = create_hero("strategy_infantry", 2)
        battle.add_unit(cannon, Position(1, 3))
        battle.add_unit(tower, Position(8, 3))
        battle.add_unit(keeper, Position(15, 8))
        battle.configure_turn_order([])
        battle.start_battle()
        public = army_public_state(battle)
        self.assertEqual(public["orders"][1]["cannon"]["order"], "advance")
        battle.set_army_order(1, "hold", "E", kind="cannon")
        self.assertEqual(public["structures"][2][0]["kind"], "arrow_tower")
        tower_hp = tower.current_hp
        self.assertFalse(cannon.siege_loaded)
        battle.end_turn()
        self.assertTrue(cannon.siege_loaded)
        self.assertEqual(cannon.siege_reload_state, "loading")
        self.assertEqual(tower.current_hp, tower_hp)
        battle.end_turn()
        battle.end_turn()
        self.assertFalse(cannon.siege_loaded)
        self.assertEqual(cannon.siege_reload_state, "empty")
        self.assertLess(tower.current_hp, tower_hp)
        shell_events = [
            event
            for event in battle.visual_events
            if event.metadata.get("vfx_style") == "shell"
        ]
        self.assertTrue(shell_events)
        self.assertEqual(shell_events[-1].metadata.get("sound"), "cannon")
        self.assertEqual(shell_events[-1].action_code, "siege_shell")
        self.assertTrue(shell_events[-1].target_cells)
        self.assertIsNotNone(shell_events[-1].source_cell)

    def test_siege_profile_upgrade_extends_range_and_splash(self) -> None:
        from wujiang.tactical.engine.core import Battle, Position
        from wujiang.tactical.engine.siege import apply_siege_profile, blast_cells

        battle = Battle(width=16, height=10)
        cannon = create_hero("strategy_cannon", 1)
        primary = create_hero("strategy_infantry", 2)
        splash_victim = create_hero("strategy_infantry", 2)
        keeper = create_hero("strategy_infantry", 2)
        battle.add_unit(cannon, Position(1, 3))
        battle.add_unit(primary, Position(8, 3))
        battle.add_unit(splash_victim, Position(9, 3))
        battle.add_unit(keeper, Position(15, 8))
        apply_siege_profile(cannon, "cannon_2")
        self.assertEqual(int(cannon.stat("attack_range")), 10)
        self.assertEqual(int(cannon.stat("attack")), 4)
        self.assertEqual(cannon.splash_radius, 1)
        battle.configure_turn_order([])
        battle.start_battle()
        battle.set_army_order(1, "hold", "E", kind="cannon", ammo="heavy_shell")
        cannon.siege_loaded = True
        splash_hp = splash_victim.current_hp
        battle.end_turn()
        self.assertLess(splash_victim.current_hp, splash_hp)
        self.assertGreaterEqual(len(blast_cells(battle, Position(8, 3), 1)), 9)
        self.assertTrue(any(event.metadata.get("splash_radius") == 1 for event in battle.visual_events))

    def test_upgraded_cannon_tower_can_damage_arrow_tower(self) -> None:
        from wujiang.tactical.engine.core import Battle, Position
        from wujiang.tactical.engine.siege import apply_siege_profile

        battle = Battle(width=12, height=8)
        battery = create_hero("strategy_arrow_tower", 1)
        escort = create_hero("strategy_infantry", 1)
        tower = create_hero("strategy_arrow_tower", 2)
        keeper = create_hero("strategy_infantry", 2)
        apply_siege_profile(battery, "cannon_tower_1", restore_hp=True)
        battle.add_unit(battery, Position(3, 3))
        battle.add_unit(escort, Position(1, 7))
        battle.add_unit(tower, Position(6, 3))
        battle.add_unit(keeper, Position(11, 7))
        battle.configure_turn_order([])
        battle.start_battle()
        battery.siege_loaded = True
        tower_hp = tower.current_hp
        battle.end_turn()
        self.assertLess(tower.current_hp, tower_hp)

    def test_siege_defender_holds_gate_then_sallies_against_kiting_cannon(self) -> None:
        from wujiang.tactical.engine.army import apply_siege_defender_ai, command_for_kind
        from wujiang.tactical.engine.core import Battle, Position

        battle = Battle(width=20, height=10)
        garrison = create_hero("strategy_infantry", 2)
        tower = create_hero("strategy_arrow_tower", 2)
        dummy = create_hero("strategy_infantry", 1)
        cannon = create_hero("strategy_cannon", 1)
        battle.add_unit(garrison, Position(16, 4))
        battle.add_unit(tower, Position(15, 4))
        battle.add_unit(dummy, Position(14, 4))
        battle.add_unit(cannon, Position(1, 4))
        battle.blocked_cells = {(15, 1), (15, 8)}
        battle.siege_defender_ai = True
        apply_siege_defender_ai(battle, 2)
        self.assertEqual(command_for_kind(battle.army_orders, 2, "infantry")["order"], "hold")

        battle.units.pop(dummy.unit_id, None)
        apply_siege_defender_ai(battle, 2)
        self.assertEqual(command_for_kind(battle.army_orders, 2, "infantry")["order"], "advance")

    def test_cannon_ignores_immunities_and_hits_friends_but_needs_direct_tower_hit(self) -> None:
        from wujiang.tactical.engine.core import Battle, Position

        battle = Battle(width=16, height=10)
        cannon = create_hero("strategy_cannon", 1)
        friend = create_hero("strategy_infantry", 1)
        tower = create_hero("strategy_arrow_tower", 2)
        graze = create_hero("strategy_arrow_tower", 2)
        keeper = create_hero("strategy_infantry", 2)
        battle.add_unit(cannon, Position(1, 3))
        battle.add_unit(friend, Position(8, 4))
        battle.add_unit(tower, Position(8, 3))
        battle.add_unit(graze, Position(9, 4))
        battle.add_unit(keeper, Position(15, 8))
        friend.magic_immunity = True
        battle.configure_turn_order([])
        battle.start_battle()
        battle.set_army_order(1, "hold", "E", kind="infantry")
        battle.set_army_order(1, "hold", "E", kind="cannon", ammo="heavy_shell")
        cannon.siege_loaded = True
        friend_hp = friend.current_hp
        tower_hp = tower.current_hp
        graze_hp = graze.current_hp
        self.assertEqual(int(cannon.stat("defense")), 2)
        self.assertEqual(int(tower.stat("defense")), 5)
        self.assertTrue(tower.physical_immunity)
        battle.end_turn()
        self.assertLess(friend.current_hp, friend_hp)
        self.assertLess(tower.current_hp, tower_hp)
        self.assertEqual(graze.current_hp, graze_hp)

    def test_cannon_cannot_fire_at_range_one(self) -> None:
        from wujiang.tactical.engine.core import Battle, Position

        battle = Battle(width=12, height=8)
        cannon = create_hero("strategy_cannon", 1)
        victim = create_hero("strategy_infantry", 2)
        keeper = create_hero("strategy_infantry", 2)
        battle.add_unit(cannon, Position(1, 3))
        battle.add_unit(victim, Position(3, 3))
        battle.add_unit(keeper, Position(11, 7))
        battle.configure_turn_order([])
        battle.start_battle()
        cannon.siege_loaded = True
        victim_hp = victim.current_hp
        battle.end_turn()
        self.assertEqual(victim.current_hp, victim_hp)
        self.assertTrue(cannon.siege_loaded)

    def test_arrow_tower_physical_immunity_blocks_infantry(self) -> None:
        from wujiang.tactical.engine.core import Battle, Position

        battle = Battle(width=12, height=8)
        infantry = create_hero("strategy_infantry", 1)
        tower = create_hero("strategy_arrow_tower", 2)
        keeper = create_hero("strategy_infantry", 2)
        battle.add_unit(infantry, Position(4, 3))
        battle.add_unit(tower, Position(5, 3))
        battle.add_unit(keeper, Position(11, 7))
        battle.configure_turn_order([])
        battle.start_battle()
        battle.set_army_order(1, "hold", "E")
        battle.set_army_order(2, "hold", "W")
        tower_hp = tower.current_hp
        battle.end_turn()
        self.assertEqual(tower.current_hp, tower_hp)

    def test_army_phase_strikes_melee_then_ranged_then_cannon(self) -> None:
        from wujiang.tactical.engine.core import Battle, Position

        battle = Battle(width=16, height=8)
        cannon = create_hero("strategy_cannon", 1)
        archer = create_hero("strategy_archer", 1)
        infantry = create_hero("strategy_infantry", 1)
        victim = create_hero("strategy_infantry", 2)
        far = create_hero("strategy_infantry", 2)
        keeper = create_hero("strategy_infantry", 2)
        battle.add_unit(cannon, Position(1, 3))
        battle.add_unit(archer, Position(4, 3))
        battle.add_unit(infantry, Position(6, 3))
        battle.add_unit(victim, Position(7, 3))
        battle.add_unit(far, Position(8, 3))
        battle.add_unit(keeper, Position(15, 7))
        battle.configure_turn_order([])
        battle.start_battle()
        battle.set_army_order(1, "hold", "E")
        battle.set_army_order(2, "hold", "W")
        cannon.siege_loaded = True
        battle.end_turn()
        waves = [
            str(event.metadata.get("army_strike_wave") or "")
            for event in battle.visual_events
            if event.actor_player_id == 1 and event.kind == "attack"
        ]
        self.assertIn("melee", waves)
        self.assertIn("ranged", waves)
        self.assertIn("cannon", waves)
        self.assertLess(waves.index("melee"), waves.index("ranged"))
        self.assertLess(waves.index("ranged"), waves.index("cannon"))

    def test_cannon_ai_creeps_forward_when_loaded(self) -> None:
        from wujiang.tactical.engine.core import Battle, Position

        battle = Battle(width=16, height=8)
        cannon = create_hero("strategy_cannon", 1)
        enemy = create_hero("strategy_infantry", 2)
        keeper = create_hero("strategy_infantry", 2)
        battle.add_unit(cannon, Position(1, 3))
        battle.add_unit(enemy, Position(15, 3))
        battle.add_unit(keeper, Position(15, 7))
        battle.configure_turn_order([])
        battle.start_battle()
        battle.army_ai_players = {1}
        cannon.siege_loaded = True
        start = cannon.position.x
        battle.end_turn()
        self.assertGreater(cannon.position.x, start)

    def test_seek_turns_back_toward_nearest_enemy(self) -> None:
        from wujiang.tactical.engine.core import Battle, Position

        battle = Battle(width=16, height=8)
        hunter = create_hero("strategy_infantry", 1)
        prey = create_hero("strategy_infantry", 2)
        battle.add_unit(hunter, Position(14, 3))
        battle.add_unit(prey, Position(2, 3))
        battle.configure_turn_order([])
        battle.start_battle()
        battle.set_army_order(1, "seek", "E")
        battle.set_army_order(2, "hold", "W")
        start = hunter.position.x
        battle.end_turn()
        self.assertLess(hunter.position.x, start)

    def test_arrow_towers_do_not_count_for_victory(self) -> None:
        from wujiang.tactical.engine.core import Battle, Position

        battle = Battle(width=12, height=8)
        hero = create_hero("ellie", 1)
        tower = create_hero("strategy_arrow_tower", 2)
        battle.add_unit(hero, Position(3, 3))
        battle.add_unit(tower, Position(8, 3))
        battle.configure_turn_order([])
        battle.start_battle()
        battle.check_win_condition()
        self.assertEqual(battle.winner, 1)

    def test_cleanup_keeps_last_position_for_destroyed_units(self) -> None:
        from wujiang.tactical.engine.core import Battle, Position

        battle = Battle(width=8, height=8)
        fallen = create_hero("ellie", 1)
        keeper = create_hero("ellie", 2)
        battle.add_unit(fallen, Position(2, 3))
        battle.add_unit(keeper, Position(6, 6))
        fallen.current_hp = 0
        fallen.alive = False
        battle.cleanup_dead_units()
        self.assertIsNone(fallen.position)
        self.assertEqual((fallen.last_position.x, fallen.last_position.y), (2, 3))
        payload = next(item for item in battle.to_public_dict()["destroyed_units"] if item["id"] == fallen.unit_id)
        self.assertEqual(payload["last_position"], {"x": 2, "y": 3})
        delattr(keeper, "last_position")
        public = keeper.to_public_dict(battle)
        self.assertIsNone(public["last_position"])

    def test_fast_ai_emits_turn_end_checkpoint(self) -> None:
        from wujiang.tactical.engine.core import Battle, Position

        seen: list[str] = []
        battle = Battle(width=8, height=8)
        battle.fast_ai_simulation = True
        battle.on_replay_checkpoint = seen.append
        attacker = create_hero("ellie", 1)
        defender = create_hero("ellie", 2)
        battle.add_unit(attacker, Position(1, 1))
        battle.add_unit(defender, Position(6, 6))
        battle.configure_turn_order([])
        battle.start_battle()
        battle.end_turn()
        self.assertIn("turn_end", seen)

    def test_arrow_tower_damages_adjacent_hero(self) -> None:
        from wujiang.tactical.engine.core import Battle, Position

        battle = Battle(width=12, height=8)
        hero = create_hero("ellie", 1)
        tower = create_hero("strategy_arrow_tower", 2)
        keeper = create_hero("strategy_infantry", 2)
        battle.add_unit(hero, Position(5, 3))
        battle.add_unit(tower, Position(4, 3))
        battle.add_unit(keeper, Position(11, 7))
        battle.configure_turn_order([])
        battle.start_battle()
        battle.set_army_order(2, "hold", "W")
        hero_hp = hero.current_hp
        battle.end_turn()
        battle.end_turn()
        self.assertLess(hero.current_hp, hero_hp)

    def test_ai_move_score_ignores_arrow_tower(self) -> None:
        from wujiang.tactical.engine.core import Battle, Position
        from wujiang.tactical.rooms.ai import difficulty_profile, hero_style, score_move_destination

        battle = Battle(width=12, height=8)
        hero = create_hero("ellie", 1)
        tower = create_hero("strategy_arrow_tower", 2)
        soldier = create_hero("strategy_infantry", 2)
        battle.add_unit(hero, Position(4, 4))
        battle.add_unit(tower, Position(6, 4))
        battle.add_unit(soldier, Position(1, 4))
        battle.configure_turn_order([])
        battle.start_battle()
        profile = difficulty_profile("standard")
        toward_soldier = score_move_destination(battle, hero, Position(3, 4), hero_style(hero), profile)
        toward_tower = score_move_destination(battle, hero, Position(5, 4), hero_style(hero), profile)
        self.assertGreater(toward_soldier, toward_tower)

    def test_follow_style_prefers_staying_near_own_army(self) -> None:
        from wujiang.tactical.engine.core import Battle, Position
        from wujiang.tactical.rooms.ai import difficulty_profile, follow_leash_adjustment, hero_style, score_move_destination

        self.assertLess(follow_leash_adjustment(10, 8, 0), follow_leash_adjustment(2, 8, 0))

        battle = Battle(width=16, height=8)
        battle.hero_ai_styles = {1: "follow"}
        hero = create_hero("ellie", 1)
        ally = create_hero("strategy_infantry", 1)
        enemy = create_hero("ellie", 2)
        battle.add_unit(hero, Position(4, 4))
        battle.add_unit(ally, Position(3, 4))
        battle.add_unit(enemy, Position(14, 4))
        battle.configure_turn_order([])
        battle.start_battle()
        profile = difficulty_profile("standard")
        near_army = score_move_destination(battle, hero, Position(3, 4), hero_style(hero), profile)
        deep_rush = score_move_destination(battle, hero, Position(12, 4), hero_style(hero), profile)
        self.assertGreater(near_army, deep_rush)

    def test_follow_style_holds_back_summon_until_near_front(self) -> None:
        from wujiang.tactical.engine.core import Battle, Position
        from wujiang.tactical.rooms.ai import follow_catch_up_skill_penalty

        battle = Battle(width=16, height=8)
        battle.hero_ai_styles = {1: "follow"}
        remi = create_hero("excel_r056", 1)
        ally = create_hero("strategy_infantry", 1)
        enemy = create_hero("ellie", 2)
        battle.add_unit(remi, Position(2, 4))
        battle.add_unit(ally, Position(3, 4))
        battle.add_unit(enemy, Position(14, 4))
        battle.configure_turn_order([])
        battle.start_battle()
        self.assertLessEqual(follow_catch_up_skill_penalty(battle, remi), -30.0)

    def test_follow_style_walks_up_to_army_front(self) -> None:
        from wujiang.tactical.engine.core import Battle, Position
        from wujiang.tactical.rooms.ai import difficulty_profile, hero_style, score_move_destination

        battle = Battle(width=16, height=8)
        battle.hero_ai_styles = {1: "follow"}
        hero = create_hero("excel_r056", 1)
        ally = create_hero("strategy_infantry", 1)
        enemy = create_hero("ellie", 2)
        battle.add_unit(hero, Position(2, 4))
        battle.add_unit(ally, Position(3, 4))
        battle.add_unit(enemy, Position(14, 4))
        battle.configure_turn_order([])
        battle.start_battle()
        profile = difficulty_profile("standard")
        stay_rear = score_move_destination(battle, hero, Position(2, 4), hero_style(hero), profile)
        step_to_front = score_move_destination(battle, hero, Position(6, 4), hero_style(hero), profile)
        self.assertGreater(step_to_front, stay_rear)

    def test_follow_anchor_refreshes_after_soldiers_advance(self) -> None:
        from wujiang.tactical.engine.core import Battle, Position
        from wujiang.tactical.rooms.ai import choose_turn_action, follow_anchor_state

        battle = Battle(width=20, height=8)
        battle.hero_ai_styles = {1: "follow"}
        hero = create_hero("ellie", 1)
        soldier = create_hero("strategy_infantry", 1)
        enemy = create_hero("ellie", 2)
        battle.add_unit(hero, Position(1, 4))
        battle.add_unit(soldier, Position(2, 4))
        battle.add_unit(enemy, Position(18, 4))
        battle.configure_turn_order([hero.unit_id])
        battle.start_battle()
        first = follow_anchor_state(battle, 1)[0]
        soldier.position = Position(12, 4)
        stale = follow_anchor_state(battle, 1)[0]
        self.assertEqual((stale.x, stale.y), (first.x, first.y))
        choose_turn_action(battle, hero, "standard")
        updated = follow_anchor_state(battle, 1)[0]
        self.assertGreater(updated.x, first.x)

    def test_follow_style_tracks_leading_soldiers_not_rear_cannons(self) -> None:
        from wujiang.tactical.engine.core import Battle, Position
        from wujiang.tactical.rooms.ai import difficulty_profile, hero_style, score_move_destination

        battle = Battle(width=20, height=10)
        battle.hero_ai_styles = {1: "follow"}
        hero = create_hero("ellie", 1)
        other_hero = create_hero("bard", 1)
        cannon = create_hero("strategy_cannon", 1)
        rear = create_hero("strategy_infantry", 1)
        front = create_hero("strategy_infantry", 1)
        enemy = create_hero("ellie", 2)
        battle.add_unit(hero, Position(1, 1))
        battle.add_unit(other_hero, Position(1, 2))
        battle.add_unit(cannon, Position(1, 3))
        battle.add_unit(rear, Position(2, 1))
        battle.add_unit(front, Position(14, 5))
        battle.add_unit(enemy, Position(18, 5))
        battle.configure_turn_order([])
        battle.start_battle()
        profile = difficulty_profile("standard")
        stay_with_heroes = score_move_destination(battle, hero, Position(1, 1), hero_style(hero), profile)
        walk_to_front = score_move_destination(battle, hero, Position(6, 3), hero_style(hero), profile)
        self.assertGreater(walk_to_front, stay_with_heroes)

    def test_cannon_creep_holds_when_targets_are_already_in_range(self) -> None:
        from wujiang.tactical.engine.army import apply_cannon_creep_ai, command_for_kind, default_army_orders
        from wujiang.tactical.engine.core import Battle, Position

        battle = Battle(width=20, height=8)
        battle.army_ai_players = {1}
        battle.army_orders = default_army_orders()
        cannon = create_hero("strategy_cannon", 1)
        enemy = create_hero("strategy_infantry", 2)
        battle.add_unit(cannon, Position(1, 3))
        battle.add_unit(enemy, Position(8, 3))
        battle.configure_turn_order([])
        battle.start_battle()
        cannon.siege_loaded = True
        apply_cannon_creep_ai(battle, 1)
        self.assertEqual(command_for_kind(battle.army_orders, 1, "cannon")["order"], "hold")

        enemy.position = Position(16, 3)
        apply_cannon_creep_ai(battle, 1)
        self.assertEqual(command_for_kind(battle.army_orders, 1, "cannon")["order"], "advance")

    def test_packed_spawn_spreads_heroes_along_the_front(self) -> None:
        from wujiang.bridge.battle_bridge import STRATEGY_BATTLE_BOARD_HEIGHT, STRATEGY_BATTLE_BOARD_WIDTH

        left = [
            RoomBattleEntry("ellie", 1, 1),
            RoomBattleEntry("bard", 1, 1),
            RoomBattleEntry("dark_human", 1, 1),
        ] + [RoomBattleEntry("strategy_infantry", 1, 1) for _ in range(24)]
        right = [
            RoomBattleEntry("excel_r056", 2, 2),
            RoomBattleEntry("n", 2, 2),
        ] + [RoomBattleEntry("strategy_infantry", 2, 2) for _ in range(24)]
        battle = create_room_battle(
            left,
            right,
            board_width=STRATEGY_BATTLE_BOARD_WIDTH,
            board_height=STRATEGY_BATTLE_BOARD_HEIGHT,
        )
        hero_ys = sorted(
            unit.position.y
            for unit in battle.player_units(1)
            if not is_army_soldier(unit)
        )
        self.assertEqual(len(hero_ys), 3)
        self.assertGreaterEqual(hero_ys[-1] - hero_ys[0], 8)

    def test_follow_style_moves_summons_with_the_front(self) -> None:
        from wujiang.tactical.engine.core import Battle, Position
        from wujiang.tactical.rooms.ai import difficulty_profile, hero_style, score_move_destination

        battle = Battle(width=16, height=8)
        battle.hero_ai_styles = {1: "follow"}
        from wujiang.tactical.heroes.excel_roster import RemiBatSummon

        remi = create_hero("excel_r056", 1)
        bat = RemiBatSummon(1)
        ally = create_hero("strategy_infantry", 1)
        enemy = create_hero("ellie", 2)
        battle.add_unit(remi, Position(2, 4))
        battle.add_unit(bat, Position(2, 5))
        battle.add_unit(ally, Position(3, 4))
        battle.add_unit(enemy, Position(14, 4))
        battle.configure_turn_order([])
        battle.start_battle()
        profile = difficulty_profile("standard")
        stay_rear = score_move_destination(battle, bat, Position(2, 5), hero_style(bat), profile)
        step_to_front = score_move_destination(battle, bat, Position(6, 4), hero_style(bat), profile)
        self.assertGreater(step_to_front, stay_rear)


if __name__ == "__main__":
    unittest.main()
