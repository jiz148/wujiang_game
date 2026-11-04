"""Source-preserving object assembly for excel_r356 / 强袭犀牛."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.rhino import RhinoDeathTrait


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r356",
        traits={
            "此单位被破坏后场上所有己方单位攻+1，守+1，同名武将效果不叠加，3轮": RhinoDeathTrait,
        },
    ))
