"""R096 exact source-fragment assembly."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.snow_woman import BlizzardSkill, IceThornsSkill, IceWallSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r096",
        skill_fragments={
            "冰墙": IceWallSkill,
            "菱撒【1.5（1回合一次；仅可对己方单位和此单位使用；对攻击或技能被菱撒单位的单位给予3，4，5的伤害）": IceThornsSkill,
            "吹雪【2（2*6；破魔；被击中单位有1/2几率守-2，到1）": BlizzardSkill,
        },
    ))
