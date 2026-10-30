"""Source-preserving object assembly for excel_r329 / 骷髅飞盗团的参谋。深邃的流星。鲁托."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.flying_dragon_ship import ShipCrewMovePolicy


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r329",
        ai_policy=ShipCrewMovePolicy(62.0),
        skills={
            "飞龙船": r.FlyingDragonShipSkill,
            "加魔": r.AddManaSkill,
            "加血": r.AddHealthSkill,
            "守+1": r.DefenseOneSkill,
            "银制的信号弹": r.SilverSignalFlareSkill,
        },
        traits={
            "攻击带有以下效果：被击中单位在下个己方回合不能使用位移技能": r.RutoAttackLockTrait,
            "此单位乘骑的“飞龙船。亚特兰大”每回合攻击次数+1": r.RutoShipAttackTrait,
            "此单位的费魔技能可以被此单位乘骑的“飞龙船。亚特兰大”每回合不费魔使用，使用后减少此单位的每回合技能使用次数": r.ShipBorrowPermissionTrait,
        },
    ))
