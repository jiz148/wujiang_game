"""Source-row assembly for the Atlanta helmsman, excel_r330."""

from wujiang.tactical.heroes.flying_dragon_crew import (
    HelmsmanAIPolicy, HelmsmanShipPermissionTrait, SandHistoryTrait,
    SandOfTimeSkill, SilverGrapplingHookSkill,
)
from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r330",
        ai_policy=HelmsmanAIPolicy(52.0),
        skills={
            "飞龙船": r.FlyingDragonShipSkill,
            "银制的飞钩": SilverGrapplingHookSkill,
            "时之沙": SandOfTimeSkill,
        },
        traits={
            "此单位乘骑的“飞龙船。亚特兰大”速+2，并且增加特性：穿人造成4的伤害": HelmsmanShipPermissionTrait,
        },
        extra_trait_factories=(SandHistoryTrait,),
    ))
