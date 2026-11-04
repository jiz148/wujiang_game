"""Source-preserving object assembly for excel_r240 / 地狱的番兵。LANZI."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.hell_core import HellPressureSkill, HellSpearSkill
from wujiang.tactical.heroes.mechanics.hell_rebirth import HellGuardRebirthTrait


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r240",
        skills={
            "魔枪": HellSpearSkill,
            "魔压": HellPressureSkill,
        },
        traits={
            "此单位被技能破坏后，下个对方回合结束时以魔0的状态召唤到任意单位的周围，并获得以下效果3轮：自然回血，攻击2次": HellGuardRebirthTrait,
        },
        extra_skill_factories=(lambda: HellSpearSkill(free=True), lambda: HellPressureSkill(free=True),),
    ))
