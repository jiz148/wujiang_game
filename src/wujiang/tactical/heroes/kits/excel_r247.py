"""Source-preserving object assembly for excel_r247 / 地狱的术士。SNOW."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.ai_policy import PaidReactionAIPolicy
from wujiang.tactical.heroes.mechanics.hell_core import HellWallSkill
from wujiang.tactical.heroes.mechanics.hell_dog_hunter import (
    HellDogSkill,
    HellSnowRebirthTrait,
    HellTeleportSkill,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r247",
        ai_policy=PaidReactionAIPolicy({"hell_wall", "hell_wall_free"}, multi_target=True),
        skills={
            "[1地狱犬": HellDogSkill,
            "魔墙": HellWallSkill,
            "瞬移": HellTeleportSkill,
        },
        traits={
            "此单位被技能破坏时，所有“地狱犬”破坏并对地狱犬周围单位造成3的伤害，之后下个对方回合结束时以魔0的状态召唤到任意单位的周围，并获得以下效果3轮：自然回魔，并且重制大招）": HellSnowRebirthTrait,
        },
        extra_skill_factories=(lambda: HellWallSkill(free=True),),
    ))
