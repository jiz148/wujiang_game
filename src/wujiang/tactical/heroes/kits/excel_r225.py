"""Source-preserving object assembly for excel_r225 / 电子屏障龙."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.electronic_barrier import (
    ElectronicBarrierAuraTrait,
    ElectronicBarrierPersonalGuardTrait,
    ElectronicDragonNameTrait,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r225",
        skills={
            "导弹": r.CommonMissileSkill,
            "离子盾": r.IonShieldSkill,
            "量子盾": r.QuantumShieldSkill,
        },
        traits={
            "此单位被比此单位守低的单位攻击或使用带有伤害的技能锁定时在伤害计算时伤害-1": ElectronicBarrierPersonalGuardTrait,
            "此单位在场上名字被视为“电子龙”": ElectronicDragonNameTrait,
            "此单位周围的单位（包括此单位）受到的伤害-1，如果是“机甲”单位则-2": ElectronicBarrierAuraTrait,
        },
    ))
