"""Source-preserving object assembly for excel_r257 / 巨盾火枪手."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.giant_shield import (
    GiantShieldDrawBladeSkill, GiantShieldFlatShotSkill, GiantShieldGunAreaSkill,
    GiantShieldGunTrait, GiantShieldNeighborTrait, GiantShieldRaiseSkill,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r257",
        skills={
            "扫射": GiantShieldGunAreaSkill,
            "平射": GiantShieldFlatShotSkill,
            "排射": lambda: GiantShieldGunAreaSkill(volley=True),
            "举盾": GiantShieldRaiseSkill,
            "拔刀": GiantShieldDrawBladeSkill,
        },
        traits={
            "所有伤害破魔，守-1，到1:若普通攻击打破对方保护类技能，对方单位还将守-1，到1，持续4轮": GiantShieldGunTrait,
            "周围有名字里带有“巨盾”的单位时所有伤害减免效果+1": GiantShieldNeighborTrait,
        },
    ))
