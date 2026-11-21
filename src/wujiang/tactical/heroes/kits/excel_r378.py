"""R378 source-fragment assembly for the updated workbook text."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.wolf_doctor_xinyue import (
    MindSupplySkill, SecretMedicineSkill, WerewolfDiceTrait, WerewolfRecoveryTrait,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r378",
        skill_fragments={
            "秘制药剂（一回合一次；破魔；血+3/4）": SecretMedicineSkill,
            "心灵补给（一回合一次；无法被回避；魔+3）": MindSupplySkill,
        },
        traits={
            "每回合开始时投骰子，如果是奇数，则此单位的种族变为兽人，持续1轮": WerewolfDiceTrait,
            "此单位的种族为兽人时，攻守速+2，范变为1，所有回复效果变为等量的减血/减魔": WerewolfRecoveryTrait,
        },
    ))
