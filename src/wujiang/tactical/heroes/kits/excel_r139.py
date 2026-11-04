"""Source-preserving object assembly for excel_r139 / 丰收之神。索拉."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.holy_sola import (
    HolyWallSkill,
    IlluminationLightSkill,
    SolaHarvestAuraTrait,
    SolaHarvestAIPolicy,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r139",
        ai_policy=SolaHarvestAIPolicy(),
        skills={
            "圣墙": HolyWallSkill,
            "照明之光": IlluminationLightSkill,
        },
        traits={
            "周围11*11内己方单位每回合血+1/4魔+1": SolaHarvestAuraTrait,
        },
    ))
