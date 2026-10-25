"""Source-preserving object assembly for excel_r252 / 古代机械巨人."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.red_lotus import RedLotusSkillDamageImmunityTrait
from wujiang.tactical.heroes.mechanics.ancient_mecha import (
    AncientArmSkill, AncientFusionSkill, AncientVigorSkill,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r252",
        skills={
            "机械臂": AncientArmSkill,
            "活力锈迹": AncientVigorSkill,
            "古代融合": AncientFusionSkill,
        },
        traits={
            "不受到技能伤害": RedLotusSkillDamageImmunityTrait,
        },
    ))
