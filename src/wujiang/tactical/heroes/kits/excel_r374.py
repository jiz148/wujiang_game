"""R374 source-fragment assembly."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.orc_tiger import StarAppraisalSkill, TigerCallTrait, TigerDefenseTrait


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r374",
        skill_fragments={
            "星象鉴（一回合一次；任意一个包括此单位的己方单位在回合结束前攻击增加投骰子的次数；因为同名技能增加的攻击数最高为12）": StarAppraisalSkill,
        },
        traits={
            "攻击对方单位时在周围召唤虎灵（攻3守2速3范1；可以在召唤回合行动）": TigerCallTrait,
            "此单位守+1此单位召唤的单位的个数": TigerDefenseTrait,
        },
    ))
