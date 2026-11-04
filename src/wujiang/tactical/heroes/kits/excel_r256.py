"""Source-preserving object assembly for excel_r256 / 巨盾剑士."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.giant_shield import (
    GiantShieldBashSkill, GiantShieldNeighborTrait, GiantShieldSwordDanceSkill,
    GiantShieldSwordTrait, GiantShieldWallSkill,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r256",
        skills={
            "剑舞": GiantShieldSwordDanceSkill,
            "盾墙": lambda: GiantShieldWallSkill(sword=True),
            "盾击": GiantShieldBashSkill,
        },
        traits={
            "每次受到伤害有50%几率完全格档，受到近战攻击时伤害-1，受到远程伤害时伤害-2，受到破魔伤害时会完全承受没有减免，可反击，反击时攻两次": GiantShieldSwordTrait,
            "周围有名字里带有“巨盾”的单位时所有伤害减免效果+1": GiantShieldNeighborTrait,
        },
    ))
