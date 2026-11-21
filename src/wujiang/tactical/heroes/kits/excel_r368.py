"""R368 source-fragment assembly."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.shadow_god_curse import (
    ShadowGravityWaveSkill, ShadowInterferenceSkill, ShadowSummonedMoveLimitTrait,
)
from wujiang.tactical.heroes.shadow_gods import ShadowApocalypseSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r368",
        skill_fragments={
            "￥影之干扰（直线7格；破魔；被击中的单位下个己方回合不能行动）": ShadowInterferenceSkill,
            "影神力。重力波（2轮一次；仅可对此单位或己方单位使用；那个单位此回合内可以攻击场上任何单位）": ShadowGravityWaveSkill,
            "默示录（一回合一次；血-1/4，召唤一个等级比此单位低的被破坏的名字里带有“影神”的单位，召唤的单位当回合可以行动）": ShadowApocalypseSkill,
        },
        traits={
            "被召唤后2轮，对方所有单位每回合加起来最多使用一次位移技能": ShadowSummonedMoveLimitTrait,
        },
    ))
