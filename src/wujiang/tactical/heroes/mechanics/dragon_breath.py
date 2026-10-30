"""Reusable dragon breath skills."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.heroes.common import match_payload_pattern
from wujiang.tactical.heroes.common import pattern_selection_preview
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.mechanics.next_five_effects import apply_piercing_status_effect
from wujiang.tactical.heroes.mechanics.next_five_effects import damage_followup_effect_applies
from wujiang.tactical.heroes.mechanics.next_five_effects import remote_rectangle_patterns
from wujiang.tactical.heroes.mechanics.next_five_geometry import nearby_rectangle_patterns


class DragonBreathSkill(Skill):
    excludes_caster_from_effect = True

    def __init__(self) -> None:
        super().__init__(
            "dragon_breath",
            "龙息",
            "普通技能：费 2 魔，每回合最多 2 次，近身选择 2*2 区域；按当前攻造成伤害。",
            mana_cost=2,
            max_uses_per_turn=2,
            target_mode="cell",
        )

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        return nearby_rectangle_patterns(battle, actor, 2, 2)

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = battle.payload_positions(payload, "declared_area_cells") if "declared_area_cells" in payload else self.chosen_cells(battle, actor, payload)
        for unit in battle.effect_units_at_cells(cells, ignore=actor):
            battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=unit,
                    attack_power=actor.stat("attack"),
                    is_skill=True,
                    action_name=self.name,
                    area_cell_hits=battle.unit_hit_count_for_cells(unit, cells),
                    tags={"skill", self.code},
                )
            )

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        return {"declared_area_cells": positions_to_dict(self.chosen_cells(battle, actor, payload))}

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        cell_keys = {(cell["x"], cell["y"]) for cell in preview["cells"]}
        targets = [
            unit.unit_id
            for unit in battle.effect_units_at_cells([Position(x, y) for x, y in cell_keys], ignore=actor)
            if battle.unit_can_be_selected(unit, actor=actor)[0]
        ]
        preview.update({"target_unit_ids": targets, "secondary_cells": [], "requires_target": True})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return battle.effect_units_at_cells(self.chosen_cells(battle, actor, payload), ignore=actor)  # type: ignore[return-value]


class RemoteDragonBreathSkill(DragonBreathSkill):
    def __init__(self) -> None:
        super().__init__()
        self.code = "remote_dragon_breath"
        self.name = "远程龙息"
        self.description = "普通技能：费 2 魔，每回合最多 2 次，远程选择 2*2 区域；按当前攻造成伤害。"

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        return remote_rectangle_patterns(battle, actor, 2, 2)


