"""Source-row assembly for the tyrant dragon."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.tyrant_dragon import (
    BrutalPolicySkill, ChaosBreathSkill, DragonWingbeatSkill, InfernoFireSkill,
    TyrantDragonPointsTrait,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r340",
        skills={
            "残暴政策": BrutalPolicySkill,
            "炼狱之火": InfernoFireSkill,
            "混沌吐息": ChaosBreathSkill,
            "巨龙振翅": DragonWingbeatSkill,
        },
        traits={
            "攻击+1魔力点": TyrantDragonPointsTrait,
            "每个己方回合时血+1/4*魔力点数量": TyrantDragonPointsTrait,
        },
    ))
