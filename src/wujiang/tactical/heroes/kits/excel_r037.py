"""Source-preserving object assembly for excel_r037 / 第七神龙。阿尔佐利亚."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.area_guard import MultiCellAreaDamageGuardTrait
from wujiang.tactical.heroes.mechanics.nuclear_gravity import (
    GravityFieldSkill,
    NuclearMutationSkill,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r037",
        skills={
            "核变": NuclearMutationSkill,
            "重力场": GravityFieldSkill,
        },
        traits={
            "不同时受到范围一格以上的伤害": MultiCellAreaDamageGuardTrait,
        },
    ))
