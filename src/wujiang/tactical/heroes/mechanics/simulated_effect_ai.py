"""Shared outcome probe for hero skills with declared area effects."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext


class SimulatedEffectDeltaScoring:
    ai_effect_base_score = -4.0

    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.owned_effect_score(self)

    def ai_effect_targets(self, context: AISkillScoreContext) -> list[Any]:
        return self.get_target_units_for_payload(context.battle, context.actor, context.payload)

    def ai_unit_delta_bonus(self, context: AISkillScoreContext, unit: Any,
                            speed: float, position: Any) -> float:
        return 0.0

    def ai_effect_score(self, context: AISkillScoreContext) -> float:
        battle, actor, payload = context.battle, context.actor, context.payload
        units = self.ai_effect_targets(context)
        before = {unit.unit_id: (unit.current_hp, unit.alive, unit.stat("speed"),
                                 unit.position, unit.total_shields()) for unit in units}
        owner_hp, owner_mana = actor.current_hp, actor.current_mana
        with context.probe_rollback():
            try:
                probe = dict(payload)
                probe.update(self.queued_payload_metadata(battle, actor, probe))
                self.execute(battle, actor, probe)
            except ActionError:
                return -1000.0
            score = self.ai_effect_base_score
            for unit in units:
                hp, alive, speed, position, shields = before[unit.unit_id]
                hostile = unit.player_id != actor.player_id
                sign = 1.0 if hostile else -1.6
                value = (hp - unit.current_hp) * 130.0 + (shields - unit.total_shields()) * 8.0
                if alive and not unit.alive:
                    value += 115.0
                value += self.ai_unit_delta_bonus(context, unit, speed, position)
                score += sign * value
            score += max(0.0, actor.current_mana - owner_mana) * 24.0
            score -= max(0.0, owner_hp - actor.current_hp) * 150.0
            return score
