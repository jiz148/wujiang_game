"""Source-preserving object assembly for excel_r113 / 诚实天使欧内斯特."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.ai_score_policies import ErnestSplitPolicy
from wujiang.tactical.heroes.mechanics.purify_duel import PurifyManaSkill, SacredDuelSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r113",
        skills={
            "净化": PurifyManaSkill,
            "神圣决斗": SacredDuelSkill,
        },
        ai_policy=ErnestSplitPolicy(),
    ))
