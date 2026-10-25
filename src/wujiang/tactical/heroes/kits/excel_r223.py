"""Source-preserving object assembly for excel_r223 / 装甲电子翼."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.electronic_fusion import ElectronicFusionSkill
from wujiang.tactical.heroes.mechanics.electronic_teleport import ElectronicWingMovementTrait


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r223",
        skills={
            "离子盾": r.IonShieldSkill,
            "电子融合": ElectronicFusionSkill,
        },
        traits={
            "每回不限制移动次数": ElectronicWingMovementTrait,
        },
    ))
