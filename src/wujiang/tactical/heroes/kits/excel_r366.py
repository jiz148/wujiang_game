"""R366 exact Shadow God fire king source-fragment assembly."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.shadow_god_fire import (
    ShadowFireWhipSkill, ShadowHoundSkill, ShadowPassiveLimitTrait,
    ShadowSummonedBurnTrait,
)
from wujiang.tactical.heroes.shadow_gods import ShadowApocalypseSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r366",
        skill_fragments={
            "影恶犬（一回合一次；场上最多存在此单位召唤的一个“影恶犬”；攻4守4速4范1；普攻破魔）": ShadowHoundSkill,
            "影神力。烈火鞭（一回合一次；被击中或者对此技能使用被动技能的单位在每个己方回合结束时受到3的伤害；4轮；无法叠加）": ShadowFireWhipSkill,
            "默示录（一回合一次；血-1/4，召唤一个等级比此单位低的被破坏的名字里带有“影神”的单位，召唤的单位当回合可以行动）": ShadowApocalypseSkill,
        },
        traits={
            "被此单位攻击的对象当回合只能使用一次被动技能": ShadowPassiveLimitTrait,
            "被召唤后2轮，对方所有单位每个己方回合结束时血-1/4": ShadowSummonedBurnTrait,
        },
    ))
