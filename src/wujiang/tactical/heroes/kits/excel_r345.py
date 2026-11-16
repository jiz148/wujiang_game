"""R345's source fragments bind to Shi Jin and the Tianwei guardian."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.guardian_tianwei import SummonTianweiSkill
from wujiang.tactical.heroes.mechanics.guardian_spirit import GuardianSummonerTrait


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r345",
        skills={"守护灵": SummonTianweiSkill},
        traits={
            "除“守护灵。天微”外所有主动技能均必须由此单为召唤的“守护灵。天微”使用":
                lambda: GuardianSummonerTrait("guardian_tianwei"),
        },
    ))
