"""Source-preserving object assembly for excel_r035 / 牛鬼."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.mountain_god import DemonBladeSkill, LargeDrainManaSkill, MountainAwakeningSkill, MountainEscapeSkill, MountainGodCounterTrait, MountainGodMuroSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r035",
        skills={
            "妖刀": DemonBladeSkill,
            "吸魔": LargeDrainManaSkill,
            "遁术": MountainEscapeSkill,
        },
        skill_fragments={
            "￥山神术。室王（魔无限，4轮）": MountainGodMuroSkill,
            "山神术。觉醒（仅可在此单位上“山神计数点”为8时使用；清空此单位上的“山神计数点”，重置大招）": MountainAwakeningSkill,
        },
        traits={
            "普攻被对方单位连锁后往此单为上放置一个“山神计数点”": MountainGodCounterTrait,
            "破坏一个单位后往此单为上放置一个“山神计数点”": MountainGodCounterTrait,
        },
    ))
