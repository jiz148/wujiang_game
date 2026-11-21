"""R363 exact source-fragment assembly."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.vampire_prince import (
    VampireBasicPierceTrait, VampireCurseThresholdTrait, VampireCurseTrait,
    VampireNestRecoveryTrait, VampireNestSkill, VampirePointTrait,
    VampireReturnSkill, VampireShadowSkill,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r363",
        skill_fragments={
            "吸血鬼巢穴（3轮一次；在原地放置一个“吸血鬼巢穴”：守5；可被此单位乘骑；此单位在“吸血鬼巢穴”上自然回复；场上己方最多有一个“吸血鬼巢穴”）": VampireNestSkill,
            "回巢（随时使用；一回合一次；瞬移回此单位召唤的吸血鬼巢穴上）": VampireReturnSkill,
            "吸血鬼之影（一回合一次；4*4：仅对被此单位第二个特性影响的单位有效果；被击中后血*1/2）": VampireShadowSkill,
        },
        traits={
            "普攻破魔且无法被回避": VampireBasicPierceTrait,
            "被此单位普攻击中的单位在每个己方回合结束时血-1/4，持续2轮，效果不叠加": VampireCurseTrait,
            "每次普攻在此单为上放置一个魔力点": VampirePointTrait,
            "在此单位上有3个魔力点后第二个效果没有回合限制并且可叠加": VampireCurseThresholdTrait,
        },
        extra_trait_factories=(VampireNestRecoveryTrait,),
    ))
