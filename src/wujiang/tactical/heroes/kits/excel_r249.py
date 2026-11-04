"""Source-preserving object assembly for excel_r249 / 古代的工兵."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.ancient_mecha import (
    AncientEngineerReactionLockTrait, AncientFusionSkill, AncientRepairSkill,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r249",
        skills={
            "万古的修整": AncientRepairSkill,
            "古代融合": AncientFusionSkill,
        },
        traits={
            "此单位攻击和技能时，在伤害计算阶段结束前，除了被攻击和技能的对象以外的单位不能使用技能": AncientEngineerReactionLockTrait,
        },
    ))
