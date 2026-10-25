"""Source-preserving object assembly for excel_r123 / 风魔."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.fuma import (
    FumaPursuitSkill,
    FumaShurikenSkill,
    FumaSkillManaTrait,
    FumaTrapSkill,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r123",
        skills={
            "追身": FumaPursuitSkill,
            "陷阱": FumaTrapSkill,
            "风魔手里剑": FumaShurikenSkill,
        },
        traits={
            "每次使用主动技能都有1/2几率魔+1": FumaSkillManaTrait,
        },
    ))
