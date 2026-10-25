"""Source-preserving object assembly for excel_r030 / 聚变人."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.fusion_rush import (
    FusionCircleAttackTrait,
    FusionDeathExplosionTrait,
    NuclearRushSkill,
)
from wujiang.tactical.heroes.mechanics.inner_dimension import MagicPointCapTrait


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r030",
        skills={
            "核冲": NuclearRushSkill,
        },
        traits={
            "攻击一周": FusionCircleAttackTrait,
            "此单位最多被放置6个魔力点": lambda: MagicPointCapTrait(6),
            "被破坏之后对周围5*5给予魔力点相同的半破魔伤害": FusionDeathExplosionTrait,
        },
    ))
