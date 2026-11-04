"""Source-preserving object assembly for excel_r246 / 地狱的武神。GALDER."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.hell_core import HellPressureSkill, HellSpearSkill
from wujiang.tactical.heroes.mechanics.hell_wargod import HellWarGodRebirthTrait, HellWarGodSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r246",
        skills={
            "魔枪": HellSpearSkill,
            "魔压": HellPressureSkill,
            "武神": HellWarGodSkill,
        },
        traits={
            "此单位被技能破坏时，对周所有对方单位造成伤害，之后下个对方回合结束时以魔0的状态召唤到任意单位的周围": HellWarGodRebirthTrait,
        },
        extra_skill_factories=(lambda: HellSpearSkill(free=True), lambda: HellPressureSkill(free=True),),
    ))
