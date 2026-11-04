"""Source-preserving object assembly for excel_r036 / 制裁者."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.sanctuary import (
    PunisherHealSkill,
    SanctuaryBanishSkill,
    SanctuaryJudgmentSkill,
    SkySanctuaryAuraTrait,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r036",
        skills={
            "治疗": PunisherHealSkill,
            "圣殿放逐": SanctuaryBanishSkill,
            "制裁": SanctuaryJudgmentSkill,
        },
        traits={
            "周围11*11天气变为“天空圣域”": SkySanctuaryAuraTrait,
        },
    ))
