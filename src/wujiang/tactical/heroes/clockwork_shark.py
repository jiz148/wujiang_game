"""R040 发条鲨: point economy, retaliatory spines and a traversing MK2 body."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, DamageContext, MoveContext, Position, QueuedAction,
    Skill, StatusEffect, Unit,
)
from wujiang.tactical.heroes.common import payload_direction, positions_to_dict
from wujiang.tactical.heroes.ai_policy import HeroAIPolicy
from wujiang.tactical.heroes.mechanics.mana_point_cost import ManaPointCostSkill
from wujiang.tactical.heroes.mechanics.next_five_geometry import ALL_DIRECTIONS


class ClockworkSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("clockwork", "发条", "每本人轮一次，获得2魔力点。",
                         max_uses_per_turn=1)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        return 24.0 if actor.mana_points < 10 else 4.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        gained = actor.gain_mana_points(2)
        battle.log_public_event(f"{actor.name} 发条，魔力点+{gained:g}。", source=actor)


class BarbedSpinesStatus(StatusEffect):
    def __init__(self) -> None:
        # Expire when this unit next starts a turn: casting on its own turn must
        # still protect it through the opponents' ensuing actions.
        super().__init__("倒刺", "攻击或对本单位使用技能者受固定4伤。",
                         duration=1, tick_scope="owner_turn_start")
        self._declared_ids: set[str] = set()

    def on_target_action_declared(self, battle: Battle, actor: Unit,
                                  action_type: str, payload: dict[str, Any]) -> None:
        owner = self.owner
        if owner is None or not owner.alive or not actor.alive or actor is owner:
            return
        if action_type not in {"attack", "skill"}:
            return
        declaration = str(payload.get("declaration_id", id(payload)))
        if declaration in self._declared_ids:
            return
        self._declared_ids.add(declaration)
        battle.queue_skill_effect_action(
            actor=owner, display_name="倒刺反伤", effect_code="barbed_spines_retaliation",
            target_units=[actor], target_cells=battle.unit_cells(actor),
            include_cell_units=False, effect_resolver=self._resolve_retaliation,
        )

    @staticmethod
    def _resolve_retaliation(battle: Battle, owner: Unit, queued: QueuedAction) -> None:
        for target_id in queued.target_unit_ids:
            target = battle.units.get(target_id)
            if target is not None and target.alive:
                battle.resolve_damage(DamageContext(
                    source=owner, target=target, attack_power=0, raw_damage=4,
                    is_skill=True, action_name="倒刺反伤", tags={"skill", "barbed_spines"},
                ))


class BarbedSpinesSkill(ManaPointCostSkill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("barbed_spines", "倒刺", "耗1魔力点；一轮内攻击或对本单位使用技能者受固定4伤。",
                         mana_point_cost=1)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        if actor.has_status("倒刺"):
            return -20.0
        nearby = sum(enemy.player_id != actor.player_id and enemy.alive
                     and enemy.position is not None
                     and battle.distance_between_units(actor, enemy) <= 3
                     for enemy in battle.all_units())
        return 18.0 + nearby * 13.0 if nearby else -5.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        for status in list(actor.statuses):
            if isinstance(status, BarbedSpinesStatus):
                actor.remove_status(status, battle)
        actor.add_status(BarbedSpinesStatus(), source=actor)
        battle.log_public_event(f"{actor.name} 张开倒刺。", source=actor)


class SharkPulseSkill(ManaPointCostSkill):
    ai_payload_value_owned = True
    target_cells_are_geometry_only = True
    is_displacement_skill = True
    ai_complete_direction_payloads = True

    def __init__(self) -> None:
        super().__init__("shark_pulse", "脉冲", "耗3魔力点；朝一方向攻击1×5，随后尽量移动至第6格。",
                         mana_point_cost=3)

    def _cells(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        if actor.position is None:
            raise ActionError("发条鲨不在战场上。")
        dx, dy = payload_direction(payload)
        return [cell for step in range(1, 6)
                if battle.in_bounds(cell := actor.position.offset(dx * step, dy * step))]

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.cannot_move or actor.position is None:
            return False, "当前不能施放位移脉冲。"
        if payload is not None and "direction" in payload:
            try:
                if not self._cells(battle, actor, payload):
                    return False, "该方向没有可击中的格子。"
            except (ActionError, TypeError, ValueError):
                return False, "请选择合法的脉冲方向。"
        return True, ""

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        origin = actor.position
        directions = []
        secondary = []
        if origin is not None:
            for dx, dy in ALL_DIRECTIONS:
                cells = [cell for step in range(1, 6)
                         if battle.in_bounds(cell := origin.offset(dx * step, dy * step))]
                if cells:
                    directions.append({"dx": dx, "dy": dy, "label": f"{dx:+d},{dy:+d}"})
                    secondary.extend(cells)
        return {"cells": [],
                "target_unit_ids": [], "secondary_cells": positions_to_dict(secondary),
                "requires_target": True,
                "selection": {"mode": "direction_only", "directions": directions,
                              "prompt": "选择脉冲的前进方向。"}}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return self._cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return battle.effect_units_at_cells(self._cells(battle, actor, payload), ignore=actor)

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        origin = actor.position
        if origin is None:
            return []
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "direction": {"dx": dx, "dy": dy}}
                for dx, dy in ALL_DIRECTIONS
                if battle.in_bounds(origin.offset(dx, dy))]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        try:
            targets = self.get_target_units_for_payload(battle, actor, payload)
        except (ActionError, TypeError, ValueError):
            return -1000.0
        value = sum((50.0 if target.player_id != actor.player_id else -75.0)
                    for target in targets)
        return value - 8.0

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit,
                                  payload: dict[str, Any], targets: list[Unit] | None = None,
                                  shared: Any = None, action: dict[str, Any] | None = None) -> bool:
        return any(unit.player_id != actor.player_id
                   for unit in self.get_target_units_for_payload(battle, actor, payload))

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cells = self._cells(battle, actor, payload)
        targets = list(battle.effect_units_at_cells(cells, ignore=actor))
        for target in targets:
            if target.alive:
                battle.resolve_damage(DamageContext(
                    source=actor, target=target, attack_power=actor.stat("attack"),
                    is_skill=True, action_name=self.name, tags={"skill", self.code},
                ))
        if not actor.alive or actor.position is None:
            return
        dx, dy = payload_direction(payload)
        reachable = set(battle.reachable_positions(actor, max_distance=6, straight_only=True))
        landing = next((cell for step in range(6, 0, -1)
                        if (cell := actor.position.offset(dx * step, dy * step)) in reachable), None)
        if landing is not None:
            battle.move_unit(actor, landing, via_skill=True, straight_only=True,
                             max_distance=6, tags={self.code})


class SharkMK2Status(StatusEffect):
    def __init__(self) -> None:
        super().__init__("MK2", "永久速+5、守变1、穿人并按所持魔力点撞伤。")

    def bind(self, owner: Unit) -> SharkMK2Status:
        super().bind(owner)
        owner.ignore_units_while_moving = True
        return self

    def modify_stat(self, stat_name: str, value: float) -> float:
        if stat_name == "speed":
            return value + 5
        if stat_name == "defense":
            return 1.0
        return value

    def on_removed(self, battle: Battle) -> None:
        if self.owner is not None:
            self.owner.ignore_units_while_moving = False

    def on_unit_moved(self, battle: Battle, ctx: MoveContext) -> None:
        owner = self.owner
        if owner is None or ctx.unit is not owner or not owner.alive:
            return
        for cell in ctx.path[1:-1]:
            for target in battle.units_at(cell, ignore=owner):
                if target.alive:
                    battle.queue_skill_effect_action(
                        actor=owner, display_name="MK2穿人", effect_code="shark_mk2_collision",
                        target_units=[target], target_cells=[cell], include_cell_units=False,
                        effect_resolver=self._resolve_collision,
                    )

    @staticmethod
    def _resolve_collision(battle: Battle, owner: Unit, queued: QueuedAction) -> None:
        for target_id in queued.target_unit_ids:
            target = battle.units.get(target_id)
            if target is None or not target.alive or not owner.alive:
                continue
            power = min(6.0, owner.mana_points)
            if power <= 0:
                continue
            result = battle.resolve_damage(DamageContext(
                source=owner, target=target, attack_power=0, raw_damage=power,
                is_skill=True, action_name="MK2穿人", tags={"skill", "shark_mk2"},
            ))
            owner.spend_mana_points(result.actual_damage)


class SharkMK2Skill(ManaPointCostSkill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("shark_mk2", "MK2", "耗10魔力点；永久速+5、守变1、穿人伤害上限6。",
                         mana_point_cost=10, max_uses_per_battle=1)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        return 52.0 if not actor.has_status("MK2") else -100.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        actor.add_status(SharkMK2Status(), source=actor)
        battle.log_public_event(f"{actor.name} 启动MK2。", source=actor)


class ClockworkSharkAIPolicy(HeroAIPolicy):
    """Price MK2's crossed bodies using the actual path the move will take."""

    @staticmethod
    def _crossing_value(battle: Battle, actor: Unit, path: list[Position]) -> float:
        remaining = actor.mana_points
        value = 0.0
        for cell in path[1:-1]:
            for target in battle.units_at(cell, ignore=actor):
                if not target.alive or target.magic_immunity or remaining <= 0:
                    continue
                expected = min(remaining, 6.0, target.current_hp)
                value += expected * (60.0 if target.player_id != actor.player_id else -85.0)
                remaining -= expected
        return value

    @staticmethod
    def _direct_path(start: Position, destination: Position) -> list[Position]:
        path = [start]
        current = start
        while current != destination:
            dx = 0 if current.x == destination.x else (1 if destination.x > current.x else -1)
            dy = 0 if current.y == destination.y else (1 if destination.y > current.y else -1)
            current = current.offset(dx, dy)
            path.append(current)
        return path

    def _crossing_routes(self, battle: Battle, actor: Unit) -> list[tuple[dict[str, Any], list[Position]]]:
        if not actor.has_status("MK2") or actor.position is None or actor.mana_points <= 0:
            return []
        remaining = actor.remaining_normal_move_distance(battle)
        if remaining < 2:
            return []
        routes = []
        seen: set[tuple[Position, ...]] = set()
        for target in battle.enemy_units(actor.player_id):
            for ingress in battle.unit_cells(target):
                for exit_cell in battle.neighbors(ingress):
                    if not battle.can_place_unit(actor, exit_cell, ignore=actor, mover=actor):
                        continue
                    path = [*self._direct_path(actor.position, ingress), exit_cell]
                    key = tuple(path)
                    if key in seen or len(path) - 1 > remaining:
                        continue
                    seen.add(key)
                    payload = {"type": "move", "unit_id": actor.unit_id,
                               "x": exit_cell.x, "y": exit_cell.y,
                               "path": positions_to_dict(path[1:])}
                    try:
                        battle.build_queued_action(payload)
                    except ActionError:
                        continue
                    routes.append((payload, path))
        return routes

    def extra_normal_move_candidates(self, battle: Battle, actor: Unit,
                                     role: str, profile: Any, builder: Any) -> list[Any]:
        if actor.position is None:
            return []
        origin_score = builder.services.score_move_destination(
            battle, actor, actor.position, role, profile)
        candidates = []
        for payload, path in self._crossing_routes(battle, actor):
            destination = path[-1]
            score = (builder.services.score_move_destination(
                battle, actor, destination, role, profile) - origin_score - 1.0
                + self._crossing_value(battle, actor, path))
            candidates.append(builder.services.AICandidate(
                payload=payload, score=score, summary=f"move:mk2:{destination.x},{destination.y}"))
        return sorted(candidates, key=lambda candidate: candidate.score, reverse=True)[:16]

    def lightweight_normal_move_payload(self, battle: Battle, actor: Unit,
                                        cells: list[Position], *, retreating: bool = False) -> dict | None:
        if retreating:
            return None
        allowed = set(cells)
        routes = [(self._crossing_value(battle, actor, path), payload)
                  for payload, path in self._crossing_routes(battle, actor)
                  if path[-1] in allowed]
        return max(routes, key=lambda item: item[0])[1] if routes and max(value for value, _ in routes) > 0 else None

    def normal_move_candidate_score(self, battle: Battle, actor: Unit,
                                    destination: Position, profile: Any,
                                    score: float, shared: Any) -> float:
        if not actor.has_status("MK2") or actor.mana_points <= 0:
            return score
        try:
            path = battle.find_path(actor, destination,
                                    max_distance=actor.remaining_normal_move_distance(battle),
                                    use_movement_cost=True)
        except ActionError:
            return score
        return score + self._crossing_value(battle, actor, path)
