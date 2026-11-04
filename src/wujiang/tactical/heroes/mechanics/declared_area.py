"""Reusable declared-area skill behavior."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.heroes.common import match_payload_pattern
from wujiang.tactical.heroes.common import positions_to_dict


class DeclaredAreaSkillMixin:
    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        cells = match_payload_pattern(payload, self.patterns(battle, actor))
        return {
            "declared_area_cells": positions_to_dict(cells),
            "declared_enemy_target_ids": [unit.unit_id for unit in battle.effect_units_at_cells(cells) if unit.player_id != actor.player_id],
        }

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return battle.payload_positions(payload, "declared_area_cells") or match_payload_pattern(payload, self.patterns(battle, actor))


