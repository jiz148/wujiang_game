"""Reusable terrain-unit and terrain-destruction helpers."""

from __future__ import annotations

from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.heroes.next_five import position_key


def _terrain_unit(unit: HeroUnit) -> bool:
    return bool(
        getattr(unit, "world_seed_terrain", False)
        or getattr(unit, "is_terrain", False)
        or getattr(unit, "role", "") == "地形单位"
    )


def _destroy_terrain_in_cells(battle: Battle, actor: HeroUnit, cells: list[Position], action_name: str) -> None:
    cell_keys = {position_key(cell) for cell in cells}
    destroyed_names: list[str] = []
    for x, y in sorted(cell_keys):
        if (x, y) not in battle.blocked_cells:
            continue
        battle.blocked_cells.remove((x, y))
        destroyed_names.append(f"墙体({x},{y})")
    for unit in list(battle.units_at_cells(cells)):
        if not _terrain_unit(unit):
            continue
        unit.alive = False
        destroyed_names.append(unit.name)
    for effect in list(battle.field_effects):
        if not getattr(effect, "is_terrain", False):
            continue
        if any(position_key(cell) in cell_keys for cell in effect.affected_cells(battle)):
            destroyed_names.append(effect.name)
            battle.remove_field_effect(effect)
    if destroyed_names:
        battle.log(f"{actor.name} 的【{action_name}】破坏了地形：{'、'.join(dict.fromkeys(destroyed_names))}。")
        battle.cleanup_dead_units()


