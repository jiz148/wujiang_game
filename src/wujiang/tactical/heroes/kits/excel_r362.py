"""R362 exact source-fragment assembly."""

from wujiang.tactical.heroes.great_demon_yubert import (
    YubertBloodPointsTrait, YubertKillAttacksTrait, YubertPhaseSkill,
    YubertSighWallSkill, YubertSwapSkill, YubertTrueDemonSkill,
)
from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r362",
        skill_fragments={
            "移形换位（一回合一次；破魔；无法被回避；可以对周围21*21内的任意对方单位使用，和那个单位交换位置，为此单位上放置1个魔力点）": YubertSwapSkill,
            "￥真恶魔形态（仅可消耗3个魔力点使用；攻守速+2，范变为7，特性增加飞行）": YubertTrueDemonSkill,
            "叹息之壁（一回合一次；周围11*11；破魔；被击中的对方单位直到下回合结束前守-1，到1，此单位直到下回合结束前每击中一个单位守+1）": YubertSighWallSkill,
            "相位（一回合一次；移动三格，下次攻击造成以下效果：半破魔，被造成伤害的单位直到下回合结束前速-2，到1）": YubertPhaseSkill,
        },
        traits={
            "普攻吸血，普攻+1魔力点": YubertBloodPointsTrait,
            "每破坏一个单位攻击次数+1": YubertKillAttacksTrait,
        },
    ))
