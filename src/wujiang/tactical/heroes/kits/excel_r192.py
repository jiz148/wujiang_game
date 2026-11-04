"""Source-preserving object assembly for excel_r192 / 剑斗兽。安德鲁."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.gladiator_cooperation import (
    AndrewMovedByAllyTrait,
    BattleRotationSkill,
    GladiatorRoarSkill,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r192",
        skills={
            "咆哮": GladiatorRoarSkill,
            "战斗轮转": BattleRotationSkill,
        },
        traits={
            "此单位因其他己方单位的效果改变位置回合和其下回和守＋1，魔＋1": AndrewMovedByAllyTrait,
        },
    ))
