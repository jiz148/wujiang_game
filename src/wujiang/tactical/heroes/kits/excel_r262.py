"""Source-preserving object assembly for excel_r262 / 鸟人王格姆."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.bird_king import (
    BirdKingDefenseTrait,
    GreatFeatherFanSkill,
    PoisonFeatherSkill,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r262",
        skills={
            "大羽毛扇": GreatFeatherFanSkill,
            "剧毒羽毛": PoisonFeatherSkill,
        },
        traits={
            "此单位在己方回合结束时周围有己方“鸟人”单位时守+“鸟人”单位的数量，到7": BirdKingDefenseTrait,
        },
    ))
