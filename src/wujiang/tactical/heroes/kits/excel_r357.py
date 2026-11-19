"""R357 exact source-fragment assembly."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.foundation import BasicAttackPierceTrait
from wujiang.tactical.heroes.desperate_gambler import (
    GamblerBasicOddsTrait, GamblerChipBoostSkill, GamblerDesperateRerollSkill,
    GamblerEndAtOneTrait, GamblerFixedHealthTrait, GamblerUnavoidableBasicTrait,
    GamblerWoundDefenseTrait,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r357",
        skills={"筹码强化": GamblerChipBoostSkill, "绝境无赖": GamblerDesperateRerollSkill},
        traits={"普攻无法被回避": GamblerUnavoidableBasicTrait,
                "普攻破魔": BasicAttackPierceTrait,
                "血量不会变化": GamblerFixedHealthTrait,
                "每次受到伤害守-1，到1": GamblerWoundDefenseTrait,
                "回合结束时守为1时，此单位破坏": GamblerEndAtOneTrait,
                "普攻只有（6-守）/6的几率给予伤害（最低为0）": GamblerBasicOddsTrait},
    ))
