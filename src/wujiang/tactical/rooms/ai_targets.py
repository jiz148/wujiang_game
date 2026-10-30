"""Small target queries shared by hero AI policies and the tactical scorer."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wujiang.tactical.engine.core import Battle, Unit


def living_hostile_combatants(battle: Battle, player_id: int) -> list[Unit]:
    return [
        unit
        for unit in battle.enemy_units(player_id)
        if unit.alive
        and unit.position is not None
        and not unit.banished
        and not bool(getattr(unit, "is_siege_structure", False))
    ]
