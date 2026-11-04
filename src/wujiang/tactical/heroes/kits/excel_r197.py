"""Source-preserving object assembly for excel_r197 / 大石块人."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.stone_people import (
    MediumStoneSummon, StoneOnEnemyAttackTrait, StoneSummonSkill,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r197",
        skills={
            "中石块人": StoneSummonSkill,
        },
        traits={
            "此单位每次被对方单位攻击都会在周围尽可能召唤一个“中石块人”": lambda: StoneOnEnemyAttackTrait(MediumStoneSummon),
        },
    ))
