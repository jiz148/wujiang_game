"""Source-preserving object assembly for excel_r379 / 妖仙。菊."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.ai_score_policies import KikuPiercePolicy
from wujiang.tactical.heroes.mechanics.kiku import KikuAfterDeathTrait, SunSlashSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r379",
        ai_policy=KikuPiercePolicy(),
        skills={
            "斩技": SunSlashSkill,
        },
        traits={
            "此单位在被破坏后，场上其他的己方单位每个进攻回合额外增加一次伤4的普攻": KikuAfterDeathTrait,
        },
    ))
