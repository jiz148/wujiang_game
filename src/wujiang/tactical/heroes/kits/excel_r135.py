"""R135 exact source-fragment assembly."""

from wujiang.tactical.heroes.child_of_water import CondenseSkill, DeepSeaRoarSkill
from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.gladiator_core import WaterWaveCannonSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r135",
        skill_fragments={
            "水波炮": WaterWaveCannonSkill,
            "凝固（一回合一次；开关技能，仅可在回合开始时使用；范-3，守+2）": CondenseSkill,
            "【1深海咆哮（一回合最多使用一次；周围11*11范围内的对方单位向此单位的相反方向尽量移动4格）": DeepSeaRoarSkill,
        },
    ))
