"""R124 蜂王 source-fragment assembly."""

from wujiang.tactical.heroes.bee_king import BeeAutoSummonTrait, BeeReversalSkill, HiveSkill, PurpleBeeSkill
from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.foundation import BasicAttackPierceTrait


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r124",
        skill_fragments={
            "紫蜂【0.5（每个己方回合开始时可以自动在周围召唤2个；攻2守1速4范1；每个攻击回合开始时在身边召唤一只；普攻破魔；飞行）": PurpleBeeSkill,
            "￥蜂巢（3轮；此单位和紫蜂攻+1）": HiveSkill,
            "【1.5蜂反（重置“蜂巢”）": BeeReversalSkill,
        },
        traits={"普攻破魔": BasicAttackPierceTrait},
        extra_trait_factories=(BeeAutoSummonTrait,),
    ))
