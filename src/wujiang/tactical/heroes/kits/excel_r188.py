"""Source-preserving object assembly for excel_r188 / 光道的守护者。洁拉."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.ai_score_policies import DemonLineWindowPolicy
from wujiang.tactical.heroes.mechanics.electric_wind import SkySanctuarySkill
from wujiang.tactical.heroes.mechanics.sanctuary import VitalityBlastSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r188",
        ai_policy=DemonLineWindowPolicy(),
        skills={
            "天使的气息": SkySanctuarySkill,
            "元气爆破": VitalityBlastSkill,
        },
    ))
