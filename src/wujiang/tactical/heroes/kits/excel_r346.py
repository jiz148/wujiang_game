"""R346 keeps Li Kui's passive skills and transfers active casts to Tiansha."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.guardian_tiansha import SummonTianshaSkill
from wujiang.tactical.heroes.mechanics.guardian_spirit import GuardianSummonerTrait


class LiKuiKit(FragmentMappedHeroKit):
    def augment_skills(self, skills):
        return [skill for skill in skills if skill.code not in {"dragon_breath", "harden"}]


def install(r) -> None:
    register_hero_kit(LiKuiKit(
        "excel_r346",
        skills={"守护灵": SummonTianshaSkill},
        traits={
            "除“守护灵。天杀”外所有主动技能均必须由此单为召唤的“守护灵。天杀”使用":
                lambda: GuardianSummonerTrait("guardian_tiansha"),
        },
    ))
