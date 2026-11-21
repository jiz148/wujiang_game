"""R116 exact source-fragment assembly."""

from wujiang.tactical.heroes.demon_zela import (
    DemonicAbsorptionSkill, DemonPounceSkill, ZelaDemonCompanyTrait,
)
from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r116",
        skill_fragments={
            "魔性吸收（一回合一次；破魔；范6；3*3；击中单位向此单位方向尽量移动4格直到移动到此单位周围；被影响的单位此回合内受到此单位的普攻伤害半破魔）": DemonicAbsorptionSkill,
            "飞扑【1（一回合最多使用一次；移动2格；对周围造成伤害）": DemonPounceSkill,
        },
        traits={
            "此单位周围每有一个“恶魔”单位，攻+1，守+1": ZelaDemonCompanyTrait,
        },
    ))
