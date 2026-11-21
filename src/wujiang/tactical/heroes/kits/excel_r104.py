"""R104 exact source-fragment assembly."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.vanguard_e import (
    VanguardChargeSkill, VanguardIsolationTrait, VanguardSpinMomentumTrait,
    VanguardSpinSkill, VanguardWindSkill,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r104",
        skill_fragments={
            "风（穿刺大）": VanguardWindSkill,
            "旋转【1.5（攻击1周）": VanguardSpinSkill,
            "冲锋（一回合一次；冲刺）": VanguardChargeSkill,
        },
        traits={
            "若周围7*7内没有己方单位而存在对方单位则攻+1守+1": VanguardIsolationTrait,
            "每次位移之后加一次本回合内不费魔使用‘旋转’的次数": VanguardSpinMomentumTrait,
        },
    ))
