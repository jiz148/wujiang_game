"""R038 exact source-fragment assembly."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.heracles import (
    EarthFissureSkill, HeavyChargeSkill, HeraclesRangedGuard, HeraclesWrathReset,
    TwelveTrialsSkill, WrathSlashSkill,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r038",
        skill_fragments={
            "地裂【1.5（2*6；对此技能进行连锁的单位直到下个回合结束前速-2，到1；无视魔免）": EarthFissureSkill,
            "重磅冲撞（一回合一次；尽量直线移动2格；给予周围伤害；伤害=此单位的守；无视魔免）": HeavyChargeSkill,
            "¥怒斩（3*4；无视魔免）": WrathSlashSkill,
            "￥十二试炼（使用后此单位获得以下特性：每破坏一次，原地除此技能重置以外满状态复活，能力值重置；计数＋1；当计数为11时此技能无效；每次复活后会随机上升3点能力值）": TwelveTrialsSkill,
        },
        traits={
            "此单位受到的远程伤害－1": HeraclesRangedGuard,
            "普攻重置“怒斩”": HeraclesWrathReset,
        },
    ))
