"""R150 魔使 source-fragment assembly."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mana_messenger import (
    ManaMessengerAIPolicy, MessengerBasicManaTrait, MessengerEnemyEffectGuard,
    MessengerSpellDefenseTrait, SuperHardenSkill,
)
from wujiang.tactical.heroes.mechanics.foundation import LargePierceSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r150",
        skill_fragments={
            "穿刺（大）": LargePierceSkill,
            "超硬化（5轮一次；3轮，自然回血，守+3）": SuperHardenSkill,
        },
        traits={
            "攻击敌人回魔": MessengerBasicManaTrait,
            "攻击队友没有伤害并且加魔": MessengerBasicManaTrait,
            "不受到对方技能伤害以外的效果影响": MessengerEnemyEffectGuard,
            "周围每次有单位使用费魔的技能时此单位直到下个对方回合结束前守+1": MessengerSpellDefenseTrait,
        },
        ai_policy=ManaMessengerAIPolicy(),
    ))
