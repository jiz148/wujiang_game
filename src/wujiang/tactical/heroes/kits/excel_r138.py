"""Source-preserving object assembly for excel_r138 / 无名信使."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.ai_score_policies import MessengerPiercePolicy
from wujiang.tactical.heroes.mechanics.nameless_messenger import MessengerCreationSkill, MessengerDamageSkillManaTrait, MessengerDragonBreathSkill, MessengerPierceSkill, MessengerReincarnationSkill, MessengerSkillDamagePierceTrait


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r138",
        ai_policy=MessengerPiercePolicy(),
        skills={
            "龙息": MessengerDragonBreathSkill,
            "穿刺": MessengerPierceSkill,
            "无极落霞轮回": MessengerReincarnationSkill,
            "造化": MessengerCreationSkill,
        },
        traits={
            "使用伤害技能后魔力点+1": MessengerDamageSkillManaTrait,
            "技能伤害破魔": MessengerSkillDamagePierceTrait,
        },
    ))
