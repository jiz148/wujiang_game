"""R349 source fragments bind Shi Xiu and his Tianhui guardian."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.guardian_tianhui import SummonTianhuiSkill
from wujiang.tactical.heroes.mechanics.guardian_spirit import GuardianSummonerTrait


class ShiXiuKit(FragmentMappedHeroKit):
    def augment_skills(self, skills):
        return [skill for skill in skills if skill.code not in {"shensu", "fly_leap"}]


def install(r) -> None:
    register_hero_kit(ShiXiuKit(
        "excel_r349",
        skills={"守护灵": SummonTianhuiSkill},
        traits={
            "除“守护灵。天慧”外所有主动技能均必须由此单为召唤的“守护灵。天慧”使用":
                lambda: GuardianSummonerTrait("guardian_tianhui"),
        },
    ))
