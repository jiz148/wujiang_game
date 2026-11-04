"""Source-preserving object assembly for excel_r195 / 剑斗兽。贝斯特罗里."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.gladiator_cooperation import GladiatorMovedByAllyTrait
from wujiang.tactical.heroes.mechanics.gladiator_core import GladiatorGaleSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r195",
        skills={
            "暴风": GladiatorGaleSkill,
        },
        traits={
            "此单位因其他己方单位的效果改变位置时魔+1，这1轮移动2次": lambda: GladiatorMovedByAllyTrait(persistent_mana=True),
        },
    ))
