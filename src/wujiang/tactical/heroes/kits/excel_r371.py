"""R371 source-fragment assembly."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.earth_spirit import (
    BoulderStrikeSkill, EarthPointTrait, EarthRollSkill, MagneticGripSkill,
    MagnetizeSkill, RockSkill,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r371",
        skill_fragments={
            "残岩（在目标地点放置一个“残岩”）": RockSkill,
            "巨石翻滚（一回合一次；直线移动3格，若碰到“残岩”则破坏那个“残岩”并且继续向前直线移动5格；若碰到对方单位则停在那个单位周围任意位置）": EarthRollSkill,
            "巨石冲击（一回合一次；可以已单位或者“残岩”为目标；被击中单位移动3格；若是目标是“残岩”则移动5格，如果碰到其他单位则给予伤害及以下破魔效果：速-2到1，直到下回合结束，之后破坏那个“残岩”）": BoulderStrikeSkill,
            "磁力之握（一回合一次；范5；只能已“残岩”为目标，那个“残岩”直线移动到此单位周围，若经过单位则给予伤害及以下破魔效果：直到下回合结束前不能使用主动技能）": MagneticGripSkill,
            "磁化（4轮一次：周围7*7内对方单位1轮每个己方回合结束时血-1/4，如果那个单位在此单位回合结束时周围有“残岩”则持续回合增加2，最多到8）": MagnetizeSkill,
        },
        traits={
            "在战斗开始时给此单位放置6个“大地计数点”": EarthPointTrait,
        },
    ))
