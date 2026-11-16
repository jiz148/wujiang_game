"""Source-row assembly for the Indian-doll puppet."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.puppet_equipment import PuppetEquipSkill
from wujiang.tactical.heroes.puppet_trio import FrenzyAxeSkill, IndianDollEquipmentTrait


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r342",
        skills={"装备": PuppetEquipSkill, "狂躁之斧": FrenzyAxeSkill},
        traits={"此单位作为道具时，对被装备对象拥有以下效果：攻击次数+1，并且增加特性：对此单位使用技能的单位此回合不能再使用主动技能": IndianDollEquipmentTrait},
    ))
