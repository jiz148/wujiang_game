"""R364 exact source-fragment assembly."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.temple_archangel import (
    HolyBodySkill, HolySongSkill, LargeHardenSkill,
    LaserEyeSkill, SuperheroLandingSkill, TempleGrappleSkill,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r364",
        skill_fragments={
            "变硬（大）": LargeHardenSkill,
            "圣体（3轮一次；直到下回合结束前无敌）": HolyBodySkill,
            "Superhero。landing【1（一回合最多使用一次；飞跃身前4格；然后周围的单位受到以下破魔效果：直到下回合结束前速-3，到1）": SuperheroLandingSkill,
            "圣殿抱摔【2（一回合最多使用一次；破魔；被击中单位随着此单位尽量直线位移3格；然后受到此单位的破魔伤害）": TempleGrappleSkill,
            "￥圣歌（范5；仅可对包括此单位的己方单位使用；那个单位血魔全满；除大招外的攻击，技能使用次数重置）": HolySongSkill,
            "镭射眼（一回合一次；范5；挡住此技能的单位受到以下破魔效果：超此单位的反方向尽量移动至边界）": LaserEyeSkill,
        },
    ))
