"""R358 exact source-fragment assembly."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.element_hunter import RendingSkill
from wujiang.tactical.heroes.ninja_master import (
    NinjaExplosiveShurikenSkill, NinjaExtraCastTrait, NinjaGroundLotusSkill,
    NinjaMagicFluteSkill,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r358",
        skill_fragments={
            "¥撕裂": RendingSkill,
            "忍法。爆破手里剑（一回合一次；范2；伤2；被击中单位此回合内守-1，到1）": NinjaExplosiveShurikenSkill,
            "¥忍法。魔笛（使用后场上“忍术大师忍者”以外的己方刺客此回合内速+1，并且可以有一次机会使用一次已经用过的大招）": NinjaMagicFluteSkill,
            "¥忍法。坐地莲（使用后无法移动和使用位移技能，直到下个己方回合开始时，不受到伤害）": NinjaGroundLotusSkill,
        },
        traits={"在使用大招以外的技能后，当回合内可以再不费魔使用2次（不计入当回合使用次数内)": NinjaExtraCastTrait},
    ))
