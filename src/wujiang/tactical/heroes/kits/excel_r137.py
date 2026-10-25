"""Source-preserving object assembly for excel_r137 / 不死小子."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.jirobo import DevourSkill, UndyingQuarterTrait


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r137",
        skills={
            "吞噬": DevourSkill,
        },
        traits={
            "此单位半血以上受到致命伤害时，可以剩1/4的血留在场上": UndyingQuarterTrait,
        },
    ))
