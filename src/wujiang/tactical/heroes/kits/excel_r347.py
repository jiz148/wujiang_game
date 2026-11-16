"""R347 source fragments bind Pei Xuan and his Dizheng guardian."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.guardian_dizheng import SummonDizhengSkill
from wujiang.tactical.heroes.mechanics.guardian_spirit import GuardianSummonerTrait


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r347",
        skills={"守护灵": SummonDizhengSkill},
        traits={
            "除“守护灵。地正”外所有主动技能均必须由此单为召唤的“守护灵。地正”使用":
                lambda: GuardianSummonerTrait("guardian_dizheng"),
        },
    ))
