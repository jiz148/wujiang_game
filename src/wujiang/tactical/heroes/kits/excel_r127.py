"""Source-preserving object assembly for excel_r127 / 幻象鸟."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.black_cat import FantasyMoveSkill
from wujiang.tactical.heroes.mechanics.rainbow_mirror import FriendlyMirrorSkill, RainbowMirrorSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r127",
        skills={
            "幻想": FantasyMoveSkill,
            "彩虹镜": RainbowMirrorSkill,
            "友好镜": FriendlyMirrorSkill,
        },
    ))
