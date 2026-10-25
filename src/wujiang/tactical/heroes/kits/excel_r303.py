"""Source-preserving object assembly for excel_r303 / 昼行武者."""

from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.daylight_hunter import DaylightHunterTrait


def install(r) -> None:
    register_hero_kit(FragmentMappedHeroKit(
        "excel_r303",
        traits={
            "此单位每回合只能普攻一个单位": DaylightHunterTrait,
        },
        extra_trait_factories=(r.AttackLifeStealTrait,),
    ))
