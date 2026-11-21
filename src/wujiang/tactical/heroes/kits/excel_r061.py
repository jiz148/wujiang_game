"""R061 exact source-fragment assembly."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.leopard_man import BeastRushSkill, CompanionTravelTrait


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r061",
        skill_fragments={"¥兽族奔袭（移动6格;回合结束之前此单位周围的对方受到的伤害+1）": BeastRushSkill},
        traits={"任意己方单位可以对此单位在范6的情况下使用“结伴而行”（一回合一次；瞬移到那个“豹头人”周围任意位置）": CompanionTravelTrait},
    ))
