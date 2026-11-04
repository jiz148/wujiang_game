"""GameRoom RoomMatchMixin methods, retained under the public API."""

from __future__ import annotations

from typing import Any, Optional

class RoomMatchMixin:
    """A cohesive Battle responsibility with no independent state."""

    def _start_blocker(self) -> Optional[str]:
        if self.mode == "autochess":
            if self.status != "lobby":
                return "自走棋已开局。"
            if self.human_seat_count() <= 0:
                return "当前至少需要一个真人席位才能开始。"
            waiting = [seat.name or f"席位 {seat.player_id}" for seat in self.seats.values() if seat.is_human and not seat.ready]
            if waiting:
                return f"等待真人席位确认准备：{'、'.join(waiting)}。"
            return None
        if self.mode == "bp" and self.status == "bp":
            return self._bp_assignment_blocker()
        blocker = self._configuration_blocker()
        if blocker is not None:
            return blocker
        waiting = [seat.name or f"席位 {seat.player_id}" for seat in self.seats.values() if seat.is_human and not seat.ready]
        if waiting:
            return f"等待真人席位确认准备：{'、'.join(waiting)}。"
        return None


    def _configuration_blocker(self) -> Optional[str]:
        from wujiang.tactical.rooms.multiplayer import (
            BP_FORMAT_TIER,
            TEAM_IDS,
            team_name,
        )
        if self.mode == "autochess":
            return None if self.status == "lobby" else "自走棋已开局。"
        if self.mode == "bp" and self.status == "bp":
            return self._bp_assignment_blocker()
        if self.battle is not None:
            return "当前房间已经在对局中。"
        if not self.seats:
            return "房间里还没有席位。"
        if any(not seat.occupied for seat in self.seats.values()):
            return "仍有开放席位未被真人或 AI 占用。"
        if self.human_seat_count() <= 0:
            return "当前至少需要一个真人席位才能开始。"
        if self.mode == "bp":
            self._sync_default_captains()
            for team_id in TEAM_IDS:
                captain = self.seats.get(self.bp_captains.get(team_id, -1))
                if captain is None or not captain.is_human or captain.team_id != team_id:
                    return f"请先为{team_name(team_id)}指定真人队长。"
            if self.bp_format == BP_FORMAT_TIER:
                if not self._tier_pool_can_start():
                    return "勾选的等级里没有足够的武将，双方无法各选到一名。"
                return None
            if not self._bp_feasible_level_pairs():
                return f"当前武将库无法在随机禁用两个等级后满足每队总等级{self._bp_level_cap()}的上限。"
            return None
        if self.mode == "random":
            for team_id in TEAM_IDS:
                if self._team_quota_sum(team_id) != self.random_roster_size:
                    return f"{team_name(team_id)}的随机武将配额之和必须等于 n = {self.random_roster_size}。"
            return None
        for team_id in TEAM_IDS:
            if sum(seat.hero_total_count for seat in self._team_seats(team_id)) <= 0:
                return f"{team_name(team_id)}还没有配置任何武将。"
        return None


    def _legacy_start_blocker(self) -> Optional[str]:
        from wujiang.tactical.rooms.multiplayer import (
            TEAM_IDS,
            team_name,
        )
        if self.battle is not None:
            return "å½“å‰æˆ¿é—´å·²ç»åœ¨å¯¹å±€ä¸­ã€‚"
        if not self.seats:
            return "æˆ¿é—´é‡Œè¿˜æ²¡æœ‰å¸­ä½ã€‚"
        if any(not seat.occupied for seat in self.seats.values()):
            return "ä»æœ‰å¼€æ”¾å¸­ä½æœªè¢«çœŸäººæˆ– AI å ç”¨ã€‚"
        if self.human_seat_count() <= 0:
            return "å½“å‰è‡³å°‘éœ€è¦ä¸€ä¸ªçœŸäººå¸­ä½æ‰èƒ½å¼€å§‹ã€‚"
        if self.mode == "random":
            for team_id in TEAM_IDS:
                if self._team_quota_sum(team_id) != self.random_roster_size:
                    return f"{team_name(team_id)} çš„éšæœºæ­¦å°†é…é¢ä¹‹å’Œå¿…é¡»ç­‰äºŽ n = {self.random_roster_size}ã€‚"
            return None
        for team_id in TEAM_IDS:
            if sum(seat.hero_total_count for seat in self._team_seats(team_id)) <= 0:
                return f"{team_name(team_id)} è¿˜æ²¡æœ‰é…ç½®ä»»ä½•æ­¦å°†ã€‚"
        return None


    def start_battle(self, token: str, *, require_confirmation: bool = False) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            ActionError,
            DEFAULT_BOARD_HEIGHT,
            DEFAULT_BOARD_WIDTH,
            DEFAULT_SIMULATION_SPEED,
            ReplayRecorder,
            RoomError,
            SKIRMISH_HERO_TURN_LIMIT,
            create_room_battle,
            default_army_orders,
            time,
        )
        with self._lock:
            if self.mode == "autochess":
                self.require_host(token)
                if self.status != "lobby" or self.autochess is None:
                    raise RoomError("自走棋已经开局。")
                blocker = self._start_blocker()
                if blocker is not None:
                    raise RoomError(blocker)
                for seat in self.seats.values():
                    if not seat.occupied:
                        seat.set_ai()
                self.autochess.use_board_size(self.board_width, self.board_height)
                self.autochess.use_preparation_seconds(int(getattr(self, "chess_prep_seconds", 90) or 0))
                self.autochess.start()
                self.status = "preparation"
                self.touch()
                return
            if self.mode == "bp" and self.status == "lobby":
                self.require_host(token)
                blocker = self._start_blocker()
                if blocker is not None:
                    raise RoomError(blocker)
                self._begin_bp()
                self.touch()
                return
            if require_confirmation:
                self.require_host(token)
            else:
                self.require_seat(token)
            if self.status != ("bp" if self.mode == "bp" else "lobby"):
                raise RoomError("å½“å‰æˆ¿é—´å·²ç»åœ¨å¯¹å±€ä¸­ã€‚")
            blocker = self._start_blocker() if require_confirmation else self._configuration_blocker()
            if blocker is not None:
                raise RoomError(blocker)
            if self.mode == "bp":
                self._materialize_bp_assignments()
            if self.mode == "random":
                assignments = self._team_random_rosters()
                for seat in self.seats.values():
                    seat.replace_roster(assignments[seat.team_id].get(seat.player_id, []))
            player1_entries = self._battle_entries_for_team(1)
            player2_entries = self._battle_entries_for_team(2)
            self.match_number += 1
            self.current_match_id = f"{self.room_id}-{self.match_number}"
            try:
                self.battle = create_room_battle(
                    player1_entries,
                    player2_entries,
                    mode=self.mode,
                    board_width=int(getattr(self, "board_width", DEFAULT_BOARD_WIDTH) or DEFAULT_BOARD_WIDTH),
                    board_height=int(getattr(self, "board_height", DEFAULT_BOARD_HEIGHT) or DEFAULT_BOARD_HEIGHT),
                    turn_timeout_limit=int(getattr(self, "hero_turn_limit", 0) or SKIRMISH_HERO_TURN_LIMIT),
                    turn_timeout_winner=int(getattr(self, "turn_limit_winner", 2) or 2),
                    special_mode_codes=self.bp_special_modes if self.mode == "bp" else (),
                )
            except (ActionError, ValueError) as exc:
                raise RoomError("当前战场放不下这些武将，请把战场调大后再开局。") from exc
            self.battle.army_orders = default_army_orders()
            for team_id, commands in self.army_orders_by_team.items():
                if not isinstance(commands, dict):
                    continue
                if commands.get("order"):
                    self.battle.set_army_order(
                        int(team_id),
                        commands.get("order", "advance"),
                        commands.get("direction"),
                        stride=commands.get("stride"),
                    )
                    continue
                for kind, command in commands.items():
                    if not isinstance(command, dict):
                        continue
                    self.battle.set_army_order(
                        int(team_id),
                        command.get("order", "advance"),
                        command.get("direction"),
                        kind=kind,
                        stride=command.get("stride"),
                        ammo=command.get("ammo"),
                    )
            self.replay = ReplayRecorder(self.room_id, self.mode, match_id=self.current_match_id)
            self.simulation_paused = False
            self.simulation_speed = DEFAULT_SIMULATION_SPEED
            self.simulation_last_advanced_at = time.time()
            self.pending_simulation_action = None
            self.last_action_id = 0
            self.last_action_meta = None
            self.turn_prompt_key = None
            self.turn_prompt_started_at = None
            self.turn_deadline_at = None
            self.last_turn_timeout = None
            self.status = "battle"
            for seat in self.seats.values():
                seat.ai_takeover = False
            self._bind_replay_checkpoint()
            self._record_replay_step("battle_start")
            self._sync_army_ai_players()
            self._advance_simulation_due(force_steps=1)
            self._sync_turn_timer()
            self.touch()


    def restart_lobby(self, token: str) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            DEFAULT_SIMULATION_SPEED,
            RoomError,
        )
        with self._lock:
            self.require_host(token)
            if self.status != "finished":
                raise RoomError("åªæœ‰å¯¹å±€ç»“æŸåŽï¼Œæ‰èƒ½é‡æ–°å¼€å§‹é€‰å°†ã€‚")
            self._record_bp_win_if_finished()
            self._finish_replay()
            self.battle = None
            self.replay = None
            self.current_match_id = None
            self.simulation_paused = False
            self.simulation_speed = DEFAULT_SIMULATION_SPEED
            self.simulation_last_advanced_at = None
            self.pending_simulation_action = None
            self.last_action_id = 0
            self.last_action_meta = None
            self.turn_prompt_key = None
            self.turn_prompt_started_at = None
            self.turn_deadline_at = None
            self.last_turn_timeout = None
            self.status = "lobby"
            for seat_id in self.bp_virtual_seat_ids:
                self.seats.pop(seat_id, None)
            self.bp_virtual_seat_ids = []
            self.bp_first_pick_team = None
            self.bp_auto_banned_levels = ()
            self.bp_hero_pool = ()
            self.bp_special_modes = ()
            self.bp_actions = []
            self.bp_assignments = {}
            for seat in self.seats.values():
                if self.mode != "bp":
                    seat.ready = False
                seat.ai_takeover = False
                if self.mode == "bp":
                    seat.clear_roster()
            if self.mode == "bp":
                if self._configuration_blocker() is None:
                    self._begin_bp()
                else:
                    self.invalidate_readiness()
            self.touch()


    def perform_action(self, token: str, payload: dict[str, Any]) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            ActionError,
            RoomError,
            copy,
        )
        with self._lock:
            seat = self.require_seat(token)
            if bool(getattr(seat, "ai_takeover", False)):
                raise RoomError("当前已交给 AI 接管，请先停止接管再操作。")
            if self.battle is None:
                raise RoomError("å½“å‰æˆ¿é—´è¿˜æ²¡æœ‰å¼€å§‹å¯¹å±€ã€‚")
            current_player = self.current_input_player_id()
            instant_override = self.allows_instant_action_override(seat, payload)
            if current_player != seat.team_id and not instant_override:
                raise RoomError("çŽ°åœ¨è¿˜æ²¡è½®åˆ°ä½ è¿™è¾¹æ“ä½œã€‚")
            actor_unit_id = payload.get("unit_id")
            if actor_unit_id:
                actor = self.battle.get_unit(str(actor_unit_id))
                if actor.player_id != seat.team_id:
                    raise RoomError("ä¸èƒ½æ“ä½œå¯¹æ–¹å•ä½ã€‚")
                if self._unit_owner_seat_id(actor) != seat.player_id:
                    raise RoomError("ä¸èƒ½æ“ä½œåŒé˜Ÿå…¶ä»–å¸­ä½æ‹¥æœ‰çš„å•ä½ã€‚")
            elif not instant_override:
                responsible_seat = self._current_prompt_seat()
                if responsible_seat is not None and responsible_seat.player_id != seat.player_id:
                    raise RoomError("çŽ°åœ¨è¿˜æ²¡è½®åˆ°ä½ æŽ§åˆ¶çš„å•ä½ã€‚")
            tutorial_response_recovery = (
                copy.deepcopy(self.battle)
                if self.tutorial_state is not None and self.tutorial_state["step_id"] == "chain_response"
                else None
            )
            try:
                self._validate_tutorial_action(payload)
                self._perform_battle_action(payload, reason="player_action")
            except ActionError as exc:
                raise RoomError(str(exc)) from exc
            self._update_tutorial_after_action(payload)
            if (
                tutorial_response_recovery is not None
                and self.tutorial_checkpoint is None
                and self.battle.winner == 2
            ):
                # A legal skip may let the queued AI attack defeat Fire Funeral.
                # Keep the last surviving formal battle state for the free-fight retry.
                self.tutorial_checkpoint = tutorial_response_recovery
            if self.battle is not None and self.battle.winner is None:
                if self.tutorial_state is not None:
                    self._resolve_ai_until_human_input()
                else:
                    self._advance_simulation_due(force_steps=1)
            self.status = "finished" if self.battle and self.battle.winner is not None else "battle"
            self.touch()


    def autochess_action(self, token: str, action: str, payload: dict[str, Any]) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            RoomError,
        )
        with self._lock:
            seat = self.require_seat(token)
            chess = self.autochess
            if self.mode != "autochess" or chess is None:
                raise RoomError("当前不是自走棋房间。")
            if action == "force_finish_round":
                if seat.player_id != self.host_player_id:
                    raise RoomError("只有房主可以强行结束本轮战斗。")
                chess.force_finish_open_matches()
                self.status = chess.phase
                self.touch()
                return
            actions = {
                "buy": lambda: chess.buy(seat.player_id, int(payload.get("slot", -1)),
                                          str(payload.get("piece_id") or "") or None),
                "reroll": lambda: chess.reroll(seat.player_id),
                "level_up": lambda: chess.level_up(seat.player_id),
                "upgrade": lambda: chess.choose_upgrade(seat.player_id, str(payload.get("piece_id") or ""), str(payload.get("stat") or "")),
                "place": lambda: chess.place(seat.player_id, str(payload.get("piece_id") or ""),
                                               int(payload["x"]) if payload.get("x") is not None else None,
                                               int(payload["y"]) if payload.get("y") is not None else None),
                "sell": lambda: chess.sell(seat.player_id, str(payload.get("piece_id") or "")),
                "draw_equipment": lambda: chess.draw_equipment(seat.player_id),
                "equip": lambda: chess.equip(seat.player_id, str(payload.get("piece_id") or ""), str(payload.get("code") or "")),
                "unequip": lambda: chess.unequip(seat.player_id, str(payload.get("piece_id") or ""), str(payload.get("code") or "")),
                "ready": lambda: chess.ready(seat.player_id),
            }
            if action not in actions:
                raise RoomError("未知自走棋操作。")
            try:
                actions[action]()
            except (TypeError, ValueError, KeyError) as exc:
                raise RoomError("自走棋操作参数无效。") from exc
            chess.tick(self.seats, action_budget=0, prepare_budget=0)
            self.status = chess.phase
            self.touch()


    def surrender(self, token: str) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            RoomError,
            team_name,
        )
        with self._lock:
            seat = self.require_seat(token)
            if self.battle is None or self.status != "battle":
                raise RoomError("\u5f53\u524d\u623f\u95f4\u4e0d\u5728\u5bf9\u5c40\u4e2d\uff0c\u4e0d\u80fd\u6295\u964d\u3002")
            winner = 2 if seat.team_id == 1 else 1
            self.battle.pending_chain = None
            self.battle.pending_respawn_unit_ids = []
            self.battle.winner = winner
            self.battle.win_reason_code = "surrender"
            self.battle.win_reason_text = f"{seat.name or f'席位 {seat.player_id}'} 投降。"
            self.battle._append_summary_event(
                "match_end",
                actor_unit_id=None,
                actor_name=seat.name or f"席位 {seat.player_id}",
                actor_player_id=winner,
                target_name=seat.name or f"席位 {seat.player_id}",
                action_name="投降",
                amount=0,
            )
            self.battle.log(
                f"{seat.name or f'\u5e2d\u4f4d {seat.player_id}'} "
                f"\u6295\u964d\u3002{team_name(winner)}\u83b7\u80dc\u3002"
            )
            self.status = "finished"
            self._record_replay_step("surrender")
            self._finish_replay()
            self.touch()


