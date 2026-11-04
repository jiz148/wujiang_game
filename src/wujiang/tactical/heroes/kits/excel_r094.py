"""Source-preserving object assembly for excel_r094 / 杂音."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.noise_interference import InterferenceSkill, NoiseWaveSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r094",
        skills={
            "干扰": InterferenceSkill,
            "乱音电波": NoiseWaveSkill,
        },
    ))
