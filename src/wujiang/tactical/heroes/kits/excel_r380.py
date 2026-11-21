"""R380 source-fragment assembly."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.penitent import (
    GodDeclarationSkill, MoonDarkSideSkill, PenanceAttackTrait,
    PenitentShackleSkill,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r380",
        skill_fragments={
            "神的宣言（一回合一次；声明一个种族，和一项魔以外的能力值，场上所有属于那个种族的单位能力值增加或减少2，到1；一轮）": GodDeclarationSkill,
            "￥月之暗面（破魔；改变那个单位的种族）": MoonDarkSideSkill,
            "枷锁（一回合一次；被击中单位速-2，到1，增加技能：荆棘诅咒：一回合一次，对周围造成3的半破魔伤害，每击中一格单位血+1/4，魔+1）": PenitentShackleSkill,
        },
        traits={
            "普攻带有以下破魔效果：被击中的单位下个己方回合若不对己方造成伤害则血-1/2": PenanceAttackTrait,
        },
    ))
