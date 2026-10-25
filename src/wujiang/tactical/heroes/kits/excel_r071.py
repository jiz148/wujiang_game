"""Source-preserving object assembly for excel_r071 / 雪巨人."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.avalanche import BigAvalancheSkill, SnowAvalancheSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r071",
        skills={
            "雪崩": SnowAvalancheSkill,
            "大雪崩": BigAvalancheSkill,
        },
    ))
