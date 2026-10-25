"""Shared H1 line damage skill."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.heroes.common import line_patterns
from wujiang.tactical.heroes.common import match_payload_pattern
from wujiang.tactical.heroes.common import pattern_selection_preview
from wujiang.tactical.heroes.next_five import ALL_DIRECTIONS


class H1LineDamageSkill(Skill):
    def __init__(
        self,
        code: str,
        name: str,
        description: str,
        *,
        length: int,
        mana_cost: float = 0,
        max_uses_per_turn: int | None = None,
        cooldown_turns: int = 0,
    ) -> None:
        super().__init__(
            code,
            name,
            description,
            mana_cost=mana_cost,
            max_uses_per_turn=max_uses_per_turn,
            cooldown_turns=cooldown_turns,
            target_mode="cell",
        )
        self.length = length

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        return line_patterns(battle, actor.position, ALL_DIRECTIONS, self.length)

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def affected_units(self, battle: Battle, actor: HeroUnit, cells: list[Position]) -> list[HeroUnit]:
        return list(battle.units_at_cells(cells))  # type: ignore[return-value]

    def resolve_hit(self, battle: Battle, actor: HeroUnit, target: HeroUnit, cells: list[Position]) -> DamageContext:
        return battle.resolve_damage(
            DamageContext(
                source=actor,
                target=target,
                attack_power=actor.stat("attack"),
                is_skill=True,
                action_name=self.name,
                area_cell_hits=battle.unit_hit_count_for_cells(target, cells),
                tags={"skill", "attack", self.code},
            )
        )

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        for target in self.affected_units(battle, actor, cells):
            self.resolve_hit(battle, actor, target, cells)

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        cells = [cell for pattern in self.patterns(battle, actor) for cell in pattern]
        targets = [unit.unit_id for unit in battle.units_at_cells(cells)]
        preview.update({"target_unit_ids": list(dict.fromkeys(targets)), "secondary_cells": [], "requires_target": True})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return self.affected_units(battle, actor, self.chosen_cells(battle, actor, payload))


