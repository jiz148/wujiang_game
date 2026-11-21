"""R062 exact source-fragment assembly."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.flame_emissary import (
    ExplosionSkill, FlameWallSkill, PassionImpactSkill, SummonFlameGiantSkill, TeamHealSkill,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r062",
        skill_fragments={
            "炎巨人【1（攻4守4速3范1；攻击两格；有一次保护；2轮可不费魔召唤一只）": SummonFlameGiantSkill,
            "爆裂【2（3*3；击中没有单位的区域时在那个区域尽可能多的召唤“炎巨人”）": ExplosionSkill,
            "￥加血（全场己方单位血+1/4）": TeamHealSkill,
            "炎墙": FlameWallSkill,
            "热情冲击【1（一回合最多使用一次；2*2；同时对全场己方“炎巨人”周围的对方单位也造成伤害）": PassionImpactSkill,
        },
    ))
