"""Source-preserving object assembly for excel_r251 / 古代机械炮台."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.ancient_mecha import (
    AncientFusionSkill, AncientRustSkill, AncientTurretAreaTrait,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r251",
        skills={
            "生锈的气味": AncientRustSkill,
            "古代融合": AncientFusionSkill,
        },
        traits={
            "攻击4*4": AncientTurretAreaTrait,
        },
    ))
