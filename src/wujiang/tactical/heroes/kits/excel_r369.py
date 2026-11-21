"""R369 source-fragment assembly."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.shadow_gods import ShadowApocalypseSkill
from wujiang.tactical.heroes.shadow_god_last import (
    ShadowAttackUpSkill, ShadowChainedAttackTrait, ShadowKillRecallTrait,
    ShadowProbabilitySkill, ShadowRebirthSkill, ShadowRebirthTrait,
    ShadowSupervisionSkill, ShadowUltimateOrderSkill,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r369",
        skill_fragments={
            "超视（一回合一次；在场上有对方单位攻击时才能随时使用；那个单位在此回合内攻击变为1）": ShadowSupervisionSkill,
            "攻+1（2轮一次；可以对此单位或着己方单位使用；那个单位攻+1；2轮）": ShadowAttackUpSkill,
            "影。终极概率（一回合不费魔一次；仅在当场上有对方单位受到伤害时随时使用，那个那次伤害+1）": ShadowProbabilitySkill,
            "￥影。终极秩序（1轮内此单位以外不能使用技能）": ShadowUltimateOrderSkill,
            "影之重生（仅在此单位为被破坏的状态时使用；将场上2个己方名字里带有“影神”的武将破坏，此单位在其中一个武将的原地满状态召唤）": ShadowRebirthSkill,
            "默示录（一回合一次；血-1/4，召唤一个等级比此单位低的被破坏的名字里带有“影神”的单位，召唤的单位当回合可以行动）": ShadowApocalypseSkill,
        },
        traits={
            "此单位的攻击被连锁后，给予被攻击单位破魔伤害，伤害=此单位的攻-被攻击单位的守": ShadowChainedAttackTrait,
            "每破坏一个武将后，在周围召唤一个被破坏的名字里带有“影神”的单位，召唤的单位当回合可以行动": ShadowKillRecallTrait,
        },
        extra_trait_factories=(ShadowRebirthTrait,),
    ))
