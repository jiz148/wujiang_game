"""R063 exact source-fragment assembly."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.ruin_child import EtherealFormSkill, RuinEntangleSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r063",
        skill_fragments={
            "加魔": r.AddManaSkill,
            "纠缠【2（3轮；破魔；被击中后，每回合被吸血，吸魔；不叠加）": RuinEntangleSkill,
            "【1.5虚灵（使一个单位不能攻击，物免，并且受到2倍的技能伤害，回复效果；破魔；2轮）": EtherealFormSkill,
        },
    ))
