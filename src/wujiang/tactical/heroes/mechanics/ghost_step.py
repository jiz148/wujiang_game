"""R021 ghost step skill."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.heroes.common import DashMoveSkill
from wujiang.tactical.heroes.common import payload_position


class GhostStepSkill(DashMoveSkill):
    def __init__(self) -> None:
        super().__init__("ghost_step", "鬼步", "随时使用：费 1 魔；移动最多 2 格，每回合最多 3 次。", max_distance=2, mana_cost=1, max_uses_per_turn=3)
        self.timing = "instant"

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        cells = battle.reachable_positions(actor, max_distance=2)
        if not cells:
            return False, "没有合法鬼步落点。"
        if payload is not None and payload.get("x") is not None and payload_position(payload) not in cells:
            return False, "鬼步落点不可达。"
        return True, ""

    def can_react_with_payload(self, battle: Battle, actor: HeroUnit, queued_action: Any, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_react_with_payload(battle, actor, queued_action, payload)
        if not ok:
            return ok, reason
        if actor.cannot_move or payload is None or payload.get("x") is None:
            return False, "请选择合法鬼步落点。"
        return (True, "") if payload_position(payload) in battle.reachable_positions(actor, max_distance=2) else (False, "鬼步落点不可达。")

    def reaction_preview(self, battle: Battle, actor: HeroUnit, queued_action: Any) -> dict[str, Any]:
        return self.preview(battle, actor)


