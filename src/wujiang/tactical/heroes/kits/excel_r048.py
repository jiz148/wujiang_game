"""R048 exact source-fragment assembly."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.photon_lang import (
    PhotonDriveSkill, PhotonSanctuarySkill, PhotonizeSkill,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r048",
        skill_fragments={
            "￥光子化（3轮；攻+2；不受到伤害）": PhotonizeSkill,
            "光子推进（2轮一次；移动到有单位为止；下个进攻回合不能移动）": PhotonDriveSkill,
            "光子圣域（一回合一次；己方单位移动到此单位周围）": PhotonSanctuarySkill,
        },
    ))
