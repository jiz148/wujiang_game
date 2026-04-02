from __future__ import annotations

from typing import Callable

from wujiang.engine.core import Battle, Position
from wujiang.heroes.first_five import Bard, DarkHuman, EliteSoldier, Ellie, FireFuneral


HeroFactory = Callable[[int], object]


HERO_REGISTRY: dict[str, HeroFactory] = {
    "ellie": Ellie,
    "dark_human": DarkHuman,
    "fire_funeral": FireFuneral,
    "elite_soldier": EliteSoldier,
    "bard": Bard,
}


def create_hero(hero_code: str, player_id: int):
    if hero_code not in HERO_REGISTRY:
        raise KeyError(f"未知武将: {hero_code}")
    return HERO_REGISTRY[hero_code](player_id)


def list_heroes() -> list[dict[str, object]]:
    result = []
    for code, factory in HERO_REGISTRY.items():
        unit = factory(1)
        result.append(
            {
                "code": code,
                "name": unit.name,
                "role": unit.role,
                "attribute": unit.attribute,
                "race": unit.race,
                "level": unit.level,
                "stats": unit.base_stats.to_dict(),
                "raw_skill_text": unit.raw_skill_text,
                "raw_trait_text": unit.raw_trait_text,
            }
        )
    return result


def create_battle(hero1_code: str, hero2_code: str) -> Battle:
    battle = Battle(width=8, height=8)
    hero1 = create_hero(hero1_code, 1)
    hero2 = create_hero(hero2_code, 2)
    battle.add_unit(hero1, Position(1, battle.height // 2))
    battle.add_unit(hero2, Position(battle.width - 2, battle.height // 2))
    battle.start_battle()
    return battle
