"""Source-preserving object assembly for excel_r255 / 巨盾长矛兵."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.giant_shield import (
    GiantShieldBlockTrait, GiantShieldNeighborTrait, GiantShieldRaiseSkill,
    GiantShieldWallSkill, GiantShieldMovePolicy,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r255",
        ai_policy=GiantShieldMovePolicy(),
        skills={
            "举盾": GiantShieldRaiseSkill,
            "盾墙[0.5": GiantShieldWallSkill,
        },
        traits={
            "每次受到伤害有1/3几率完全格档": GiantShieldBlockTrait,
            "周围有名字里带有“巨盾”的单位时所有伤害减免效果+1": GiantShieldNeighborTrait,
        },
    ))
