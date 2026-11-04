"""Source-preserving object assembly for excel_r191 / 剑斗兽。拉克艾鲁."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.gladiator_cooperation import GladiatorMovedByAllyTrait
from wujiang.tactical.heroes.mechanics.gladiator_core import GladiatorClawSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r191",
        skills={
            "剑斗力爪": GladiatorClawSkill,
        },
        traits={
            "此单位因其他己方单位的效果改变位置回合和其下回合攻＋1，魔＋1": lambda: GladiatorMovedByAllyTrait(persistent_mana=False),
        },
    ))
