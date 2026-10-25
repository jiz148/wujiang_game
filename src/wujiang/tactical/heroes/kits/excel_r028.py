"""Source-preserving object assembly for excel_r028 / 飞王."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.iaido import (
    BigShensuSkill,
    FeiWangSpeedOnHeroKillTrait,
    LargePiercePlusSkill,
    PierceImmunityTrait,
)
from wujiang.tactical.heroes.mechanics.inner_dimension import (
    InnerDimensionSwordSkill,
    KingsInsightSkill,
)
from wujiang.tactical.heroes.mechanics.fei_wang_gale import GaleSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r028",
        skills={
            "神速": BigShensuSkill,
            "狂风": GaleSkill,
            "穿刺": LargePiercePlusSkill,
            "里次元大剑": InnerDimensionSwordSkill,
            "王者的看破": KingsInsightSkill,
        },
        traits={
            "不受到破魔效果影响": PierceImmunityTrait,
            "每破坏一个武将速+1": FeiWangSpeedOnHeroKillTrait,
        },
    ))
