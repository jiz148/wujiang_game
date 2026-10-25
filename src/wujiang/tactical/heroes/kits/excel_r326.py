"""Source-preserving object assembly for excel_r326 / 蝴蝶刺客。弗伦萨."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.vain_shadow import (
    FlorenzaAttackFollowupTrait,
    VainGiantShadowSkill,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r326",
        skills={
            "虚荣巨影": VainGiantShadowSkill,
        },
        traits={
            "普攻带有以下破魔效果：吸魔，下回合结束前攻守速-1，到1": FlorenzaAttackFollowupTrait,
        },
    ))
