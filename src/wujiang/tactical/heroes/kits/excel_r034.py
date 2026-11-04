"""Source-preserving object assembly for excel_r034 / 樱火."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.floating_cannon import FloatingCannonBerserkSkill, FloatingCannonCoverSkill, FloatingCannonsSkill, SakuraFloatingCannonTrait


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r034",
        skills={
            "浮游炮*4": FloatingCannonsSkill,
            "浮游炮狂暴化": FloatingCannonBerserkSkill,
            "浮游炮掩护": FloatingCannonCoverSkill,
        },
        extra_trait_factories=(SakuraFloatingCannonTrait,),
    ))
