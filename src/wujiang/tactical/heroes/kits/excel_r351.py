"""R351 exact source-fragment assembly."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.heat_source_beast import (
    BeastAttackCountTrait, BeastCircleAttackTrait, BeastDeathTrait,
    BeastDevourSkill, BeastSelfDestructSkill, HeatConductionSkill,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r351",
        skills={"热导": HeatConductionSkill, "自爆": BeastSelfDestructSkill,
                "吞噬": BeastDevourSkill},
        traits={"攻击一周": BeastCircleAttackTrait,
                "每回合攻击次数增加对方场上的武将的次数": BeastAttackCountTrait,
                "被破坏后对周围11*11造成4的伤害": BeastDeathTrait},
    ))
