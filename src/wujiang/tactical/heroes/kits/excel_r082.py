"""R082 exact source-fragment assembly."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.overlaid_mecha_king import MagneticStormSkill, SummonSteelCannonSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r082",
        skill_fragments={
            "钢加农（攻4守7范5速1；3轮一次）": SummonSteelCannonSkill,
            "￥磁暴（无视魔免；周围两格给予4的破魔伤害；并且带有以下破魔效果：速-1；魔免无效）": MagneticStormSkill,
        },
        attributes={"footprint_width": 2, "footprint_height": 2,
                    "entry_footprint_width": 2, "entry_footprint_height": 2},
    ))
