"""Source-preserving object assembly for excel_r244 / 地狱的刺客。KAKI."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.hell_core import HellPressureSkill, HellSpearSkill
from wujiang.tactical.heroes.mechanics.hell_rebirth import HellAssassinRebirthTrait


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r244",
        skills={
            "魔压": HellPressureSkill,
            "魔枪": HellSpearSkill,
        },
        traits={
            "此单位被技能破坏时，对周围任意单位进行2次伤害，之后下个对方回合结束时以魔0的隐身状态召唤到对方任意单位的周围": HellAssassinRebirthTrait,
        },
        extra_skill_factories=(lambda: HellSpearSkill(free=True), lambda: HellPressureSkill(free=True),),
    ))
