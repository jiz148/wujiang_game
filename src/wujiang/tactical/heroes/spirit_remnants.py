"""Shared battlefield markers and point budgets for the three late Spirits."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, BattleFieldEffect, Position, Trait, Unit,
)


def square_cells(battle: Battle, center: Position, radius: int) -> list[Position]:
    return [cell for dy in range(-radius, radius + 1)
            for dx in range(-radius, radius + 1)
            if battle.in_bounds(cell := center.offset(dx, dy))]


def visible_units_at(battle: Battle, cells: list[Position], *, ignore: Unit | None = None) -> list[Unit]:
    return [unit for unit in battle.effect_units_at_cells(cells, ignore=ignore)
            if unit.alive and not unit.banished and unit.position is not None]


class SpiritRemnant(BattleFieldEffect):
    """A visible, nonblocking location; the owning skill interprets contact."""

    def __init__(self, name: str, source_unit_id: str, cell: Position,
                 *, direction: tuple[int, int] | None = None,
                 duration: int | None = None) -> None:
        super().__init__(name, f"{name}：技能可选的场地标记。", duration=duration)
        self.source_unit_id = source_unit_id
        self.cell = cell
        self.direction = direction

    def affected_cells(self, battle: Battle) -> list[Position]:
        return [self.cell]

    def board_marker(self, battle: Battle) -> str:
        return self.name[-1]

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data["source_unit_id"] = self.source_unit_id
        if self.direction is not None:
            data["direction"] = {"dx": self.direction[0], "dy": self.direction[1]}
        return data


def remnants_for(battle: Battle, actor: Unit, name: str) -> list[SpiritRemnant]:
    return [effect for effect in battle.field_effects
            if isinstance(effect, SpiritRemnant) and effect.name == name
            and effect.source_unit_id == actor.unit_id]


def remnant_at(battle: Battle, actor: Unit, name: str,
               cell: Position) -> SpiritRemnant | None:
    return next((effect for effect in remnants_for(battle, actor, name)
                 if effect.cell == cell), None)


class SpiritPointTrait(Trait):
    """A bounded point bank; only source-listed skills can declare spending."""

    def __init__(self, name: str, capacity: int, *, gain_on_owner_only: bool,
                 spending_skill_codes: frozenset[str] = frozenset()) -> None:
        super().__init__(name, f"开局{capacity}点；合适回合初+1，最多{capacity}点。")
        self.capacity = capacity
        self.points = capacity
        self.gain_on_owner_only = gain_on_owner_only
        self.spending_skill_codes = spending_skill_codes

    def on_owner_turn_start(self, battle: Battle) -> None:
        if self.gain_on_owner_only:
            self.points = min(self.capacity, self.points + 1)

    def on_any_turn_start(self, battle: Battle, active_unit: Unit) -> None:
        if not self.gain_on_owner_only:
            self.points = min(self.capacity, self.points + 1)

    def on_owner_action_declared(self, battle: Battle, action_type: str,
                                 payload: dict[str, Any]) -> None:
        if action_type != "skill" or payload.get("skill_code") not in self.spending_skill_codes:
            return
        if self.points < 1:
            raise ActionError(f"{self.name}不足。")
        self.points -= 1

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data["points"] = self.points
        data["capacity"] = self.capacity
        return data


def spirit_points(actor: Unit, name: str) -> SpiritPointTrait:
    return next(trait for trait in actor.traits
                if isinstance(trait, SpiritPointTrait) and trait.name == name)
