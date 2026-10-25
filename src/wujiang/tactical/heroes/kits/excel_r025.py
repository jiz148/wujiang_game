"""Source-preserving object assembly for excel_r025 / 代行者。暮别."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.iaido import LargePiercePlusSkill, ManaDrainImmunityTrait
from wujiang.tactical.heroes.mechanics.agency import (
    AgencyBorrowedSkill, AgencyContractSkill, AgencyPassivePunishTrait,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r025",
        skills={
            "穿刺": LargePiercePlusSkill,
            "代行契约": AgencyContractSkill,
        },
        traits={
            "无法被吸魔": ManaDrainImmunityTrait,
            "使用被动技能让次单位攻击没能造成伤害的单位此回合不能再次使用被动技能": AgencyPassivePunishTrait,
        },
        extra_skill_factories=(AgencyBorrowedSkill,),
    ))
