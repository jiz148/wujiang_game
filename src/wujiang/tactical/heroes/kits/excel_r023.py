"""Source-preserving object assembly for excel_r023 / 芙蕾."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.frey import (
    FreyAttackPolicy,
    FreyDamageCapTrait,
    FreyGodStabSkill,
    FreyLionSpearSkill,
    FreyQuickFlashSkill,
    FreySkillPierceTrait,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r023",
        ai_policy=FreyAttackPolicy(),
        skills={
            "快闪": FreyQuickFlashSkill,
            "神刺": FreyGodStabSkill,
            "狮子神枪": FreyLionSpearSkill,
        },
        traits={
            "所有技能破魔": FreySkillPierceTrait,
            "每次受到伤害最多-1/4血": FreyDamageCapTrait,
        },
    ))
