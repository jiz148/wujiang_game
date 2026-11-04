"""Source-preserving object assembly for excel_r158 / 魔战士."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.hell_slash import HellSlashSkill
from wujiang.tactical.heroes.mechanics.martial_seal import MartialGodSealSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r158",
        skills={
            "魔界武神之印": MartialGodSealSkill,
            "地狱之斩": HellSlashSkill,
        },
    ))
