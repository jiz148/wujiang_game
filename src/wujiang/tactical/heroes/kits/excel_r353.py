"""R353 exact source-fragment assembly."""

from wujiang.tactical.heroes.disorder_wisp import (
    BlindSkill, HauntingSkill, ParanormalTerrorSkill, PhantomChangeSkill,
    PoltergeistSkill, WispEvasionImmunityTrait, WispFreeTargetingTrait,
    WispFullHealthSurvivalTrait,
)
from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r353",
        skills={"作祟": HauntingSkill, "骚灵现象": PoltergeistSkill,
                "致盲": BlindSkill, "灵异恐吓": ParanormalTerrorSkill,
                "幻变魅影": PhantomChangeSkill},
        traits={"此单位的攻击和技能的范围为周围9*9内任意位置": WispFreeTargetingTrait,
                "使用回避后直到回合结束前魔免": WispEvasionImmunityTrait,
                "满血时受到致命伤害时会已1/4血留在场上": WispFullHealthSurvivalTrait},
    ))
