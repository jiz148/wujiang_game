"""Source-preserving object assembly for excel_r224 / 电子镭射龙."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.ai_score_policies import DemonLineWindowPolicy
from wujiang.tactical.heroes.mechanics.electronic_barrier import (
    ElectronicDragonNameTrait,
    ElectronicLaserDamageBonusTrait,
    ElectronicLaserSkill,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r224",
        ai_policy=DemonLineWindowPolicy(),
        skills={
            "离子盾": r.IonShieldSkill,
            "镭射": ElectronicLaserSkill,
        },
        traits={
            "此单位攻击或使用带有伤害的技能锁定攻比此单位低单位时在伤害计算时伤害+1": ElectronicLaserDamageBonusTrait,
            "此单位在场上名字被视为“电子龙”": ElectronicDragonNameTrait,
        },
    ))
