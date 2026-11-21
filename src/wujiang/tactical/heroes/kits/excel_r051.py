"""R051 exact source-fragment assembly."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.wokin import (
    BreathCannonSkill, HundredFistsSkill, ShockwaveSkill,
    WokinMeleeRebound, WokinStraightPursuit, WokinUltimateReset,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r051",
        skill_fragments={
            "￥气息炮（6格）": BreathCannonSkill,
            "￥震波（一周2格）": ShockwaveSkill,
            "￥百裂拳（对一个方向攻击3次，最后一次攻击破半破魔)": HundredFistsSkill,
        },
        traits={
            "对此单位近身攻击或使用技能的单位被弹开一格": WokinMeleeRebound,
            "直线移动向对方时速+2": WokinStraightPursuit,
            "血在3/4以下时重置大招": WokinUltimateReset,
        },
    ))
