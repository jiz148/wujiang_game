"""Source-preserving object assembly for excel_r242 / 地狱的赤候。SIKA."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.hell_core import HellPressureSkill, HellSpearSkill
from wujiang.tactical.heroes.mechanics.hell_wargod import HellSikaSacrificeTrait


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r242",
        skills={
            "魔压": HellPressureSkill,
            "魔枪": HellSpearSkill,
        },
        traits={
            "此单位被技能破坏后，下个对方回合结束时以魔0的状态召唤到任意单位的周围，并可以破坏场上任意一个名字里带有“地狱”己方单位（被视为技能破坏）": HellSikaSacrificeTrait,
        },
        extra_skill_factories=(lambda: HellSpearSkill(free=True), lambda: HellPressureSkill(free=True),),
    ))
