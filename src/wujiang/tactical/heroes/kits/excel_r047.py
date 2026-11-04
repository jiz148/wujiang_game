"""Source-preserving object assembly for excel_r047 / 次郎坊."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.jirobo import JiroboAfterAttackTrait, JiroboFollowStepSkill
from wujiang.tactical.heroes.mechanics.true_blade_bird import HundredBirdBurialSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r047",
        skills={
            "百鸟葬": HundredBirdBurialSkill,
        },
        traits={
            "每次攻击后可以移动2格并且直到下回合结束前守+1": JiroboAfterAttackTrait,
        },
        extra_skill_factories=(JiroboFollowStepSkill,),
    ))
