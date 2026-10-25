"""Source-preserving object assembly for excel_r219 / 正义盟军。次时代鸟人."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.bird_soul import BirdSoulSkill
from wujiang.tactical.heroes.mechanics.bird_movement import BirdDashSkill, BirdSkillMoveBoostTrait


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r219",
        skills={
            "疾走": lambda: BirdDashSkill(free=False),
            "鸟人之魂": lambda: BirdSoulSkill(free=False),
        },
        traits={
            "此单位因为技能被移动后的回合和其下回合攻守翻倍": BirdSkillMoveBoostTrait,
        },
        extra_skill_factories=(lambda: BirdDashSkill(free=True), lambda: BirdSoulSkill(free=True),),
    ))
