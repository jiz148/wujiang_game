"""R355 exact source-fragment assembly."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.koala_coach import (
    KoalaAngryTableSkill, KoalaCooperativeRollSkill,
    KoalaMountainCrushSkill, KoalaZeroManaRecoveryTrait,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r355",
        skills={"愤怒掀桌！": KoalaAngryTableSkill,
                "协力翻滚": KoalaCooperativeRollSkill, "泰山压顶": KoalaMountainCrushSkill},
        traits={"魔0受到伤害后，在回合结束时魔+1": KoalaZeroManaRecoveryTrait},
    ))
