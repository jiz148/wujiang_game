"""Skill-owned AI impact plans for shared positive-effect score families."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from wujiang.tactical.heroes.ai_policy import AISkillScoreContext


@dataclass(frozen=True, slots=True)
class PositiveEffectImpactRule:
    """Keep a skill's effect-family choice with the skill object."""

    score_method: Literal[
        "r12_effect_score", "r13_effect_score", "r17_effect_score",
        "r18_effect_score", "r19_effect_score", "sun_slash_score",
        "snow_avalanche_score",
    ]
    phase: Literal["before_targets", "after_targets"] = "after_targets"

    def has_impact(self, context: AISkillScoreContext, skill: object) -> bool:
        return getattr(context, self.score_method)(skill) > 0


R12_IMPACT = PositiveEffectImpactRule("r12_effect_score", "before_targets")
R13_IMPACT = PositiveEffectImpactRule("r13_effect_score")
R17_IMPACT = PositiveEffectImpactRule("r17_effect_score")
R18_IMPACT = PositiveEffectImpactRule("r18_effect_score")
R19_IMPACT = PositiveEffectImpactRule("r19_effect_score")
SUN_SLASH_IMPACT = PositiveEffectImpactRule("sun_slash_score")
SNOW_AVALANCHE_IMPACT = PositiveEffectImpactRule("snow_avalanche_score")
