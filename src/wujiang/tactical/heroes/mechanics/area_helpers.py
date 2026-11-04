"""Shared declared-area pattern and damage helpers."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.heroes.common import pattern_selection_preview
from wujiang.tactical.heroes.common import pattern_signature
from wujiang.tactical.heroes.mechanics.next_five_effects import remote_rectangle_patterns


def combined_remote_rectangle_patterns(
    battle: Battle,
    actor: HeroUnit,
    sizes: list[tuple[int, int]],
) -> list[list[Position]]:
    patterns: list[list[Position]] = []
    seen: set[tuple[tuple[int, int], ...]] = set()
    for width, height in sizes:
        for pattern in remote_rectangle_patterns(battle, actor, width, height):
            key = pattern_signature(pattern)
            if key in seen:
                continue
            seen.add(key)
            patterns.append(pattern)
    patterns.sort(key=pattern_signature)
    return patterns


def area_patterns_preview(
    battle: Battle,
    actor: HeroUnit,
    patterns: list[list[Position]],
) -> dict[str, Any]:
    preview = pattern_selection_preview(patterns)
    cell_keys = {(cell["x"], cell["y"]) for cell in preview["cells"]}
    targets = [unit.unit_id for unit in battle.effect_units_at_cells([Position(x, y) for x, y in cell_keys])
               if battle.unit_can_be_selected(unit, actor=actor)[0]]
    preview.update({"target_unit_ids": targets, "secondary_cells": [], "requires_target": True})
    return preview


def resolve_area_damage(
    battle: Battle,
    actor: HeroUnit,
    *,
    action_name: str,
    cells: list[Position],
    attack_power: float,
    tags: set[str],
    enemy_only: bool = False,
    ignore_shield: bool = False,
    half_ignore_shield: bool = False,
    ignore_magic_immunity: bool = False,
    cannot_evade: bool = False,
    raw_damage: float | None = None,
) -> tuple[set[str], set[str]]:
    original_enemy_ids: set[str] = set()
    damaged_enemy_ids: set[str] = set()
    for unit in battle.effect_units_at_cells(cells):
        if enemy_only and unit.player_id == actor.player_id:
            continue
        if unit.player_id != actor.player_id:
            original_enemy_ids.add(unit.unit_id)
        ctx = battle.resolve_damage(
            DamageContext(
                source=actor,
                target=unit,
                attack_power=attack_power,
                is_skill=True,
                action_name=action_name,
                ignore_shield=ignore_shield,
                half_ignore_shield=half_ignore_shield,
                ignore_magic_immunity=ignore_magic_immunity,
                cannot_evade=cannot_evade,
                area_cell_hits=battle.unit_hit_count_for_cells(unit, cells),
                raw_damage=raw_damage,
                tags=set(tags),
            )
        )
        if unit.player_id != actor.player_id and (ctx.actual_damage > 0 or (unit.is_clone and not unit.alive and not ctx.cancelled)):
            damaged_enemy_ids.add(unit.unit_id)
    return original_enemy_ids, damaged_enemy_ids


