"""Reusable action-outcome probe for body-resource and doom skills."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext


class ResourceEffectScoring:
    stat_weights = {"attack": 18.0, "defense": 17.0, "speed": 10.0,
                    "attack_range": 10.0, "mana": 14.0}

    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.owned_effect_score(self)

    def ai_effect_preflight(self, context: AISkillScoreContext) -> bool:
        return True

    def ai_probe_prepare(self, context: AISkillScoreContext, probe: dict[str, Any]) -> None:
        pass

    def ai_probe_finish(self, context: AISkillScoreContext) -> None:
        pass

    def ai_unit_stat_bonus(self, context: AISkillScoreContext, unit: Any,
                           stats: dict[str, float], mana: float, sign: float) -> float:
        return 0.0

    def ai_after_effect_score(self, context: AISkillScoreContext, score: float,
                              new_doom_damage: float) -> float | None:
        return score

    def ai_effect_score(self, context: AISkillScoreContext) -> float:
        battle, actor, payload = context.battle, context.actor, context.payload
        if not self.ai_effect_preflight(context):
            return -1000.0
        units = list(battle.all_units())
        before = {unit.unit_id: (unit.current_hp, unit.alive, unit.total_shields(),
                                 unit.has_status("末日光"),
                                 {stat: unit.stat(stat) for stat in self.stat_weights},
                                 unit.current_mana) for unit in units}
        with context.probe_rollback():
            try:
                probe = dict(payload)
                probe.update(self.queued_payload_metadata(battle, actor, probe))
                self.ai_probe_prepare(context, probe)
                self.execute(battle, actor, probe)
                self.ai_probe_finish(context)
            except (ActionError, ValueError, KeyError):
                return -1000.0
            if not actor.alive:
                return -1000.0
            score = -8.0
            new_doom_damage = 0.0
            for unit in units:
                hp, alive, shields, had_doom, stats, mana = before[unit.unit_id]
                friendly = unit.player_id == actor.player_id
                sign = 1.0 if friendly else -1.0
                score += (unit.current_hp - hp) * sign * (135.0 if friendly else 110.0)
                if alive and not unit.alive:
                    score += -240.0 if friendly else 160.0
                score += (unit.total_shields() - shields) * sign * 8.0
                score += self.ai_unit_stat_bonus(context, unit, stats, mana, sign)
                if unit.alive and not had_doom and unit.has_status("末日光"):
                    expected_loss = unit.current_hp * (1.0 - 0.5 ** 4)
                    score += expected_loss * (-120.0 if friendly else 85.0)
                    if not friendly:
                        new_doom_damage += expected_loss
            score = self.ai_after_effect_score(context, score, new_doom_damage)
            if score is None:
                return -1000.0
            if self.max_uses_per_battle == 1 and score < context.profile.once_per_battle_threshold:
                score -= 24.0
            return score
