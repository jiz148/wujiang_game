"""Source-preserving object assembly for excel_r143 / 托尔."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.thor import (
    ThorDestroyLightningSkill,
    ThorHeavyHammerSkill,
    ThorRageImpactSkill,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r143",
        skills={
            "重锤": ThorHeavyHammerSkill,
            "暴怒冲击": ThorRageImpactSkill,
            "毁灭电击": ThorDestroyLightningSkill,
        },
    ))
