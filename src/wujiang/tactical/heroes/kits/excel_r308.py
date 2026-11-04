"""Source-preserving object assembly for excel_r308 / 恶魔。希尔法."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.demon_link import DemonDeathBacklashTrait, DemonLinkSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r308",
        skills={
            "远程龙吸": r.RemoteDragonBreathSkill,
            "恶魔链接": DemonLinkSkill,
        },
        traits={
            "此单位因为攻击或技能伤害破坏时，只要此单位处于被破坏的状态，那次攻击或使用技能的单位攻-1": DemonDeathBacklashTrait,
        },
    ))
