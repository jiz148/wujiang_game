"""Electronic teleport, satellite cannon, and wing movement."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.engine.core import Unit
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.heroes.common import match_payload_pattern
from wujiang.tactical.heroes.common import pattern_selection_preview
from wujiang.tactical.heroes.common import positions_to_dict


class ElectronicDragonTeleportTrait(Trait):
    requires_damage_choice = True

    def __init__(self) -> None:
        super().__init__("电子龙轮末瞬移", "每个武将回合结束时，可瞬移到任意己方单位周围的合法位置。")

    def routes(self, battle: Battle, owner: Unit) -> dict[str, Position]:
        if (owner.position is None or owner.banished or owner.cannot_move
                or owner.direct_effects_blocked() or getattr(owner, "attached_to_unit_id", None)):
            return {}
        routes: dict[str, Position] = {}
        for ally in battle.player_units(owner.player_id):
            if ally.position is None or ally.banished:
                continue
            for cell in battle.unit_cells(ally):
                for destination in battle.neighbors(cell):
                    if destination == owner.position:
                        continue
                    if battle.can_place_unit(owner, destination, ignore=owner, mover=owner):
                        routes[f"{destination.x},{destination.y}"] = destination
        return routes

    def on_before_any_turn_end(self, battle: Battle) -> bool:
        owner = self.owner
        if owner is None or not owner.alive or owner.banished or owner.position is None:
            return False
        routes = self.routes(battle, owner)
        choice = battle.optional_position_choice(owner, list(routes), action_name="电子龙轮末瞬移",
                                                 kind="electronic_teleport")
        if choice not in routes:
            return False
        anchor_name = next((ally.name for ally in battle.player_units(owner.player_id)
                            if ally.position is not None and not ally.banished
                            and any(cell.distance_to(routes[choice]) == 1 for cell in battle.unit_cells(ally))),
                           owner.name)
        try:
            battle.move_unit(owner, routes[choice], forced=True, allow_anywhere=True,
                             tags={"electronic_teleport", f"source:{owner.unit_id}"})
        except ActionError:
            return False
        battle.log_public_event(f"{owner.name} 以 {anchor_name} 为支点轮末瞬移至 ({routes[choice].x},{routes[choice].y})。",
                                source=owner)
        return True


class SatelliteCannonSkill(Skill):
    target_cells_are_geometry_only = True

    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.paid_skill_value()

    def __init__(self) -> None:
        super().__init__("satellite_cannon", "卫星加农炮",
                         "每回合一次，在战场任意位置选择3*3区域，按当前攻对区域内双方单位造成伤害。",
                         max_uses_per_turn=1, target_mode="cell")

    def patterns(self, battle: Battle) -> list[list[Position]]:
        patterns: dict[tuple[Position, ...], list[Position]] = {}
        for start_y in range(-2, battle.height):
            for start_x in range(-2, battle.width):
                cells = [Position(x, y) for y in range(start_y, start_y + 3)
                         for x in range(start_x, start_x + 3)
                         if 0 <= x < battle.width and 0 <= y < battle.height]
                if cells:
                    patterns[tuple(cells)] = cells
        return list(patterns.values())

    def chosen_cells(self, battle: Battle, payload: dict[str, Any]) -> list[Position]:
        if payload.get("queued_resolution") and payload.get("satellite_cells"):
            return [Position(**cell) for cell in payload["satellite_cells"]]
        return match_payload_pattern(payload, self.patterns(battle))

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok or not payload or not payload.get("cells"):
            return ok, reason
        try:
            self.chosen_cells(battle, payload)
        except (ActionError, KeyError, TypeError, ValueError) as exc:
            return False, str(exc)
        return True, ""

    def queued_payload_metadata(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> dict[str, Any]:
        return {"satellite_cells": positions_to_dict(self.chosen_cells(battle, payload))}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Unit]:
        return []

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        battle.queue_area_damage_effect(actor=actor, display_name=self.name,
                                        cells=self.chosen_cells(battle, payload),
                                        attack_power=actor.stat("attack"),
                                        tags={"skill", self.code}, refresh_cell_targets=True)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        patterns = self.patterns(battle)
        result = pattern_selection_preview(patterns)
        result.update({"target_unit_ids": list(dict.fromkeys(
            unit.unit_id for cells in patterns for unit in battle.effect_units_at_cells(cells)
            if battle.unit_can_be_selected(unit, actor=actor)[0])),
            "secondary_cells": [], "requires_target": True})
        return result


class ElectronicWingMovementTrait(Trait):
    def __init__(self) -> None:
        super().__init__("电子翼连续移动", "每本人回合可多次正常移动，累计距离不超过当前速。")

    def modify_normal_move_actions_per_turn(self, value: int) -> int:
        return 1_000_000

    def allows_split_normal_movement(self, battle: Battle, unit: Unit) -> bool:
        return True


