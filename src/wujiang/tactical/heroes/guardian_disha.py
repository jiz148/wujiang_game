"""R344: Huang Xin and the Disha guardian's real action ownership."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import ActionError, Battle, Position, Skill, Stats, Trait, Unit
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.common import payload_direction, payload_position
from wujiang.tactical.heroes.mechanics.guardian_spirit import (
    GuardianTetherTrait, SummonGuardianSkill, adjacent_legal_cells, named_guardians, present,
)
from wujiang.tactical.heroes.mechanics.next_five_geometry import ALL_DIRECTIONS, square_around_cells


def _area_value(battle: Battle, actor: Unit, cells: list[Position], *,
                half_pierce: bool = False) -> float:
    score = 0.0
    for target in battle.effect_units_at_cells(cells):
        if target is actor or target.magic_immunity:
            continue
        power = actor.stat("attack") + max(0, battle.unit_hit_count_for_cells(target, cells) - 1)
        damage = battle.damage_rule.calculate_damage(power, target.stat("defense"))
        if target.total_shields() and not half_pierce:
            damage = 0
        elif target.total_shields() and half_pierce:
            damage = battle.damage_rule.calculate_damage(max(0, power - 1), target.stat("defense"))
        gain = min(target.current_hp, damage) * 100 + (12 if target.total_shields() else 0)
        score += gain * (1 if target.player_id != actor.player_id else -1.5)
    return score


class DishaGuardian(AbstractHero):
    hero_code = "guardian_disha"
    hero_name = "守护灵。地煞"
    role = "召唤物"
    attribute = "土"
    race = "守护灵"
    level = 1
    base_stats = Stats(attack=5, defense=999, speed=3, attack_range=1, mana=0)

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id, is_summon=True)

    def build_skills(self) -> list[Skill]:
        return [HeavenlyOxSitSkill(), FallingKickSkill(), StarShiftSkill()]

    def build_traits(self) -> list[Trait]:
        return [GuardianTetherTrait()]


class SummonDishaSkill(SummonGuardianSkill):
    def __init__(self) -> None:
        super().__init__("summon_guardian_disha", "守护灵。地煞", DishaGuardian)
        self.description = "每场一次；在本人周围召唤攻5、守无限、速3、范1的地煞守护灵。"


class HeavenlyOxSitSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("heavenly_ox_sit", "天牛坐地",
                         "每回合一次；以守护灵为中心的5×5内，除自身外双方单位受到当前攻的技能伤。",
                         max_uses_per_turn=1, target_mode="self")

    def area(self, battle: Battle, actor: Unit) -> list[Position]:
        return [cell for cell in square_around_cells(battle, battle.unit_cells(actor), radius=2)
                if cell not in battle.unit_cells(actor)]

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        cells = self.area(battle, actor)
        return {"cells": [cell.to_dict() for cell in cells],
                "target_unit_ids": [unit.unit_id for unit in battle.effect_units_at_cells(cells)],
                "secondary_cells": [], "requires_target": False}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return self.area(battle, actor)

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return battle.effect_units_at_cells(self.area(battle, actor))

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        battle.queue_area_damage_effect(actor=actor, display_name=self.name, cells=self.area(battle, actor),
                                        attack_power=actor.stat("attack"), exclude_actor=True,
                                        refresh_cell_targets=True, tags={"skill", self.code})

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float | None:
        return _area_value(battle, actor, self.area(battle, actor)) - 18

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload) or -100


class FallingKickSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False
    is_displacement_skill = True
    target_cells_are_geometry_only = True

    def __init__(self) -> None:
        super().__init__("falling_kick", "落天踢",
                         "每回合一次；直线恰移动4格，再独立选一方向造成当前攻的半破魔技能伤。",
                         max_uses_per_turn=1, target_mode="cell")

    def available_cells(self, battle: Battle, actor: Unit) -> list[Position]:
        if actor.position is None or actor.cannot_move:
            return []
        result = []
        for dx, dy in ALL_DIRECTIONS:
            cell = actor.position.offset(dx * 4, dy * 4)
            if not battle.in_bounds(cell) or not battle.can_place_unit(actor, cell, mover=actor):
                continue
            try:
                battle.find_path(actor, cell, max_distance=4, exact_distance=4, straight_only=True)
            except ActionError:
                continue
            result.append(cell)
        return result

    def directions_from(self, battle: Battle, actor: Unit,
                        cell: Position) -> list[dict[str, Any]]:
        return [{"dx": dx, "dy": dy, "label": f"{dx:+d},{dy:+d}"}
                for dx, dy in ALL_DIRECTIONS
                if battle.line_positions(cell, (dx, dy), actor.targeting_range())]

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        cells = self.available_cells(battle, actor)
        return {"cells": [cell.to_dict() for cell in cells], "target_unit_ids": [],
                "secondary_cells": [cell.to_dict() for cell in battle.unit_cells(actor)],
                "requires_target": True,
                "selection": {"mode": "cell_direction", "directions": [
                    {"dx": dx, "dy": dy, "label": f"{dx:+d},{dy:+d}"} for dx, dy in ALL_DIRECTIONS]}}

    def _aim(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> tuple[Position, list[Position]]:
        landing = payload_position(payload)
        direction = payload_direction(payload)
        if landing not in self.available_cells(battle, actor) or direction not in ALL_DIRECTIONS:
            raise ActionError("落天踢需要合法的四格直线落点和攻击方向。")
        cells = battle.line_positions(landing, direction, actor.targeting_range())
        if not cells:
            raise ActionError("落天踢的攻击方向超出战场。")
        return landing, cells

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return self._aim(battle, actor, payload)[1]

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        # The damage is a separate queued effect after the move. Do not open its
        # reaction window against pre-move occupants at skill declaration.
        return []

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        landing, cells = self._aim(battle, actor, payload)
        battle.move_unit(actor, landing, via_skill=True, straight_only=True,
                         max_distance=4, exact_distance=4, tags={"falling_kick"})
        if not present(actor):
            return
        battle.queue_area_damage_effect(actor=actor, display_name=self.name, cells=cells,
                                        attack_power=actor.stat("attack"), half_ignore_shield=True,
                                        refresh_cell_targets=True, tags={"skill", self.code})

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]] | None:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "x": landing.x, "y": landing.y,
                 "direction": {"dx": entry["dx"], "dy": entry["dy"]}}
                for landing in self.available_cells(battle, actor)
                for entry in self.directions_from(battle, actor, landing)]

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float | None:
        try:
            landing, cells = self._aim(battle, actor, payload)
        except (ActionError, KeyError, TypeError, ValueError):
            return -1000
        summoner = battle.units.get(actor.summoner_id or "")
        if not present(summoner) or landing.distance_to(summoner.position) > 6:
            return -1000
        return _area_value(battle, actor, cells, half_pierce=True) - 25

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload) or -1000


class StarShiftSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False
    is_displacement_skill = True

    def __init__(self) -> None:
        super().__init__("star_shift", "星占术。移",
                         "每场一次；选择己方守护灵或召唤者，瞬移到其周围合法空格。",
                         max_uses_per_battle=1, target_mode="cell")

    def anchors(self, battle: Battle, actor: Unit) -> list[Unit]:
        anchors = named_guardians(battle, actor.player_id)
        summoner = battle.units.get(actor.summoner_id or "")
        if present(summoner) and summoner not in anchors:
            anchors.append(summoner)
        return anchors

    def candidates(self, battle: Battle, actor: Unit) -> list[tuple[Unit, list[Position]]]:
        summoner = battle.units.get(actor.summoner_id or "")
        return [(anchor, cells) for anchor in self.anchors(battle, actor)
                if (cells := [cell for cell in adjacent_legal_cells(battle, actor, anchor)
                              if cell != actor.position and
                              (not present(summoner) or cell.distance_to(summoner.position) <= 6)])]

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        candidates = self.candidates(battle, actor)
        return {"cells": [cell.to_dict() for _, cells in candidates for cell in cells],
                "target_unit_ids": [unit.unit_id for unit, _ in candidates],
                "secondary_cells": [], "requires_target": True,
                "selection": {"mode": "unit_cell", "target_key": "target_unit_id",
                              "candidates": [{"id": unit.unit_id, "cells": [cell.to_dict() for cell in cells]}
                                             for unit, cells in candidates]}}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return [payload_position(payload)]

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        anchor_id = str(payload.get("target_unit_id") or "")
        cell = payload_position(payload)
        if not any(unit.unit_id == anchor_id and cell in cells
                   for unit, cells in self.candidates(battle, actor)):
            raise ActionError("请选择一名己方守护灵或召唤者周围的合法落格。")
        battle.move_unit(actor, cell, via_skill=True, allow_anywhere=True, tags={"star_shift"})

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float | None:
        try:
            cell = payload_position(payload)
        except (KeyError, TypeError, ValueError):
            return -1000
        if not any(unit.unit_id == str(payload.get("target_unit_id")) and cell in cells
                   for unit, cells in self.candidates(battle, actor)):
            return -1000
        summoner = battle.units.get(actor.summoner_id or "")
        current_gap = actor.position.distance_to(summoner.position) if present(summoner) else 99
        new_gap = cell.distance_to(summoner.position) if present(summoner) else 99
        enemies = [unit for unit in battle.all_units() if present(unit) and unit.player_id != actor.player_id]
        current_enemy = min((actor.position.distance_to(unit.position) for unit in enemies), default=8)
        new_enemy = min((cell.distance_to(unit.position) for unit in enemies), default=8)
        return (max(0, current_gap - new_gap) * 8 if current_gap >= 5 else 0) + \
            max(0, current_enemy - new_enemy) * 6 - 22

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload) or -1000
