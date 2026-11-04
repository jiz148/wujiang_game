"""Source-preserving object assembly for excel_r337 / 湿地之主。缇娜."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.ai_policy import CounterReactionAIPolicy, PaidReactionAIPolicy
from wujiang.tactical.heroes.ai_score_policies import WetlandHealPolicy
from wujiang.tactical.heroes.mechanics.racial_weather import WeatherUltimateSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r337",
        ai_policy=WetlandHealPolicy(
            PaidReactionAIPolicy({"light_wall"}, multi_target=True),
            CounterReactionAIPolicy(with_followup=True)),
        skills={
            "回复": r.HealSkill,
            "湿地草原": lambda: WeatherUltimateSkill("wetland_grassland", "湿地草原", marker="湿"),
        },
    ))
