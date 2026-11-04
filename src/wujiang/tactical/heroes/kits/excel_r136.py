"""Source-preserving object assembly for excel_r136 / 一刀斩。胧月."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.ai_score_policies import OboroSplitPolicy
from wujiang.tactical.heroes.mechanics.holy_sola import MeditateManaSkill
from wujiang.tactical.heroes.mechanics.true_blade_bird import TrueBladeAirSlashSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r136",
        skills={
            "真刀": TrueBladeAirSlashSkill,
            "凝神": MeditateManaSkill,
        },
        ai_policy=OboroSplitPolicy(),
    ))
