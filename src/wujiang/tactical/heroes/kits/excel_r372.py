"""R372 source-fragment assembly."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.void_spirit import (
    DarkRemnantSkill, PhaseSkill, ResonancePulseSkill, VoidPathSkill, VoidPointTrait,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r372",
        skill_fragments={
            "残阴（放置一个“残阴”；“残阴”有朝向；3轮一次；持续3轮；在此单位回合结束时在“残阴”朝向3格内第一个对方单位受到伤害及以下破魔效果：向“残阴”尽量移动并且直到下回合结束前不能移动）": DarkRemnantSkill,
            "异化（3轮一次；随时使用；此单位消失，并且在当回合结束时在消失位置周围9*9内出现，并对周围造成伤害）": PhaseSkill,
            "共鸣脉冲（3轮一次；对周围5*5造成伤害；每有一个单位为对象此单位增加额外血量1/4，直到下回合前受到普攻伤害时优先扣除额外血量，只要额外血量还存在此单位不受到普攻的伤害以外的效果影响，额外血量不会抵挡技能伤害）": ResonancePulseSkill,
            "太虚之径（拿掉一个“太虚计数点”发动；直线穿人移动5格，对经过的单位造成伤害）": VoidPathSkill,
        },
        traits={
            "在战斗开始时给此单位放置2个“太虚计数点”": VoidPointTrait,
        },
    ))
