"""Source-preserving object assembly for excel_r264 / 豹头人。飞翼."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.winged_leopard import EagleEyeSkill, WingedLeopardLeapSkill, WingedLeopardMoveResetTrait


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r264",
        skills={
            "飞跃": WingedLeopardLeapSkill,
            "鹰眼": EagleEyeSkill,
        },
        traits={
            "使用“飞跃”后重置此回合移动次数": WingedLeopardMoveResetTrait,
        },
    ))
