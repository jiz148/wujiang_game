"""Source-preserving object assembly for excel_r196 / 剑斗兽。斯巴达克斯."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.spartacus import (
    EarthShatterSkill,
    SpartacusMovedByAllyTrait,
    SpartacusSkillSwapTrait,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r196",
        skills={
            "天崩地裂": EarthShatterSkill,
        },
        traits={
            "此单位被技能影响后与场上任意一个己方交换位置": SpartacusSkillSwapTrait,
            "此单位因其他己方单位的效果改变位置回合和其下回合攻击2次，并且重置大招，魔+1": SpartacusMovedByAllyTrait,
        },
    ))
