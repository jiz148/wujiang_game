"""Source-preserving object assembly for excel_r070 / 天蟹."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.heaven_punishment import HeavenPunishmentSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r070",
        skills={
            "天罚": HeavenPunishmentSkill,
        },
    ))
