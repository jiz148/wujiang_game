"""Source-preserving object assembly for excel_r254 / 巨盾弩兵."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.giant_shield import (
    GiantShieldAllPierceTrait, GiantShieldFlatShotSkill, GiantShieldLobSkill,
    GiantShieldMovePolicy, GiantShieldNeighborTrait, GiantShieldRaiseSkill,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r254",
        ai_policy=GiantShieldMovePolicy(),
        skills={
            "平射": GiantShieldFlatShotSkill,
            "抛射": GiantShieldLobSkill,
            "举盾": GiantShieldRaiseSkill,
        },
        traits={
            "所有伤害破魔": GiantShieldAllPierceTrait,
            "周围有名字里带有“巨盾”的单位时所有伤害减免效果+1": GiantShieldNeighborTrait,
        },
    ))
