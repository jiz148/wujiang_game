"""Source-preserving object assembly for excel_r027 / 无常."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.wuchang import MigratoryBirdMarkSkill, WuchangAttackSealTrait, WuchangMistSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r027",
        skills={
            "无常之雾": WuchangMistSkill,
            "侯鸟标记": MigratoryBirdMarkSkill,
        },
        traits={
            "被此单位普攻击中的单位不能普攻或使用技能，2轮": WuchangAttackSealTrait,
        },
    ))
