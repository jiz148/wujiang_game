"""Source-preserving object assembly for excel_r056 / 蕾米."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.remi_bat import (
    RemiBatSkill,
    RemiChaosSkill,
    RemiUndyingTrait,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r056",
        skills={
            "混沌": RemiChaosSkill,
            "蝙蝠": RemiBatSkill,
        },
        traits={
            "吸血": r.AttackLifeStealTrait,
            "此单位当血量为0时不会被破坏，而是剩1/4，魔-1，因为此效果魔变为0时破坏": RemiUndyingTrait,
            "普攻吸魔": r.AttackManaDrainTrait,
        },
    ))
