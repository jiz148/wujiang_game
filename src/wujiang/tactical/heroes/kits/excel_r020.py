"""Source-preserving object assembly for excel_r020 / 妖精王奥尔贝隆."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.heaven_lock import HeavenLockSkill
from wujiang.tactical.heroes.mechanics.world_seed import (
    JudgmentStoneSkill,
    WorldSeedSkill,
    WorldSeedTrait,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r020",
        skills={
            "审判之石": JudgmentStoneSkill,
            "世界之种": WorldSeedSkill,
            "天锁": HeavenLockSkill,
        },
        extra_trait_factories=(WorldSeedTrait,),
    ))
