"""Source-preserving object assembly for excel_r142 / 猫叔."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.cat_taunt import CatRetaliationTrait, CatTauntRoarSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r142",
        skills={
            "嘲讽之吼": CatTauntRoarSkill,
        },
        traits={
            "攻击或技能此单位的单位魔-1": CatRetaliationTrait,
        },
    ))
