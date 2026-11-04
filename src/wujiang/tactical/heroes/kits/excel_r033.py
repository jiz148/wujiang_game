"""Source-preserving object assembly for excel_r033 / 拉奥魔导士."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.lao_mage import LaoDamageStatCancelSkill, LaoDamageStatCancelTrait, LaoMageHandSkill, LaoWaveBulletSkill, MageCloakSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r033",
        skills={
            "波导弹": LaoWaveBulletSkill,
            "法师之手": LaoMageHandSkill,
            "法师斗篷": MageCloakSkill,
        },
        traits={
            "可以减1魔以外的能力使一次伤害不计算": LaoDamageStatCancelTrait,
        },
        extra_skill_factories=(LaoDamageStatCancelSkill,),
    ))
