"""Reusable mana-point skill cost behavior."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import QueuedAction
from wujiang.tactical.engine.core import Skill


class ManaPointCostSkill(Skill):
    def __init__(
        self,
        code: str,
        name: str,
        description: str,
        *,
        mana_point_cost: float,
        cooldown_turns: int = 0,
        max_uses_per_turn: int | None = None,
        max_uses_per_battle: int | None = None,
        target_mode: str = "none",
        timing: str = "active",
    ) -> None:
        super().__init__(
            code,
            name,
            description,
            mana_cost=0,
            cooldown_turns=cooldown_turns,
            max_uses_per_turn=max_uses_per_turn,
            max_uses_per_battle=max_uses_per_battle,
            target_mode=target_mode,  # type: ignore[arg-type]
            timing=timing,  # type: ignore[arg-type]
        )
        self.mana_point_cost = mana_point_cost

    def mana_point_cost_for_payload(
        self,
        battle: Battle,
        actor: HeroUnit,
        payload: dict[str, Any] | None = None,
    ) -> float:
        return self.mana_point_cost

    def can_use(
        self,
        battle: Battle,
        actor: HeroUnit,
        payload: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.mana_points + 1e-9 < self.mana_point_cost_for_payload(battle, actor, payload):
            return False, "魔力点不足。"
        return True, ""

    def can_react_with_payload(
        self,
        battle: Battle,
        actor: HeroUnit,
        queued_action: QueuedAction,
        payload: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        ok, reason = super().can_react_with_payload(battle, actor, queued_action, payload)
        if not ok:
            return ok, reason
        if actor.mana_points + 1e-9 < self.mana_point_cost_for_payload(battle, actor, payload):
            return False, "魔力点不足。"
        return True, ""

    def prepay_resources(
        self,
        battle: Battle,
        actor: HeroUnit,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.sync_turn_scope(battle)
        actor.spend_mana_points(self.mana_point_cost_for_payload(battle, actor, payload))
        self.uses_this_turn += 1
        self.uses_this_battle += 1
        if self.cooldown_turns:
            self.cooldown_remaining = self.cooldown_turns

    def mana_cost_text(self) -> str | None:
        value = int(self.mana_point_cost) if float(self.mana_point_cost).is_integer() else self.mana_point_cost
        return f"费 {value} 魔力点"

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data["mana_point_cost"] = self.mana_point_cost
        return data


