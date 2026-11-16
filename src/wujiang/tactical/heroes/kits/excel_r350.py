"""R350 source fragments bind Delongkula's owned skills and traits."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.dragon_blood_delongkula import (
    AncientDragonHuntTrait, AncientDragonKillTrait, DragonSpearSkill,
    FearReflectionSkill, MeleeBloodCurseTrait,
)
from wujiang.tactical.heroes.mechanics.li import AttackLifeStealTrait


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r350",
        skills={"击龙枪": DragonSpearSkill, "恐惧倒影": FearReflectionSkill},
        traits={
            "攻击和技能对“古龙”武将和其召唤物破魔，普攻吸血": AncientDragonHuntTrait,
            "破坏一个“古龙”武将时，攻+1，守+1": AncientDragonKillTrait,
            "所有对此单位造成近战伤害的单位受到一下效果：每个己方回合结束时血*1/2": MeleeBloodCurseTrait,
        },
        extra_trait_factories=(AttackLifeStealTrait,),
    ))
