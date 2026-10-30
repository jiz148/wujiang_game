"""R277 Tianyuan counter and breakthrough."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import StatusEffect
from wujiang.tactical.engine.core import Unit
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.heroes.common import pattern_selection_preview
from wujiang.tactical.heroes.common import payload_position
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.mechanics.terrain_helpers import _destroy_terrain_in_cells
from wujiang.tactical.heroes.mechanics.terrain_helpers import _terrain_unit
from wujiang.tactical.heroes.mechanics.next_five_geometry import ALL_DIRECTIONS
from wujiang.tactical.prediction import direct_scoring
from wujiang.tactical.prediction import forecasting_active
from wujiang.tactical.prediction import random


class TianyuanCounterStatus(StatusEffect):
    def __init__(self, points: int) -> None:
        super().__init__("叹号计数点", "天元掷点累加；突破以当前点数决定必走距离和每名撞击单位的独立伤害次数，结束后清零。")
        self.points = points

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data["points"] = self.points
        data["description"] = f"当前叹号计数点：{self.points}。突破后全部清零。"
        return data


class TianyuanSkill(Skill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        status = context.actor.get_status("叹号计数点")
        points = int(getattr(status, "points", 0))
        return max(-1000.0, 32.0 - points * 5.0)

    def __init__(self) -> None:
        super().__init__("tianyuan", "天元", "每本人轮一次；公开掷1–6点并累加叹号计数点。",
                         max_uses_per_turn=1, target_mode="self")

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        rolled = 3.5 if direct_scoring() else (3 if getattr(battle, "_ai_probe_active", False) and not forecasting_active() else random.randint(1, 6))
        counter = actor.get_status("叹号计数点")
        if isinstance(counter, TianyuanCounterStatus):
            counter.points += rolled
        else:
            counter = TianyuanCounterStatus(rolled)
            actor.add_status(counter, source=actor)
        battle.log_public_event(f"{actor.name} 的【天元】掷出{rolled}点，当前叹号共{counter.points}点。", source=actor)


class TianyuanBreakthroughSkill(Skill):
    selection_cells_are_effect_cells = False
    resolves_from_current_position = True

    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        battle, actor = context.battle, context.actor
        try:
            route = self.route(battle, actor, context.payload)
        except ActionError:
            return -1000.0
        points = self.points(actor)
        if points <= 0 or actor.position is None:
            return -1000.0
        score = 0.0
        stop = actor.position
        seen: set[str] = set()
        for cell in route:
            blockers = [unit for unit in battle.units_at(cell) if unit.unit_id != actor.unit_id]
            terrain = (cell.x, cell.y) in battle.blocked_cells
            terrain = terrain or any(getattr(effect, "is_terrain", False) and cell in effect.affected_cells(battle)
                                     for effect in battle.field_effects)
            terrain = terrain or any(getattr(unit, "world_seed_terrain", False) or getattr(unit, "is_terrain", False)
                                     or getattr(unit, "role", "") == "地形单位" for unit in blockers)
            if terrain:
                score += 24.0
            remaining = []
            for target in blockers:
                if (getattr(target, "world_seed_terrain", False) or getattr(target, "is_terrain", False)
                        or getattr(target, "role", "") == "地形单位"):
                    continue
                if target.unit_id not in seen:
                    seen.add(target.unit_id)
                    damage = context.estimate_damage(target, actor.stat("attack")) * points
                    value = min(target.current_hp, damage) * 125.0 + (18.0 if damage >= target.current_hp else 0.0)
                    score += value if target.player_id != actor.player_id else -value * 1.6
                if context.estimate_damage(target, actor.stat("attack")) * points < target.current_hp - 1e-9:
                    remaining.append(target)
            if remaining:
                break
            stop = cell
        role = context.hero_style()
        score += context.move_score(stop, role) - context.move_score(actor.position, role)
        return score - max(12.0, points * 3.0)

    def __init__(self) -> None:
        super().__init__("tianyuan_breakthrough", "突破", "每本人轮一次；沿八方向走满叹号点数格，破地形、对双方碰撞单位各结算点数次技能伤害，结束清零。",
                         max_uses_per_turn=1, target_mode="cell")

    @staticmethod
    def points(actor: Unit) -> int:
        counter = actor.get_status("叹号计数点")
        return counter.points if isinstance(counter, TianyuanCounterStatus) else 0

    def routes(self, battle: Battle, actor: Unit) -> dict[Position, list[Position]]:
        if actor.position is None or actor.cannot_move or self.points(actor) <= 0:
            return {}
        routes: dict[Position, list[Position]] = {}
        for dx, dy in ALL_DIRECTIONS:
            cells: list[Position] = []
            for step in range(1, self.points(actor) + 1):
                cell = actor.position.offset(dx * step, dy * step)
                if not battle.in_bounds(cell):
                    break
                cells.append(cell)
            if len(cells) == self.points(actor):
                routes[cells[-1]] = cells
        return routes

    def route(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        declared = battle.payload_positions(payload, "declared_breakthrough_cells")
        if declared and payload.get("queued_resolution"):
            return declared
        if payload.get("x") is not None and payload.get("y") is not None:
            end = payload_position(payload)
        else:
            selected = payload.get("cells") or []
            if len(selected) != 1:
                raise ActionError("突破须选择一条直线路线的终点。")
            end = payload_position(selected[0])
        route = self.routes(battle, actor).get(end)
        if route is None:
            raise ActionError("突破须沿八方向选择叹号距离内的路线终点。")
        return route

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if self.points(actor) <= 0:
            return False, "没有可用于突破的叹号计数点。"
        if actor.cannot_move:
            return False, "当前不能位移。"
        if payload and (payload.get("x") is not None or payload.get("cells")):
            try:
                self.route(battle, actor, payload)
            except ActionError as exc:
                return False, str(exc)
        return (True, "") if self.routes(battle, actor) else (False, "没有可选择的突破方向。")

    def queued_payload_metadata(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> dict[str, Any]:
        route = self.route(battle, actor, payload)
        return {"declared_breakthrough_cells": positions_to_dict(route),
                "declared_breakthrough_points": self.points(actor),
                "declared_breakthrough_origin": actor.position.to_dict() if actor.position else None}

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        route = self.route(battle, actor, payload)
        points = int(payload.get("declared_breakthrough_points") or self.points(actor))
        origin = payload.get("declared_breakthrough_origin")
        counter = actor.get_status("叹号计数点")
        try:
            if actor.position is None or (origin and actor.position != payload_position(origin)):
                battle.log_public_event(f"{actor.name} 的【突破】起点已改变，本次冲刺落空。", source=actor)
                return
            seen: set[str] = set()
            for cell in route:
                if not actor.alive or actor.position is None:
                    break
                _destroy_terrain_in_cells(battle, actor, [cell], self.name)
                for target in list(battle.units_at(cell)):
                    if target.unit_id == actor.unit_id or _terrain_unit(target) or target.unit_id in seen:
                        continue
                    seen.add(target.unit_id)
                    for _ in range(points):
                        if not target.alive or target.position is None or not actor.alive:
                            break
                        battle.resolve_damage(DamageContext(source=actor, target=target,
                            attack_power=actor.stat("attack"), is_skill=True, action_name=self.name,
                            tags={"skill", "attack", self.code}))
                if not battle.can_place_unit(actor, cell, ignore=actor, mover=actor):
                    battle.log_public_event(f"{actor.name} 的【突破】被({cell.x},{cell.y})仍在场的单位挡下。", source=actor)
                    break
                try:
                    battle.move_unit(actor, cell, via_skill=True, straight_only=True,
                                     max_distance=1, path=[cell], tags={self.code})
                except ActionError:
                    battle.log_public_event(f"{actor.name} 的【突破】在({cell.x},{cell.y})前停下。", source=actor)
                    break
        finally:
            if isinstance(counter, TianyuanCounterStatus) and counter in actor.statuses:
                actor.remove_status(counter, battle)
            battle.log_public_event(f"{actor.name} 的【突破】结束，叹号计数点清零。", source=actor)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        routes = self.routes(battle, actor)
        preview = pattern_selection_preview([[end] for end in routes])
        preview.update({"target_unit_ids": [], "secondary_cells": [], "requires_target": True,
                        "pattern_destinations": [{"pattern": [end.to_dict()], "destination_cells": positions_to_dict(path)}
                                                 for end, path in routes.items()],
                        "selection": {"mode": "pattern_cells", "ordered": False,
                                      "patterns": [[end.to_dict()] for end in routes],
                                      "cell_prompt": "选择突破方向；正常走满叹号点数，遇到仍在场的单位时提前停下。"}})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        return self.route(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Unit]:
        return [unit for unit in battle.effect_units_at_cells(self.route(battle, actor, payload))
                if unit.unit_id != actor.unit_id]


def resolve_true_red_move_blast(battle: Battle, actor: Unit, queued_action: Any) -> None:
    cells = battle.payload_positions(queued_action.payload, "cells")
    for target in battle.effect_units_at_cells(cells, ignore=actor):
        battle.resolve_damage(DamageContext(source=actor, target=target, attack_power=5,
                                            is_skill=False, from_field_effect=True,
                                            action_name="真红位移爆破", tags={"movement", "true_red_blast"},
                                            area_cell_hits=battle.unit_hit_count_for_cells(target, cells)))
    battle.check_win_condition()


