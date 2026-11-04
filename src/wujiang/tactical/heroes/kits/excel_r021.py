"""Source-preserving object assembly for excel_r021 / 过时的完美剑客."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.ghost_step import GhostStepSkill
from wujiang.tactical.heroes.mechanics.iaido import (
    FocusSkill,
    IaidoChargeSkill,
    PerfectSwordsmanDefenseTrait,
    TimeStopSkill,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r021",
        skills={
            "鬼步": GhostStepSkill,
            "聚气": IaidoChargeSkill,
            "时停": TimeStopSkill,
            "定神": FocusSkill,
        },
        traits={
            "每回合将剩余的攻击数转化为那个回合防守回合的挡开攻击或技能的次数，破魔的攻击或技能对此效果无效": PerfectSwordsmanDefenseTrait,
        },
    ))
