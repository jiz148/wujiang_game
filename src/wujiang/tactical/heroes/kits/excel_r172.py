"""Source-preserving object assembly for excel_r172 / 甲虫装甲骑士."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.beetle import (
    BeetleArmorDeploySkill,
    BeetleFullForceSkill,
    BeetleSpearSkill,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r172",
        skills={
            "兵刺": BeetleSpearSkill,
            "全力攻击": BeetleFullForceSkill,
            "铠甲部署": BeetleArmorDeploySkill,
        },
    ))
