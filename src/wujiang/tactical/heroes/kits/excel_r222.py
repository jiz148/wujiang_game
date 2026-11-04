"""Source-preserving object assembly for excel_r222 / 电子修理工厂."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.electronic_fusion import ElectronicRepairSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r222",
        skills={
            "电子维修": ElectronicRepairSkill,
            "离子盾": r.IonShieldSkill,
        },
    ))
