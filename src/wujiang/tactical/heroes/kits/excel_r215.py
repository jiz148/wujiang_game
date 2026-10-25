"""Source-preserving object assembly for excel_r215 / 军神。上衫."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.uesugi import UesugiEndDashTrait, UesugiFlashSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r215",
        skills={
            "一闪": UesugiFlashSkill,
        },
        traits={
            "此单位可以在每个己方回合结束时穿人直线移动6格，穿人造成攻-1的伤害": UesugiEndDashTrait,
        },
    ))
