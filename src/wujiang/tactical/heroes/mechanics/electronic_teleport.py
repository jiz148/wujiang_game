"""Electronic teleport, satellite cannon, and wing movement."""

from __future__ import annotations
from wujiang.tactical.settlement_reuse import AreaDamageSpec, independent_area_skill

from wujiang.tactical.heroes.ai_candidate_plan import DAMAGE_DEDUPED_RANKED_CANDIDATES

from typing import Any
import os
from wujiang.tactical.engine.geometry import clipped_rectangle_coordinates
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.engine.core import Unit
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.heroes.common import match_payload_pattern
from wujiang.tactical.heroes.common import payload_cells, pattern_signature
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


@independent_area_skill('r23')
class SatelliteCannonSkill(Skill):
    ai_payload_value_owned = True

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        try:
            cells = self.chosen_cells(battle, payload)
        except (ActionError, KeyError, TypeError, ValueError):
            return -1000.0
        score = -8.0
        for target in battle.effect_units_at_cells(cells):
            if target.magic_immunity:
                continue
            power = actor.stat("attack") + max(0, battle.unit_hit_count_for_cells(target, cells) - 1)
            damage = min(target.current_hp, max(0.0, battle.damage_rule.calculate_damage(
                power, target.stat("defense"))))
            impact = damage * 100.0 + (20.0 if target.total_shields() > 0 else 0.0)
            score += impact if target.player_id != actor.player_id else -impact * 1.6
        return score

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        ranked = []
        for cells in self.patterns(battle):
            payload = {"type": "skill", "unit_id": actor.unit_id,
                       "skill_code": self.code, "cells": positions_to_dict(cells)}
            value = self.ai_payload_value(battle, actor, payload)
            if value > 0:
                ranked.append((value, payload))
        ranked.sort(key=lambda entry: entry[0], reverse=True)
        return [payload for _, payload in ranked[:8]]

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit,
                                  payload: dict[str, Any], targets: list[Unit] | None = None,
                                  shared: Any = None, action: dict[str, Any] | None = None) -> bool:
        try:
            cells = self.chosen_cells(battle, payload)
        except (ActionError, KeyError, TypeError, ValueError):
            return False
        for target in battle.effect_units_at_cells(cells):
            if target.player_id == actor.player_id or target.magic_immunity:
                continue
            power = actor.stat("attack") + max(0, battle.unit_hit_count_for_cells(target, cells) - 1)
            if target.total_shields() > 0 or target.dodge_charges > 0 or battle.damage_rule.calculate_damage(
                    power, target.stat("defense")) > 0:
                return True
        return False

    def ai_direct_area_patterns(self, battle: Battle, actor: Unit):
        return self.patterns(battle)

    def ai_direct_area_spec(self, battle: Battle, actor: Unit, payload: dict[str, Any], cells=None) -> AreaDamageSpec:
        return AreaDamageSpec(cells if cells is not None else tuple(self.chosen_cells(battle, payload)),
            {'attack_power':actor.stat('attack'),'is_skill':True,'action_name':self.name,
             'tags':{'skill',self.code}})
    ai_candidate_plan = DAMAGE_DEDUPED_RANKED_CANDIDATES
    target_cells_are_geometry_only = True

    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.paid_skill_value()

    def __init__(self) -> None:
        super().__init__("satellite_cannon", "卫星加农炮",
                         "每回合一次，在战场任意位置选择3*3区域，按当前攻对区域内双方单位造成伤害。",
                         max_uses_per_turn=1, target_mode="cell")

    def patterns(self, battle: Battle) -> list[list[Position]]:
        coordinates = (clipped_rectangle_coordinates(battle.width, battle.height, 3, 3)
                       if os.environ.get('WUJIANG_AI_FIXED_GEOMETRY', '1') != '0' else None)
        if coordinates is not None:
            return [[Position(x, y) for x, y in row] for row in coordinates]
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
        if (getattr(self.patterns, '__func__', None) is _SATELLITE_PATTERNS[0]
                and _SATELLITE_PATTERNS[0].__code__ is _SATELLITE_PATTERNS[1]
                and os.environ.get('WUJIANG_AI_FIXED_GEOMETRY', '1') != '0'):
            key = pattern_signature(payload_cells(payload))
            coordinates = clipped_rectangle_coordinates(battle.width, battle.height, 3, 3, selection=key)
            if coordinates is not None:
                if not coordinates:
                    raise ActionError('所选格子不符合该技能的形状要求。')
                return [Position(x, y) for x, y in coordinates]
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


_SATELLITE_PATTERNS = SatelliteCannonSkill.patterns, SatelliteCannonSkill.patterns.__code__


class ElectronicWingMovementTrait(Trait):
    def __init__(self) -> None:
        super().__init__("电子翼连续移动", "每本人回合可多次正常移动，累计距离不超过当前速。")

    def modify_normal_move_actions_per_turn(self, value: int) -> int:
        return 1_000_000

    def allows_split_normal_movement(self, battle: Battle, unit: Unit) -> bool:
        return True


