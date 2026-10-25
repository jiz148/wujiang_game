"""Source-preserving object assembly for excel_r059 / 年兽."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.nian import (
    NianDragonDanceSkill,
    NianJadeFlashSkill,
    NianLargeDragonBreathSkill,
    NianRoarSkill,
    NianSpiritPressureSkill,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r059",
        skills={
            "龙息": NianLargeDragonBreathSkill,
            "龙舞": NianDragonDanceSkill,
            "灵压": NianSpiritPressureSkill,
            "怒吼": NianRoarSkill,
            "碧玉闪光": NianJadeFlashSkill,
        },
    ))
