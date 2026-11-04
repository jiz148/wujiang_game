"""Shared battlefield geometry for next-five heroes."""

from __future__ import annotations

from typing import Iterable
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.heroes.common import dedupe_positions
from wujiang.tactical.heroes.common import pattern_signature


ALL_DIRECTIONS: list[tuple[int, int]] = [
    (-1, -1),
    (-1, 0),
    (-1, 1),
    (0, -1),
    (0, 1),
    (1, -1),
    (1, 0),
    (1, 1),
]

ORTHOGONAL_DIRECTIONS: list[tuple[int, int]] = [(-1, 0), (1, 0), (0, -1), (0, 1)]


def position_key(cell: Position) -> tuple[int, int]:
    return (cell.x, cell.y)


def positions_connected(cells: Iterable[Position]) -> bool:
    keys = {position_key(cell) for cell in cells}
    if not keys:
        return False
    queue = [next(iter(keys))]
    seen = {queue[0]}
    while queue:
        x, y = queue.pop(0)
        for dx, dy in ORTHOGONAL_DIRECTIONS:
            nxt = (x + dx, y + dy)
            if nxt not in keys or nxt in seen:
                continue
            seen.add(nxt)
            queue.append(nxt)
    return seen == keys


def nearby_rectangle_patterns(
    battle: Battle,
    actor: HeroUnit,
    width: int,
    height: int,
) -> list[list[Position]]:
    actor_cells = battle.unit_cells(actor)
    actor_keys = {position_key(cell) for cell in actor_cells}
    patterns: list[list[Position]] = []
    seen: set[tuple[tuple[int, int], ...]] = set()
    for start_x in range(1 - width, battle.width):
        for start_y in range(1 - height, battle.height):
            cells: list[Position] = []
            touches_actor = False
            for dx in range(width):
                for dy in range(height):
                    cell = Position(start_x + dx, start_y + dy)
                    if not battle.in_bounds(cell):
                        continue
                    if position_key(cell) in actor_keys:
                        continue
                    cells.append(cell)
                    if actor_cells and battle.unit_distance_to_cell(actor, cell) <= 1:
                        touches_actor = True
            if not cells or not touches_actor:
                continue
            deduped = dedupe_positions(cells)
            key = pattern_signature(deduped)
            if key in seen:
                continue
            seen.add(key)
            patterns.append(deduped)
    patterns.sort(key=pattern_signature)
    return patterns


def square_around_cells(battle: Battle, cells: Iterable[Position], radius: int) -> list[Position]:
    result: list[Position] = []
    seen: set[tuple[int, int]] = set()
    for origin in cells:
        for x in range(origin.x - radius, origin.x + radius + 1):
            for y in range(origin.y - radius, origin.y + radius + 1):
                cell = Position(x, y)
                key = position_key(cell)
                if key in seen or not battle.in_bounds(cell):
                    continue
                seen.add(key)
                result.append(cell)
    result.sort(key=lambda cell: (cell.y, cell.x))
    return result


def impact_area(battle: Battle, center: Position, radius: int = 1) -> list[Position]:
    return [
        Position(x, y)
        for x in range(center.x - radius, center.x + radius + 1)
        for y in range(center.y - radius, center.y + radius + 1)
        if battle.in_bounds(Position(x, y))
    ]


