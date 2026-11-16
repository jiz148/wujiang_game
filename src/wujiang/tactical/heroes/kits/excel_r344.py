"""R344's source fragments bind to the summoner/guardian pair."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.guardian_disha import SummonDishaSkill
from wujiang.tactical.heroes.mechanics.guardian_spirit import GuardianSummonerTrait


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r344",
        skills={"守护灵": SummonDishaSkill},
        traits={
            "除“守护灵。地煞”外所有主动技能均必须由此单为召唤的“守护灵。地煞”使用":
                lambda: GuardianSummonerTrait("guardian_disha"),
        },
    ))
