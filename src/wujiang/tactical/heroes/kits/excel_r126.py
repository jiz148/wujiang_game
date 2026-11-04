"""Source-preserving object assembly for excel_r126 / 太阳神."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.sphinx_solar import (
    SolarFlameSkill,
    SolarJudgmentSkill,
    SphinxCannonSkill,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r126",
        skills={
            "爆炎": SolarFlameSkill,
            "斯芬克斯炮": SphinxCannonSkill,
            "审判": SolarJudgmentSkill,
        },
    ))
