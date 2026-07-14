from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable, Sequence

from wujiang.engine.core import Battle, Position
from wujiang.heroes.excel_roster import EXCEL_HERO_REGISTRY, IMPLEMENTED_EXCEL_HERO_CODES
from wujiang.heroes.first_five import Bard, DarkHuman, EliteSoldier, Ellie, FireFuneral
from wujiang.heroes.next_five import BloodEater, Chanter, DoomlightDragon, DragonRider, ElementHunter, ErasureApostle, Jade, Li, Masamune, N, RockGod, SoulWraith, UndeadKingLina
from wujiang.heroes.strategy_soldiers import (
    StrategyArcher,
    StrategyCavalry,
    StrategyEtherScout,
    StrategyGarrison,
    StrategyInfantry,
    StrategyMountainSoldier,
    StrategyWallEngineer,
)


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
    "doomlight_dragon": DoomlightDragon,
    "masamune": Masamune,
    "jade": Jade,
    "n": N,
    "blood_eater": BloodEater,
    "li": Li,
    "chanter": Chanter,
    "erasure_apostle": ErasureApostle,
    "dragon_rider": DragonRider,
    "soul_wraith": SoulWraith,
    "strategy_infantry": StrategyInfantry,
    "strategy_garrison": StrategyGarrison,
    "strategy_archer": StrategyArcher,
    "strategy_cavalry": StrategyCavalry,
    "strategy_mountain_soldier": StrategyMountainSoldier,
    "strategy_ether_scout": StrategyEtherScout,
    "strategy_wall_engineer": StrategyWallEngineer,
}
HERO_REGISTRY.update(EXCEL_HERO_REGISTRY)

CLASSIC_BATTLE_MODE = "classic"
RANDOM_HERO_BATTLE_MODE = "random"
CLASSIC_BOARD_BASE_SIDE = 8
CLASSIC_SPAWN_GAP = 1
LEGACY_DUEL_BOARD_SIZE = 8


@dataclass(frozen=True, slots=True)
class RoomBattleEntry:
    hero_code: str
    player_id: int
    owner_seat_id: int


def create_hero(hero_code: str, player_id: int):
    if hero_code not in HERO_REGISTRY:
        raise KeyError(f"未知武将: {hero_code}")
    return HERO_REGISTRY[hero_code](player_id)


def list_heroes() -> list[dict[str, object]]:
    result = []
    for code, factory in HERO_REGISTRY.items():
        if code.startswith("strategy_"):
            continue
        if code.startswith("excel_") and code not in IMPLEMENTED_EXCEL_HERO_CODES:
            continue
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


def normalize_hero_roster(hero_codes: str | Sequence[str]) -> list[str]:
    if isinstance(hero_codes, str):
        normalized = [str(hero_codes).strip()]
    else:
        normalized = [str(code).strip() for code in hero_codes]
    return [code for code in normalized if code]


def start_order_key(unit: object, *, tie_breaker: float = 0.0) -> tuple[float, float, float, float, float, float, float]:
    return (
        float(unit.stat("speed")),
        float(unit.level),
        float(unit.stat("attack")),
        float(unit.stat("defense")),
        float(unit.stat("attack_range")),
        -float(unit.stat("mana")),
        float(tie_breaker),
    )


def opening_player_for_units(unit1: object, unit2: object) -> int:
    if start_order_key(unit2, tie_breaker=random.random()) > start_order_key(unit1, tie_breaker=random.random()):
        return 2
    return 1


def entry_footprint_offsets(unit: object | None) -> list[tuple[int, int]]:
    if unit is None:
        return [(0, 0)]
    offsets = getattr(unit, "entry_footprint_offsets", None)
    if offsets:
        return [(int(dx), int(dy)) for dx, dy in offsets]
    width = int(getattr(unit, "entry_footprint_width", getattr(unit, "footprint_width", 1)) or 1)
    height = int(getattr(unit, "entry_footprint_height", getattr(unit, "footprint_height", 1)) or 1)
    min_dx = int(getattr(unit, "entry_footprint_min_dx", 0) or 0)
    min_dy = int(getattr(unit, "entry_footprint_min_dy", 0) or 0)
    return [
        (min_dx + dx, min_dy + dy)
        for dy in range(height)
        for dx in range(width)
    ]


def entry_footprint_bounds(unit: object | None) -> dict[str, int]:
    offsets = entry_footprint_offsets(unit)
    min_dx = min(dx for dx, _ in offsets)
    max_dx = max(dx for dx, _ in offsets)
    min_dy = min(dy for _, dy in offsets)
    max_dy = max(dy for _, dy in offsets)
    return {
        "min_dx": min_dx,
        "max_dx": max_dx,
        "min_dy": min_dy,
        "max_dy": max_dy,
        "width": max_dx - min_dx + 1,
        "height": max_dy - min_dy + 1,
    }


def sort_units_for_classic(units: Sequence[object]) -> list[object]:
    tiebreaks = {id(unit): random.random() for unit in units}
    return sorted(units, key=lambda unit: start_order_key(unit, tie_breaker=tiebreaks[id(unit)]), reverse=True)


def side_requirements(units: Sequence[object]) -> tuple[int, int]:
    if not units:
        return 1, 0
    bounds_list = [entry_footprint_bounds(unit) for unit in units]
    max_width = max(bounds["width"] for bounds in bounds_list)
    total_height = sum(bounds["height"] for bounds in bounds_list)
    total_height += CLASSIC_SPAWN_GAP * max(0, len(bounds_list) - 1)
    return max_width, total_height


def random_side_occupied_bands(
    board_side: int,
    player1_units: Sequence[object],
    player2_units: Sequence[object],
) -> dict[int, tuple[int, int]]:
    left_width, _ = side_requirements(player1_units)
    right_width, _ = side_requirements(player2_units)
    spare_columns = max(0, board_side - (left_width + right_width + 4))
    left_band_width = left_width + spare_columns // 2
    right_band_width = right_width + (spare_columns - spare_columns // 2)
    left_min = 1
    left_max = min(board_side - 2, left_min + left_band_width - 1)
    right_max = board_side - 2
    right_min = max(1, right_max - right_band_width + 1)
    return {
        1: (left_min, left_max),
        2: (right_min, right_max),
    }


def spawn_cells_for_anchor(unit: object, anchor: Position) -> set[tuple[int, int]]:
    return {
        (anchor.x + dx, anchor.y + dy)
        for dx, dy in entry_footprint_offsets(unit)
    }


def random_side_spawn_positions(
    units: Sequence[object],
    board_side: int,
    *,
    occupied_min_x: int,
    occupied_max_x: int,
) -> dict[int, Position]:
    if not units:
        return {}

    def placement_key(unit: object) -> tuple[int, int, int]:
        bounds = entry_footprint_bounds(unit)
        area = bounds["width"] * bounds["height"]
        return area, bounds["height"], bounds["width"]

    def valid_anchors(unit: object, occupied_cells: set[tuple[int, int]]) -> list[Position]:
        bounds = entry_footprint_bounds(unit)
        min_anchor_x = occupied_min_x - bounds["min_dx"]
        max_anchor_x = occupied_max_x - bounds["max_dx"]
        min_anchor_y = 0 - bounds["min_dy"]
        max_anchor_y = board_side - 1 - bounds["max_dy"]
        candidates: list[Position] = []
        for x in range(min_anchor_x, max_anchor_x + 1):
            for y in range(min_anchor_y, max_anchor_y + 1):
                anchor = Position(x, y)
                if spawn_cells_for_anchor(unit, anchor) & occupied_cells:
                    continue
                candidates.append(anchor)
        return candidates

    ordered_units = sorted(units, key=placement_key, reverse=True)
    for _ in range(48):
        occupied_cells: set[tuple[int, int]] = set()
        positions: dict[int, Position] = {}
        attempt_units = sorted(
            ordered_units,
            key=lambda unit: (*placement_key(unit), random.random()),
            reverse=True,
        )
        success = True
        for unit in attempt_units:
            candidates = valid_anchors(unit, occupied_cells)
            if not candidates:
                success = False
                break
            anchor = random.choice(candidates)
            positions[id(unit)] = anchor
            occupied_cells.update(spawn_cells_for_anchor(unit, anchor))
        if success:
            return positions
    raise ValueError("Random mode could not generate legal spawn positions for the current roster.")


def random_mode_spawn_positions(
    player1_units: Sequence[object],
    player2_units: Sequence[object],
    board_side: int,
) -> tuple[dict[int, Position], dict[int, Position]]:
    occupied_bands = random_side_occupied_bands(board_side, player1_units, player2_units)
    player1_positions = random_side_spawn_positions(
        player1_units,
        board_side,
        occupied_min_x=occupied_bands[1][0],
        occupied_max_x=occupied_bands[1][1],
    )
    player2_positions = random_side_spawn_positions(
        player2_units,
        board_side,
        occupied_min_x=occupied_bands[2][0],
        occupied_max_x=occupied_bands[2][1],
    )
    return player1_positions, player2_positions


def legacy_duel_spawn_positions(unit1: object, unit2: object) -> tuple[Position, Position]:
    board_side = LEGACY_DUEL_BOARD_SIZE
    bounds1 = entry_footprint_bounds(unit1)
    bounds2 = entry_footprint_bounds(unit2)
    return (
        Position(1 - bounds1["min_dx"], 4 - bounds1["min_dy"]),
        Position(board_side - 2 - bounds2["min_dx"], 4 - bounds2["min_dy"]),
    )


def classic_board_side(player1_units: Sequence[object], player2_units: Sequence[object]) -> int:
    max_roster = max(len(player1_units), len(player2_units), 1)
    base_side = max(CLASSIC_BOARD_BASE_SIDE, 2 * max_roster + 6)

    left_width, left_height = side_requirements(player1_units)
    right_width, right_height = side_requirements(player2_units)
    required_width = left_width + right_width + 4
    required_height = max(left_height, right_height) + 2
    return max(base_side, required_width, required_height)


def classic_spawn_positions(units: Sequence[object], board_side: int, *, player_id: int) -> dict[int, Position]:
    if not units:
        return {}
    bounds_list = [entry_footprint_bounds(unit) for unit in units]
    total_height = sum(bounds["height"] for bounds in bounds_list)
    total_height += CLASSIC_SPAWN_GAP * max(0, len(bounds_list) - 1)
    top_y = max(1, (board_side - total_height) // 2)
    positions: dict[int, Position] = {}
    cursor_y = top_y
    for unit, bounds in zip(units, bounds_list):
        if player_id == 1:
            anchor_x = 1 - bounds["min_dx"]
        else:
            anchor_x = board_side - 2 - bounds["max_dx"]
        anchor_y = cursor_y - bounds["min_dy"]
        positions[id(unit)] = Position(anchor_x, anchor_y)
        cursor_y += bounds["height"] + CLASSIC_SPAWN_GAP
    return positions


def interleaved_classic_turn_order(player1_units: Sequence[object], player2_units: Sequence[object]) -> list[object]:
    if not player1_units and not player2_units:
        return []
    opening_player = 1
    if player1_units and player2_units:
        opening_player = opening_player_for_units(player1_units[0], player2_units[0])
    first = player1_units if opening_player == 1 else player2_units
    second = player2_units if opening_player == 1 else player1_units
    turn_order: list[object] = []
    for index in range(max(len(first), len(second))):
        if index < len(first):
            turn_order.append(first[index])
        if index < len(second):
            turn_order.append(second[index])
    return turn_order


def create_classic_battle(hero1_codes: Sequence[str], hero2_codes: Sequence[str]) -> Battle:
    player1_units = [create_hero(code, 1) for code in hero1_codes]
    player2_units = [create_hero(code, 2) for code in hero2_codes]
    sorted_player1 = sort_units_for_classic(player1_units)
    sorted_player2 = sort_units_for_classic(player2_units)
    board_side = classic_board_side(sorted_player1, sorted_player2)
    battle = Battle(width=board_side, height=board_side)
    player1_positions = classic_spawn_positions(sorted_player1, board_side, player_id=1)
    player2_positions = classic_spawn_positions(sorted_player2, board_side, player_id=2)
    for unit in sorted_player1:
        battle.add_unit(unit, player1_positions[id(unit)])
    for unit in sorted_player2:
        battle.add_unit(unit, player2_positions[id(unit)])
    turn_order = interleaved_classic_turn_order(sorted_player1, sorted_player2)
    battle.configure_turn_order([unit.unit_id for unit in turn_order], starting_index=0)
    battle.start_battle()
    return battle


def create_legacy_duel_battle(hero1_code: str, hero2_code: str) -> Battle:
    battle = Battle(width=LEGACY_DUEL_BOARD_SIZE, height=LEGACY_DUEL_BOARD_SIZE)
    battle.legacy_player_turn_mode = True
    hero1 = create_hero(hero1_code, 1)
    hero2 = create_hero(hero2_code, 2)
    player1_spawn, player2_spawn = legacy_duel_spawn_positions(hero1, hero2)
    battle.add_unit(hero1, player1_spawn)
    battle.add_unit(hero2, player2_spawn)
    battle.configure_turn_order([hero1.unit_id, hero2.unit_id], starting_index=0)
    battle.start_battle()
    return battle


def create_random_battle(hero1_codes: Sequence[str], hero2_codes: Sequence[str]) -> Battle:
    player1_units = [create_hero(code, 1) for code in hero1_codes]
    player2_units = [create_hero(code, 2) for code in hero2_codes]
    sorted_player1 = sort_units_for_classic(player1_units)
    sorted_player2 = sort_units_for_classic(player2_units)
    board_side = classic_board_side(sorted_player1, sorted_player2)
    battle = Battle(width=board_side, height=board_side)
    player1_positions, player2_positions = random_mode_spawn_positions(sorted_player1, sorted_player2, board_side)
    for unit in sorted_player1:
        battle.add_unit(unit, player1_positions[id(unit)])
    for unit in sorted_player2:
        battle.add_unit(unit, player2_positions[id(unit)])
    turn_order = [unit.unit_id for unit in interleaved_classic_turn_order(sorted_player1, sorted_player2)]
    battle.configure_turn_order(turn_order, starting_index=0)
    battle.start_battle()
    return battle


def _create_units_for_room_entries(entries: Sequence[RoomBattleEntry], expected_player_id: int) -> list[object]:
    units: list[object] = []
    for entry in entries:
        if int(entry.player_id) != expected_player_id:
            raise ValueError(f"Room battle entry team mismatch: expected {expected_player_id}, got {entry.player_id}.")
        unit = create_hero(entry.hero_code, expected_player_id)
        unit.owner_seat_id = int(entry.owner_seat_id)
        units.append(unit)
    return units


def create_room_battle(
    player1_entries: Sequence[RoomBattleEntry],
    player2_entries: Sequence[RoomBattleEntry],
    *,
    mode: str = CLASSIC_BATTLE_MODE,
) -> Battle:
    normalized_mode = str(mode or CLASSIC_BATTLE_MODE).strip().lower()
    player1_units = _create_units_for_room_entries(player1_entries, 1)
    player2_units = _create_units_for_room_entries(player2_entries, 2)
    if not player1_units or not player2_units:
        raise ValueError("Room battle requires both teams to have at least one hero.")

    sorted_player1 = sort_units_for_classic(player1_units)
    sorted_player2 = sort_units_for_classic(player2_units)
    board_side = classic_board_side(sorted_player1, sorted_player2)
    battle = Battle(width=board_side, height=board_side)
    if normalized_mode == RANDOM_HERO_BATTLE_MODE:
        player1_positions, player2_positions = random_mode_spawn_positions(sorted_player1, sorted_player2, board_side)
    else:
        player1_positions = classic_spawn_positions(sorted_player1, board_side, player_id=1)
        player2_positions = classic_spawn_positions(sorted_player2, board_side, player_id=2)
    for unit in sorted_player1:
        battle.add_unit(unit, player1_positions[id(unit)])
    for unit in sorted_player2:
        battle.add_unit(unit, player2_positions[id(unit)])
    turn_order = [unit.unit_id for unit in interleaved_classic_turn_order(sorted_player1, sorted_player2)]
    battle.configure_turn_order(turn_order, starting_index=0)
    battle.start_battle()
    return battle


def create_battle(
    hero1_code: str | Sequence[str],
    hero2_code: str | Sequence[str],
    *,
    mode: str = CLASSIC_BATTLE_MODE,
) -> Battle:
    normalized_mode = str(mode or CLASSIC_BATTLE_MODE).strip().lower()
    if normalized_mode == RANDOM_HERO_BATTLE_MODE:
        roster1 = normalize_hero_roster(hero1_code)
        roster2 = normalize_hero_roster(hero2_code)
        if not roster1 or not roster2:
            raise ValueError("随机模式需要双方各至少 1 个武将。")
        return create_random_battle(roster1, roster2)
    if isinstance(hero1_code, str) and isinstance(hero2_code, str):
        hero1 = str(hero1_code).strip()
        hero2 = str(hero2_code).strip()
        if not hero1 or not hero2:
            raise ValueError("标准模式需要双方各至少 1 个武将。")
        return create_legacy_duel_battle(hero1, hero2)
    roster1 = normalize_hero_roster(hero1_code)
    roster2 = normalize_hero_roster(hero2_code)
    if not roster1 or not roster2:
        raise ValueError("标准模式需要双方各至少 1 个武将。")
    return create_classic_battle(roster1, roster2)
