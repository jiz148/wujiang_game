"""Source-preserving object assembly for excel_r118 / 零崎."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.zero_pass_through import (
    ZeroDashSkill,
    ZeroPassThroughManaTrait,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r118",
        skills={
            "冲刺": ZeroDashSkill,
        },
        traits={
            "穿人魔+0.5": ZeroPassThroughManaTrait,
        },
    ))
