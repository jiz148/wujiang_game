"""R360 exact source-fragment assembly."""

from wujiang.tactical.heroes.birdman_agad import (
    AgadAngelIdentityTrait, AgadAssassinateSkill, AgadDropSkill, AgadUltimateResetTrait,
)
from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r360",
        skill_fragments={
            "打落（一回合一次；给予此单位攻击一半的伤害，如果对方拥有“飞行”特性，则取消“飞行”特性，然后将那个单位血*1/2，速-3，到1，3轮）": AgadDropSkill,
            "行刺（4轮一次；2轮内隐身，在隐身过程中的第一次普攻或技能破魔且无法被回避）": AgadAssassinateSkill,
        },
        traits={
            "破坏对方武将后大招重置": AgadUltimateResetTrait,
            "此单位也被视为“天使”武将": AgadAngelIdentityTrait,
        },
    ))
