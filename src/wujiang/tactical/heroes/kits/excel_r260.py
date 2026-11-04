"""Source-preserving object assembly for excel_r260 / 野蛮人国王."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.barbarian_king import (
    BarbarianAxeSkill, BarbarianGluttonySkill, BarbarianHarassSkill,
    BarbarianWildmanTrait,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r260",
        skills={
            "飞越": lambda: r.DashMoveSkill("fly_leap", "飞越", "费1魔、每本人轮一次，直线飞行恰好三格。", max_distance=3, exact_distance=3, mana_cost=1, max_uses_per_turn=1, straight_only=True, ignore_units=True),
            "疾风战斧": BarbarianAxeSkill,
            "人身骚扰": BarbarianHarassSkill,
            "暴食": BarbarianGluttonySkill,
        },
        traits={
            "名字为“野人”的单位对此单位伤害-2，并且无法连锁此单位": BarbarianWildmanTrait,
        },
        extra_skill_factories=(lambda: BarbarianHarassSkill(free=True),),
    ))
