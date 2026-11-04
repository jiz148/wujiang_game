"""Source-preserving object assembly for excel_r248 / 地狱的古龙。GRUFER."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.hell_dragon import (
    FlyingDragonOrbSkill,
    HellDragonBreathSkill,
    HellDragonFireSkill,
    HellDragonSacrificeTrait,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r248",
        skills={
            "龙吸": HellDragonBreathSkill,
            "地狱之火": HellDragonFireSkill,
            "飞龙宝珠": FlyingDragonOrbSkill,
        },
        traits={
            "此单位被破坏后，可以选择场上2个名字里带有“地狱”的己方武将破坏（被视为技能破坏），任意时间满状态召唤到其中一个武将原来所在的地方": HellDragonSacrificeTrait,
        },
    ))
