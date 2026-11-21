"""R077 exact source-fragment assembly."""

from wujiang.tactical.heroes.boxing_kangaroo import (
    BeastFormSkill, EarthFissureSkill, KangarooLeapSkill, NextDamageDodgeSkill,
    SecondAttackPierceTrait,
)
from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r077",
        skill_fragments={
            "飞跃(大）（6格）": KangarooLeapSkill,
            "￥地裂（4格；伤=血*1/2）": EarthFissureSkill,
            "兽化（一回合一次；开关技能，仅可在回合开始时使用；攻+2，范-1，守-1）": BeastFormSkill,
            "闪避（一回合一次；被动技能；无效下一次伤害）": NextDamageDodgeSkill,
        },
        traits={"第二次攻击破魔": SecondAttackPierceTrait},
    ))
