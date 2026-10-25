"""Source-preserving object assembly for excel_r029 / 红衣."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.weapon_copy import (
    InfiniteArmorSkill,
    InfiniteRobeSkill,
    InfiniteSkill,
    WeaponCopySkill,
)
from wujiang.tactical.heroes.mechanics.deadly_bow import DeadlyBowSkill
from wujiang.tactical.heroes.mechanics.inner_dimension import (
    MagicPointCapTrait,
    RedChargeSkill,
    WeaponTransferSkill,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r029",
        skills={
            "武器传送": WeaponTransferSkill,
            "蓄力": RedChargeSkill,
            "致命之弓": DeadlyBowSkill,
            "武装复制": WeaponCopySkill,
            "无限": InfiniteSkill,
            "无限铠甲": InfiniteArmorSkill,
            "无限法袍": InfiniteRobeSkill,
        },
        traits={
            "此单位最多放置5的魔力点": lambda: MagicPointCapTrait(5),
        },
        attributes={'stat_minimums': {'attack': 0.0}},
    ))
