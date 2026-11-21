"""R365 exact Shadow God king source-fragment assembly."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.shadow_god_king import (
    ShadowFriendlyLossTrait, ShadowFullHealthSurvivalTrait, ShadowGuardSkill,
    ShadowRaidSkill, ShadowSummonedNoHealTrait,
)
from wujiang.tactical.heroes.shadow_gods import ShadowApocalypseSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r365",
        skill_fragments={
            "影的加护（随时使用；仅能在受到伤害时使用；一回合不费魔一次；那次伤害-1）": ShadowGuardSkill,
            "影神力。影袭（一回合一次；直线移动4格，然后对一个方向攻击3次；每次攻击被被动技能挡住，或者被格挡，都在被攻击单位周围召唤一个分身）": ShadowRaidSkill,
            "默示录（一回合一次；血-1/4，召唤一个等级比此单位低的被破坏的名字里带有“影神”的单位，召唤的单位当回合可以行动）": ShadowApocalypseSkill,
        },
        traits={
            "周围（包括此单位）9*9因为己方效果的扣血全部变为加血": ShadowFriendlyLossTrait,
            "在满血时受到致命伤害时，不会被破坏，而是剩余1/4血留在场上": ShadowFullHealthSurvivalTrait,
            "被召唤后2轮对方所有单位不能回复": ShadowSummonedNoHealTrait,
        },
    ))
