"""Source-preserving object assembly for excel_r206 / 反击拳手."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.boxer import BoxerBlockCounterSkill, RingTauntSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r206",
        skills={
            "擂台嘲讽": RingTauntSkill,
        },
        traits={
            "此单位可以使用格挡的同时反击": r.BlockCounterTrait,
        },
        extra_skill_factories=(BoxerBlockCounterSkill,),
    ))
