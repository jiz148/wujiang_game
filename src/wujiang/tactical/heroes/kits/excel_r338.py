"""Source-row assembly for Hermopolis."""

from wujiang.tactical.heroes.hermopolis import (
    HermopolisBlinkSkill, HermopolisDesignerSkill, HermopolisExperimentSkill,
    HermopolisFullHealTrait, HermopolisMirrorTrait, HermopolisSpecializationSkill,
)
from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r338",
        skills={
            "不定态": HermopolisBlinkSkill,
            "地狱设计者": HermopolisDesignerSkill,
            "实验体": HermopolisExperimentSkill,
            "特化起点": HermopolisSpecializationSkill,
        },
        traits={
            "此单位攻为场上除此单位外最高的守+1（随时变化）": HermopolisMirrorTrait,
            "此单位守为场上除此单位外最高的攻+1（随时变化）": HermopolisMirrorTrait,
            "每个回合结束后血变满": HermopolisFullHealTrait,
        },
    ))
