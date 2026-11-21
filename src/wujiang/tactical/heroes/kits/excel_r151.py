"""R151 exact source-fragment assembly."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.l_spear import (
    CursedGoldSpearSkill, LAttackMobility, LDamageMana, LKillGrowth, RedSpearSkill,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r151",
        skill_fragments={
            "破魔的红枪（2轮一次；破魔；被击中的单位魔变为0；无视魔免）": RedSpearSkill,
            "￥诅咒的金枪（不能被回避；无视魔免；击中后不能回复）": CursedGoldSpearSkill,
        },
        traits={
            "每次攻击后此回合内移动次数+1": LAttackMobility,
            "每破坏一个对方武将速+1，魔+1": LKillGrowth,
            "每次造成伤害或受到伤害魔+1": LDamageMana,
        },
    ))
