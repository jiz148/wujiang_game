"""R367 source-fragment assembly."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.shadow_god_wind import (
    ShadowSpearSkill, ShadowSummonedAttackLimitTrait, ShadowTwoDamageTrait,
)
from wujiang.tactical.heroes.shadow_gods import ShadowApocalypseSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r367",
        skill_fragments={
            "影神力。神枪（一回合一次；可以对包括此单位的己方单位使用；那个单位下一次攻击半破魔）": ShadowSpearSkill,
            "默示录（一回合一次；血-1/4，召唤一个等级比此单位低的被破坏的名字里带有“影神”的单位，召唤的单位当回合可以行动）": ShadowApocalypseSkill,
        },
        traits={
            "每回合只会受到最多2次伤害": ShadowTwoDamageTrait,
            "被召唤后2轮，对方所有单位，每回合加起来只能攻击一次": ShadowSummonedAttackLimitTrait,
        },
    ))
