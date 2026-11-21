"""R045 exact source-fragment assembly."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.nameless_rock_soldier import (
    RockAttackPointsTrait, RockFullHealthRescueTrait, RockGrowSkill,
    RockHealPointTaxTrait, RockStoredAttackTrait,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r045",
        skill_fragments={
            "巨大化（魔力点-2；体型扩大一圈；速+1，攻+1，守+1；一回合最多使用一次）": RockGrowSkill,
        },
        traits={
            "受到攻击魔力点+2": RockAttackPointsTrait,
            "不攻击的回合结束时直到下次攻击攻+1到4": RockStoredAttackTrait,
            "满血受到致命伤害时剩1/2的血": RockFullHealthRescueTrait,
            "每次回血时魔力点-1": RockHealPointTaxTrait,
        },
    ))
