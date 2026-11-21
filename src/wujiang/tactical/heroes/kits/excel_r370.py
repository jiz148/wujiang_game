"""R370 source-fragment assembly."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.ash_spirit import (
    ActivateEmberSkill, AshPointTrait, EmberSkill, FireCoverSkill,
    ShadowlessFistSkill, SunChainSkill,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r370",
        skill_fragments={
            "无影拳（一回合一次；对身前5*5所有单位攻击一次，之后可以选择其中任意2个单位使用“炎阳索”）": ShadowlessFistSkill,
            "炎阳索（2轮一次；一回合内最多对2个单位使用“炎阳索”；对周围任意2个单位使用，被命中单位不能移动或使用位移技能，在每个己方回合结束时受到1/4伤害，2轮）": SunChainSkill,
            "烈火罩（4轮一次；2轮；每个己方回合结束时对周围造成3的伤害；并且增加2点额外血量，每次收到技能伤害时，优先扣除额外血量，但不会抵挡普攻伤害，只要额外血量还存在此单位不受到技能的伤害以外的效果影响）": FireCoverSkill,
            "残焰（拿掉一个“灰烬计数点”发动；范5；在目标地点放置一个“残焰”）": EmberSkill,
            "激活残焰（随时使用；移动到“残焰”位置，破坏那个“残焰”，并对周围造成伤害）": ActivateEmberSkill,
        },
        traits={
            "在战斗开始时给此单位放置3个“灰烬计数点”": AshPointTrait,
        },
    ))
