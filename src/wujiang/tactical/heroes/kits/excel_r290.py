"""Source-preserving object assembly for excel_r290 / 雀皇."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.messenger_crow import (
    CrowEmptyAttackTrait, CrowSwapSkill, GreatCrowFlockSkill, MessengerCrowSkill,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r290",
        skills={
            "报信乌鸦": MessengerCrowSkill,
            "牵引式传送": CrowSwapSkill,
            "超大群乌鸦": GreatCrowFlockSkill,
        },
        traits={
            "攻击没有单位的区域时在那个区域召唤“报信乌鸦”": CrowEmptyAttackTrait,
        },
    ))
