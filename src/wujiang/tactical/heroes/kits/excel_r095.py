"""R095 exact source-fragment assembly."""

from wujiang.tactical.heroes.dream_demon import CalmSkill, EnterDreamSkill, YawnSkill
from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r095",
        skill_fragments={
            "镇定【1（使一个单位除了魔以外回到原始能力值）": CalmSkill,
            "哈欠【1（使一个单位不能移动；每个此单位的攻击回合开始时回合血+1/4，魔-1，此单位魔+1，直到任意一方被普攻击中；不叠加）": YawnSkill,
            "入梦（开关技能；一回合一次，仅可在回合开始时使用；不能使用“哈欠”，守变为1，攻+2，范+2，速+2；对“哈欠”状态的目标技能和普攻破魔）": EnterDreamSkill,
        },
    ))
