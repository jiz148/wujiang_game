"""Source-preserving object assembly for excel_r304 / 妖仙。柳."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.gladiator_core import (
    WillowAfterDeathWallTrait,
    WillowExchangeSkill,
    WillowGhostLightWallSkill,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r304",
        skills={
            "光墙": WillowGhostLightWallSkill,
            "交换": WillowExchangeSkill,
        },
        traits={
            "此单位在被破坏的状态时每个对方回合可以对己方使用2次“光墙”": WillowAfterDeathWallTrait,
        },
    ))
