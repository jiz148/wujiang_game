from __future__ import annotations

import random
from typing import Callable

from wujiang.engine.core import Battle, Position
from wujiang.heroes.first_five import Bard, DarkHuman, EliteSoldier, Ellie, FireFuneral
from wujiang.heroes.next_five import ElementHunter, RockGod, UndeadKingLina


HeroFactory = Callable[[int], object]


HERO_REGISTRY: dict[str, HeroFactory] = {
    "ellie": Ellie,
    "dark_human": DarkHuman,
    "fire_funeral": FireFuneral,
    "elite_soldier": EliteSoldier,
    "bard": Bard,
    "element_hunter": ElementHunter,
    "undead_king_lina": UndeadKingLina,
    "rock_god": RockGod,
}

CLASSIC_BATTLE_MODE = "classic"
RANDOM_HERO_BATTLE_MODE = "random"
CLASSIC_BOARD_SIZE = (8, 8)
RANDOM_BOARD_SIZE = (10, 10)


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


def start_order_key(unit: object) -> tuple[float, float, float, float, float, float, float]:
    return (
        float(unit.stat("speed")),
        float(unit.level),
        float(unit.stat("attack")),
        float(unit.stat("defense")),
        float(unit.stat("speed")),
        float(unit.stat("attack_range")),
        float(unit.stat("mana")),
    )


def opening_player_for_units(unit1: object, unit2: object) -> int:
    if start_order_key(unit2) > start_order_key(unit1):
        return 2
    return 1


def random_mode_spawn_positions(width: int, height: int, unit1: object | None = None, unit2: object | None = None) -> tuple[Position, Position]:
    def valid_rows(unit: object | None) -> list[int]:
        footprint_height = int(getattr(unit, "footprint_height", 1) or 1)
        return list(range(max(1, height - footprint_height + 1)))

    def valid_columns(columns: list[int], unit: object | None) -> list[int]:
        footprint_width = int(getattr(unit, "footprint_width", 1) or 1)
        return [x for x in columns if x + footprint_width <= width]

    band_width = max(2, width // 4)
    left_columns = valid_columns(list(range(1, min(width - 1, 1 + band_width))), unit1)
    right_start = max(1, width - 1 - band_width)
    right_columns = valid_columns(list(range(right_start, width - 1)), unit2)
    return (
        Position(random.choice(left_columns), random.choice(valid_rows(unit1))),
        Position(random.choice(right_columns), random.choice(valid_rows(unit2))),
    )


def create_battle(hero1_code: str, hero2_code: str, *, mode: str = CLASSIC_BATTLE_MODE) -> Battle:
    normalized_mode = str(mode or CLASSIC_BATTLE_MODE).strip().lower()
    width, height = RANDOM_BOARD_SIZE if normalized_mode == RANDOM_HERO_BATTLE_MODE else CLASSIC_BOARD_SIZE
    battle = Battle(width=width, height=height)
    hero1 = create_hero(hero1_code, 1)
    hero2 = create_hero(hero2_code, 2)
    if normalized_mode == RANDOM_HERO_BATTLE_MODE:
        player1_spawn, player2_spawn = random_mode_spawn_positions(battle.width, battle.height, hero1, hero2)
        battle.add_unit(hero1, player1_spawn)
        battle.add_unit(hero2, player2_spawn)
        battle.active_player = opening_player_for_units(hero1, hero2)
    else:
        battle.add_unit(hero1, Position(1, battle.height // 2))
        battle.add_unit(hero2, Position(battle.width - 2, battle.height // 2))
    battle.start_battle()
    return battle
