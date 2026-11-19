"""R361 exact source-fragment assembly."""

from wujiang.tactical.heroes.grand_magus_astrasa import (
    AstrasaAreaAttackTrait, AstrasaDivineBlastSkill, AstrasaExclusionTrait,
    AstrasaGreatManaTrait, AstrasaLargeBreathSkill, AstrasaMageWallSkill,
    AstrasaSacredArraySkill,
)
from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.hell_dog_hunter import HellTeleportSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r361",
        skill_fragments={
            "远程龙息（大）": AstrasaLargeBreathSkill,
            "￥瞬移": HellTeleportSkill,
            "神力爆破【2.5（一回合最多使用一次；5*5；破魔）": AstrasaDivineBlastSkill,
            "￥神圣魔导阵（4轮；此单位周围7*7的对方单位不能使用主动技能）": AstrasaSacredArraySkill,
            "魔法使之墙【1（一回合最多使用一次；被动技能；挡住一次攻击，技能；无视破魔）": AstrasaMageWallSkill,
        },
        traits={
            "自然大回魔": AstrasaGreatManaTrait,
            "攻击一格或者对周围5*5": AstrasaAreaAttackTrait,
            "此单位周围5*5的对方单位不能攻击": AstrasaExclusionTrait,
        },
    ))
