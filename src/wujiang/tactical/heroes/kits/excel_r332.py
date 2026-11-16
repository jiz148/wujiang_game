"""Source-row assembly for the Atlanta fighter, excel_r332."""

from wujiang.tactical.heroes.flying_dragon_crew_next import (
    FighterAIPolicy, FighterShipAttackPermissionTrait, SilverHeavyArmorSkill,
)
from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r332",
        ai_policy=FighterAIPolicy(43.0),
        skills={"飞龙船": r.FlyingDragonShipSkill,
                "银制的重甲": SilverHeavyArmorSkill},
        traits={
            "此单位乘骑的“飞龙船。亚特兰大”攻+1": FighterShipAttackPermissionTrait,
        },
    ))
