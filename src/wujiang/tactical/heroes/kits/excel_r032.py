"""Source-preserving object assembly for excel_r032 / 亚伦."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.aaron_unicorn import AaronLightAuraTrait, AaronMountedStartTrait, GreatUnicornSkill, MorningHolyLightSkill
from wujiang.tactical.heroes.mechanics.foundation import LargePierceSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r032",
        skills={
            "大独角兽": GreatUnicornSkill,
            "穿刺": LargePierceSkill,
            "晨曦圣光": MorningHolyLightSkill,
        },
        traits={
            "此单位周围7*7的单位每个己方回合开始时血+1/4": AaronLightAuraTrait,
            "此单位及此单位的召唤物对暗属性单位造成的伤害+1": AaronLightAuraTrait,
            "此单位的召唤物被破坏了的下个回合攻守速+2，血魔变满，使用技能不费魔": AaronLightAuraTrait,
        },
        extra_trait_factories=(AaronMountedStartTrait,),
        attributes={'entry_footprint_width': 1, 'entry_footprint_height': 2},
    ))
