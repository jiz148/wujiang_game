"""R044 exact source-fragment assembly."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.element_hunter import RendingSkill
from wujiang.tactical.heroes.ninja_medic_sai import (
    AcupointSkill, MedicalAssassinationSkill, SeamlessHealSkill, SpiderSenseSkill,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r044",
        skill_fragments={
            "点穴（一回合一次；目标随机一项能力直到下个回合结束前+1）": AcupointSkill,
            "无缝之缝补（一回合一次；目标血全满）": SeamlessHealSkill,
            "￥撕裂": RendingSkill,
            "蜘蛛感应（一回合一次；被动技能；移动5格）": SpiderSenseSkill,
            "￥医学暗杀术（对身前2*4造成破魔伤害；之后所有受到伤害的单位血-1/4）": MedicalAssassinationSkill,
        },
    ))
