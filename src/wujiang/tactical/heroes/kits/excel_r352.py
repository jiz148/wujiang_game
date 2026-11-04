"""Source-preserving object assembly for excel_r352 / 水忍."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.ai_policy import CounterReactionAIPolicy, PaidReactionAIPolicy
from wujiang.tactical.heroes.ai_score_policies import WaterNinjaAttackPolicy
from wujiang.tactical.heroes.mechanics.water_ninja import WaterNinjaCloneAfterAttackTrait, WaterNinjaShensuSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r352",
        ai_policy=WaterNinjaAttackPolicy(
            PaidReactionAIPolicy({"evasion"}, multi_target=False),
            CounterReactionAIPolicy(with_followup=True)),
        skills={"神速": WaterNinjaShensuSkill},
        traits={
            "每次攻击在在周围召唤一个分身": WaterNinjaCloneAfterAttackTrait,
        },
    ))
