import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wujiang.platform.http import runtime
from wujiang.bridge.battle_bridge import room_state_with_strategy_sync
from wujiang.platform.match_history import MatchHistoryStore
from wujiang.tactical.rooms.history import record_finished_room_history
from wujiang.tactical.rooms.multiplayer import GameRoom


class BPStatisticsTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = MatchHistoryStore(Path(folder.name) / "history.db")
        self.enterContext(patch.object(runtime, "MATCH_HISTORY_STORE", self.store))

    def test_context_opportunities_identity_and_retry(self):
        for index, (allies, enemy, hero, eligible, user, winner) in enumerate([
            ("[]", "[]", "a", ["a", "b"], 10, 1),
            ("[]", "[]", "b", ["b"], 20, 2),
            ('["x"]', "[]", "a", ["a"], 20, 2),
            ("[]", '["x"]', "b", ["a", "b"], 10, 1),
        ]):
            match = dict(match_id=str(index), winner_team_id=winner, bp_choices=[
                dict(step=2, rules="r", allies=allies, enemies=enemy, eligible=eligible,
                     team=1, hero=hero, user_id=user)])
            self.assertTrue(self.store.record_match(match=match, participants=[]))
            self.assertFalse(self.store.record_match(match=match, participants=[]))
        self.assertEqual(self.store.bp_rates("r", "[]", "[]"), {
            "a": dict(available=1, picks=1, wins=1), "b": dict(available=2, picks=1, wins=0)})
        self.assertEqual(self.store.bp_rates("r", "[]", "[]", user_id=10)["b"],
                         dict(available=1, picks=0, wins=0))
        self.assertEqual(self.store.bp_rates("r", '["x"]', "[]")["a"]["wins"], 0)
        self.assertEqual(self.store.bp_rates("other", "[]", "[]"), {})

    def test_draft_against_ai_and_record_result(self):
        for fmt in ("full", "tier"):
            for size in (3, 5):
                with self.subTest(fmt=fmt, size=size):
                    room = GameRoom(f"BP{fmt}{size}", mode="bp")
                    _, token = room.create_host("Captain", account_user_id=10)
                    room.set_seat_controller(token, 2, "ai")
                    room.set_bp_settings(token, team_size=size, bp_format=fmt, level_cap=size * 5,
                                         show_win_count=True, rule_mode="fixed", fixed_rule="none")
                    room.set_ready(token, True)
                    room.start_battle(token)
                    while room._bp_phase() == "draft":
                        step = len(room.bp_actions)
                        kind, team = room._bp_sequence()[step]
                        self.assertEqual(team, 1)
                        legal = room._bp_candidates(kind, team)
                        target = legal[-1]
                        room.bp_choose(token, target)
                        for reply in room.bp_actions[step + 1:]:
                            self.assertEqual(reply["team_id"], 2)
                        self.assertEqual(room.bp_actions[step]["hero_code"], target)
                    self.assertTrue(room._bp_picks(2))
                    room.open_bp_assign(token)
                    room.start_battle(token)
                    room.battle.winner = 1
                    room.status = "finished"
                    room_state_with_strategy_sync(room, token, base_url=None)
                    self.assertFalse(record_finished_room_history(room))
                    for sample in room.bp_choice_samples:
                        args = (sample["rules"], sample["allies"], sample["enemies"])
                        self.assertGreater(self.store.bp_rates(*args)[sample["hero"]]["picks"], 0)
                        self.assertEqual(sample["user_id"], 10 if sample["team"] == 1 else None)

    def test_next_draft_uses_most_specific_available_history_per_hero(self):
        rules = '["tier", 3, ["arena"], false, 2, null]'
        for i, (context, allies, eligible, hero, user) in enumerate([
            (rules, "[]", ["a"], "a", 10),
            (rules, '["x"]', ["a", "b"], "b", 10),
            (rules.replace("arena", "chain"), "[]", ["a", "b", "c"], "c", 20),
            (rules.replace("3,", "5,"), "[]", ["d"], "d", 10),
        ]):
            sample = dict(step=1, rules=context, allies=allies, enemies="[]",
                          eligible=eligible, team=1, hero=hero, user_id=user)
            self.store.record_match(match=dict(match_id=str(i), winner_team_id=1, bp_choices=[sample]),
                                    participants=[])
        counts = self.store.bp_rates(rules, "[]", "[]", fallback=True)
        self.assertEqual(counts, {
            "a": dict(available=2, picks=1, wins=1, scope="lineup"),
            "b": dict(available=1, picks=0, wins=0, scope="lineup"),
            "c": dict(available=1, picks=1, wins=1, scope="lineup"),
        })
        new_rule = rules.replace("arena", "new")
        self.assertEqual(self.store.bp_rates(new_rule, "[]", "[]", fallback=True), counts)
        self.assertEqual(set(self.store.bp_results(new_rule)), {"a", "b", "c"})
        personal = self.store.bp_rates(new_rule, "[]", "[]", fallback=True, user_id=10)
        self.assertEqual(personal["a"], dict(available=1, picks=1, wins=1, scope="lineup"))
        self.assertEqual(personal["b"], dict(available=1, picks=1, wins=1, scope="format"))
        self.assertEqual(set(personal), {"a", "b"})
        self.assertEqual(set(self.store.bp_rates(rules, "[]", "[]")), {"a"})
