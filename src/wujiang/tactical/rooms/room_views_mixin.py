"""GameRoom RoomViewsMixin methods, retained under the public API."""

from __future__ import annotations

from typing import Any, Optional

class RoomViewsMixin:
    """A cohesive Battle responsibility with no independent state."""

    def _action_display_name(self, actor: Optional[Unit], payload: dict[str, Any]) -> str:
        from wujiang.tactical.rooms.multiplayer import (
            ActionError,
        )
        action_type = str(payload.get("type") or "")
        if action_type == "move":
            return "移动"
        if action_type == "attack":
            return "普攻"
        if action_type == "skill" and actor is not None:
            try:
                return actor.get_skill(str(payload.get("skill_code") or "")).name
            except ActionError:
                return str(payload.get("skill_code") or "技能")
        if action_type == "chain_react" and actor is not None:
            action_code = str(payload.get("action_code") or "")
            if action_code == "block":
                return "格挡"
            if action_code == "counter":
                return "反击"
            try:
                return actor.get_skill(action_code).name
            except ActionError:
                return action_code or "连锁"
        if action_type == "respawn_select":
            return "重新出现"
        if action_type == "hell_dragon_return":
            return "古龙满状态复活"
        if action_type == "damage_choice":
            if payload.get("target_unit_id"):
                if "@" in str(payload.get("target_unit_id") or ""):
                    return "剑斗布阵"
                return "战斗轮转" if payload.get("target_unit_id") != "decline" else "保持位置"
            return "能力抵消" if payload.get("stat_name") != "decline" else "承受伤害"
        if action_type == "end_turn":
            return "结束回合"
        if action_type == "chain_skip":
            return "不连锁"
        return action_type or "动作"


    def _build_last_action_meta(
        self,
        payload: dict[str, Any],
        *,
        reason: str,
        actor: Optional[Unit],
        log_lines: list[str],
    ) -> dict[str, Any]:
        self.last_action_id += 1
        path = payload.get("path") if isinstance(payload.get("path"), list) else []
        cells = payload.get("cells") if isinstance(payload.get("cells"), list) else []
        target_unit_ids = payload.get("target_unit_ids") if isinstance(payload.get("target_unit_ids"), list) else []
        target_unit_id = payload.get("target_unit_id")
        if target_unit_id:
            target_unit_ids = [*target_unit_ids, str(target_unit_id)]
        point_cells: list[dict[str, int]] = []
        if payload.get("x") is not None and payload.get("y") is not None:
            point_cells.append({"x": int(payload["x"]), "y": int(payload["y"])})
        point_cells.extend(
            {"x": int(cell["x"]), "y": int(cell["y"])}
            for cell in cells
            if isinstance(cell, dict) and cell.get("x") is not None and cell.get("y") is not None
        )
        return {
            "id": self.last_action_id,
            "reason": reason,
            "action_type": str(payload.get("type") or ""),
            "display_name": self._action_display_name(actor, payload),
            "actor_id": actor.unit_id if actor is not None else str(payload.get("unit_id") or ""),
            "actor_name": actor.name if actor is not None else "",
            "actor_player_id": actor.player_id if actor is not None else None,
            "actor_is_ai": self._actor_is_ai_controlled(actor),
            "path": [
                {"x": int(cell["x"]), "y": int(cell["y"])}
                for cell in path
                if isinstance(cell, dict) and cell.get("x") is not None and cell.get("y") is not None
            ],
            "cells": point_cells,
            "target_unit_ids": [str(unit_id) for unit_id in target_unit_ids],
            "log_lines": [str(line) for line in log_lines if str(line).strip()],
        }


    def _visible_last_action_for_viewer(self, viewer: Optional[PlayerSeat]) -> Optional[dict[str, Any]]:
        if self.last_action_meta is None or self.battle is None:
            return None
        payload = dict(self.last_action_meta)
        actor_id = str(payload.get("actor_id") or "")
        if actor_id:
            try:
                actor = self.battle.get_unit(actor_id)
            except Exception:
                actor = None
            if actor is not None and actor.has_status("隐身") and (viewer is None or viewer.team_id != actor.player_id):
                return None
        hidden_unit_ids = {
            unit.unit_id
            for unit in self.battle.all_units()
            if unit.has_status("隐身") and (viewer is None or viewer.team_id != unit.player_id)
        }
        payload["target_unit_ids"] = [
            str(unit_id)
            for unit_id in payload.get("target_unit_ids", [])
            if str(unit_id) not in hidden_unit_ids
        ]
        return payload


    def _build_pending_action_meta(
        self,
        payload: dict[str, Any],
        *,
        reason: str,
        actor: Optional[Unit],
    ) -> dict[str, Any]:
        from wujiang.tactical.rooms.multiplayer import (
            SIMULATION_ACTION_ANNOUNCE_SECONDS,
            time,
        )
        path = payload.get("path") if isinstance(payload.get("path"), list) else []
        cells = payload.get("cells") if isinstance(payload.get("cells"), list) else []
        target_unit_ids = payload.get("target_unit_ids") if isinstance(payload.get("target_unit_ids"), list) else []
        target_unit_id = payload.get("target_unit_id")
        if target_unit_id:
            target_unit_ids = [*target_unit_ids, str(target_unit_id)]
        point_cells: list[dict[str, int]] = []
        if payload.get("x") is not None and payload.get("y") is not None:
            point_cells.append({"x": int(payload["x"]), "y": int(payload["y"])})
        point_cells.extend(
            {"x": int(cell["x"]), "y": int(cell["y"])}
            for cell in cells
            if isinstance(cell, dict) and cell.get("x") is not None and cell.get("y") is not None
        )
        return {
            "id": self.last_action_id + 1,
            "reason": reason,
            "action_type": str(payload.get("type") or ""),
            "display_name": self._action_display_name(actor, payload),
            "actor_id": actor.unit_id if actor is not None else str(payload.get("unit_id") or ""),
            "actor_name": actor.name if actor is not None else "",
            "actor_player_id": actor.player_id if actor is not None else None,
            "actor_is_ai": self._actor_is_ai_controlled(actor),
            "path": [
                {"x": int(cell["x"]), "y": int(cell["y"])}
                for cell in path
                if isinstance(cell, dict) and cell.get("x") is not None and cell.get("y") is not None
            ],
            "cells": point_cells,
            "target_unit_ids": [str(unit_id) for unit_id in target_unit_ids],
            "visible_count": 0,
            "phase": "announce",
            "payload": dict(payload),
            "next_due_at": time.time() + self._scaled_simulation_delay(SIMULATION_ACTION_ANNOUNCE_SECONDS),
        }


    def _visible_pending_action_for_viewer(self, viewer: Optional[PlayerSeat]) -> Optional[dict[str, Any]]:
        if self.pending_simulation_action is None or self.battle is None:
            return None
        payload = {
            key: value
            for key, value in self.pending_simulation_action.items()
            if key not in {"payload", "next_due_at"}
        }
        actor_id = str(payload.get("actor_id") or "")
        if actor_id:
            try:
                actor = self.battle.get_unit(actor_id)
            except Exception:
                actor = None
            if actor is not None and actor.has_status("éšèº«") and (viewer is None or viewer.team_id != actor.player_id):
                return None
        hidden_unit_ids = {
            unit.unit_id
            for unit in self.battle.all_units()
            if unit.has_status("éšèº«") and (viewer is None or viewer.team_id != unit.player_id)
        }
        payload["target_unit_ids"] = [
            str(unit_id)
            for unit_id in payload.get("target_unit_ids", [])
            if str(unit_id) not in hidden_unit_ids
        ]
        return payload


    def _bind_replay_checkpoint(self) -> None:
        if self.battle is None:
            return
        self.battle.on_replay_checkpoint = self._record_replay_step


    def _replay_mode(self) -> str:
        # 子战斗默认不录像；开启后只在当前房间内存中保留本场步骤。
        forced = getattr(self, "replay_forced", None)
        if forced in {"off", "actions", "full"}:
            return str(forced)
        from wujiang.platform.app_settings import replay_recording

        return replay_recording()


    def _record_replay_step(self, reason: str) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            ReplayRecorder,
            battle_state_for_viewer,
        )
        if self.battle is None:
            return
        mode = self._replay_mode()
        if mode == "off":
            return
        if (
            bool(getattr(self, "fast_ai_simulation", False))
            and not bool(getattr(self, "record_fast_replay_steps", False))
            and reason not in {
                "battle_start",
                "surrender",
                "turn_end",
                "match_end",
            }
        ):
            return
        if self.replay is None:
            self.replay = ReplayRecorder(
                self.room_id,
                str(getattr(self, "replay_mode_name", "") or self.mode),
                match_id=self.current_match_id,
            )
        # 自走棋一场里单位很多。完整动作列表和多份视角会在锁里把内存和请求一起拖死。
        # 自走棋每步只留一份观战快照，避免复制多份视角。
        if bool(getattr(self, "record_fast_replay_steps", False)):
            from wujiang.tactical.rooms.autochess import battle_observer_snapshot
            snapshot = battle_observer_snapshot(self.battle)
            self.replay.append_step(
                reason=reason,
                omniscient_battle=snapshot,
                spectator_battle=snapshot,
                seat_views={},
            )
            return
        # actions 档只在开局留一份棋盘，之后每步只记「发生了什么」，不再复制
        # 全体单位。逐步回放要重演才看得懂，所以这档不对外提供播放。
        if mode == "actions" and reason != "battle_start":
            self.replay.append_step(
                reason=reason,
                omniscient_battle={
                    "replay_kind": "action",
                    "reason": reason,
                    "round_number": getattr(self.battle, "round_number", None),
                    "winner": self.battle.winner,
                },
                spectator_battle={},
                seat_views={},
            )
            return
        omniscient_battle = self.battle.to_public_dict()
        spectator_battle = battle_state_for_viewer(self.battle, None, None)
        seat_views = {
            str(seat.player_id): battle_state_for_viewer(self.battle, seat.team_id, seat.player_id)
            for seat in self.seats.values()
            if seat.occupied
        }
        if (
            reason == "match_end"
            and self.replay.steps
            and not any(unit.get("position") for unit in (omniscient_battle.get("units") or []))
        ):
            previous = self.replay.steps[-1]
            omniscient_battle = dict(omniscient_battle)
            omniscient_battle["units"] = list(previous.omniscient_battle.get("units") or [])
            if not omniscient_battle.get("destroyed_units"):
                omniscient_battle["destroyed_units"] = list(previous.omniscient_battle.get("destroyed_units") or [])
            spectator_battle = dict(previous.spectator_battle)
            spectator_battle["winner"] = omniscient_battle.get("winner")
            spectator_battle["win_reason_text"] = omniscient_battle.get("win_reason_text")
            spectator_battle["win_reason_code"] = omniscient_battle.get("win_reason_code")
            seat_views = {
                key: {**value, "winner": omniscient_battle.get("winner")}
                for key, value in previous.seat_views.items()
            }
        self.replay.append_step(
            reason=reason,
            omniscient_battle=omniscient_battle,
            spectator_battle=spectator_battle,
            seat_views=seat_views,
        )


    def _finish_replay(self) -> None:
        if self.battle is None or self.battle.winner is None or self.replay is None:
            return
        self.replay.finish()


    def invite_path(self) -> str:
        return f"/?room={self.room_id}"


    def invite_url(self, base_url: Optional[str]) -> str:
        if not base_url:
            return self.invite_path()
        return f"{base_url}{self.invite_path()}"


    def serialize_summary(self, *, base_url: Optional[str] = None) -> dict[str, Any]:
        from wujiang.tactical.rooms.multiplayer import (
            DEFAULT_BOARD_HEIGHT,
            DEFAULT_BOARD_WIDTH,
            DEFAULT_TURN_TIMEOUT_SECONDS,
            SKIRMISH_HERO_TURN_LIMIT,
            build_postgame_summary,
            hero_lookup,
            public_launch_context,
            room_mode_payload,
        )
        with self._lock:
            heroes_by_code = hero_lookup()
            seats = [seat.to_public_dict(heroes_by_code, self.host_player_id) for seat in self.seats.values()]
            occupied_count = self.occupied_seat_count()
            is_full = occupied_count == len(self.seats)
            mode_meta = room_mode_payload(self.mode)
            return {
                "room_id": self.room_id,
                "match_id": self.current_match_id,
                "status": self.status,
                "mode": mode_meta["code"],
                "mode_name": mode_meta["name"],
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "invite_path": self.invite_path(),
                "invite_url": self.invite_url(base_url),
                "host_player_id": self.host_player_id,
                "owner_user_id": getattr(self, "owner_user_id", None),
                "random_roster_size": self.random_roster_size,
                "bp": self._bp_public_state() if self.mode == "bp" else None,
                "hero_limit": self.hero_limit,
                "army_enabled": bool(getattr(self, "army_enabled", False)),
                "board_width": int(getattr(self, "board_width", DEFAULT_BOARD_WIDTH) or DEFAULT_BOARD_WIDTH),
                "board_height": int(getattr(self, "board_height", DEFAULT_BOARD_HEIGHT) or DEFAULT_BOARD_HEIGHT),
                "turn_timeout_seconds": int(getattr(self, "turn_timeout_seconds", DEFAULT_TURN_TIMEOUT_SECONDS) or 0),
                "hero_turn_limit": int(getattr(self, "hero_turn_limit", 0) or SKIRMISH_HERO_TURN_LIMIT),
                "turn_limit_winner": int(getattr(self, "turn_limit_winner", 2) or 2),
                "default_ai_difficulty": self.default_ai_difficulty,
                "occupied_seat_count": occupied_count,
                "human_seat_count": self.human_seat_count(),
                "ai_seat_count": self.ai_seat_count(),
                "seat_count": len(self.seats),
                "is_full": is_full,
                "can_join": self.status == "lobby" and any(seat.can_join for seat in self.seats.values()),
                "can_start": self.can_start(),
                "configuration_ready": self._configuration_blocker() is None,
                "start_blocker": self._start_blocker(),
                "human_ready_count": self.human_ready_count(),
                "can_rematch": self.mode != "autochess" and self.status == "finished" and public_launch_context(self).get("allow_rematch", True),
                "launch_context": public_launch_context(self),
                "postgame": build_postgame_summary(
                    self.battle,
                    self.replay,
                    seat_names={seat.player_id: seat.name for seat in self.seats.values() if seat.name},
                ),
                "seats": seats,
            }


    def _replay_state_for_viewer(self, viewer: Optional[PlayerSeat]) -> dict[str, Any]:
        if self.replay is None or self.replay.step_count <= 0:
            return {
                "available": False,
                "step_count": 0,
                "last_step_index": 0,
                "saved_path": None,
                "finished": False,
                "can_use_omniscient": False,
                "default_view": "spectator",
            }
        return {
            "available": True,
            "step_count": self.replay.step_count,
            "last_step_index": self.replay.last_index,
            "saved_path": self.replay.saved_path,
            "finished": self.replay.finished_at is not None,
            "can_use_omniscient": self.status == "finished",
            "default_view": "seat" if viewer is not None else "spectator",
        }


    def _simulation_state_for_viewer(self, viewer: Optional[PlayerSeat]) -> dict[str, Any]:
        from wujiang.tactical.rooms.multiplayer import (
            SIMULATION_SPEED_OPTIONS,
        )
        return {
            "enabled": self._simulation_enabled(),
            "paused": self.simulation_paused,
            "speed": self.simulation_speed,
            "speed_options": list(SIMULATION_SPEED_OPTIONS),
            "can_control": (
                viewer is not None
                and viewer.player_id == self.host_player_id
                and self._simulation_enabled()
                and not self._has_human_ai_takeover()
            ),
            "live_step_index": self.replay.last_index if self.replay is not None and self.replay.step_count > 0 else 0,
            "last_action": self._visible_last_action_for_viewer(viewer),
            "pending_action": self._visible_pending_action_for_viewer(viewer),
        }


    def serialize_replay_step(
        self,
        viewer_token: Optional[str],
        *,
        step_index: Any,
        omniscient: bool = False,
    ) -> dict[str, Any]:
        from wujiang.tactical.rooms.multiplayer import (
            RoomError,
        )
        with self._lock:
            if self.replay is None or self.replay.step_count <= 0:
                raise RoomError("å½“å‰æˆ¿é—´è¿˜æ²¡æœ‰å¯ç”¨çš„å›žæ”¾æ•°æ®ã€‚")
            viewer = self.seat_for_token(viewer_token)
            viewer_seat_id = viewer.player_id if viewer is not None else None
            try:
                requested_index = int(step_index)
            except (TypeError, ValueError) as exc:
                raise RoomError("å›žæ”¾æ­¥æ•°å¿…é¡»æ˜¯æ•´æ•°ã€‚") from exc
            resolved_index = max(0, min(requested_index, self.replay.last_index))
            allow_omniscient = bool(omniscient and self.status == "finished")
            return {
                "replay": {
                    **self._replay_state_for_viewer(viewer),
                    "requested_step_index": requested_index,
                    "step_index": resolved_index,
                    "omniscient": allow_omniscient,
                },
                "battle": self.replay.battle_for_step(
                    resolved_index,
                    seat_id=viewer_seat_id,
                    omniscient=allow_omniscient,
                ),
            }


    def serialize_state(self, viewer_token: Optional[str] = None, *, base_url: Optional[str] = None,
                        advance_chess: bool = True, advance_due: bool = True) -> dict[str, Any]:
        from wujiang.tactical.rooms.multiplayer import (
            ARMY_KIND_LABELS,
            DEFAULT_ARMY_AI_STYLE,
            DEFAULT_BOARD_HEIGHT,
            DEFAULT_BOARD_WIDTH,
            DEFAULT_HERO_AI_STYLE,
            DEFAULT_TURN_TIMEOUT_SECONDS,
            MAX_ROOM_SEAT_COUNT,
            MIN_ROOM_SEAT_COUNT,
            SKIRMISH_HERO_TURN_LIMIT,
            _ensure_autochess_worker,
            battle_state_for_viewer,
            build_postgame_summary,
            copy,
            default_army_orders,
            hero_lookup,
            heroes_catalog,
            public_launch_context,
            room_mode_list_payload,
            room_mode_payload,
            time,
        )
        if self.mode == "autochess" and self.autochess is not None:
            # 战斗线程算一步时会占着锁。人多、召唤物多时这一步能拖很久，
            # 状态请求如果一直等，浏览器就会把它们取消。等不及就交上一帧。
            cache_key = (str(viewer_token or ""), str(base_url or ""))
            cached = getattr(self, "_chess_state_cache", {}).get(cache_key)
            if cached is not None:
                from wujiang.tactical.rooms.autochess_frames import current_frame
                if current_frame(self, cached):
                    self.seat_for_token(viewer_token)
                    if advance_chess:
                        _ensure_autochess_worker(self)
                    return copy.deepcopy(cached)
            now = time.monotonic()
            published = getattr(self, "_chess_state_cache_at", {}).get(cache_key, now)
            # Brief duplicate polls can reuse a fresh frame immediately. Older
            # frames retain the bounded wait so polling cannot miss every tiny
            # gap between AI steps and starve committed-frame refreshes.
            acquired = (self._lock.acquire(blocking=False) if cached is not None and now - published < 0.5
                        else self._lock.acquire(timeout=0.25))
            if not acquired:
                cached = getattr(self, "_chess_state_cache", {}).get(cache_key)
                if cached is not None:
                    return copy.deepcopy(cached)
                self._lock.acquire()
            try:
                payload = self._autochess_serialize(viewer_token, base_url=base_url, advance=advance_chess)
                cache = getattr(self, "_chess_state_cache", None)
                if not isinstance(cache, dict):
                    cache = {}
                    self._chess_state_cache = cache
                # A detached committed frame cannot change during AI trials.
                cache[cache_key] = copy.deepcopy(payload)
                self._chess_frame_publish_failed = False
                published_at = getattr(self, "_chess_state_cache_at", None)
                if not isinstance(published_at, dict):
                    published_at = {}
                    self._chess_state_cache_at = published_at
                published_at[cache_key] = time.monotonic()
                while len(cache) > 32:
                    oldest = next(iter(cache))
                    cache.pop(oldest)
                    published_at.pop(oldest, None)
                return payload
            finally:
                self._lock.release()
        with self._lock:
            viewer = self.seat_for_token(viewer_token)
            if advance_due and self.battle is not None and self.battle.winner is None:
                self._advance_simulation_due()
                self._process_due_turn_timeout()
            viewer_player_id = viewer.player_id if viewer else None
            viewer_team_id = viewer.team_id if viewer else None
            viewer_name = viewer.name if viewer else None
            heroes_by_code = hero_lookup()
            mode_meta = room_mode_payload(self.mode)
            room_state = {
                "room_id": self.room_id,
                "match_id": self.current_match_id,
                "status": self.status,
                "mode": mode_meta["code"],
                "mode_name": mode_meta["name"],
                "mode_description": mode_meta["description"],
                "experience_kind": self.experience_kind,
                "launch_context": public_launch_context(self),
                "tutorial": self.tutorial_public_state(),
                "available_modes": room_mode_list_payload(),
                "version": self.version,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "invite_path": self.invite_path(),
                "invite_url": self.invite_url(base_url),
                "host_player_id": self.host_player_id,
                "random_roster_size": self.random_roster_size,
                "bp": self._bp_public_state() if self.mode == "bp" else None,
                "hero_limit": self.hero_limit,
                "army_enabled": bool(getattr(self, "army_enabled", False)),
                "board_width": int(getattr(self, "board_width", DEFAULT_BOARD_WIDTH) or DEFAULT_BOARD_WIDTH),
                "board_height": int(getattr(self, "board_height", DEFAULT_BOARD_HEIGHT) or DEFAULT_BOARD_HEIGHT),
                "turn_timeout_seconds": int(getattr(self, "turn_timeout_seconds", DEFAULT_TURN_TIMEOUT_SECONDS) or 0),
                "hero_turn_limit": int(getattr(self, "hero_turn_limit", 0) or SKIRMISH_HERO_TURN_LIMIT),
                "turn_limit_winner": int(getattr(self, "turn_limit_winner", 2) or 2),
                "default_ai_difficulty": self.default_ai_difficulty,
                "seat_count": len(self.seats),
                "seat_count_min": MIN_ROOM_SEAT_COUNT,
                "seat_count_max": MAX_ROOM_SEAT_COUNT,
                "viewer_player_id": viewer_player_id,
                "viewer_team_id": viewer_team_id,
                "viewer_name": viewer_name,
                "viewer_ai_takeover": bool(viewer is not None and getattr(viewer, "ai_takeover", False)),
                "viewer_hero_ai_style": getattr(viewer, "hero_ai_style", DEFAULT_HERO_AI_STYLE) if viewer else DEFAULT_HERO_AI_STYLE,
                "viewer_army_ai_style": getattr(viewer, "army_ai_style", DEFAULT_ARMY_AI_STYLE) if viewer else DEFAULT_ARMY_AI_STYLE,
                "viewer_is_host": viewer_player_id == self.host_player_id if viewer_player_id is not None else False,
                "occupied_seat_count": self.occupied_seat_count(),
                "human_seat_count": self.human_seat_count(),
                "ai_seat_count": self.ai_seat_count(),
                "is_full": all(seat.occupied for seat in self.seats.values()),
                "can_start": self.can_start(),
                "configuration_ready": self._configuration_blocker() is None,
                "start_blocker": self._start_blocker(),
                "human_ready_count": self.human_ready_count(),
                "can_rematch": (
                    self.status == "finished"
                    and viewer_player_id == self.host_player_id
                    and public_launch_context(self).get("allow_rematch", True)
                ),
                "seats": [seat.to_public_dict(heroes_by_code, self.host_player_id) for seat in self.seats.values()],
                "army_orders": {
                    1: dict(self.army_orders_by_team.get(1) or default_army_orders()[1]),
                    2: dict(self.army_orders_by_team.get(2) or default_army_orders()[2]),
                },
                "army_kinds": [
                    {"kind": kind, "name": ARMY_KIND_LABELS[kind]}
                    for kind in ("infantry", "archer", "cavalry")
                ],
                "turn_timer": self._turn_timer_public_state(),
                "postgame": build_postgame_summary(
                    self.battle,
                    self.replay,
                    seat_names={seat.player_id: seat.name for seat in self.seats.values() if seat.name},
                ),
                "replay": self._replay_state_for_viewer(viewer),
                "simulation": self._simulation_state_for_viewer(viewer),
            }
            battle_state = (
                battle_state_for_viewer(self.battle, viewer_team_id, viewer_player_id)
                if self.battle is not None
                else None
            )
            return {
                "heroes": heroes_catalog(),
                "room": room_state,
                "battle": battle_state,
            }


    def _autochess_serialize(self, viewer_token: Optional[str], *, base_url: Optional[str],
                             advance: bool = True) -> dict[str, Any]:
        from wujiang.tactical.rooms.multiplayer import (
            AUTOCHESS_SEAT_COUNT,
            DEFAULT_SIMULATION_SPEED,
            ROOM_MODES,
            RoomError,
            _ensure_autochess_worker,
            hero_lookup,
            public_launch_context,
            room_mode_list_payload,
            time,
        )
        if self.autochess is None:
            raise RoomError("自走棋状态不存在。")
        prior_phase = self.autochess.phase
        if advance:
            if self.autochess.phase != "battle" and self.autochess.tick(self.seats):
                self.status = self.autochess.phase
                should_persist = (self.autochess.phase != prior_phase or
                                  time.time() - float(getattr(self, "_last_chess_persist_at", 0)) >= 5)
                self.touch(persist_chess=should_persist)
            if self.autochess.phase == "battle":
                _ensure_autochess_worker(self)
        viewer = self.seat_for_token(viewer_token)
        viewer_id = viewer.player_id if viewer else None
        catalog = hero_lookup()
        chess = self.autochess.public(viewer_id, self.seats)
        waiting = [seat.name or f"席位 {seat.player_id}" for seat in self.seats.values() if seat.is_human and not seat.ready]
        room_state = {
            "room_id": self.room_id, "match_id": self.current_match_id,
            "status": self.status, "mode": "autochess", "mode_name": "自走棋",
            "mode_description": ROOM_MODES["autochess"]["description"],
            "experience_kind": "autochess", "launch_context": public_launch_context(self),
            "available_modes": room_mode_list_payload(), "version": self.version,
            "created_at": self.created_at, "updated_at": self.updated_at,
            "invite_path": self.invite_path(), "invite_url": self.invite_url(base_url),
            "host_player_id": self.host_player_id,
            "viewer_player_id": viewer_id, "viewer_team_id": None,
            "viewer_name": viewer.name if viewer else None,
            "viewer_is_host": viewer_id == self.host_player_id,
            "seat_count": len(self.seats), "seat_count_min": AUTOCHESS_SEAT_COUNT, "seat_count_max": AUTOCHESS_SEAT_COUNT,
            "occupied_seat_count": self.occupied_seat_count(),
            "human_seat_count": self.human_seat_count(), "ai_seat_count": self.ai_seat_count(),
            "is_full": all(seat.occupied for seat in self.seats.values()),
            "can_start": self.status == "lobby" and self.human_seat_count() > 0 and not waiting,
            "configuration_ready": self.status == "lobby",
            "start_blocker": (
                None if self.status != "lobby"
                else ("当前至少需要一个真人席位才能开始。" if self.human_seat_count() <= 0
                      else (f"等待真人席位确认准备：{'、'.join(waiting)}。" if waiting else None))
            ),
            "can_rematch": False, "hero_limit": 0,
            "board_width": int(getattr(self, "board_width", 10) or 10),
            "board_height": int(getattr(self, "board_height", 10) or 10), "turn_timeout_seconds": 0,
            "chess_prep_seconds": int(getattr(self, "chess_prep_seconds", 90) or 0),
            "chess_battle_speed": float(getattr(self, "chess_battle_speed", DEFAULT_SIMULATION_SPEED) or DEFAULT_SIMULATION_SPEED),
            "seats": [seat.to_public_dict(catalog, self.host_player_id) for seat in self.seats.values()],
            "autochess": chess, "bp": None,
        }
        return {"room": room_state, "battle": None}


