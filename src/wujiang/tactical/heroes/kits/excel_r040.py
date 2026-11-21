"""R040 exact source-fragment assembly."""

from wujiang.tactical.heroes.clockwork_shark import (
    BarbedSpinesSkill, ClockworkSharkAIPolicy, ClockworkSkill, SharkMK2Skill,
    SharkPulseSkill,
)
from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r040",
        skill_fragments={
            "发条（魔力点+2；一回合一次）": ClockworkSkill,
            "倒刺（攻击或对此单位使用技能的单位受到4的伤害；1轮；魔力点-1）": BarbedSpinesSkill,
            "脉冲（魔力点-3；1*5；然后尽量移动到第6格）": SharkPulseSkill,
            "￥MK2（魔力点-10；速+5；守变为1；可穿人；穿人造成持有魔力点伤害；伤害上限为6；造成伤害后魔力点-造成的伤害数）": SharkMK2Skill,
        },
        ai_policy=ClockworkSharkAIPolicy(),
    ))
