"""R068 exact source-fragment assembly."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.wolf_king import (
    SummonWolfSkill, WolfKingAIPolicy, WolfKingAttackCoin, WolfRiderGuardTrait,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r068",
        skill_fragments={
            "召狼（一回合一次；攻3守3范1速5；魔免；被此单位击中的单位在每个己方回合结束时血-1/8；可被“狼王”乘骑）": SummonWolfSkill,
        },
        traits={
            "攻击有1/2几率伤害+1": WolfKingAttackCoin,
            "此单位乘骑的“召狼”有1/2几率使受到的伤害无效": WolfRiderGuardTrait,
        },
        ai_policy=WolfKingAIPolicy(),
    ))
