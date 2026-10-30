"""Source-preserving object assembly for excel_r328 / 骷髅飞盗团的团长。星海的起源。弗兰斯."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.flying_dragon_ship import ShipCrewMovePolicy


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r328",
        ai_policy=ShipCrewMovePolicy(42.0),
        skills={
            "飞龙船": r.FlyingDragonShipSkill,
            "启航号": r.DepartureSkill,
        },
        traits={
            "此单位的费魔技能可以被此单位乘骑的“飞龙船。亚特兰大”每回合不费魔使用，使用后减少此单位的每回合技能使用次数": r.ShipBorrowPermissionTrait,
        },
    ))
