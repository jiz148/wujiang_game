"""R134 exact source-fragment assembly."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.f_warrior import (
    FBigHardenSkill, FBigLeapSkill, FBigShensuSkill, FLargeDragonBreathSkill,
    FLargeMachineGunSkill, FLargeManaDrainSkill, LargePierceSkill,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r134",
        skill_fragments={
            "穿刺（大）": LargePierceSkill,
            "飞跃（大）": FBigLeapSkill,
            "神速（大）": FBigShensuSkill,
            "硬化（大）": FBigHardenSkill,
            "吸魔（大）": FLargeManaDrainSkill,
            "龙吸（大）": FLargeDragonBreathSkill,
            "机枪（大）": FLargeMachineGunSkill,
        },
    ))
