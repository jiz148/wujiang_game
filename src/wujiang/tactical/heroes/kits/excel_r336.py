"""Source-row assembly for Earth Fissure Rock Bamboo."""

from wujiang.tactical.heroes.earth_fissure_bamboo import (
    BambooSurvivalTrait, BambooWoundHealingTrait, RockBambooSkill, SoulBambooSkill,
)
from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r336",
        skills={"岩之竹光": RockBambooSkill, "魂之竹光": SoulBambooSkill},
        traits={
            "满血时受到来自对方的致命伤害时，此单位会以1/4的血留在场上": BambooSurvivalTrait,
            "此单位受到来自对方的伤害时，可以选择场上9*9内一个己方单位，那个单位回复受到的伤害量": BambooWoundHealingTrait,
        },
    ))
