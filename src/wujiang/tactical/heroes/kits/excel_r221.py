"""Source-preserving object assembly for excel_r221 / 电子龙."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.electronic_teleport import (
    ElectronicDragonTeleportTrait,
    SatelliteCannonSkill,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r221",
        skills={
            "离子盾": r.IonShieldSkill,
            "卫星加农炮": SatelliteCannonSkill,
        },
        traits={
            "每个对方或己方回合结束时可以选择瞬移到任意一个己方单位周围": ElectronicDragonTeleportTrait,
        },
    ))
