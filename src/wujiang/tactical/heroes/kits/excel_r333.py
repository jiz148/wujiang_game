"""Source-row assembly for the Atlanta gunner, excel_r333."""

from wujiang.tactical.heroes.flying_dragon_crew_next import (
    GunnerAIPolicy, GunnerShipAreaPermissionTrait,
    GunnerShipRangePermissionTrait, SilverPistolSkill,
)
from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r333",
        ai_policy=GunnerAIPolicy(49.0),
        skills={"飞龙船": r.FlyingDragonShipSkill,
                "银制的手枪": SilverPistolSkill},
        traits={
            "此单位乘骑的“飞龙船。亚特兰大”范+2": GunnerShipRangePermissionTrait,
            "攻击变为2*2": GunnerShipAreaPermissionTrait,
        },
    ))
