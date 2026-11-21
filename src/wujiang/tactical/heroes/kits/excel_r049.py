"""R049 exact source-fragment assembly."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.silent_swordsman import (
    DamageAttemptManaPoints, OriginBurstSkill, SacrificeSwordSkill,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r049",
        skill_fragments={
            "祭纳之剑（一回合2次；四格，伤害分别为5，4，3，2）": SacrificeSwordSkill,
            "起源爆发（一回合一次；消耗所有魔力点使用依照以下顺序来消耗魔力点，若每一步有剩余魔力点则剩余魔力点应用到下一步：1。每点剩余魔力点回复1点魔；2。每点剩余魔力点回复1/4血；3。对n*n造成一次伤害，n为魔力点的数量）": OriginBurstSkill,
        },
        traits={
            "每次此单位对对方单位的位置试图造成伤害时往此单位上放置相同数量的魔力点": DamageAttemptManaPoints,
        },
    ))
