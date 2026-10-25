"""Source-preserving object assembly for excel_r259 / 巨盾骑士."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.giant_shield import GiantShieldBashSkill, GiantShieldNeighborTrait
from wujiang.tactical.heroes.mechanics.shield_knight import ShieldKnightChargeSkill, ShieldKnightPassingTrait


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r259",
        skills={
            "冲锋": ShieldKnightChargeSkill,
            "盾击": GiantShieldBashSkill,
        },
        traits={
            "周围有名字里带有“巨盾”的单位时所有伤害减免效果+1": GiantShieldNeighborTrait,
            "此单位位移经过的名字里带有“巨盾”的单位直到下回合结束前速+1，并且血+1/4魔+1": ShieldKnightPassingTrait,
        },
    ))
