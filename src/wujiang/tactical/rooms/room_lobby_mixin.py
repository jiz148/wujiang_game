"""GameRoom RoomLobbyMixin methods, retained under the public API."""

from __future__ import annotations

from typing import Any, Optional

from wujiang.tactical.rooms.room_defaults import (
    AUTO_CONFIGURE_COUNT_DEFAULT,
    AUTO_CONFIGURE_POINTS_DEFAULT,
)

class RoomLobbyMixin:
    """Lobby operations composed into GameRoom without independent state."""

    def occupied_seat_count(self) -> int:
        return sum(1 for seat in self.seats.values() if seat.occupied)


    def human_seat_count(self) -> int:
        return sum(1 for seat in self.seats.values() if seat.is_human)


    def ai_seat_count(self) -> int:
        return sum(1 for seat in self.seats.values() if seat.is_ai)


    def seat_for_token(self, token: Optional[str]) -> Optional[PlayerSeat]:
        if not token:
            return None
        for seat in self.seats.values():
            if seat.is_human and seat.token == token:
                seat.mark_seen()
                return seat
        return None


    def require_seat(self, token: Optional[str]) -> PlayerSeat:
        from wujiang.tactical.rooms.multiplayer import (
            RoomError,
        )
        seat = self.seat_for_token(token)
        if seat is None:
            raise RoomError("当前房间身份无效，请重新加入房间。")
        return seat


    def require_host(self, token: Optional[str]) -> PlayerSeat:
        from wujiang.tactical.rooms.multiplayer import (
            RoomError,
        )
        seat = self.require_seat(token)
        if seat.player_id != self.host_player_id:
            raise RoomError("只有房主可以执行这个操作。")
        return seat


    def open_seat(self) -> Optional[PlayerSeat]:
        for player_id in sorted(self.seats):
            seat = self.seats[player_id]
            if seat.can_join:
                return seat
        return None


    def seat_for_name(self, player_name: str) -> Optional[PlayerSeat]:
        from wujiang.tactical.rooms.multiplayer import (
            RoomError,
        )
        matches = [seat for seat in self.seats.values() if seat.matches_name(player_name)]
        if len(matches) > 1:
            raise RoomError("房间内存在同名玩家，无法仅凭昵称恢复席位。")
        return matches[0] if matches else None


    def _first_human_player_id(self) -> Optional[int]:
        for player_id in sorted(self.seats):
            if self.seats[player_id].is_human:
                return player_id
        return None


    def _seat(self, player_id: Any) -> PlayerSeat:
        from wujiang.tactical.rooms.multiplayer import (
            RoomError,
        )
        normalized = int(player_id)
        seat = self.seats.get(normalized)
        if seat is None:
            raise RoomError(f"席位 {normalized} 不存在。")
        return seat


    def _team_seats(self, team_id: int) -> list[PlayerSeat]:
        from wujiang.tactical.rooms.multiplayer import (
            normalize_team_id,
        )
        normalized_team = normalize_team_id(team_id)
        return [seat for seat in sorted(self.seats.values(), key=lambda item: item.player_id) if seat.team_id == normalized_team]


    def _team_quota_sum(self, team_id: int) -> int:
        return sum(seat.random_quota for seat in self._team_seats(team_id))


    def _reset_random_quotas_to_defaults(self) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            TEAM_IDS,
        )
        for seat in self.seats.values():
            seat.random_quota = 0
        for team_id in TEAM_IDS:
            team_seats = self._team_seats(team_id)
            if team_seats:
                team_seats[0].random_quota = self.random_roster_size


    def _seat_has_owned_hero_presence(self, seat: PlayerSeat) -> bool:
        if self.battle is None:
            return False
        return any(
            getattr(unit, "owner_seat_id", None) == seat.player_id and not unit.is_summon
            for unit in self.battle.all_units()
        )


    def _has_human_ai_takeover(self) -> bool:
        return any(seat.is_human and bool(getattr(seat, "ai_takeover", False)) for seat in self.seats.values())


    def _has_interactive_human_presence(self) -> bool:
        return any(
            seat.is_human
            and not bool(getattr(seat, "ai_takeover", False))
            and self._seat_has_owned_hero_presence(seat)
            for seat in self.seats.values()
        )


    def _simulation_enabled(self) -> bool:
        return self.battle is not None and self.battle.winner is None and not self._has_interactive_human_presence()


    def _simulation_interval_seconds(self) -> float:
        return max(0.1, 0.9 / max(self.simulation_speed, 0.1))


    def _scaled_simulation_delay(self, seconds: float) -> float:
        return max(0.05, float(seconds) / max(self.simulation_speed, 0.1))


    def create_host(self, player_name: str, *, account_user_id: Optional[int] = None) -> tuple[int, str]:
        with self._lock:
            seat = self.seats[self.host_player_id]
            token = seat.claim(player_name, account_user_id=account_user_id)
            if account_user_id is not None:
                self.owner_user_id = int(account_user_id)
            self.invalidate_readiness()
            self.touch()
            return seat.player_id, token


    def join(self, player_name: str, *, account_user_id: Optional[int] = None) -> tuple[int, str]:
        from wujiang.tactical.rooms.multiplayer import (
            RoomError,
        )
        with self._lock:
            if self.mode == "autochess" and account_user_id is not None:
                existing_account = next((seat for seat in self.seats.values()
                                         if seat.is_human and seat.account_user_id == account_user_id), None)
                if existing_account is not None and existing_account.token:
                    existing_account.mark_seen()
                    return existing_account.player_id, existing_account.token
            if self.status != "lobby":
                existing = self.seat_for_name(player_name)
                if existing is not None:
                    token = existing.reclaim(player_name, account_user_id=account_user_id)
                    self.touch()
                    return existing.player_id, token
                raise RoomError("对局已经开始，只能用原来的昵称恢复原席位。")
            seat = self.open_seat()
            if seat is None:
                existing = self.seat_for_name(player_name)
                if existing is not None:
                    token = existing.reclaim(player_name, account_user_id=account_user_id)
                    self.touch()
                    return existing.player_id, token
                raise RoomError("房间已经满员。")
            token = seat.claim(player_name, account_user_id=account_user_id)
            self.invalidate_readiness()
            self.touch()
            return seat.player_id, token


    def set_seat_count(self, token: str, seat_count: Any) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            PlayerSeat,
            RoomError,
            default_team_for_seat,
            normalize_room_seat_count,
        )
        with self._lock:
            self.require_host(token)
            if self.status != "lobby":
                raise RoomError("只有在大厅中才能调整席位数。")
            next_count = normalize_room_seat_count(seat_count)
            if self.mode == "bp" and next_count != 2:
                raise RoomError("BP 模式固定两个席位。")
            if self.mode == "autochess":
                raise RoomError("自走棋固定 8 个席位。")
            if self.mode != "autochess" and next_count > 6:
                raise RoomError("该模式最多 6 个席位。")
            current_count = len(self.seats)
            if next_count == current_count:
                return
            if next_count < current_count:
                for player_id in range(next_count + 1, current_count + 1):
                    if self.seats[player_id].occupied:
                        raise RoomError(f"席位 {player_id} 仍被占用，不能直接缩减房间席位数。")
                for player_id in range(current_count, next_count, -1):
                    self.seats.pop(player_id, None)
            else:
                for player_id in range(current_count + 1, next_count + 1):
                    self.seats[player_id] = PlayerSeat(
                        player_id=player_id,
                        team_id=default_team_for_seat(player_id),
                    )
            if self.mode == "random":
                self._reset_random_quotas_to_defaults()
            if self.mode == "autochess":
                from wujiang.tactical.rooms.autochess import AutoChessState
                self.autochess = AutoChessState(next_count)
            self.invalidate_readiness()
            self.touch()


    def remove_seat(self, token: str, seat_id: Any) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            MIN_ROOM_SEAT_COUNT,
            RoomError,
        )
        with self._lock:
            self.require_host(token)
            if self.status != "lobby":
                raise RoomError("只有在大厅中才能删除席位。")
            if self.mode == "autochess":
                raise RoomError("自走棋固定 8 个席位。")
            if len(self.seats) <= MIN_ROOM_SEAT_COUNT:
                raise RoomError(f"房间至少保留 {MIN_ROOM_SEAT_COUNT} 个席位。")
            seat = self._seat(seat_id)
            if seat.player_id == self.host_player_id:
                raise RoomError("不能删除自己的席位。")
            if seat.is_human:
                seat.release()
            host_seat = self.seats.get(self.host_player_id)
            ordered = [item for player_id, item in sorted(self.seats.items()) if player_id != seat.player_id]
            rebuilt: dict[int, PlayerSeat] = {}
            for index, item in enumerate(ordered, start=1):
                item.player_id = index
                rebuilt[index] = item
            self.seats = rebuilt
            if host_seat is not None:
                self.host_player_id = host_seat.player_id
            if self.mode == "random":
                self._reset_random_quotas_to_defaults()
            self.invalidate_readiness()
            self.touch()


    def claim_seat(self, token: str, seat_id: Any) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            RoomError,
        )
        with self._lock:
            actor = self.require_seat(token)
            if self.status != "lobby":
                raise RoomError("只有在大厅中才能换席。")
            target = self._seat(seat_id)
            if target.player_id == actor.player_id:
                return
            if target.is_human:
                raise RoomError("这个席位已经有人。")
            name = actor.name
            account = actor.account_user_id
            heroes = dict(actor.hero_counts)
            army = dict(getattr(actor, "army_counts", {}) or {})
            quota = getattr(actor, "random_quota", 0)
            was_host = actor.player_id == self.host_player_id
            kept_token = actor.token
            # AI 席位的 can_join 是假的。先改成空位再坐进去；失败时不能先把自己的席位清掉，
            # 否则房主令牌没了，界面会掉回大厅而且之后什么都点不动。
            if target.is_ai:
                target.set_open()
            target.claim(name, account_user_id=account)
            target.token = kept_token
            target.hero_counts.clear()
            target.hero_counts.update(heroes)
            target.army_counts = army
            target.random_quota = quota
            actor.release()
            if was_host:
                self.host_player_id = target.player_id
            self.invalidate_readiness()
            self.touch()


    def set_seat_team(self, token: str, seat_id: Any, team_id: Any) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            RoomError,
            normalize_team_id,
        )
        with self._lock:
            self.require_host(token)
            if self.status != "lobby":
                raise RoomError("只有在大厅中才能调整席位队伍。")
            seat = self._seat(seat_id)
            next_team = normalize_team_id(team_id)
            if next_team == seat.team_id:
                return
            seat.team_id = next_team
            self.bp_captains = {team: captain for team, captain in self.bp_captains.items() if captain != seat.player_id}
            self.invalidate_readiness()
            self.touch()


    def set_seat_controller(self, token: str, seat_id: Any, controller_type: str) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            RoomError,
            normalize_controller_type,
        )
        with self._lock:
            self.require_host(token)
            if self.status != "lobby":
                raise RoomError("只有在大厅中才能调整席位状态。")
            seat = self._seat(seat_id)
            next_controller = normalize_controller_type(controller_type)
            if seat.player_id == self.host_player_id and next_controller != "human":
                raise RoomError("不能把房主席位改成开放或 AI。")
            if next_controller == seat.controller_type:
                return
            if next_controller == "human":
                if seat.is_human:
                    return
                raise RoomError("真人席位需要由玩家自己加入。")
            if next_controller == "open":
                if seat.is_human:
                    raise RoomError("已有真人加入的席位不能由房主直接清空。")
                seat.set_open()
                self.invalidate_readiness()
                self.touch()
                return
            if seat.is_human:
                raise RoomError("已有真人加入的席位不能直接改成 AI。")
            seat.set_ai()
            self.invalidate_readiness()
            self.touch()


    def set_default_ai_difficulty(self, token: str, difficulty: Any) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            RoomError,
            normalize_ai_difficulty,
        )
        with self._lock:
            self.require_host(token)
            if self.status != "lobby":
                raise RoomError("只有在大厅中才能调整 AI 默认难度。")
            next_difficulty = normalize_ai_difficulty(difficulty)
            if next_difficulty == self.default_ai_difficulty:
                return
            self.default_ai_difficulty = next_difficulty
            self.invalidate_readiness()
            self.touch()


    def set_seat_ai_difficulty(self, token: str, seat_id: Any, difficulty: Any) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            RoomError,
            normalize_ai_difficulty,
        )
        with self._lock:
            self.require_host(token)
            if self.status != "lobby":
                raise RoomError("只有在大厅中才能调整席位 AI 难度。")
            seat = self._seat(seat_id)
            if not seat.is_ai:
                raise RoomError("只有 AI 席位才能单独设置 AI 难度。")
            next_difficulty = normalize_ai_difficulty(difficulty)
            if next_difficulty == seat.ai_difficulty_override:
                return
            seat.ai_difficulty_override = next_difficulty
            self.invalidate_readiness()
            self.touch()


    def set_mode(self, token: str, mode: str) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            AUTOCHESS_SEAT_COUNT,
            BP_DEFAULT_BOARD_HEIGHT,
            BP_DEFAULT_BOARD_WIDTH,
            DEFAULT_BOARD_HEIGHT,
            DEFAULT_BOARD_WIDTH,
            PlayerSeat,
            ROOMS,
            RoomError,
            default_team_for_seat,
            normalize_room_mode,
        )
        with self._lock:
            self.require_host(token)
            if self.status != "lobby":
                raise RoomError("只有在大厅中才能切换房间模式。")
            next_mode = normalize_room_mode(mode)
            if next_mode == self.mode:
                return
            if next_mode == "bp":
                extras = [seat for seat in self.seats.values() if seat.player_id > 2 and seat.occupied]
                if extras:
                    raise RoomError("BP 模式固定两个席位。请先请出多出的玩家。")
            previous_mode = self.mode
            self.mode = next_mode
            previous_default = ((BP_DEFAULT_BOARD_WIDTH, BP_DEFAULT_BOARD_HEIGHT)
                                if previous_mode == "bp" else (DEFAULT_BOARD_WIDTH, DEFAULT_BOARD_HEIGHT))
            if (self.board_width, self.board_height) == previous_default:
                self.board_width, self.board_height = (
                    (BP_DEFAULT_BOARD_WIDTH, BP_DEFAULT_BOARD_HEIGHT)
                    if next_mode == "bp" else (DEFAULT_BOARD_WIDTH, DEFAULT_BOARD_HEIGHT)
                )
            if next_mode == "autochess":
                for player_id in range(len(self.seats) + 1, AUTOCHESS_SEAT_COUNT + 1):
                    self.seats[player_id] = PlayerSeat(player_id=player_id, team_id=default_team_for_seat(player_id))
                from wujiang.tactical.rooms.autochess import AutoChessState
                self.autochess = AutoChessState(len(self.seats))
            else:
                self.autochess = None
                if previous_mode == "autochess":
                    ROOMS._chess_path(self.room_id).unlink(missing_ok=True)
            self.bp_wins = {1: 0, 2: 0}
            self.bp_counted_match_ids = set()
            self.bp_captains = {}
            self.bp_first_pick_team = None
            self.bp_auto_banned_levels = ()
            self.bp_hero_pool = ()
            self.bp_special_modes = ()
            self.bp_actions = []
            self.bp_assignments = {}
            if self.mode == "bp":
                for player_id in [seat_id for seat_id in list(self.seats) if seat_id > 2]:
                    self.seats.pop(player_id, None)
            for seat in self.seats.values():
                seat.clear_roster()
            if self.mode == "random":
                self._reset_random_quotas_to_defaults()
            self.invalidate_readiness()
            self.touch()


    def set_random_roster_size(self, token: str, roster_size: Any) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            RoomError,
            validate_random_roster_size_for_catalog,
        )
        with self._lock:
            self.require_host(token)
            if self.status != "lobby":
                raise RoomError("只有在大厅中才能设置随机模式的人数 n。")
            if self.mode != "random":
                raise RoomError("只有随机模式才能设置人数 n。")
            next_size = validate_random_roster_size_for_catalog(roster_size)
            if next_size == self.random_roster_size:
                return
            self.random_roster_size = next_size
            self._reset_random_quotas_to_defaults()
            self.invalidate_readiness()
            self.touch()


    def set_random_quota(self, token: str, seat_id: Any, quota: Any) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            RoomError,
            normalize_random_quota,
        )
        with self._lock:
            self.require_host(token)
            if self.status != "lobby":
                raise RoomError("只有在大厅中才能设置随机配额。")
            if self.mode != "random":
                raise RoomError("只有随机模式才需要设置随机配额。")
            seat = self._seat(seat_id)
            next_quota = normalize_random_quota(quota)
            if next_quota == seat.random_quota:
                return
            seat.random_quota = next_quota
            self.invalidate_readiness()
            self.touch()


    def set_board_size(self, token: str, width: Any, height: Any) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            RoomError,
            normalize_board_axis,
        )
        with self._lock:
            self.require_host(token)
            if self.status != "lobby":
                raise RoomError("只有在大厅中才能设置战场大小。")
            next_width = normalize_board_axis(width, label="战场宽度")
            next_height = normalize_board_axis(height, label="战场高度")
            if next_width == self.board_width and next_height == self.board_height:
                return
            self.board_width = next_width
            self.board_height = next_height
            self.touch()


    def set_chess_prep_seconds(self, token: str, seconds: Any) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            RoomError,
        )
        with self._lock:
            self.require_host(token)
            if self.mode != "autochess":
                raise RoomError("只有自走棋房间可以设置备战时间。")
            if self.status != "lobby":
                raise RoomError("只有在大厅中才能设置备战时间。")
            try:
                normalized = int(seconds)
            except (TypeError, ValueError) as exc:
                raise RoomError("备战时间必须是整数。") from exc
            if normalized not in {0, 30, 45, 60, 90}:
                raise RoomError("备战时间只能是 30、45、60、90 秒或无限。")
            if normalized == int(getattr(self, "chess_prep_seconds", 90) or 0):
                return
            self.chess_prep_seconds = normalized
            self.touch()


    def set_chess_battle_speed(self, token: str, speed: Any) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            DEFAULT_SIMULATION_SPEED,
            RoomError,
            normalize_simulation_speed,
        )
        with self._lock:
            self.require_host(token)
            if self.mode != "autochess":
                raise RoomError("只有自走棋房间可以设置交战速度。")
            normalized = normalize_simulation_speed(speed)
            if normalized == float(getattr(self, "chess_battle_speed", DEFAULT_SIMULATION_SPEED) or DEFAULT_SIMULATION_SPEED):
                return
            self.chess_battle_speed = normalized
            self.touch()


    def auto_configure(
        self,
        token: str,
        *,
        method: Any = "count",
        count: Any = AUTO_CONFIGURE_COUNT_DEFAULT,
        points: Any = AUTO_CONFIGURE_POINTS_DEFAULT,
        allow_duplicates: bool = False,
    ) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            AUTO_CONFIGURE_COUNT_DEFAULT,
            AUTO_CONFIGURE_POINTS_DEFAULT,
            RoomError,
            normalize_auto_configure_count,
            normalize_auto_configure_method,
            normalize_auto_configure_points,
            pick_auto_configure_roster,
        )
        normalized_method = normalize_auto_configure_method(method)
        normalized_count = AUTO_CONFIGURE_COUNT_DEFAULT
        normalized_points = AUTO_CONFIGURE_POINTS_DEFAULT
        if normalized_method == "count":
            normalized_count = normalize_auto_configure_count(count)
        else:
            normalized_points = normalize_auto_configure_points(points)
        allow_repeat = bool(allow_duplicates)
        with self._lock:
            self.require_host(token)
            if self.status != "lobby":
                raise RoomError("只有在大厅中才能自动配置。")
            if self.mode == "bp":
                raise RoomError("BP模式由队长禁选武将，不能自动配置阵容。")
            for seat in self.seats.values():
                if not seat.is_human and seat.controller_type != "ai":
                    seat.set_ai()
            if self.mode != "random":
                rosters = [
                    pick_auto_configure_roster(
                        method=normalized_method,
                        count=normalized_count,
                        points=normalized_points,
                        allow_duplicates=allow_repeat,
                    )
                    for _ in self.seats
                ]
                needed = max((len(roster) for roster in rosters), default=0)
                if self.hero_limit > 0 and needed > self.hero_limit:
                    self.hero_limit = needed
                for seat, roster in zip(self.seats.values(), rosters):
                    if seat.is_human and seat.ready:
                        seat.ready = False
                    seat.replace_roster(roster)
            self.invalidate_readiness()
            self.touch()


    def set_turn_timeout(self, token: str, seconds: Any) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            RoomError,
            normalize_turn_timeout,
        )
        with self._lock:
            self.require_host(token)
            if self.status != "lobby":
                raise RoomError("只有在大厅中才能设置回合时限。")
            next_timeout = normalize_turn_timeout(seconds)
            if next_timeout == self.turn_timeout_seconds:
                return
            self.turn_timeout_seconds = next_timeout
            self.touch()


    def set_hero_limit(self, token: str, hero_limit: Any) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            RoomError,
            normalize_room_hero_limit,
        )
        with self._lock:
            self.require_host(token)
            if self.status != "lobby":
                raise RoomError("只有在大厅中才能设置武将数量上限。")
            next_limit = normalize_room_hero_limit(hero_limit)
            if next_limit == self.hero_limit:
                return
            self.hero_limit = next_limit
            if next_limit > 0:
                for seat in self.seats.values():
                    seat.trim_roster_from_end(next_limit)
            self.invalidate_readiness()
            self.touch()


    def set_army_enabled(self, token: str, enabled: Any) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            RoomError,
        )
        with self._lock:
            self.require_host(token)
            if self.status != "lobby":
                raise RoomError("只有在大厅中才能调整士兵配置。")
            self.army_enabled = bool(enabled)
            self.touch()


    def _editable_seat(self, token: str, seat_id: Any | None) -> PlayerSeat:
        from wujiang.tactical.rooms.multiplayer import (
            RoomError,
        )
        viewer = self.require_seat(token)
        if seat_id in {None, "", viewer.player_id}:
            return viewer
        target = self._seat(seat_id)
        if viewer.player_id != self.host_player_id:
            raise RoomError("只能编辑自己控制的席位。")
        if not target.is_ai:
            raise RoomError("房主当前只能代为配置 AI 席位。")
        return target


    def select_hero(self, token: str, hero_code: str, delta: int = 1, seat_id: Any | None = None) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            RoomError,
            hero_lookup,
            normalize_hero_delta,
        )
        with self._lock:
            if self.status != "lobby":
                raise RoomError("对局已经开始，不能再更改武将。")
            if self.mode in {"random", "bp"}:
                raise RoomError("当前模式下不能在大厅手动选将。")
            seat = self._editable_seat(token, seat_id)
            if seat.is_human and seat.ready:
                raise RoomError("准备之后不能再改阵容，请先取消准备。")
            if hero_code not in hero_lookup():
                raise RoomError("所选武将不存在。")
            next_delta = normalize_hero_delta(delta)
            if next_delta > 0 and self.hero_limit > 0 and seat.hero_total_count + next_delta > self.hero_limit:
                raise RoomError(f"每个席位最多选择 {self.hero_limit} 名武将。")
            seat.adjust_hero_count(hero_code, next_delta)
            self.invalidate_readiness()
            self.touch()


    def set_roster(self, token: str, hero_codes: list[str], seat_id: Any | None = None) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            RoomError,
            hero_lookup,
        )
        with self._lock:
            if self.status != "lobby":
                raise RoomError("对局已经开始，不能再更改武将。")
            if self.mode in {"random", "bp"}:
                raise RoomError("当前模式下不能在大厅手动选将。")
            seat = self._editable_seat(token, seat_id)
            if seat.is_human and seat.ready:
                raise RoomError("准备之后不能再改阵容，请先取消准备。")
            known = hero_lookup()
            normalized = [str(code or "").strip() for code in hero_codes]
            if not normalized or any(code not in known for code in normalized):
                raise RoomError("推荐阵容包含不存在的武将。")
            if self.hero_limit > 0 and len(normalized) > self.hero_limit:
                raise RoomError(f"每个席位最多选择 {self.hero_limit} 名武将。")
            seat.hero_counts.clear()
            for code in normalized:
                seat.hero_counts[code] = seat.hero_counts.get(code, 0) + 1
            self.invalidate_readiness()
            self.touch()


    def can_start(self) -> bool:
        return self._start_blocker() is None


    def set_ready(self, token: str, ready: bool) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            RoomError,
        )
        with self._lock:
            seat = self.require_seat(token)
            if self.status != "lobby":
                raise RoomError("只有在房间大厅中才能确认准备。")
            next_ready = bool(ready)
            if next_ready:
                blocker = self._configuration_blocker()
                if blocker is not None:
                    raise RoomError(blocker)
            if seat.ready == next_ready:
                return
            seat.ready = next_ready
            self.touch()


    def human_ready_count(self) -> int:
        return sum(1 for seat in self.seats.values() if seat.is_human and seat.ready)


    def _team_random_rosters(self) -> dict[int, dict[int, list[str]]]:
        from wujiang.tactical.rooms.multiplayer import (
            RoomError,
            TEAM_IDS,
            random_room_hero_codes,
            team_name,
        )
        team1_roster, team2_roster = random_room_hero_codes(self.random_roster_size)
        rosters_by_team = {1: team1_roster, 2: team2_roster}
        assignments: dict[int, dict[int, list[str]]] = {1: {}, 2: {}}
        for team_id in TEAM_IDS:
            offset = 0
            seats = self._team_seats(team_id)
            for seat in seats:
                quota = seat.random_quota
                assignments[team_id][seat.player_id] = list(rosters_by_team[team_id][offset : offset + quota])
                offset += quota
            if offset != len(rosters_by_team[team_id]):
                raise RoomError(f"{team_name(team_id)} 的随机配额总数与 n 不一致。")
        return assignments


    def _battle_entries_for_team(self, team_id: int) -> list[RoomBattleEntry]:
        from wujiang.tactical.rooms.multiplayer import (
            RoomBattleEntry,
            army_codes_from_counts,
        )
        entries: list[RoomBattleEntry] = []
        for seat in self._team_seats(team_id):
            for hero_code in seat.expanded_roster():
                entries.append(
                    RoomBattleEntry(
                        hero_code=hero_code,
                        player_id=team_id,
                        owner_seat_id=seat.player_id,
                    )
                )
            for hero_code in army_codes_from_counts(seat.army_counts):
                entries.append(
                    RoomBattleEntry(
                        hero_code=hero_code,
                        player_id=team_id,
                        owner_seat_id=seat.player_id,
                    )
                )
        return entries


    def set_army_composition(self, token: str, composition: Any, seat_id: Any | None = None) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            RoomError,
            normalize_army_counts,
        )
        with self._lock:
            if self.status != "lobby":
                raise RoomError("对局已经开始，不能再增减士兵。")
            seat = self._editable_seat(token, seat_id)
            if seat.is_human and seat.ready:
                raise RoomError("准备之后不能再改士兵，请先取消准备。")
            try:
                seat.army_counts = normalize_army_counts(composition)
            except ValueError as exc:
                raise RoomError(str(exc)) from exc
            self.invalidate_readiness()
            self.touch()


    def set_army_order(
        self,
        token: str,
        order: Any,
        direction: Any = None,
        team_id: Any | None = None,
        kind: Any | None = None,
        stride: Any | None = None,
        ammo: Any | None = None,
    ) -> dict[str, str]:
        from wujiang.tactical.rooms.multiplayer import (
            ARMY_KIND_LABELS,
            RoomError,
            living_army_units,
            normalize_army_command,
        )
        with self._lock:
            from wujiang.tactical.engine.army import (
                apply_army_order,
                command_for_kind,
                normalize_army_kind,
                present_army_kinds,
            )

            seat = self.require_seat(token)
            try:
                next_team = 1 if int(team_id or seat.team_id) != 2 else 2
            except (TypeError, ValueError) as exc:
                raise RoomError("队伍编号无效。") from exc
            if next_team != seat.team_id and seat.player_id != self.host_player_id:
                raise RoomError("只能设置己方军队指令。")
            if self.status == "lobby":
                has_army = any(
                    sum(int(item.army_counts.get(item_kind, 0) or 0) for item_kind in ARMY_KIND_LABELS) > 0
                    for item in self._team_seats(next_team)
                )
                present_kinds = [
                    item_kind
                    for item_kind in ARMY_KIND_LABELS
                    if any(int(item.army_counts.get(item_kind, 0) or 0) > 0 for item in self._team_seats(next_team))
                ]
            else:
                has_army = self.battle is not None and bool(living_army_units(self.battle, next_team))
                present_kinds = present_army_kinds(self.battle, next_team) if self.battle is not None else []
            if not has_army:
                raise RoomError("这一方还没有士兵，不能设置军队指令。")
            if (
                self.battle is not None
                and bool(getattr(seat, "ai_takeover", False))
                and next_team == seat.team_id
            ):
                raise RoomError("当前已交给 AI 接管，请先停止接管再调整军队指令。")
            try:
                target_kind = None if kind in {None, ""} else normalize_army_kind(kind)
                previous = command_for_kind(self.army_orders_by_team, next_team, target_kind or "infantry")
                command = normalize_army_command(
                    order,
                    direction,
                    player_id=next_team,
                    previous=previous,
                    stride=stride,
                    ammo=ammo,
                )
            except ValueError as exc:
                raise RoomError(str(exc)) from exc
            apply_army_order(
                self.army_orders_by_team,
                next_team,
                command,
                kind=target_kind,
                kinds=present_kinds or None,
            )
            if self.battle is not None:
                self.battle.set_army_order(
                    next_team,
                    command["order"],
                    command["direction"],
                    kind=target_kind,
                    stride=command.get("stride"),
                    ammo=command.get("ammo"),
                )
            self.touch()
            return dict(command)


    def leave(self, token: str) -> int:
        with self._lock:
            seat = self.require_seat(token)
            if self.status == "battle":
                self.surrender(token)
            if self.status == "bp":
                self.status = "lobby"
                self.bp_first_pick_team = None
                self.bp_auto_banned_levels = ()
                self.bp_hero_pool = ()
                self.bp_special_modes = ()
                self.bp_actions = []
                self.bp_assignments = {}
            leaving_player_id = seat.player_id
            seat.release()
            self.bp_captains = {team: captain for team, captain in self.bp_captains.items() if captain != leaving_player_id}
            if leaving_player_id == self.host_player_id:
                self.host_player_id = self._first_human_player_id() or 1
            self.invalidate_readiness()
            self.touch()
            return leaving_player_id


