"""R133 exact source-fragment assembly."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mercury_mage import FixateSkill, MercuryMageRecovery, SummonMercurySkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r133",
        skill_fragments={
            "水银【1（2轮召唤一只；攻3守5速2范1;每回合可以使用一次“神速”；不受到技能伤害，受到技能伤害时血全满；此单位周围的单位不能使用位移技能）": SummonMercurySkill,
            "￥固定（10格；破魔；被击中的单位2轮不能移动，攻击和主动技能）": FixateSkill,
        },
        traits={
            "对方回合结束时，对方单位周围每有一个“水银”，此单位魔+1": MercuryMageRecovery,
        },
    ))
