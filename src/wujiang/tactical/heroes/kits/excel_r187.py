"""Source-preserving object assembly for excel_r187 / 集团恶魔首领."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.ai_score_policies import DemonWeatherAttackPolicy
from wujiang.tactical.heroes.mechanics.racial_weather import PandemoniumSkill, PandemoniumSpeedTrait


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r187",
        ai_policy=DemonWeatherAttackPolicy(),
        skills={
            "万魔殿": PandemoniumSkill,
        },
        traits={
            "在“万魔殿”中速+3": PandemoniumSpeedTrait,
        },
    ))
