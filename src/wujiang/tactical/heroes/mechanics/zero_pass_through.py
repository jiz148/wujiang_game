"""Zero dash and pass-through mechanics."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.heroes.common import payload_position
from wujiang.tactical.heroes.mechanics.remi_bat import _payload_direction
from wujiang.tactical.heroes.mechanics.next_five_geometry import ALL_DIRECTIONS


class ZeroDashSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "zero_dash",
            "冲刺",
            "普通技能：每回合最多 1 次；向指定方向直线移动恰好 8 格，可穿过单位。",
            max_uses_per_turn=1,
            target_mode="cell",
            direction_mode="required",
        )

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.cannot_move:
            return False, "无法移动时不能使用冲刺。"
        return True, ""

    def reaction_window_timing(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> str:
        return "after"

    def destination_for_direction(self, battle: Battle, actor: HeroUnit, direction: tuple[int, int]) -> Position:
        if actor.position is None:
            raise ActionError("单位不在战场上。")
        return actor.position.offset(direction[0] * 8, direction[1] * 8)

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        destination = self.destination_for_direction(battle, actor, _payload_direction(payload))
        if payload_position(payload) != destination:
            raise ActionError("冲刺必须选择该方向的第8格。")
        battle.find_path(actor, destination, max_distance=8, exact_distance=8, straight_only=True, ignore_units=True)
        return {}

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        destination = self.destination_for_direction(battle, actor, _payload_direction(payload))
        return battle.find_path(actor, destination, max_distance=8, exact_distance=8, straight_only=True, ignore_units=True)[1:]

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        direction = _payload_direction(payload)
        destination = self.destination_for_direction(battle, actor, direction)
        actor.position = getattr(actor, "_resolution_actual_position", None) or actor.position
        battle.move_unit(
            actor,
            destination,
            via_skill=True,
            straight_only=True,
            ignore_units=True,
            max_distance=8,
            exact_distance=8,
            tags={"zero_dash"},
        )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        cells: list[Position] = []
        if actor.cannot_move:
            return {"cells": [], "target_unit_ids": [], "secondary_cells": [], "requires_target": True}
        for direction in ALL_DIRECTIONS:
            try:
                destination = self.destination_for_direction(battle, actor, direction)
                battle.find_path(actor, destination, max_distance=8, exact_distance=8, straight_only=True, ignore_units=True)
            except ActionError:
                continue
            cells.append(destination)
        return {"cells": [cell.to_dict() for cell in cells], "target_unit_ids": [], "secondary_cells": [], "requires_target": True}


class PassThroughDamageTrait(Trait):
    def __init__(self) -> None:
        super().__init__("穿人有伤害", "移动结束后，路径每次穿过单位所在格分别造成一次可连锁的伤害。")

    def bind(self, owner: HeroUnit) -> "PassThroughDamageTrait":
        super().bind(owner)
        owner.ignore_units_while_moving = True
        return self

    def on_unit_moved(self, battle: Battle, ctx: Any) -> None:
        owner = self.owner
        if owner is None or ctx.unit.unit_id != owner.unit_id or len(ctx.path) < 2:
            return
        crossings = battle.path_crossing_events(owner, ctx.path)
        for index, (unit, cell) in enumerate(crossings):
            battle.queue_skill_effect_action(
                actor=owner,
                display_name="穿人伤害",
                effect_code="pass_through_damage",
                payload={
                    "cells": [cell.to_dict()],
                    "attack_power": owner.stat("attack"),
                    "pass_through_damage": True,
                    "refresh_cell_targets": True,
                },
                target_cells=[cell],
                segment_index=index,
                segment_count=len(crossings),
            )


class ZeroPassThroughManaTrait(Trait):
    def __init__(self) -> None:
        super().__init__("穿人魔+0.5", "每次穿过单位获得 0.5 魔。")

    def on_unit_moved(self, battle: Battle, ctx: Any) -> None:
        owner = self.owner
        if owner is None or ctx.unit.unit_id != owner.unit_id or len(ctx.path) < 2:
            return
        for unit in battle.path_crossing_units(owner, ctx.path):
            gained = owner.gain_mana(0.5)
            if gained:
                battle.log(f"{owner.name} 穿过 {unit.name}，获得 {gained} 点魔。")


