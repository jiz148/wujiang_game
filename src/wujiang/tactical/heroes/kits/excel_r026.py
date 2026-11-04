"""Source-preserving object assembly for excel_r026 / 最后的守护者."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.foundation import GuardianFinaleSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r026",
        skills={
            "终结": GuardianFinaleSkill,
        },
    ))
