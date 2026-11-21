"""R046 exact source-fragment assembly."""

from wujiang.tactical.heroes.dwarf_mage import IceWindSkill, LargeIceSpikeSkill, SummonIceBearsSkill
from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.snow_woman import IceWallSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r046",
        skill_fragments={
            "冰刺（大）": LargeIceSpikeSkill,
            "冰墙": IceWallSkill,
            "￥冰熊*2（攻4守5速4范1；可被“矮人法师”乘骑；乘骑此单位的单位自然回魔）": SummonIceBearsSkill,
            "冰风（一回合一次；身前2*3；被击中单位速-2，到1）": IceWindSkill,
        },
    ))
