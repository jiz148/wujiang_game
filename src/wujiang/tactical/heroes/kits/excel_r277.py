"""Source-preserving object assembly for excel_r277 / 天元行者."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.tianyuan import TianyuanBreakthroughSkill, TianyuanSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r277",
        skills={
            "飞越": lambda: r.DashMoveSkill("fly_leap", "飞越", "费1魔、每本人轮一次，直线飞行恰好三格。", max_distance=3, exact_distance=3, mana_cost=1, max_uses_per_turn=1, straight_only=True, ignore_units=True),
            "天元": TianyuanSkill,
            "突破": TianyuanBreakthroughSkill,
        },
    ))
