"""R042 exact source-fragment assembly."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.wing_god import (
    AngelWingsSkill, FoldWingsSkill, SaintArrowSkill, WingGodLongShotTrait,
    WingGodRecoveryTrait,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r042",
        skill_fragments={
            "圣箭【1（伤害一格，带有以下破魔效果：速-1,到1）": SaintArrowSkill,
            "￥折翼（不能飞行，魔+4）": FoldWingsSkill,
            "¥天使之翼（此单位周围5*5的己方单位物免；3轮；使用“折翼”后此技能重置）": AngelWingsSkill,
        },
        traits={
            "不攻击和使用技能的回合结束时魔+2": WingGodRecoveryTrait,
            "普攻距离为4时伤害+2": WingGodLongShotTrait,
        },
    ))
