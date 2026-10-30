"""R021 ghost step skill."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
if TYPE_CHECKING:
    from wujiang.tactical.heroes.ai_policy import AIReactionScoreContext
    from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.engine.core import ActionError, Battle
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.heroes.common import DashMoveSkill
from wujiang.tactical.heroes.common import payload_position


class GhostStepSkill(DashMoveSkill):
    ai_reaction_score_phase = "after_hidden_source"

    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.owned_effect_score(self)

    def ai_effect_score(self, context: AISkillScoreContext) -> float:
        battle, actor = context.battle, context.actor
        destination = context.destination()
        if destination is None or actor.position is None:
            return -1000.0
        style = context.hero_style()
        gain = context.move_score(destination, style) - context.move_score(actor.position, style)
        before = context.best_attack_score()
        with context.probe_rollback():
            try:
                self.execute(battle, actor, context.payload)
            except ActionError:
                return -1000.0
            gain += max(0.0, context.best_attack_score() - before)
        return gain - 22.0 - (20.0 if actor.current_mana <= 1 else 0.0)

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

    def ai_reaction_score(self, context: AIReactionScoreContext) -> float:
        threat = context.threat(context.proxy_target())
        destination = context.destination()
        if destination is None or context.destination_still_targeted(destination):
            return -1000.0
        skill_context = context.skill_context(self.code)
        return threat + 4.0 + skill_context.move_score(destination,
            skill_context.hero_style()) * 0.2


