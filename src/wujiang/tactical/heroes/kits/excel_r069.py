"""R069 exact source-fragment assembly."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.moon_beast import MoonBeastTrait, MoonFlashSkill, MoonPulseSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r069",
        skill_fragments={
            "脉冲（前方5格；1回合一次）": MoonPulseSkill,
            "闪光弹（一回合一次；3*3；没有伤害；攻-1，速-1，到1；攻击有1/2几率无效；3轮）": MoonFlashSkill,
        },
        traits={
            "破坏后打一周": MoonBeastTrait,
        },
    ))
