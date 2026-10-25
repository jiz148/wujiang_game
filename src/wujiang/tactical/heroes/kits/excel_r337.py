"""Source-preserving object assembly for excel_r337 / 湿地之主。缇娜."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.racial_weather import WeatherUltimateSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r337",
        skills={
            "回复": r.HealSkill,
            "湿地草原": lambda: WeatherUltimateSkill("wetland_grassland", "湿地草原", marker="湿"),
        },
    ))
