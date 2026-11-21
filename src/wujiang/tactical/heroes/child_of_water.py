"""R135 Child of Water: defensive condensation and outward sea roar."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, Position, Skill, Unit,
)
from wujiang.tactical.heroes.common import StatModifierStatus, positions_to_dict


class CondensedStatus(StatModifierStatus):
    def __init__(self) -> None:
        super().__init__("凝固", defense_delta=2, range_delta=-3,
                         description="守+2、范-3；本人轮初可切换关闭。")


class CondenseSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("condense", "凝固", "每本人轮一次，只在轮初开关；开启后守+2、范-3。",
                         max_uses_per_turn=1, target_mode="self")

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.actions_taken_this_turn or actor.move_used or actor.attacks_used or actor.performed_active_skill:
            return False, "凝固只能在本人回合开始阶段切换。"
        return True, ""

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {"cells": positions_to_dict(battle.unit_cells(actor)),
                "target_unit_ids": [actor.unit_id], "secondary_cells": [], "requires_target": False}

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        enemies = [enemy for enemy in battle.enemy_units(actor.player_id)
                   if enemy.alive and enemy.position is not None]
        if not enemies:
            return -1000.0
        nearest = min(battle.distance_between_units(actor, enemy) for enemy in enemies)
        if actor.has_status("凝固"):
            return 26.0 if nearest > 3 else -1000.0
        return 30.0 if nearest <= 2 and actor.current_hp <= 0.75 else -1000.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        old = actor.get_status("凝固")
        if old is not None:
            actor.remove_status(old, battle)
            battle.log_public_event(f"{actor.name} 解除凝固。", source=actor)
        else:
            actor.add_status(CondensedStatus(), source=actor)
            battle.log_public_event(f"{actor.name} 进入凝固形态。", source=actor)


def _area(battle: Battle, actor: Unit) -> list[Position]:
    occupied = battle.unit_cells(actor)
    return [Position(x, y) for x in range(battle.width) for y in range(battle.height)
            if occupied and min(cell.distance_to(Position(x, y)) for cell in occupied) <= 5]


class DeepSeaRoarSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False
    target_cells_are_geometry_only = True

    def __init__(self) -> None:
        super().__init__("deep_sea_roar", "深海咆哮",
                         "费1魔、每本人轮一次；周围11×11敌人向外尽量强制移动4格。",
                         mana_cost=1, max_uses_per_turn=1, target_mode="none")

    def _targets(self, battle: Battle, actor: Unit) -> list[Unit]:
        cells = _area(battle, actor)
        return [unit for unit in battle.effect_units_at_cells(cells, ignore=actor)
                if unit.player_id != actor.player_id]

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return _area(battle, actor)

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return self._targets(battle, actor)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {"cells": positions_to_dict(_area(battle, actor)),
                "target_unit_ids": [unit.unit_id for unit in self._targets(battle, actor)],
                "secondary_cells": [], "requires_target": False}

    def _destination(self, battle: Battle, actor: Unit, target: Unit) -> Position | None:
        if target.position is None or actor.position is None:
            return None
        source_cell, target_cell = min(((a, b) for a in battle.unit_cells(actor)
                                        for b in battle.unit_cells(target)),
                                       key=lambda pair: (pair[0].distance_to(pair[1]), pair[1].y, pair[1].x))
        dx = 0 if target_cell.x == source_cell.x else (1 if target_cell.x > source_cell.x else -1)
        dy = 0 if target_cell.y == source_cell.y else (1 if target_cell.y > source_cell.y else -1)
        if dx == dy == 0:
            return None
        reachable = set(battle.reachable_positions(target, max_distance=4, straight_only=True))
        return next((destination for step in range(4, 0, -1)
                     if (destination := target.position.offset(dx * step, dy * step)) in reachable), None)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        score = 0.0
        for target in self._targets(battle, actor):
            if target.magic_immunity or target.total_shields() > 0 or target.dodge_charges > 0:
                continue
            destination = self._destination(battle, actor, target)
            if destination is None:
                continue
            distance = battle.distance_between_units(actor, target)
            if distance <= 3:
                score += 35.0 + 10.0 * target.position.distance_to(destination)
        return score - 15.0 if score > 0 else -1000.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        for target in self._targets(battle, actor):
            checked = battle.validate_target(actor, target, action_name=self.name, is_skill=True,
                                             is_hostile=True)
            if checked.cancelled:
                continue
            recipient = checked.target
            destination = self._destination(battle, actor, recipient)
            if destination is None:
                continue
            try:
                battle.move_unit(recipient, destination, via_skill=True, forced=True,
                                 straight_only=True, max_distance=4, tags={self.code})
                battle.record_skill_effect(recipient)
            except ActionError:
                battle.log_public_event(f"{recipient.name} 的深海咆哮位移因路径受阻落空。",
                                        source=actor, target=recipient)

