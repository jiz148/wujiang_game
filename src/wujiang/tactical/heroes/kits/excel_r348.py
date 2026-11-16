"""R348 source fragments bind Zheng Tianshou and his Diyi guardian."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.guardian_diyi import SummonDiyiSkill
from wujiang.tactical.heroes.mechanics.guardian_spirit import GuardianSummonerTrait


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r348",
        skills={"守护灵": SummonDiyiSkill},
        traits={
            "除“守护灵。地异”外所有主动技能均必须由此单为召唤的“守护灵。地异”使用":
                lambda: GuardianSummonerTrait("guardian_diyi"),
        },
    ))
