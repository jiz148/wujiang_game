"""GameRoom RoomTutorialMixin methods, retained under the public API."""

from __future__ import annotations

from typing import Any, Optional

class RoomTutorialMixin:
    """A cohesive Battle responsibility with no independent state."""

    def configure_tutorial(self) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            Position,
            ReplayRecorder,
            RoomError,
            TUTORIAL_ID,
            time,
        )
        with self._lock:
            if self.battle is None:
                raise RoomError("教学战斗尚未创建。")
            fire = next((unit for unit in self.battle.player_units(1) if unit.hero_code == "fire_funeral"), None)
            ellie = next((unit for unit in self.battle.player_units(2) if unit.hero_code == "ellie"), None)
            if fire is None or ellie is None:
                raise RoomError("教学固定阵容不完整。")
            fire.position = Position(3, 4)
            ellie.position = Position(6, 4)
            self.battle.configure_turn_order([fire.unit_id, ellie.unit_id], starting_index=0)
            self.battle.active_turn_unit_id = fire.unit_id
            self.experience_kind = "tutorial"
            self.tutorial_state = {
                "tutorial_id": TUTORIAL_ID,
                "step_id": "select_unit",
                "completed_step_ids": [],
                "started_at": time.time(),
                "first_effective_action_at": None,
                "completed_at": None,
                "retry_count": 0,
            }
            self.tutorial_checkpoint = None
            self.replay = ReplayRecorder(self.room_id, self.mode, match_id=self.current_match_id)
            self._record_replay_step("tutorial_start")
            self.touch()


    def tutorial_public_state(self) -> Optional[dict[str, Any]]:
        from wujiang.tactical.rooms.multiplayer import (
            tutorial_step,
        )
        if self.tutorial_state is None:
            return None
        step_id = str(self.tutorial_state["step_id"])
        return {
            **self.tutorial_state,
            "step": tutorial_step(step_id),
            "can_retry_checkpoint": self.tutorial_checkpoint is not None,
        }


    def _tutorial_advance(self, completed_step_id: str) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            next_tutorial_step_id,
        )
        if self.tutorial_state is None or self.tutorial_state["step_id"] != completed_step_id:
            return
        completed = self.tutorial_state["completed_step_ids"]
        if completed_step_id not in completed:
            completed.append(completed_step_id)
        self.tutorial_state["step_id"] = next_tutorial_step_id(completed_step_id)


    def tutorial_select_unit(self, token: str, unit_id: str) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            RoomError,
        )
        with self._lock:
            seat = self.require_seat(token)
            if seat.team_id != 1 or self.tutorial_state is None or self.battle is None:
                raise RoomError("当前不是可操作的新手教学。")
            if self.tutorial_state["step_id"] != "select_unit":
                return
            unit = self.battle.get_unit(str(unit_id or ""))
            if unit.hero_code != "fire_funeral" or unit.player_id != 1:
                raise RoomError("这一步请点击你控制的火葬者。")
            self._tutorial_advance("select_unit")
            self.touch()


    def _validate_tutorial_action(self, payload: dict[str, Any]) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            RoomError,
        )
        if self.tutorial_state is None or self.battle is None:
            return
        step_id = str(self.tutorial_state["step_id"])
        action_type = str(payload.get("type") or "")
        fire = next(unit for unit in self.battle.player_units(1) if unit.hero_code == "fire_funeral")
        ellie = next(unit for unit in self.battle.player_units(2) if unit.hero_code == "ellie")
        if step_id == "select_unit":
            raise RoomError("先点击火葬者完成选中教学。")
        if step_id == "move":
            if action_type != "move" or str(payload.get("unit_id") or "") != fire.unit_id:
                raise RoomError("这一步只需要让火葬者移动到金色目标格。")
            if (int(payload.get("x", -1)), int(payload.get("y", -1))) != (4, 4):
                raise RoomError("请移动到火葬者与艾莉之间的金色目标格。")
            return
        if step_id == "basic_attack":
            if action_type != "attack" or str(payload.get("target_unit_id") or "") != ellie.unit_id:
                raise RoomError("这一步请选择普通攻击并以艾莉为目标。")
            return
        if step_id == "active_skill":
            if action_type != "skill" or str(payload.get("skill_code") or "") != "pierce":
                raise RoomError("这一步请使用火葬者的主动技能“穿刺”。")
            selected_cells = {
                (int(cell.get("x", -1)), int(cell.get("y", -1)))
                for cell in (payload.get("cells") or [])
                if isinstance(cell, dict)
            }
            if (ellie.position.x, ellie.position.y) not in selected_cells:
                raise RoomError("请用穿刺选择朝向艾莉的两格直线。")
            return
        if step_id == "end_turn":
            if action_type != "end_turn":
                raise RoomError("这一步只需要点击结束回合。")
            return
        if step_id == "chain_response" and action_type not in {"chain_react", "chain_skip"}:
            raise RoomError("等待艾莉触发连锁后，请选择响应或放弃连锁。")


    def _update_tutorial_after_action(self, payload: dict[str, Any]) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            copy,
            time,
        )
        if self.tutorial_state is None or self.battle is None:
            return
        step_id = str(self.tutorial_state["step_id"])
        action_type = str(payload.get("type") or "")
        if step_id == "move" and action_type == "move":
            self._tutorial_advance("move")
            if self.tutorial_state["first_effective_action_at"] is None:
                self.tutorial_state["first_effective_action_at"] = time.time()
        elif step_id == "basic_attack" and action_type == "attack":
            self._tutorial_advance("basic_attack")
        elif step_id == "active_skill" and action_type == "skill":
            self._tutorial_advance("active_skill")
        elif step_id == "end_turn" and action_type == "end_turn":
            self._tutorial_advance("end_turn")
        elif step_id == "chain_response" and action_type in {"chain_react", "chain_skip"}:
            self._tutorial_advance("chain_response")
            if self.battle.winner is None:
                self.tutorial_checkpoint = copy.deepcopy(self.battle)
        if self.tutorial_state["step_id"] == "win_objective" and self.battle.winner == 1:
            completed = self.tutorial_state["completed_step_ids"]
            if "win_objective" not in completed:
                completed.append("win_objective")
            self.tutorial_state["completed_at"] = time.time()


    def retry_tutorial_checkpoint(self, token: str) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            ReplayRecorder,
            RoomError,
            copy,
        )
        with self._lock:
            self.require_seat(token)
            if self.tutorial_state is None or self.tutorial_checkpoint is None:
                raise RoomError("当前教学阶段还没有可恢复的检查点。")
            self.battle = copy.deepcopy(self.tutorial_checkpoint)
            self.battle.winner = None
            self.status = "battle"
            self.tutorial_state["step_id"] = "win_objective"
            self.tutorial_state["completed_at"] = None
            self.tutorial_state["retry_count"] += 1
            self.replay = ReplayRecorder(self.room_id, self.mode, match_id=self.current_match_id)
            self._record_replay_step("tutorial_checkpoint_retry")
            self.touch()


