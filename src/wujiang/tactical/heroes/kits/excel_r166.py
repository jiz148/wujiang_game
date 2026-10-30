"""Source-preserving object assembly for excel_r166 / E。电击人."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.electric_wind import AutoElectricWindTrait, ElectricWindAIPolicy, ElectricWindSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r166",
        ai_policy=ElectricWindAIPolicy(),
        skills={
            "电风": ElectricWindSkill,
        },
        traits={
            "每个己方回合开始时对周围5*5自动使用“电风”": AutoElectricWindTrait,
        },
    ))
