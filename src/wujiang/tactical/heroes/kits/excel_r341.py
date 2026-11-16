"""Source-row assembly for the cursed-face puppet."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.puppet_equipment import PuppetEquipSkill
from wujiang.tactical.heroes.puppet_trio import CursedFaceEquipmentTrait, CurseGazeSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r341",
        skills={"装备": PuppetEquipSkill, "诅咒凝视": CurseGazeSkill},
        traits={"此单位作为道具时，对被装备对象拥有以下效果：守+1，速+1，并且增加特性：对攻击此单位的对象造成2的伤害": CursedFaceEquipmentTrait},
    ))
