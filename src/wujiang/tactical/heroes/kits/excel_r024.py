"""Source-preserving object assembly for excel_r024 / 不败将军。弗里德."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.fried_soldier import (
    FriedAllyAttackHealTrait, FriedAuraTrait, FriedInspireSkill, RoyalSoldierSkill,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r024",
        skills={
            "鼓舞": FriedInspireSkill,
            "皇家士兵": RoyalSoldierSkill,
        },
        traits={
            "攻击己方加血": FriedAllyAttackHealTrait,
            "周围的非武将己方单位也拥有此单位的特性和能力值加成效果": FriedAuraTrait,
        },
    ))
