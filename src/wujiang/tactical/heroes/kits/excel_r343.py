"""Source-row assembly for the charming-skirt puppet."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.puppet_equipment import PuppetEquipSkill
from wujiang.tactical.heroes.puppet_trio import GorgeousDanceSkill, SkirtEquipmentTrait


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r343",
        skills={"装备": PuppetEquipSkill, "华丽狂舞": GorgeousDanceSkill},
        traits={
            "此单位作为道具时，对被装备对象拥有以下效果：速+1，范+1": SkirtEquipmentTrait,
            "并且增加特性：在回合结束时对周围造成投骰子点数/2的伤害": SkirtEquipmentTrait,
        },
    ))
