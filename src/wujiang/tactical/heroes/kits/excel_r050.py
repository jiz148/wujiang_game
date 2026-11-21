"""R050 exact source-fragment assembly."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.revenge_seed import RevengeDamageSharingTrait, RevengeSeedSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r050",
        skill_fragments={
            "复仇（一回合一次；每当己方武将在对方回合被破坏时此技能伤害*2；破魔，无法被回避）": RevengeSeedSkill,
        },
        traits={
            "当受到来自其他单位的伤害时将伤害-1，同时把这个-1的伤害施加到此单位和对此单位造成伤害的单位上": RevengeDamageSharingTrait,
        },
    ))
