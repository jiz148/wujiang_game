"""Bounded caches of integer geometry. No units, rules, RNG or mutable state."""
from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=4096)
def body_coordinates(x: int, y: int, offsets: tuple[tuple[int, int], ...]) -> tuple[tuple[int, int], ...]:
    return tuple((x + dx, y + dy) for dx, dy in offsets)


@lru_cache(maxsize=4096)
def neighbor_coordinates(width: int, height: int, x: int, y: int) -> tuple[tuple[int, int], ...]:
    return tuple((x + dx, y + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                 if (dx or dy) and 0 <= x + dx < width and 0 <= y + dy < height)


def _range_coordinates(width: int, height: int, x: int, y: int,
                       offsets: tuple[tuple[int, int], ...], distance: int) -> tuple[tuple[int, int], ...]:
    if not offsets:
        # Match the original min(empty) query when the board contains cells.
        if width > 0 and height > 0:
            min(())
        return ()
    return tuple((cx, cy) for cy in range(height) for cx in range(width)
                 if min(max(abs(x + dx - cx), abs(y + dy - cy)) for dx, dy in offsets) <= distance)


_cached_range_coordinates = lru_cache(maxsize=128)(_range_coordinates)


def range_coordinates(width: int, height: int, x: int, y: int,
                      offsets: tuple[tuple[int, int], ...], distance: int) -> tuple[tuple[int, int], ...]:
    # Bound retained area as well as entry count; huge custom boards still work.
    query = _cached_range_coordinates if width * height <= 4096 else _range_coordinates
    return query(width, height, x, y, offsets, distance)


def _pattern_budget(width, height, work):
    return (type(width) is int and type(height) is int and width > 0 and height > 0
            and width * height <= 1024 and work <= 65536)


@lru_cache(maxsize=16)
def _rectangle_templates(board_width, board_height, width, height):
    grid = {cell: cell for cell in ((x, y) for x in range(board_width) for y in range(board_height))}
    patterns, seen = [], set()
    for start_x in range(1 - width, board_width):
        for start_y in range(1 - height, board_height):
            bounds = (max(0, start_x), min(board_width, start_x + width),
                      max(0, start_y), min(board_height, start_y + height))
            if bounds in seen:
                continue
            seen.add(bounds)
            cells = tuple(grid[x, y] for x in range(bounds[0], bounds[1]) for y in range(bounds[2], bounds[3]))
            mask = sum(1 << (y * board_width + x) for x, y in cells)
            patterns.append((mask, cells))
    # Rectangle cells are already unique and sorted by (x, y), exactly the
    # original pattern_signature. First clipped occurrence has the same order.
    return tuple(sorted(patterns, key=lambda pattern: pattern[1]))


@lru_cache(maxsize=64)
def _rectangle_patterns(board_width, board_height, width, height, range_mask):
    return tuple(cells for mask, cells in _rectangle_templates(board_width, board_height, width, height)
                 if mask & range_mask)


def rectangle_pattern_coordinates(board_width, board_height, width, height, in_range):
    """Immutable exact templates, filtered by the caller's freshly read range.

    None requests the original path for unusual types or unbounded retention.
    No actor, battle, Position or caller-owned container enters either cache.
    """
    if any(type(value) is not int or value <= 0 for value in (board_width, board_height, width, height)):
        return None
    work = (board_width + width - 1) * (board_height + height - 1) * min(board_width, width) * min(board_height, height)
    if not _pattern_budget(board_width, board_height, work):
        return None
    if any(type(x) is not int or type(y) is not int for x, y in in_range):
        return None
    range_mask = sum(1 << (y * board_width + x) for x, y in in_range
                     if 0 <= x < board_width and 0 <= y < board_height)
    return _rectangle_patterns(board_width, board_height, width, height, range_mask)


@lru_cache(maxsize=16)
def _line_templates(width, height, directions, length):
    grid = {cell: cell for cell in ((x, y) for x in range(width) for y in range(height))}
    patterns, seen = [], set()
    for dx, dy in directions:
        for start_x in range(-length + 1, width):
            for start_y in range(-length + 1, height):
                cells = []
                for step in range(length):
                    x, y = start_x + dx * step, start_y + dy * step
                    if 0 <= x < width and 0 <= y < height:
                        cells.append(grid[x, y])
                if not cells:
                    continue
                # Range, touch and origin predicates depend only on the cell
                # set. Deduplicate now, retaining the first direction's order.
                ordered = tuple(dict.fromkeys(cells))
                signature = tuple(sorted(ordered))
                if signature not in seen:
                    seen.add(signature)
                    patterns.append((signature, ordered))
    return tuple(cells for _, cells in sorted(patterns, key=lambda pattern: pattern[0]))


@lru_cache(maxsize=64)
def _localized_line_patterns(width, height, x, y, directions, length, radius, touch_distance):
    patterns = []
    for cells in _line_templates(width, height, directions, length):
        if (x, y) in cells:
            continue
        distances = tuple(max(abs(x - cx), abs(y - cy)) for cx, cy in cells)
        if any(distance > radius for distance in distances):
            continue
        if touch_distance is not None and not any(distance <= touch_distance for distance in distances):
            continue
        patterns.append(cells)
    return tuple(patterns)


def localized_line_coordinates(width, height, x, y, directions, length, radius, touch_distance):
    if (any(type(value) is not int for value in (width, height, x, y, length, radius))
            or length <= 0 or (touch_distance is not None and type(touch_distance) is not int)
            or type(directions) not in (list, tuple) or len(directions) > 16):
        return None
    if any(type(direction) not in (list, tuple) or len(direction) != 2
           or any(type(value) is not int for value in direction) for direction in directions):
        return None
    work = (width + length - 1) * (height + length - 1) * length * max(1, len(directions))
    if not _pattern_budget(width, height, work):
        return None
    return _localized_line_patterns(width, height, x, y, tuple(tuple(direction) for direction in directions),
                                    length, radius, touch_distance)
