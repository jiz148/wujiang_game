"""Source-preserving object assembly for excel_r302 / 机甲忍者。真红."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.true_red import (
    TrueRedAIPolicy,
    TrueRedFreeDashSkill,
    TrueRedFreeLeapSkill,
    TrueRedMovementTrait,
)
from wujiang.tactical.heroes.mechanics.zero_pass_through import ZeroDashSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r302",
        ai_policy=TrueRedAIPolicy(),
        skills={
            "冲刺": ZeroDashSkill,
        },
        traits={
            "每回合可不费魔使用一次位移近技能": TrueRedMovementTrait,
        },
        extra_skill_factories=(TrueRedFreeLeapSkill, TrueRedFreeDashSkill,),
        attributes={'stat_minimums': {'attack': 0.0}},
    ))
