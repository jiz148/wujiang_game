"""R375 source-fragment assembly."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.orc_rabbit import (
    RabbitBurrowAIPolicy, RabbitEmptyAttackTrait, RabbitEscapeSkill, RabbitRelayTrait,
)
from wujiang.tactical.heroes.orc_tiger import StarAppraisalSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r375",
        skill_fragments={
            "星象鉴（一回合一次；任意一个包括此单位的己方单位在回合结束前攻击增加投骰子的次数；因为同名技能增加的攻击数最高为12）": StarAppraisalSkill,
            "脱兔（一回合2次；被动技能；瞬移到一个兔子洞旁边）": RabbitEscapeSkill,
        },
        traits={
            "攻击空地时在攻击地点制造一个“兔子洞”（守1），被视为地形，被破坏后场上所有此单位制造的“兔子洞”都破坏": RabbitEmptyAttackTrait,
            "此单位攻击兔子洞时没有伤害，然后由兔子洞做出攻击，攻击的范围，造成的伤害，效果同此单位相同": RabbitRelayTrait,
        },
        ai_policy=RabbitBurrowAIPolicy(),
    ))
