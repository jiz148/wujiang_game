"""Source-preserving object assembly for excel_r022 / D。黑豹."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.iaido import DPantherManaTrait
from wujiang.tactical.heroes.mechanics.mimic import MimicSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r022",
        skills={
            "模仿": MimicSkill,
        },
        traits={
            "每回合开始时给此单位放置场上己方名字里带有“D。”的武将数量的魔力点": DPantherManaTrait,
        },
    ))
