"""Source-preserving object assembly for excel_r253 / 古代机械骑士."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.ancient_mecha import (
    AncientCursedRustSkill, AncientDamageModeTrait, AncientFusionSkill,
    AncientMechanicalHorseSkill, AncientMechanicalHorseStartTrait,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r253",
        skills={
            "机械马": AncientMechanicalHorseSkill,
            "诅咒的锈迹": AncientCursedRustSkill,
            "古代融合": AncientFusionSkill,
        },
        traits={
            "此单位和此单位的召唤物若受到攻击伤害，则回合结束前仅受到攻击伤害，若受到技能伤害，则回合结束前仅受到技能伤害": AncientDamageModeTrait,
        },
        extra_trait_factories=(AncientMechanicalHorseStartTrait,),
        attributes={'entry_footprint_width': 1, 'entry_footprint_height': 2},
    ))
