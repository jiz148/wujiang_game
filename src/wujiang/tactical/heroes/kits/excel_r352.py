"""Source-preserving object assembly for excel_r352 / 水忍."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.water_ninja import WaterNinjaCloneAfterAttackTrait


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r352",
        traits={
            "每次攻击在在周围召唤一个分身": WaterNinjaCloneAfterAttackTrait,
        },
    ))
