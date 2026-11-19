"""R354 exact source-fragment assembly."""

from wujiang.tactical.heroes.amethyst_warrior import AmethystChainManaTrait, AmethystFocusSkill, AmethystMomentumTrait, AmethystTidalFistSkill
from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r354",
        skills={"潮汐拳": AmethystTidalFistSkill, "集中": AmethystFocusSkill},
        traits={"每当场上有此单位以外的单位使用技能时，此单位直到下个己方回合开始前攻+1，守+1": AmethystMomentumTrait,
                "每次行动被连锁，魔+2": AmethystChainManaTrait},
    ))
