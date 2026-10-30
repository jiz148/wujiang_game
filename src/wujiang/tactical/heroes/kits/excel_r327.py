"""Source-preserving object assembly for excel_r327 / 红莲魔。盖泽尔."""

from wujiang.tactical.heroes.ai_policy import CounterReactionAIPolicy
from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.foundation import BasicAttackPierceTrait
from wujiang.tactical.heroes.mechanics.red_lotus import RedLotusDestroyedHeroesTrait, RedLotusOnceDamagePerTurnTrait, RedLotusSkillDamageImmunityTrait, RedLotusUnevadableAttackTrait


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r327",
        ai_policy=CounterReactionAIPolicy(with_followup=True),
        traits={
            "此单位攻，守+被破坏的武将的数量（随时变化）": RedLotusDestroyedHeroesTrait,
            "不受到技能伤害": RedLotusSkillDamageImmunityTrait,
            "普攻破魔": BasicAttackPierceTrait,
            "普攻无法被回避": RedLotusUnevadableAttackTrait,
            "每回合仅受到一次伤害": RedLotusOnceDamagePerTurnTrait,
        },
    ))
