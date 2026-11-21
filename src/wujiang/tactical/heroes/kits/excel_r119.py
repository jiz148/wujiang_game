"""R119 exact current-workbook source-fragment assembly."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.huangquan import (
    CorruptionSkill, HuangquanAnyTimeMoveTrait, TripleSpeedSkill,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r119",
        skill_fragments={
            "三倍速（2轮一次）": TripleSpeedSkill,
            "腐化（开关技能，一回合一次，仅可在回合开始时使用：不能用魔；攻守速+1）": CorruptionSkill,
        },
        traits={
            "此单位进攻回合可以在任意时间点移动": HuangquanAnyTimeMoveTrait,
        },
    ))
