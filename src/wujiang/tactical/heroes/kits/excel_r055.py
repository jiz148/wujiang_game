"""R055 exact source-fragment assembly."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.kanzaki import KanzakiFlashSkill, SummonFollowerSkill
from wujiang.tactical.heroes.mechanics.foundation import LargePierceSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r055",
        skill_fragments={
            "穿刺（大）": LargePierceSkill,
            "一闪（4轮一次；4格；随时使用；伤害为此单位攻+1）": KanzakiFlashSkill,
            "追随者（一回合一次；追随者：攻？守？速？范？；每个能力值丢骰子决定）": SummonFollowerSkill,
        },
    ))
