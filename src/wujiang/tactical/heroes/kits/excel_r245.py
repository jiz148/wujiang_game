"""Source-preserving object assembly for excel_r245 / 地狱的猎人。BRAO."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.hell_core import HellRangedSpearSkill
from wujiang.tactical.heroes.mechanics.hell_dog_hunter import HellHunterRescueTrait


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r245",
        skills={
            "魔枪": HellRangedSpearSkill,
        },
        traits={
            "此单位被技能破坏时，在原地以魔0的状态召唤一个被破坏的名字里带有“地狱”的己方单位，之后下个对方回合结束时以魔0的状态召唤到任意单位的周围": HellHunterRescueTrait,
        },
        extra_skill_factories=(lambda: HellRangedSpearSkill(free=True),),
    ))
