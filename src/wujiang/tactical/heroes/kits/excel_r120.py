"""Source-preserving object assembly for excel_r120 / 盲人剑士."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.ai_score_policies import BlindMovePolicy
from wujiang.tactical.heroes.mechanics.gladiator_cooperation import BlindManaMovementTrait
from wujiang.tactical.heroes.mechanics.thor import (
    BlindCircleAttackTrait,
    BlindEvasionAttackResetTrait,
)
from wujiang.tactical.heroes.mechanics.foundation import BasicAttackPierceTrait


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r120",
        ai_policy=BlindMovePolicy(),
        traits={
            "一回合内不限次数移动": BlindManaMovementTrait,
            "普攻破魔": BasicAttackPierceTrait,
            "攻击一周": BlindCircleAttackTrait,
            "普攻被回避后攻击次数重置": BlindEvasionAttackResetTrait,
        },
    ))
