"""Source-preserving object assembly for excel_r305 / 电子龙。新星."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.electronic_guard import (
    ElectromagneticFieldSkill,
    NovaManaReactionTrait,
    QuantumSeparationSkill,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r305",
        skills={
            "离子盾": r.IonShieldSkill,
            "量子分离": QuantumSeparationSkill,
            "电磁场": ElectromagneticFieldSkill,
        },
        traits={
            "此单位周围7*7内对方单位使用魔时，此单位直到下个回合结束前攻，守+1": NovaManaReactionTrait,
        },
    ))
