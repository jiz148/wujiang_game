from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable, Sequence

from wujiang.tactical.engine.army import is_army_soldier
from wujiang.tactical.engine.core import Battle, Position
from wujiang.tactical.heroes.excel_roster import EXCEL_HERO_REGISTRY, IMPLEMENTED_EXCEL_HERO_CODES
from wujiang.tactical.heroes.first_five import Bard, DarkHuman, EliteSoldier, Ellie, FireFuneral
from wujiang.tactical.heroes.next_five import BloodEater, Chanter, DoomlightDragon, DragonRider, ElementHunter, ErasureApostle, Jade, Li, Masamune, N, RockGod, SoulWraith, UndeadKingLina
from wujiang.tactical.heroes.strategy_soldiers import (
    StrategyArcher,
    StrategyCavalry,
    StrategyEtherScout,
    StrategyGarrison,
    StrategyInfantry,
    StrategyMountainSoldier,
    StrategyWallEngineer,
    StrategySnowGhost,
    StrategyArrowTower,
    StrategySiegeCannon,
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
    "strategy_snow_ghost": StrategySnowGhost,
    "strategy_arrow_tower": StrategyArrowTower,
    "strategy_cannon": StrategySiegeCannon,
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


def _stack_requirements(units: Sequence[object]) -> tuple[int, int]:
    if not units:
        return 1, 0
    bounds_list = [entry_footprint_bounds(unit) for unit in units]
    max_width = max(bounds["width"] for bounds in bounds_list)
    total_height = sum(bounds["height"] for bounds in bounds_list)
    total_height += CLASSIC_SPAWN_GAP * max(0, len(bounds_list) - 1)
    return max_width, total_height


def side_requirements(units: Sequence[object]) -> tuple[int, int]:
    if not units:
        return 1, 0
    heroes = [unit for unit in units if not is_army_soldier(unit)]
    soldiers = [unit for unit in units if is_army_soldier(unit)]
    if heroes and soldiers:
        hero_width, hero_height = _stack_requirements(heroes)
        soldier_width, soldier_height = _stack_requirements(soldiers)
        return hero_width + soldier_width, max(hero_height, soldier_height)
    return _stack_requirements(units)


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


def _spread_target_ys(count: int, height: int) -> list[float]:
    if count <= 0:
        return []
    if count == 1:
        return [(max(0, height) - 1) / 2]
    last = max(0, int(height) - 1)
    return [index * last / (count - 1) for index in range(count)]


def _preferred_spawn_x_rank(item: Position, *, prefer_high_x: bool, toward_enemy: bool) -> int:
    if toward_enemy:
        return item.x if prefer_high_x else -item.x
    return -item.x if prefer_high_x else item.x


def choose_spread_spawn_anchor(
    candidates: Sequence[Position],
    *,
    prefer_high_x: bool,
    toward_enemy: bool,
    target_y: float,
) -> Position:
    def x_rank(item: Position) -> int:
        return _preferred_spawn_x_rank(item, prefer_high_x=prefer_high_x, toward_enemy=toward_enemy)

    best = min(x_rank(item) for item in candidates)
    front = [item for item in candidates if x_rank(item) <= best + 1]
    pool = front or list(candidates)
    return min(pool, key=lambda item: (abs(item.y - target_y), x_rank(item), item.x, item.y))


def random_side_spawn_positions(
    units: Sequence[object],
    board_side: int,
    *,
    occupied_min_x: int,
    occupied_max_x: int,
    board_height: int | None = None,
) -> dict[int, Position]:
    if not units:
        return {}
    height = board_side if board_height is None else int(board_height)

    def placement_key(unit: object) -> tuple[int, int, int]:
        bounds = entry_footprint_bounds(unit)
        area = bounds["width"] * bounds["height"]
        return area, bounds["height"], bounds["width"]

    def valid_anchors(unit: object, occupied_cells: set[tuple[int, int]]) -> list[Position]:
        bounds = entry_footprint_bounds(unit)
        min_anchor_x = occupied_min_x - bounds["min_dx"]
        max_anchor_x = occupied_max_x - bounds["max_dx"]
        min_anchor_y = 0 - bounds["min_dy"]
        max_anchor_y = height - 1 - bounds["max_dy"]
        candidates: list[Position] = []
        for x in range(min_anchor_x, max_anchor_x + 1):
            for y in range(min_anchor_y, max_anchor_y + 1):
                anchor = Position(x, y)
                if spawn_cells_for_anchor(unit, anchor) & occupied_cells:
                    continue
                candidates.append(anchor)
        return candidates

    ordered_units = sorted(units, key=placement_key, reverse=True)
    home_is_high_x = occupied_min_x + occupied_max_x >= board_side
    for _ in range(48):
        occupied_cells: set[tuple[int, int]] = set()
        positions: dict[int, Position] = {}
        heroes = [unit for unit in ordered_units if not is_army_soldier(unit)]
        soldiers = [unit for unit in ordered_units if is_army_soldier(unit)]
        random.shuffle(heroes)
        random.shuffle(soldiers)
        hero_ys = _spread_target_ys(len(heroes), height)
        soldier_ys = _spread_target_ys(len(soldiers), height)
        attempt_units = [
            (unit, hero_ys[index], True)
            for index, unit in enumerate(heroes)
        ] + [
            (unit, soldier_ys[index], False)
            for index, unit in enumerate(soldiers)
        ]
        success = True
        for unit, target_y, toward_enemy in attempt_units:
            candidates = valid_anchors(unit, occupied_cells)
            if not candidates:
                success = False
                break
            jitter = random.uniform(-1.25, 1.25) if height > 2 else 0.0
            anchor = choose_spread_spawn_anchor(
                candidates,
                prefer_high_x=home_is_high_x,
                toward_enemy=toward_enemy,
                target_y=target_y + jitter,
            )
            positions[id(unit)] = anchor
            occupied_cells.update(spawn_cells_for_anchor(unit, anchor))
        if success:
            return positions
    raise ValueError("Random mode could not generate legal spawn positions for the current roster.")


def random_mode_spawn_positions(
    player1_units: Sequence[object],
    player2_units: Sequence[object],
    board_side: int,
    *,
    board_height: int | None = None,
) -> tuple[dict[int, Position], dict[int, Position]]:
    occupied_bands = random_side_occupied_bands(board_side, player1_units, player2_units)
    player1_positions = random_side_spawn_positions(
        player1_units,
        board_side,
        occupied_min_x=occupied_bands[1][0],
        occupied_max_x=occupied_bands[1][1],
        board_height=board_height,
    )
    player2_positions = random_side_spawn_positions(
        player2_units,
        board_side,
        occupied_min_x=occupied_bands[2][0],
        occupied_max_x=occupied_bands[2][1],
        board_height=board_height,
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


def _column_overflows(units: Sequence[object], height: int) -> bool:
    heroes = [unit for unit in units if not is_army_soldier(unit)]
    soldiers = [unit for unit in units if is_army_soldier(unit)]
    groups = [heroes, soldiers] if heroes and soldiers else [list(units)]
    limit = max(1, int(height))
    for group in groups:
        if not group:
            continue
        if sum(entry_footprint_bounds(unit)["height"] for unit in group) > limit:
            return True
    return False


def packed_spawn_positions(
    units: Sequence[object],
    board_side: int,
    *,
    occupied_min_x: int,
    occupied_max_x: int,
    board_height: int | None = None,
    prefer_high_x: bool = False,
) -> dict[int, Position]:
    if not units:
        return {}
    height = board_side if board_height is None else int(board_height)

    def placement_key(unit: object) -> tuple[int, int, int]:
        bounds = entry_footprint_bounds(unit)
        return bounds["width"] * bounds["height"], bounds["height"], bounds["width"]

    def valid_anchors(unit: object, occupied_cells: set[tuple[int, int]]) -> list[Position]:
        bounds = entry_footprint_bounds(unit)
        min_anchor_x = occupied_min_x - bounds["min_dx"]
        max_anchor_x = occupied_max_x - bounds["max_dx"]
        min_anchor_y = 0 - bounds["min_dy"]
        max_anchor_y = height - 1 - bounds["max_dy"]
        candidates: list[Position] = []
        for x in range(min_anchor_x, max_anchor_x + 1):
            for y in range(min_anchor_y, max_anchor_y + 1):
                anchor = Position(x, y)
                if spawn_cells_for_anchor(unit, anchor) & occupied_cells:
                    continue
                candidates.append(anchor)
        return candidates

    occupied_cells: set[tuple[int, int]] = set()
    positions: dict[int, Position] = {}
    heroes = [unit for unit in units if not is_army_soldier(unit)]
    soldiers = [unit for unit in units if is_army_soldier(unit)]
    hero_ys = _spread_target_ys(len(heroes), height)
    soldier_ys = _spread_target_ys(len(soldiers), height)
    random.shuffle(hero_ys)
    random.shuffle(soldier_ys)
    placement_order = [
        (unit, hero_ys[index], True)
        for index, unit in enumerate(sorted(heroes, key=placement_key, reverse=True))
    ] + [
        (unit, soldier_ys[index], False)
        for index, unit in enumerate(sorted(soldiers, key=placement_key, reverse=True))
    ]
    for unit, target_y, toward_enemy in placement_order:
        candidates = valid_anchors(unit, occupied_cells)
        if not candidates:
            raise ValueError("当前战场放不下这些武将，请把战场调大后再开局。")
        anchor = choose_spread_spawn_anchor(
            candidates,
            prefer_high_x=prefer_high_x,
            toward_enemy=toward_enemy,
            target_y=target_y,
        )
        positions[id(unit)] = anchor
        occupied_cells.update(spawn_cells_for_anchor(unit, anchor))
    return positions


def _classic_column_positions(
    units: Sequence[object],
    board_side: int,
    height: int,
    *,
    player_id: int,
    column_x: int,
) -> dict[int, Position]:
    if not units:
        return {}
    bounds_list = [entry_footprint_bounds(unit) for unit in units]
    body_height = sum(bounds["height"] for bounds in bounds_list)
    gap = CLASSIC_SPAWN_GAP
    if body_height + gap * max(0, len(bounds_list) - 1) > height:
        gap = 0
    total_height = body_height + gap * max(0, len(bounds_list) - 1)
    top_y = max(0, (height - total_height) // 2)
    positions: dict[int, Position] = {}
    cursor_y = top_y
    for unit, bounds in zip(units, bounds_list):
        if player_id == 1:
            anchor_x = column_x - bounds["min_dx"]
        else:
            anchor_x = column_x - bounds["max_dx"]
        anchor_x = max(-bounds["min_dx"], min(anchor_x, board_side - 1 - bounds["max_dx"]))
        anchor_y = cursor_y - bounds["min_dy"]
        anchor_y = max(-bounds["min_dy"], min(anchor_y, height - 1 - bounds["max_dy"]))
        positions[id(unit)] = Position(anchor_x, anchor_y)
        cursor_y += bounds["height"] + gap
    return positions


def classic_spawn_positions(
    units: Sequence[object],
    board_side: int,
    *,
    player_id: int,
    board_height: int | None = None,
) -> dict[int, Position]:
    if not units:
        return {}
    height = board_side if board_height is None else int(board_height)
    heroes = [unit for unit in units if not is_army_soldier(unit)]
    soldiers = [unit for unit in units if is_army_soldier(unit)]
    if player_id == 1:
        front_x, back_x = (2, 0) if heroes and soldiers and board_side >= 6 else (1, 0)
    else:
        front_x, back_x = (
            (board_side - 3, board_side - 1)
            if heroes and soldiers and board_side >= 6
            else (board_side - 2, board_side - 1)
        )
    if heroes and soldiers:
        positions = _classic_column_positions(heroes, board_side, height, player_id=player_id, column_x=front_x)
        positions.update(
            _classic_column_positions(soldiers, board_side, height, player_id=player_id, column_x=back_x)
        )
        return positions
    column_x = 1 if player_id == 1 else board_side - 2
    return _classic_column_positions(units, board_side, height, player_id=player_id, column_x=column_x)


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
    turn_order = interleaved_classic_turn_order(
        [unit for unit in sorted_player1 if not is_army_soldier(unit)],
        [unit for unit in sorted_player2 if not is_army_soldier(unit)],
    )
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
    turn_order = [
        unit.unit_id
        for unit in interleaved_classic_turn_order(
            [unit for unit in sorted_player1 if not is_army_soldier(unit)],
            [unit for unit in sorted_player2 if not is_army_soldier(unit)],
        )
    ]
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
    board_width: int | None = None,
    board_height: int | None = None,
    turn_timeout_limit: int | None = None,
    turn_timeout_winner: int | None = None,
) -> Battle:
    normalized_mode = str(mode or CLASSIC_BATTLE_MODE).strip().lower()
    player1_units = _create_units_for_room_entries(player1_entries, 1)
    player2_units = _create_units_for_room_entries(player2_entries, 2)
    if not player1_units or not player2_units:
        raise ValueError("Room battle requires both teams to have at least one hero.")

    sorted_player1 = sort_units_for_classic(player1_units)
    sorted_player2 = sort_units_for_classic(player2_units)
    width = int(board_width) if board_width else classic_board_side(sorted_player1, sorted_player2)
    height = int(board_height) if board_height else width
    battle = Battle(width=width, height=height)
    if normalized_mode == RANDOM_HERO_BATTLE_MODE:
        player1_positions, player2_positions = random_mode_spawn_positions(
            sorted_player1,
            sorted_player2,
            width,
            board_height=height,
        )
    elif _column_overflows(sorted_player1, height) or _column_overflows(sorted_player2, height):
        band = max(4, min(8, width // 4))
        player1_positions = packed_spawn_positions(
            sorted_player1,
            width,
            occupied_min_x=0,
            occupied_max_x=band - 1,
            board_height=height,
        )
        player2_positions = packed_spawn_positions(
            sorted_player2,
            width,
            occupied_min_x=width - band,
            occupied_max_x=width - 1,
            board_height=height,
            prefer_high_x=True,
        )
    else:
        player1_positions = classic_spawn_positions(sorted_player1, width, player_id=1, board_height=height)
        player2_positions = classic_spawn_positions(sorted_player2, width, player_id=2, board_height=height)
    for unit in sorted_player1:
        battle.add_unit(unit, player1_positions[id(unit)])
    for unit in sorted_player2:
        battle.add_unit(unit, player2_positions[id(unit)])
    turn_order = [
        unit.unit_id
        for unit in interleaved_classic_turn_order(
            [unit for unit in sorted_player1 if not is_army_soldier(unit)],
            [unit for unit in sorted_player2 if not is_army_soldier(unit)],
        )
    ]
    battle.configure_turn_order(turn_order, starting_index=0)
    if turn_timeout_limit is not None:
        battle.turn_timeout_limit = max(1, int(turn_timeout_limit))
    if turn_timeout_winner in {1, 2}:
        battle.turn_timeout_winner = int(turn_timeout_winner)
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
