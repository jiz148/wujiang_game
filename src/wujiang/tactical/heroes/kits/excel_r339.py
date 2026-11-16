"""Source-row assembly for the soft armor dragon."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.soft_armor_dragon import (
    EarthDragonAssaultSkill, SoftArmorDamageTrait, SoftArmorEntangleSkill,
    SoftArmorLevelTrait,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r339",
        skills={
            "缠绕": SoftArmorEntangleSkill,
            "地龙突袭": EarthDragonAssaultSkill,
        },
        traits={
            "每次受到伤害，在回合结束前守+1": SoftArmorDamageTrait,
            "此单位在场上时，可以当作等级2到5使用": SoftArmorLevelTrait,
        },
    ))
