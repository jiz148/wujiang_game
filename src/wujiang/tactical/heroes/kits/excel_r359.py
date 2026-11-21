"""R359 exact source-fragment assembly."""

from wujiang.tactical.heroes.dios_floating_cannon import DiosCannonTrackerTrait, DiosFloatingCannonsSkill
from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r359",
        skill_fragments={
            "￥暗。浮游炮*4（攻4守1速2范3；每回合可以使用一次吸魔，吸的魔加到召唤者上；被破坏后在下回合开始时召唤；周围7*7的单位使用被动技能时费魔+1，不叠加；被破坏后，破坏此单位的单位血*1/2）": DiosFloatingCannonsSkill,
        },
        extra_trait_factories=(DiosCannonTrackerTrait,),
    ))
