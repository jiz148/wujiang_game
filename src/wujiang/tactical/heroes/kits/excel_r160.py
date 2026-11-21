"""R160 exact source-fragment assembly."""

from wujiang.tactical.heroes.dead_soul_echo import ForbiddenMagusSinSkill, HungrySoulRewindSkill, SpiritAxeSkill
from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r160",
        skill_fragments={
            "精神斧（一回合一次；弧形攻击；半破魔）": SpiritAxeSkill,
            "禁忌。魔法使之罪（牺牲一点魔以外的能力值，重置攻击或移动次数）": ForbiddenMagusSinSkill,
            "饿魂回溯（一回合一次；仅能在对方回合结束时随时使用；除魔以外所有能力值重置）": HungrySoulRewindSkill,
        },
    ))
