"""Source-preserving object assembly for excel_r194 / 剑斗兽。亚历山大."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.ai_score_policies import AlexanderAttackPolicy
from wujiang.tactical.heroes.mechanics.alexander import (
    AlexanderAfterAttackSwapTrait,
    AlexanderHurricaneSkill,
    AlexanderMagicAuraTrait,
    AlexanderMovedByAllyTrait,
    AlexanderSkillEffectGuardTrait,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r194",
        ai_policy=AlexanderAttackPolicy(),
        skills={
            "战斗飓风": AlexanderHurricaneSkill,
        },
        traits={
            "不受到除伤害以外的技能效果影响": AlexanderSkillEffectGuardTrait,
            "此单位攻击后与场上任意单位己方交换位置": AlexanderAfterAttackSwapTrait,
            "此单位因其他己方单位的效果改变位置回合和其下回合攻＋1，守＋1，并且重置大招": AlexanderMovedByAllyTrait,
            "此单位周围5*5内的己方单位魔免": AlexanderMagicAuraTrait,
        },
    ))
