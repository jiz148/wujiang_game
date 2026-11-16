"""Source-row assembly for the Atlanta scout, excel_r331."""

from wujiang.tactical.heroes.flying_dragon_crew import (
    ScoutAIPolicy, ScoutAttackSlowTrait, ScoutShipGuardPermissionTrait,
    SilverLockClawSkill,
)
from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r331",
        ai_policy=ScoutAIPolicy(44.0),
        skills={
            "飞龙船": r.FlyingDragonShipSkill,
            "银制的锁爪": SilverLockClawSkill,
        },
        traits={
            "攻击带有以下效果：被击中单位下个己方回合速-1": ScoutAttackSlowTrait,
            "此单位乘骑的“飞龙船。亚特兰大”每次受到伤害都有1/3几率将那次伤害无效化": ScoutShipGuardPermissionTrait,
        },
    ))
