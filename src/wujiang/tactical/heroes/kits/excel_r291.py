"""Source-preserving object assembly for excel_r291 / 机甲忍者。赤."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.iron_chain_red import (
    IronChainPathSkill,
    RedDrainManaSkill,
    RedRandomPierceTrait,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r291",
        skills={
            "铁锁路径": IronChainPathSkill,
            "吸魔": RedDrainManaSkill,
        },
        traits={
            "攻击，技能有1/2几率破魔": RedRandomPierceTrait,
        },
    ))
