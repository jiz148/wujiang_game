"""Source-preserving object assembly for excel_r193 / 剑斗兽。塞克托鲁."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.gladiator_core import (
    GladiatorSoulSkill,
    WaterWaveCannonSkill,
)
from wujiang.tactical.heroes.mechanics.sektoru import SektoruMovedByAllyTrait


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r193",
        skills={
            "水波炮": WaterWaveCannonSkill,
            "剑斗之魂": GladiatorSoulSkill,
        },
        traits={
            "此单位因其他己方单位的效果改变位置回合和其下回和攻＋1，魔＋1，并且再把场上任意两个单位移动到周围": SektoruMovedByAllyTrait,
        },
    ))
