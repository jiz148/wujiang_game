"""R376 source-fragment assembly."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.ice_eye_rosita import (
    FrostVenomLockSkill, IceEyeMarkTrait, RositaAIPolicy, RositaChainPullSkill,
    RositaDamageWard, RositaRemotePierceSkill,
)


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r376",
        skill_fragments={
            "极寒毒锁（2轮一次；直到下个己方回合开始，因对方单位的伤害被破坏后，那个单位在同一回合结束时受到那个单位原本的守+1的伤害）": FrostVenomLockSkill,
            "锁链": RositaChainPullSkill,
            "远程穿刺": RositaRemotePierceSkill,
        },
        traits={
            "此单位受到的伤害大于此单位的守的场合，那次伤害-此单位的守": RositaDamageWard,
            "被此单位的攻击或技能命中的单位被放置一个“冰眼计数点”，被放置8个以上的单位在当回合受到其原本守+1的伤害": IceEyeMarkTrait,
        },
        ai_policy=RositaAIPolicy(),
    ))
