"""Source-preserving object assembly for excel_r066 / 黑猫."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.black_cat import BlackCatFormSkill, BlackCatPawSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r066",
        skills={
            "猫手": BlackCatPawSkill,
            "化猫": BlackCatFormSkill,
        },
    ))
