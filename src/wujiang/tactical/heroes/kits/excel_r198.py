"""Source-preserving object assembly for excel_r198 / 三轮车人."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.cycle_rider import ExtraMoveAfterAttackTrait, SummonBicycleSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r198",
        skills={
            "二轮车": SummonBicycleSkill,
        },
        traits={
            "每回合攻击后移动次数+1": ExtraMoveAfterAttackTrait,
        },
    ))
