"""GameRoom RoomTurnMixin methods, retained under the public API."""

from __future__ import annotations

from typing import Any, Optional

class RoomTurnMixin:
    """A cohesive Battle responsibility with no independent state."""

    def current_input_player_id(self) -> Optional[int]:
        if self.battle is None:
            return None
        return int(self.battle.to_public_dict()["input_player"])


    def _unit_owner_seat_id(self, unit: Unit | None) -> Optional[int]:
        from wujiang.tactical.rooms.multiplayer import (
            battle_unit_owner_seat_id,
        )
        if self.battle is None:
            return None
        return battle_unit_owner_seat_id(self.battle, unit)


    def _seat_for_actor(self, unit: Unit | None) -> Optional[PlayerSeat]:
        owner_seat_id = self._unit_owner_seat_id(unit)
        if owner_seat_id is None:
            return None
        return self.seats.get(owner_seat_id)


    def _actor_is_ai_controlled(self, actor: Optional[Unit]) -> bool:
        if actor is None:
            return False
        seat = self._seat_for_actor(actor)
        return bool(seat is not None and seat.is_ai_controlled)


    def _current_prompt_seat(self) -> Optional[PlayerSeat]:
        if self.battle is None:
            return None
        if self.battle.pending_damage_choice is not None:
            return self.seats.get(self._unit_owner_seat_id(self.battle.get_unit(self.battle.pending_damage_choice["unit_id"])) or -1)
        prompt = self.battle.current_respawn_prompt()
        if prompt is not None:
            return self.seats.get(self._unit_owner_seat_id(self.battle.get_unit(prompt.unit_id)) or -1)
        if self.battle.pending_chain is not None:
            current_unit_id = self.battle.pending_chain.current_unit_id()
            if current_unit_id:
                return self.seats.get(self._unit_owner_seat_id(self.battle.get_unit(current_unit_id)) or -1)
            return None
        active_units = self.battle.current_turn_bundle_units()
        current_unit = active_units[0] if active_units else self.battle.current_turn_unit()
        if current_unit is None:
            return None
        return self.seats.get(self._unit_owner_seat_id(current_unit) or -1)


    def _current_turn_prompt(self) -> tuple[Optional[str], Optional[str], Optional[PlayerSeat]]:
        if self.battle is None or self.battle.winner is not None:
            return None, None, None
        seat = self._current_prompt_seat()
        damage_prompt = self.battle.pending_damage_choice
        if damage_prompt is not None:
            return f"damage:{damage_prompt['prompt_id']}", "damage", seat
        prompt = self.battle.current_respawn_prompt()
        if prompt is not None:
            return f"respawn:{prompt.unit_id}", "respawn", seat
        if self.battle.pending_chain is not None:
            current_unit_id = self.battle.pending_chain.current_unit_id()
            return (
                f"chain:{self.battle.completed_turns}:{current_unit_id or 'none'}",
                "chain",
                seat,
            )
        current_unit = self.battle.current_turn_unit()
        if current_unit is None:
            return None, None, seat
        return f"turn:{self.battle.completed_turns}:{current_unit.unit_id}:{self.battle.active_player}", "turn", seat


    def _sync_turn_timer(self, *, now: Optional[float] = None) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            DEFAULT_TURN_TIMEOUT_SECONDS,
            time,
        )
        current_time = time.time() if now is None else now
        prompt_key, _prompt_kind, seat = self._current_turn_prompt()
        enabled = (
            self.status == "battle"
            and self.tutorial_state is None
            and int(getattr(self, "turn_timeout_seconds", DEFAULT_TURN_TIMEOUT_SECONDS) or 0) > 0
            and prompt_key is not None
            and seat is not None
            and seat.is_human
            and not bool(getattr(seat, "ai_takeover", False))
        )
        if not enabled:
            self.turn_prompt_key = None
            self.turn_prompt_started_at = None
            self.turn_deadline_at = None
            return
        if prompt_key != self.turn_prompt_key or self.turn_deadline_at is None:
            self.turn_prompt_key = prompt_key
            self.turn_prompt_started_at = current_time
            self.turn_deadline_at = current_time + float(self.turn_timeout_seconds)


    def _process_due_turn_timeout(self, *, now: Optional[float] = None) -> bool:
        from wujiang.tactical.rooms.multiplayer import (
            time,
        )
        current_time = time.time() if now is None else now
        self._sync_turn_timer(now=current_time)
        if self.turn_deadline_at is None or current_time < self.turn_deadline_at or self.battle is None:
            return False
        _prompt_key, prompt_kind, seat = self._current_turn_prompt()
        if seat is None or not seat.is_human or prompt_kind is None:
            self._sync_turn_timer(now=current_time)
            return False
        if prompt_kind == "chain":
            payload = {"type": "chain_skip"}
            action_label = "自动放弃连锁"
        elif prompt_kind == "damage":
            prompt = self.battle.pending_damage_choice
            if prompt is None:
                return False
            payload = {"type": "damage_choice", "unit_id": prompt["unit_id"],
                       "stat_name": "decline", "target_unit_id": (prompt["options"][0]
                       if prompt.get("kind") in {"attack_swap", "formation"} else "decline")}
            action_label = ("自动剑斗布阵" if prompt.get("kind") == "formation" else
                            "自动交换位置" if prompt.get("kind") == "attack_swap" else
                            "自动放弃可选换位或搬运" if prompt.get("kind") in {"optional_swap", "optional_placement"} else
                            "自动放弃电子龙瞬移" if prompt.get("kind") == "electronic_teleport" else
                            "自动放弃轮末穿行" if prompt.get("kind") == "end_dash" else
                            "自动保持位置" if prompt.get("kind") == "rotation" else "自动承受伤害")
        elif prompt_kind == "respawn":
            prompt = self.battle.current_respawn_prompt()
            if prompt is None:
                return False
            unit = self.battle.get_unit(prompt.unit_id)
            options = sorted(self.battle.respawn_options_for(unit), key=lambda item: (item.x, item.y))
            if not options:
                return False
            fallback = options[0]
            payload = {"type": "respawn_select", "unit_id": unit.unit_id, "x": fallback.x, "y": fallback.y}
            action_label = "自动选择复活位置"
        else:
            payload = {"type": "end_turn"}
            action_label = "自动结束回合"
        self.battle.log(f"{seat.name or f'席位 {seat.player_id}'} 操作超时，系统已{action_label}。")
        self._perform_battle_action(payload, reason="turn_timeout")
        self.last_turn_timeout = {
            "seat_id": seat.player_id,
            "player_name": seat.name or None,
            "prompt_kind": prompt_kind,
            "action": payload["type"],
            "occurred_at": current_time,
        }
        self.turn_prompt_key = None
        self.turn_prompt_started_at = None
        self.turn_deadline_at = None
        if self.battle.winner is None:
            self._advance_simulation_due(force_steps=1)
        self.status = "finished" if self.battle.winner is not None else "battle"
        self._sync_turn_timer(now=current_time)
        self.touch()
        return True


    def _turn_timer_public_state(self) -> dict[str, Any]:
        from wujiang.tactical.rooms.multiplayer import (
            DEFAULT_TURN_TIMEOUT_SECONDS,
            time,
        )
        now = time.time()
        self._sync_turn_timer(now=now)
        _prompt_key, prompt_kind, seat = self._current_turn_prompt()
        remaining = None
        if self.turn_deadline_at is not None:
            remaining = max(0, int(self.turn_deadline_at - now + 0.999))
        return {
            "enabled": self.turn_deadline_at is not None,
            "duration_seconds": int(getattr(self, "turn_timeout_seconds", DEFAULT_TURN_TIMEOUT_SECONDS) or 0),
            "deadline_at": self.turn_deadline_at,
            "remaining_seconds": remaining,
            "prompt_kind": prompt_kind if self.turn_deadline_at is not None else None,
            "prompt_seat_id": seat.player_id if self.turn_deadline_at is not None and seat is not None else None,
            "last_timeout": dict(self.last_turn_timeout) if self.last_turn_timeout is not None else None,
            "server_now": now,
        }


