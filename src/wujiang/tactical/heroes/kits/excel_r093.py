"""Source-preserving object assembly for excel_r093 / 魔神凯撒."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.kaiser import KaiserFistSkill
from wujiang.tactical.heroes.mechanics.foundation import LargePierceSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r093",
        skills={
            "穿刺": LargePierceSkill,
            "凯撒神拳": KaiserFistSkill,
        },
    ))
