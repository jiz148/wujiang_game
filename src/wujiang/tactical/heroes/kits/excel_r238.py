"""Source-preserving object assembly for excel_r238 / 地狱的尖兵。BAKI."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.hell_core import HellPressureSkill, HellSpearSkill
from wujiang.tactical.heroes.mechanics.hell_rebirth import HellPierceRebirthTrait


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r238",
        skills={
            "魔枪": HellSpearSkill,
            "魔压": HellPressureSkill,
        },
        traits={
            "此单位被技能破坏后，下个对方回合结束时以魔0的状态召唤到任意单位的周围，并获得以下效果3轮：普攻破魔": HellPierceRebirthTrait,
        },
        extra_skill_factories=(lambda: HellSpearSkill(free=True), lambda: HellPressureSkill(free=True),),
    ))
