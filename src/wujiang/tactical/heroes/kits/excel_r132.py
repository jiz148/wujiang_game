"""R132 exact source-fragment assembly."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.twin_devil import BodySwapSkill, DevilAuraSkill, FriendlyDamageImmunity


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r132",
        skill_fragments={
            "光环（3轮一次；可在3轮内使自己的和一个已破坏单位的技能互换）": DevilAuraSkill,
            "换身（2轮一次；被击中的单位将控制权与此单位互换；破魔；无法被回避；无视魔免）": BodySwapSkill,
        },
        traits={"不受到己方伤害": FriendlyDamageImmunity},
    ))
