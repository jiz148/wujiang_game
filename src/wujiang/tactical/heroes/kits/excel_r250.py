"""Source-preserving object assembly for excel_r250 / 古代机械兽."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.ancient_mecha import (
    AncientBeastTheftTrait, AncientFusionSkill, AncientJawSkill,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r250",
        skills={
            "溶解之颌": AncientJawSkill,
            "古代融合": AncientFusionSkill,
        },
        traits={
            "此单位破坏的单位失去特性": AncientBeastTheftTrait,
        },
    ))
