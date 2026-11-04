"""Source-preserving object assembly for excel_r263 / 冰帝。浮鹤."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.ice_emperor import (
    AbsoluteZeroSkill,
    FloatingCraneCitySkill,
    IceEmperorPierceGuardTrait,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r263",
        skills={
            "飞越": lambda: r.DashMoveSkill("fly_leap", "飞越", "费1魔、每本人轮一次，直线飞行恰好三格。", max_distance=3, exact_distance=3, mana_cost=1, max_uses_per_turn=1, straight_only=True, ignore_units=True),
            "绝对零度": AbsoluteZeroSkill,
            "浮鹤城": FloatingCraneCitySkill,
        },
        traits={
            "破魔的伤害对此单位-3，到1": IceEmperorPierceGuardTrait,
        },
    ))
