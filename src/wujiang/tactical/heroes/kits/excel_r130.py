"""R130 exact source-fragment assembly."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.iron_fist_rodick import (
    DangerAuraSkill, RodickAllDamagePierceTrait, RodickPierceSkill,
    RodickPressureResetTrait, RodickPressureTrait, SteelFistSkill,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r130",
        skill_fragments={
            "穿刺": RodickPierceSkill,
            "钢拳（4轮一次；2*3）": SteelFistSkill,
            "DANGER！危险气场（2轮一次；此单位周围11*11的单位无法回避，直到回合结束）": DangerAuraSkill,
        },
        traits={
            "此单位在己方结束时周围11*11有对方单位的话攻+1，守+1": RodickPressureTrait,
            "攻击或使用带有伤害的技能后攻守还原": RodickPressureResetTrait,
            "所有伤害半破魔": RodickAllDamagePierceTrait,
        },
    ))
