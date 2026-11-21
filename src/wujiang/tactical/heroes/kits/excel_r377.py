"""R377 source-fragment assembly."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.northern_king import (
    IronShackleSkill, IronSoulSkill, NorthernDamageCoinTrait,
    NorthernSkillGuardTrait, NorthernSoulLockTrait, northern_can_take_turn_actions,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r377",
        skill_fragments={
            "铁枷锁（范5；没有伤害；被击中的单位向此单位尽量移动2格；速-2，1轮；无视魔免）": IronShackleSkill,
            "铁魂魄（一回合一次；范3；在击中的空地召唤“铁魂魄”，攻4守0.5速4范1，普攻半破魔，在召唤的回合可以行动；场上最多存在一个此单位召唤的“铁魂魄”；场上存在此单位召唤的“铁魂魄”时此单位不能行动；铁魂魄可以使用此单位的所有技能，并且拥有此单位的所有特性）": IronSoulSkill,
        },
        traits={
            "此单位不受到伤害以外的技能效果": NorthernSkillGuardTrait,
            "此单位受到伤害的场合，投硬币，若正面，那次伤害无效，若反面，那次伤害+1": NorthernDamageCoinTrait,
        },
        extra_trait_factories=(NorthernSoulLockTrait,),
        attributes={"can_take_turn_actions": northern_can_take_turn_actions},
    ))
