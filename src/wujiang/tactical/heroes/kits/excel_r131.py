"""R131 exact source-fragment assembly."""

from wujiang.tactical.heroes.francis import FrancisTurnMana, FrancisUnlimitedShensu, JusticeDescendsSkill, SearingArrowSkill
from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r131",
        skill_fragments={
            "神速": FrancisUnlimitedShensu,
            "【1灼热之箭（一回合最多使用一次；对击中单位给予伤害，并且带有以下破魔效果：每个己方回合结束时魔-1，血-1/8；3轮，不叠加；持续效果不给予致命伤害）": SearingArrowSkill,
            "天降正义【2（一回合最多使用一次；身前3*3，投骰子，给予与点数相同次数的伤害）": JusticeDescendsSkill,
        },
        traits={"每个己方回合结束时魔+5": FrancisTurnMana},
    ))
