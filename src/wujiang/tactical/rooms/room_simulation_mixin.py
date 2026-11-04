"""GameRoom RoomSimulationMixin methods, retained under the public API."""

from __future__ import annotations

from typing import Any, Optional

class RoomSimulationMixin:
    """A cohesive Battle responsibility with no independent state."""

    def _next_ai_planned_action(self) -> Optional[tuple[dict[str, Any], str, Optional[Unit]]]:
        from wujiang.tactical.rooms.multiplayer import (
            ActionError,
            choose_chain_reaction,
            choose_damage_choice_action,
            choose_respawn_action,
            choose_turn_bundle_action,
        )
        if self.battle is None or self.battle.winner is not None:
            return None
        try:
            if self.battle.pending_damage_choice is not None:
                seat = self._current_prompt_seat()
                if seat is None or not seat.is_ai_controlled:
                    return None
                prompt = self.battle.pending_damage_choice
                unit = self.battle.get_unit(prompt["unit_id"])
                return choose_damage_choice_action(self.battle), "ai_damage_choice", unit
            if self.battle.current_respawn_prompt() is not None:
                seat = self._current_prompt_seat()
                if seat is None or not seat.is_ai_controlled:
                    return None
                prompt = self.battle.current_respawn_prompt()
                if prompt is None:
                    return None
                unit = self.battle.get_unit(prompt.unit_id)
                options = sorted(self.battle.respawn_options_for(unit), key=lambda item: (item.x, item.y))
                if not options:
                    return None
                difficulty = seat.ai_difficulty_override or self.default_ai_difficulty
                payload = choose_respawn_action(self.battle, unit, options, difficulty)
                if payload is None:
                    fallback = options[0]
                    payload = {"type": "respawn_select", "unit_id": unit.unit_id, "x": fallback.x, "y": fallback.y}
                return payload, "ai_respawn", unit
            if self.battle.pending_chain is not None:
                seat = self._current_prompt_seat()
                if seat is None or not seat.is_ai_controlled:
                    return None
                current_unit_id = self.battle.pending_chain.current_unit_id()
                if not current_unit_id:
                    return None
                reactor = self.battle.get_unit(current_unit_id)
                options = self.battle.reaction_snapshot_for(reactor).get("actions", [])
                difficulty = seat.ai_difficulty_override or self.default_ai_difficulty
                payload = choose_chain_reaction(self.battle, reactor, options, difficulty)
                return (payload or {"type": "chain_skip"}, "ai_chain", reactor)
            dragon_payload = self._choose_ai_dragon_return_payload()
            if dragon_payload is not None:
                actor = self.battle.get_unit(str(dragon_payload["unit_id"]))
                return dragon_payload, "ai_hell_dragon_return", actor
            instant_payload = self._choose_ai_instant_payload()
            if instant_payload is not None:
                actor = self.battle.get_unit(str(instant_payload.get("unit_id") or ""))
                return instant_payload, "ai_instant", actor
            if self.battle.is_army_turn():
                return {"type": "end_turn"}, "army_turn", None
            seat = self._current_prompt_seat()
            if seat is None or not seat.is_ai_controlled:
                return None
            current_unit = self.battle.current_turn_unit()
            actors = self.battle.current_turn_bundle_units(include_banished=False)
            if current_unit is None and not actors:
                return None
            difficulty = seat.ai_difficulty_override or self.default_ai_difficulty
            payload, actor = choose_turn_bundle_action(
                self.battle,
                actors,
                difficulty,
            )
            return payload, "ai_turn", actor or current_unit
        except ActionError:
            seat = self._current_prompt_seat()
            if seat is None or not seat.is_ai_controlled or self.battle is None:
                return None
            if self.battle.pending_damage_choice is not None:
                prompt = self.battle.pending_damage_choice
                unit = self.battle.get_unit(prompt["unit_id"])
                return {"type": "damage_choice", "unit_id": unit.unit_id,
                        "stat_name": "decline", "target_unit_id": (prompt["options"][0]
                        if prompt.get("kind") in {"attack_swap", "formation"} else "decline")}, "ai_damage_fallback", unit
            if self.battle.pending_chain is not None:
                current_unit_id = self.battle.pending_chain.current_unit_id()
                reactor = self.battle.get_unit(current_unit_id) if current_unit_id else None
                return {"type": "chain_skip"}, "ai_chain_fallback", reactor
            if self.battle.current_respawn_prompt() is not None:
                prompt = self.battle.current_respawn_prompt()
                if prompt is None:
                    return None
                unit = self.battle.get_unit(prompt.unit_id)
                options = sorted(self.battle.respawn_options_for(unit), key=lambda item: (item.x, item.y))
                if not options:
                    return None
                fallback = options[0]
                return (
                    {"type": "respawn_select", "unit_id": unit.unit_id, "x": fallback.x, "y": fallback.y},
                    "ai_respawn_fallback",
                    unit,
                )
            actor = self.battle.current_turn_unit()
            return {"type": "end_turn"}, "ai_turn_fallback", actor


    def _prepare_pending_simulation_action(self) -> bool:
        planned = self._next_ai_planned_action()
        if planned is None:
            return False
        payload, reason, actor = planned
        self.pending_simulation_action = self._build_pending_action_meta(payload, reason=reason, actor=actor)
        self.touch()
        return True


    def _advance_pending_simulation_action(self, *, ignore_due: bool = False) -> bool:
        from wujiang.tactical.rooms.multiplayer import (
            ActionError,
            pending_preview_select_step,
            time,
        )
        pending = self.pending_simulation_action
        if pending is None:
            return False
        if not ignore_due and time.time() < float(pending.get("next_due_at") or 0):
            return False
        preview_cells = list(pending.get("path") or pending.get("cells") or [])
        visible_count = max(0, int(pending.get("visible_count") or 0))
        if visible_count < len(preview_cells):
            using_path = bool(pending.get("path"))
            step, delay = pending_preview_select_step(
                using_path=using_path,
                remaining=len(preview_cells) - visible_count,
            )
            visible_count += max(1, step)
            pending["visible_count"] = visible_count
            pending["phase"] = "selecting" if visible_count < len(preview_cells) else "confirm"
            pending["next_due_at"] = time.time() + self._scaled_simulation_delay(delay)
            self.touch()
            return True
        payload = dict(pending.get("payload") or {})
        reason = str(pending.get("reason") or "ai_action")
        self.pending_simulation_action = None
        try:
            self._perform_battle_action(payload, reason=reason)
        except ActionError as exc:
            self._record_failed_ai_action(payload, reason=reason, error=exc)
            raise
        self.touch()
        return True


    def _record_failed_ai_action(self, payload: dict[str, Any], *, reason: str, error: ActionError) -> None:
        if self.battle is None:
            return
        actor = None
        actor_unit_id = payload.get("unit_id")
        if actor_unit_id:
            try:
                actor = self.battle.get_unit(str(actor_unit_id))
            except Exception:
                actor = None
        self.last_action_meta = self._build_last_action_meta(
            payload,
            reason=f"{reason}_failed",
            actor=actor,
            log_lines=[f"AI 行动未能执行：{error}"],
        )
        self._record_replay_step(f"{reason}_failed")
        self.touch()


    def _damage_choice_fallback_payload(self) -> dict[str, Any]:
        from wujiang.tactical.rooms.multiplayer import (
            ActionError,
        )
        if self.battle is None or self.battle.pending_damage_choice is None:
            raise ActionError("当前没有待选择的伤害。")
        prompt = self.battle.pending_damage_choice
        mandatory = prompt.get("kind") in {"attack_swap", "formation"}
        if mandatory and not prompt.get("options"):
            raise ActionError("当前必选提示没有合法选项，不能跳过结算。")
        return {"type": "damage_choice", "unit_id": prompt["unit_id"],
                "stat_name": "decline",
                "target_unit_id": prompt["options"][0] if mandatory else "decline"}


    def _perform_ai_damage_choice(self) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            ActionError,
            _LOGGER,
            choose_damage_choice_action,
        )
        prompt = self.battle.pending_damage_choice
        action_id = self.last_action_id
        try:
            self._perform_battle_action(choose_damage_choice_action(self.battle), reason="ai_damage_choice")
        except Exception as error:
            _LOGGER.exception("AI damage choice failed: room=%s match=%s turn=%s slot=%s kind=%s event=%s",
                              self.room_id, self.current_match_id, self.battle.turn_number,
                              self.battle.active_turn_unit_id, prompt.get("kind"),
                              prompt.get("event_index"))
            # A post-commit reporting error must not replay an already accepted
            # action or accidentally answer the next choice with a fallback.
            if self.last_action_id != action_id:
                if isinstance(error, ActionError):
                    return  # The accepted action only reported a stale actor.
                raise
            if self.battle.pending_damage_choice is None:
                raise
            try:
                self._perform_battle_action(self._damage_choice_fallback_payload(), reason="ai_damage_fallback")
            except Exception:
                _LOGGER.exception("AI damage fallback failed; prompt retained: room=%s match=%s",
                                  self.room_id, self.current_match_id)
                raise


    def _perform_ai_fallback_after_error(self) -> bool:
        if self.battle is None:
            return False
        seat = self._current_prompt_seat()
        if self.battle.pending_damage_choice is not None:
            if seat is None or not seat.is_ai_controlled:
                return False
            self._perform_battle_action(self._damage_choice_fallback_payload(), reason="ai_damage_fallback")
            return True
        if self.battle.pending_chain is not None:
            if seat is None or not seat.is_ai_controlled:
                return False
            self._perform_battle_action({"type": "chain_skip"}, reason="ai_chain_fallback")
            return True
        if self.battle.current_respawn_prompt() is not None:
            if seat is None or not seat.is_ai_controlled:
                return False
            prompt = self.battle.current_respawn_prompt()
            if prompt is None:
                return False
            unit = self.battle.get_unit(prompt.unit_id)
            options = sorted(self.battle.respawn_options_for(unit), key=lambda item: (item.x, item.y))
            if not options:
                return False
            fallback = options[0]
            self._perform_battle_action(
                {"type": "respawn_select", "unit_id": unit.unit_id, "x": fallback.x, "y": fallback.y},
                reason="ai_respawn_fallback",
            )
            return True
        current_unit = self.battle.current_turn_unit()
        if current_unit is None or not self.battle.units_can_act_in_current_turn():
            self._perform_battle_action({"type": "end_turn"}, reason="ai_turn_fallback")
            return True
        if seat is None or not seat.is_ai_controlled:
            return False
        self._perform_battle_action({"type": "end_turn"}, reason="ai_turn_fallback")
        return True


    def _recover_stalled_inactive_turn(self) -> bool:
        if self.battle is None or self.battle.winner is not None:
            return False
        if self.battle.pending_damage_choice is not None or self.battle.pending_chain is not None or self.battle.current_respawn_prompt() is not None:
            return False
        if self.battle.is_army_turn():
            self._perform_battle_action({"type": "end_turn"}, reason="army_turn")
            return True
        if self.battle.units_can_act_in_current_turn():
            return False
        self._perform_battle_action({"type": "end_turn"}, reason="ai_turn_fallback")
        return True


    def _perform_battle_action(self, payload: dict[str, Any], *, reason: str) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            ActionError,
            RoomError,
            STALE_QUEUED_ACTOR_LOG_MARKER,
        )
        if self.battle is None:
            raise RoomError("å½“å‰æˆ¿é—´è¿˜æ²¡æœ‰å¼€å§‹å¯¹å±€ã€‚")
        actor: Optional[Unit] = None
        actor_unit_id = payload.get("unit_id")
        if actor_unit_id:
            try:
                actor = self.battle.get_unit(str(actor_unit_id))
            except Exception:
                actor = None
        elif payload.get("type") == "chain_skip" and self.battle.pending_chain is not None:
            current_unit_id = self.battle.pending_chain.current_unit_id()
            if current_unit_id:
                actor = self.battle.get_unit(current_unit_id)
        before_log_count = len(self.battle.logs)
        before_stale_count = int(getattr(self.battle, "stale_queued_action_count", 0))
        replay_before = self.replay
        replay_steps_before = len(replay_before.steps) if replay_before is not None else 0
        try:
            self.battle.perform_action(payload)
        except Exception:
            if payload.get("type") == "damage_choice":
                # Engine rollback cannot undo frames appended by the external
                # replay callback during a failed formal choice resolution.
                self.replay = replay_before
                if replay_before is not None:
                    del replay_before.steps[replay_steps_before:]
            raise
        if payload.get("type") == "damage_choice" and self.battle.pending_damage_choice is not None:
            self.replay = replay_before
            if replay_before is not None:
                del replay_before.steps[replay_steps_before:]
        self.battle.record_postgame_action(actor, payload)
        new_logs = self.battle.logs[before_log_count:]
        self.last_action_meta = self._build_last_action_meta(
            payload,
            reason=reason,
            actor=actor,
            log_lines=new_logs,
        )
        self._record_replay_step(reason)
        if self.battle.winner is not None:
            self.status = "finished"
            self._record_bp_win_if_finished()
            self._finish_replay()
        after_stale_count = int(getattr(self.battle, "stale_queued_action_count", 0))
        stale_queued_action = after_stale_count > before_stale_count or any(
            STALE_QUEUED_ACTOR_LOG_MARKER in line for line in new_logs
        )
        if reason.startswith("ai_") and stale_queued_action:
            raise ActionError("AI action actor is no longer present.")


    def _advance_simulation_due(self, *, force_steps: Optional[int] = None) -> int:
        from wujiang.tactical.rooms.multiplayer import (
            ActionError,
            time,
        )
        if self.battle is None or self.battle.winner is not None:
            return 0
        if self.simulation_paused and force_steps is None and self._simulation_enabled():
            return 0
        step_budget = max(0, int(force_steps)) if force_steps is not None else 4
        steps = 0
        safety = 0
        while self.battle is not None and self.battle.winner is None and safety < 64:
            if force_steps is not None and steps >= step_budget:
                break
            if self.pending_simulation_action is not None:
                if force_steps is None and time.time() < float(self.pending_simulation_action.get("next_due_at") or 0):
                    break
                try:
                    if not self._advance_pending_simulation_action(ignore_due=force_steps is not None):
                        break
                except ActionError:
                    if not self._perform_ai_fallback_after_error():
                        break
                steps += 1
            else:
                if self._recover_stalled_inactive_turn():
                    steps += 1
                    safety += 1
                    continue
                if not self._prepare_pending_simulation_action():
                    break
                steps += 1
            safety += 1
            if force_steps is None and self.pending_simulation_action is not None:
                break
        if steps > 0:
            self.simulation_last_advanced_at = time.time()
        self.status = "finished" if self.battle and self.battle.winner is not None else "battle"
        self._record_bp_win_if_finished()
        return steps


    def allows_instant_action_override(self, seat: PlayerSeat, payload: dict[str, Any]) -> bool:
        if self.battle is None or self.battle.pending_damage_choice is not None or self.battle.pending_chain is not None or self.battle.current_respawn_prompt() is not None:
            return False
        if payload.get("type") == "hell_dragon_return":
            unit_id = str(payload.get("unit_id") or "")
            if unit_id not in self.battle.hell_dragon_return_cells:
                return False
            actor = self.battle.get_unit(unit_id)
            return actor.player_id == seat.team_id and self._unit_owner_seat_id(actor) == seat.player_id
        if payload.get("type") != "skill":
            return False
        actor_unit_id = payload.get("unit_id")
        skill_code = str(payload.get("skill_code") or "")
        if not actor_unit_id or not skill_code:
            return False
        actor = self.battle.get_unit(str(actor_unit_id))
        if actor.player_id != seat.team_id:
            return False
        if self._unit_owner_seat_id(actor) != seat.player_id:
            return False
        skill = actor.get_skill(skill_code)
        if skill.timing != "instant":
            return False
        ok, _ = skill.can_use(self.battle, actor, payload)
        return ok


    def _choose_ai_instant_payload(self) -> Optional[dict[str, Any]]:
        from wujiang.tactical.rooms.multiplayer import (
            choose_instant_action,
        )
        if (
            self.battle is None
            or self.battle.winner is not None
            or self.battle.pending_damage_choice is not None
            or self.battle.pending_chain is not None
            or self.battle.current_respawn_prompt() is not None
        ):
            return None
        active_team = self.battle.active_player
        for seat in sorted(self.seats.values(), key=lambda item: item.player_id):
            if not seat.is_ai_controlled or seat.team_id == active_team:
                continue
            owned_units = [
                unit
                for unit in self.battle.instant_action_units_for_player(seat.team_id)
                if self._unit_owner_seat_id(unit) == seat.player_id
            ]
            if not owned_units:
                continue
            difficulty = seat.ai_difficulty_override or self.default_ai_difficulty
            payload = choose_instant_action(self.battle, owned_units, difficulty)
            if payload is not None:
                return payload
        return None


    def _choose_ai_dragon_return_payload(self) -> Optional[dict[str, Any]]:
        from wujiang.tactical.rooms.multiplayer import (
            choose_hell_dragon_return_action,
        )
        if (self.battle is None or self.battle.pending_damage_choice is not None
                or self.battle.pending_chain is not None or self.battle.current_respawn_prompt() is not None
                or self.battle.pending_followup_actions):
            return None
        for unit_id in list(self.battle.hell_dragon_return_cells):
            unit = self.battle.get_unit(unit_id)
            seat = self._seat_for_actor(unit)
            if seat is None or not seat.is_ai_controlled:
                continue
            payload = choose_hell_dragon_return_action(self.battle, unit)
            if payload is not None:
                return payload
        return None


    def _resolve_ai_until_human_input(self, max_steps: Optional[int] = None) -> int:
        from wujiang.tactical.rooms.multiplayer import (
            ActionError,
            DamageChoiceRequired,
            choose_chain_reaction,
            choose_respawn_action,
            choose_turn_bundle_action,
        )
        if self.battle is None or self.battle.winner is not None:
            self.status = "finished" if self.battle and self.battle.winner is not None else self.status
            return 0
        safety = 0
        steps = 0
        while self.battle is not None and self.battle.winner is None and safety < 512:
            if max_steps is not None and steps >= max_steps:
                break
            damage_choice_attempted = False
            try:
                if self.battle.destroyed_active_turn_is_idle():
                    # No actor remains to own an AI prompt. Finish the existing
                    # slot through normal hooks rather than returning zero
                    # forever or directly jumping to another slot.
                    self._perform_battle_action({"type": "end_turn"}, reason="ai_destroyed_turn")
                    steps += 1
                elif self.battle.pending_damage_choice is not None:
                    seat = self._current_prompt_seat()
                    if seat is None or not seat.is_ai:
                        break
                    damage_choice_attempted = True
                    self._perform_ai_damage_choice()
                    steps += 1
                elif self.battle.current_respawn_prompt() is not None:
                    seat = self._current_prompt_seat()
                    if seat is None or not seat.is_ai:
                        break
                    prompt = self.battle.current_respawn_prompt()
                    if prompt is None:
                        break
                    unit = self.battle.get_unit(prompt.unit_id)
                    options = sorted(self.battle.respawn_options_for(unit), key=lambda item: (item.x, item.y))
                    if not options:
                        break
                    difficulty = seat.ai_difficulty_override or self.default_ai_difficulty
                    payload = choose_respawn_action(self.battle, unit, options, difficulty)
                    if payload is None:
                        fallback = options[0]
                        payload = {"type": "respawn_select", "unit_id": unit.unit_id, "x": fallback.x, "y": fallback.y}
                    self._perform_battle_action(payload, reason="ai_respawn")
                    steps += 1
                elif self.battle.pending_chain is not None:
                    seat = self._current_prompt_seat()
                    if seat is None or not seat.is_ai:
                        break
                    current_unit_id = self.battle.pending_chain.current_unit_id()
                    if not current_unit_id:
                        break
                    reactor = self.battle.get_unit(current_unit_id)
                    options = self.battle.reaction_snapshot_for(reactor).get("actions", [])
                    difficulty = seat.ai_difficulty_override or self.default_ai_difficulty
                    payload = choose_chain_reaction(self.battle, reactor, options, difficulty)
                    self._perform_battle_action(payload or {"type": "chain_skip"}, reason="ai_chain")
                    steps += 1
                else:
                    dragon_payload = self._choose_ai_dragon_return_payload()
                    instant_payload = None if dragon_payload is not None else self._choose_ai_instant_payload()
                    if dragon_payload is not None:
                        self._perform_battle_action(dragon_payload, reason="ai_hell_dragon_return")
                        steps += 1
                    elif instant_payload is not None:
                        self._perform_battle_action(instant_payload, reason="ai_instant")
                        steps += 1
                    elif self.battle.is_army_turn():
                        self._perform_battle_action({"type": "end_turn"}, reason="army_turn")
                        steps += 1
                    else:
                        seat = self._current_prompt_seat()
                        if seat is None or not seat.is_ai:
                            break
                        current_unit = self.battle.current_turn_unit()
                        actors = self.battle.current_turn_bundle_units(include_banished=False)
                        if current_unit is None and not actors:
                            break
                        difficulty = seat.ai_difficulty_override or self.default_ai_difficulty
                        payload, _actor = choose_turn_bundle_action(
                            self.battle,
                            actors,
                            difficulty,
                        )
                        self._perform_battle_action(
                            payload,
                            reason="ai_turn",
                        )
                        steps += 1
            except DamageChoiceRequired:
                if self.battle is None or self.battle.pending_damage_choice is None:
                    break
                steps += 1
            except ActionError:
                if damage_choice_attempted:
                    # The choice helper already tried its legal fallback. Do
                    # not keep retrying the same broken prompt in this loop.
                    raise
                seat = self._current_prompt_seat()
                if seat is None or not seat.is_ai:
                    break
                if self.battle.pending_damage_choice is not None:
                    self._perform_battle_action(self._damage_choice_fallback_payload(), reason="ai_damage_fallback")
                    steps += 1
                elif self.battle.pending_chain is not None:
                    self._perform_battle_action({"type": "chain_skip"}, reason="ai_chain_fallback")
                    steps += 1
                elif self.battle.current_respawn_prompt() is not None:
                    prompt = self.battle.current_respawn_prompt()
                    if prompt is None:
                        break
                    unit = self.battle.get_unit(prompt.unit_id)
                    options = sorted(self.battle.respawn_options_for(unit), key=lambda item: (item.x, item.y))
                    if not options:
                        break
                    fallback = options[0]
                    self._perform_battle_action(
                        {"type": "respawn_select", "unit_id": unit.unit_id, "x": fallback.x, "y": fallback.y},
                        reason="ai_respawn_fallback",
                    )
                    steps += 1
                else:
                    self._perform_battle_action({"type": "end_turn"}, reason="ai_turn_fallback")
                    steps += 1
            safety += 1
        self.status = "finished" if self.battle and self.battle.winner is not None else "battle"
        self._record_bp_win_if_finished()
        return steps


    def resolve_ai_until_human_input(self, max_steps: Optional[int] = None) -> int:
        with self._lock:
            return self._resolve_ai_until_human_input(max_steps=max_steps)


    def run_ai_simulation_to_end(self, *, max_steps: int = 5000) -> int:
        with self._lock:
            if self.battle is None:
                return 0
            self.fast_ai_simulation = True
            self.battle.fast_ai_simulation = True
            self._bind_replay_checkpoint()
            self.simulation_paused = False
            self.pending_simulation_action = None
            self._sync_army_ai_players()
            steps = self._resolve_ai_until_human_input(max_steps=max_steps)
            if self.battle.winner is not None:
                self.status = "finished"
                self._finish_replay()
            self.fast_ai_simulation = False
            if self.battle is not None:
                self.battle.fast_ai_simulation = False
            self.touch()
            return steps


    def set_ai_takeover(self, token: str, enabled: Any) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            RoomError,
        )
        with self._lock:
            seat = self.require_seat(token)
            if self.tutorial_state is not None or str(self.experience_kind or "") == "tutorial":
                raise RoomError("教学模式不能交给 AI 接管。")
            if self.battle is None or self.status != "battle" or self.battle.winner is not None:
                raise RoomError("只有正在进行的对局里才能切换 AI 接管。")
            if not seat.is_human:
                raise RoomError("只有玩家席位才能切换 AI 接管。")
            if isinstance(enabled, str):
                next_enabled = enabled.strip().lower() in {"1", "true", "yes", "on"}
            else:
                next_enabled = bool(enabled)
            if next_enabled == bool(getattr(seat, "ai_takeover", False)):
                return
            seat.ai_takeover = next_enabled
            if not next_enabled:
                current = self._current_prompt_seat()
                if current is not None and current.player_id == seat.player_id:
                    self.pending_simulation_action = None
            self._sync_army_ai_players()
            self._sync_turn_timer()
            if next_enabled:
                self.battle.log(f"{seat.name or f'席位 {seat.player_id}'} 让 AI 接管了操作。")
                self._advance_simulation_due(force_steps=2)
            else:
                self.battle.log(f"{seat.name or f'席位 {seat.player_id}'} 收回了操作。")
            self.touch()


    def set_ai_styles(self, token: str, *, hero_ai_style: Any = None, army_ai_style: Any = None) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            RoomError,
            normalize_army_ai_style,
            normalize_hero_ai_style,
        )
        with self._lock:
            seat = self.require_seat(token)
            if self.tutorial_state is not None or str(self.experience_kind or "") == "tutorial":
                raise RoomError("教学模式不能调整 AI 倾向。")
            if self.battle is None or self.status != "battle" or self.battle.winner is not None:
                raise RoomError("只有正在进行的对局里才能调整 AI 倾向。")
            if not seat.is_human:
                raise RoomError("只有玩家席位才能调整 AI 倾向。")
            if hero_ai_style is not None:
                seat.hero_ai_style = normalize_hero_ai_style(hero_ai_style)
            if army_ai_style is not None:
                seat.army_ai_style = normalize_army_ai_style(army_ai_style)
            self._sync_army_ai_players()
            self.touch()


    def control_simulation(self, token: str, action: str, *, speed: Any = None) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            RoomError,
            normalize_simulation_speed,
            time,
        )
        with self._lock:
            self.require_host(token)
            if self.battle is None or self.replay is None:
                raise RoomError("å½“å‰è¿˜æ²¡æœ‰å¯æŽ§åˆ¶çš„ AI å¯¹å±€ / å›žæ”¾ã€‚")
            normalized = str(action or "").strip().lower()
            if normalized == "set_speed":
                self.simulation_speed = normalize_simulation_speed(speed)
                self.touch()
                return
            if normalized == "pause":
                if not self._simulation_enabled():
                    raise RoomError("å½“å‰æ²¡æœ‰å¯ä»¥æš‚åœçš„è‡ªåŠ¨æ¨¡æ‹Ÿã€‚")
                self.simulation_paused = True
                self.touch()
                return
            if normalized == "resume":
                if not self._simulation_enabled():
                    raise RoomError("å½“å‰æ²¡æœ‰å¯ä»¥ç»§ç»­çš„è‡ªåŠ¨æ¨¡æ‹Ÿã€‚")
                self.simulation_paused = False
                self.simulation_last_advanced_at = time.time()
                self.touch()
                return
            if normalized == "step":
                if not self._simulation_enabled():
                    raise RoomError("å½“å‰æ²¡æœ‰å¯ä»¥å•æ­¥æŽ¨è¿›çš„è‡ªåŠ¨æ¨¡æ‹Ÿã€‚")
                self.simulation_paused = True
                self._advance_simulation_due(force_steps=1)
                self.touch()
                return
            raise RoomError("æœªçŸ¥çš„æ¨¡æ‹ŸæŽ§åˆ¶æŒ‡ä»¤ã€‚")


