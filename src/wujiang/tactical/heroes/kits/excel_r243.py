"""Source-preserving object assembly for excel_r243 / 地狱的骑士。ZUR."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.hell_core import HellPressureSkill, HellSpearSkill
from wujiang.tactical.heroes.mechanics.hell_rebirth import HellKnightRebirthTrait


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r243",
        skills={
            "魔压": HellPressureSkill,
            "魔枪": HellSpearSkill,
        },
        traits={
            "此单位被技能破坏后，下个对方回合结束时以魔0的状态召唤到任意单位的周围，并变为乘骑坐骑的状态，坐骑名字是地狱马（速4守4）": HellKnightRebirthTrait,
        },
        extra_skill_factories=(lambda: HellSpearSkill(free=True), lambda: HellPressureSkill(free=True),),
    ))
