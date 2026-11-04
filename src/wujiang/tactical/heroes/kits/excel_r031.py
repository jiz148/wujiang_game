"""Source-preserving object assembly for excel_r031 / 夏目."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.ai_score_policies import NatsumeMovePolicy
from wujiang.tactical.heroes.mechanics.natsume import NatsumeAllyAttackManaTrait, NatsumeDispelSkill, NatsumeWindWallSkill, NatsumeWindWordSkill, NatsumeShensuSkill


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r031",
        ai_policy=NatsumeMovePolicy(),
        skills={
            "神速": NatsumeShensuSkill,
            "风之语": NatsumeWindWordSkill,
            "风壁": NatsumeWindWallSkill,
            "驱散": NatsumeDispelSkill,
        },
        traits={
            "攻击己方加魔，并且在那个单位上施加一次“风壁计数点”": NatsumeAllyAttackManaTrait,
        },
    ))
