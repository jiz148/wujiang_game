"""Source-preserving object assembly for excel_r239 / 地狱的狂王。BURON."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.hell_core import (
    HellOrbSkill,
    HellShuffleSkill,
    HellWallSkill,
)
from wujiang.tactical.heroes.mechanics.hell_rebirth import HellRebirthTrait


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r239",
        skills={
            "魔动球": HellOrbSkill,
            "魔墙": HellWallSkill,
            "地狱洗牌": HellShuffleSkill,
        },
        traits={
            "此单位被技能破坏后，下个对方回合结束时以魔0的状态召唤到对方任意单位的周围，并且大招重置": lambda: HellRebirthTrait("excel_r239"),
        },
        extra_skill_factories=(lambda: HellOrbSkill(free=True), lambda: HellWallSkill(free=True),),
    ))
