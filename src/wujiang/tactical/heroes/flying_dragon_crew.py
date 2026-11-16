"""Object-owned rules for the Atlanta's helmsman and scout (source rows 330–331)."""

from __future__ import annotations

from copy import copy, deepcopy
from dataclasses import dataclass
from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, DamageContext, Position, QueuedAction, Skill, Stats,
    StatusEffect, Trait, Unit,
)
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.flying_dragon_ship import CREW_NAME, ShipCrewMovePolicy, allied_ship
from wujiang.tactical.heroes.mechanics.next_five_effects import damage_followup_effect_applies
from wujiang.tactical.heroes.mechanics.next_five_geometry import ALL_DIRECTIONS


def _crew_units(battle: Battle, actor: Unit) -> list[Unit]:
    return [unit for unit in battle.player_units(actor.player_id)
            if unit is not actor and unit.alive and not unit.banished
            and unit.position is not None and not unit.is_summon and not unit.is_clone
            and not getattr(unit, "attached_to_unit_id", None) and not unit.direct_effects_blocked()
            and not unit.skill_non_damage_effects_blocked()
            and CREW_NAME in unit.name]


def _moment_memo(battle: Battle) -> dict[int, Any]:
    return {id(unit): unit for unit in battle.all_units()}


def _nearest_enemy_distance(battle: Battle, actor: Unit, cell: Position) -> int:
    return min((cell.distance_to(enemy_cell)
                for enemy in battle.all_units() if enemy.player_id != actor.player_id
                and enemy.alive and not enemy.banished
                for enemy_cell in battle.unit_cells(enemy)), default=20)


@dataclass(frozen=True)
class UnitMoment:
    position: Position
    hp: float
    mana: float
    stats: Stats
    statuses: tuple[StatusEffect, ...]


class SandHistoryTrait(Trait):
    """Keep two completed helmsman-turn snapshots; no global battle history."""

    def __init__(self) -> None:
        super().__init__("时之沙记录", "记录舵手最近两个已完成本人回合的场上单位状态。")
        self.history: list[dict[str, UnitMoment]] = []

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        owner = self.owner
        if owner is None or battle.current_turn_slot_unit_id() != owner.unit_id:
            return
        memo = _moment_memo(battle)
        moment = {
            unit.unit_id: UnitMoment(unit.position, unit.current_hp, unit.current_mana,
                                     copy(unit.base_stats), tuple(deepcopy(unit.statuses, memo)))
            for unit in battle.all_units()
            if unit.alive and not unit.banished and unit.position is not None
        }
        self.history.append(moment)
        self.history = self.history[-2:]

    def two_turns_ago(self, unit: Unit) -> UnitMoment | None:
        return self.history[-2].get(unit.unit_id) if len(self.history) >= 2 else None


def _sand_history(actor: Unit) -> SandHistoryTrait | None:
    return next((trait for trait in actor.traits if isinstance(trait, SandHistoryTrait)), None)


class SilverGrapplingHookSkill(Skill):
    is_displacement_skill = True
    requires_damage_choice = True
    ai_payload_value_owned = True
    ai_optional_choice_owned = True
    ai_enemy_impact_requirement = False
    target_cells_are_geometry_only = True

    def __init__(self) -> None:
        super().__init__("silver_grappling_hook", "银制的飞钩",
                         "费0.5魔、每本人轮一次；先直线飞跃恰3格，再选场上一名己方骷髅飞盗团真武将移到本人周围。",
                         mana_cost=0.5, max_uses_per_turn=1, target_mode="cell")

    def routes(self, battle: Battle, actor: Unit) -> list[list[Position]]:
        if actor.position is None or actor.cannot_move:
            return []
        routes = []
        for dx, dy in ALL_DIRECTIONS:
            route = [actor.position.offset(dx * step, dy * step) for step in range(1, 4)]
            try:
                battle.explicit_path(actor, route, max_distance=3, exact_distance=3,
                                     straight_only=True, ignore_units=True)
            except ActionError:
                continue
            if not self.pull_options_at(battle, actor, route[-1]):
                continue
            routes.append(route)
        return routes

    def pull_targets_at(self, battle: Battle, actor: Unit,
                        destination: Position) -> list[tuple[Unit, Position]]:
        body = set(battle.unit_cells_at(actor, destination))
        ring = {cell for origin in body for cell in battle.neighbors(origin)} - body
        options = []
        for ally in _crew_units(battle, actor):
            for cell in sorted(ring, key=lambda item: (item.y, item.x)):
                if battle.in_bounds(cell) and battle.can_place_unit(ally, cell, ignore=ally, mover=ally):
                    options.append((ally, cell))
        return options

    def pull_options_at(self, battle: Battle, actor: Unit, destination: Position) -> list[str]:
        return [f"{ally.unit_id}@{cell.x},{cell.y}"
                for ally, cell in self.pull_targets_at(battle, actor, destination)]

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        routes = self.routes(battle, actor)
        if not routes or not _crew_units(battle, actor):
            return False, "没有合法三格飞跃路线或可拉的飞盗团武将。"
        if payload and "x" in payload and "y" in payload and all(
                route[-1] != Position(int(payload["x"]), int(payload["y"])) for route in routes):
            return False, "飞钩必须沿完整合法直线飞跃三格。"
        return True, ""

    def pull_options(self, battle: Battle, actor: Unit) -> list[str]:
        if actor.position is None:
            return []
        return self.pull_options_at(battle, actor, actor.position)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        destination = Position(int(payload["x"]), int(payload["y"]))
        route = next((path for path in self.routes(battle, actor) if path[-1] == destination), None)
        if route is None:
            raise ActionError("飞钩飞跃路线已经失效。")
        battle.move_unit(actor, destination, via_skill=True, straight_only=True,
                         ignore_units=True, path=route, max_distance=3, exact_distance=3,
                         tags={self.code, "movement"})
        if not actor.alive or actor.position is None:
            return
        options = self.pull_options(battle, actor)
        choice = battle.optional_position_choice(actor, options, action_name="银制的飞钩选择队友与落格",
                                                 kind="optional_placement")
        if choice not in options:
            return
        unit_id, location = choice.rsplit("@", 1)
        x, y = (int(part) for part in location.split(",", 1))
        ally = battle.units.get(unit_id)
        if ally not in _crew_units(battle, actor):
            return
        target_ctx = battle.validate_target(actor, ally, action_name=self.name, is_skill=True,
                                            is_hostile=False)
        if target_ctx.cancelled:
            return
        try:
            battle.move_unit(ally, Position(x, y), via_skill=True, forced=True,
                             allow_anywhere=True, tags={self.code, "movement"})
        except ActionError:
            battle.log_public_event(f"{actor.name} 的飞钩拉人落格已失效。", source=actor)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        routes = self.routes(battle, actor)
        return {"cells": positions_to_dict([route[-1] for route in routes]),
                "target_unit_ids": [], "secondary_cells": positions_to_dict(
                    [cell for route in routes for cell in route]), "requires_target": True,
                "routes": [positions_to_dict(route) for route in routes],
                "selection": {"mode": "cell", "cell_prompt": "先选三格飞跃落点；移动后选择队友及其邻格。"}}

    def simple_ai_cast_useful(self, battle: Battle, actor: Unit) -> bool:
        return bool(_crew_units(battle, actor) and self.routes(battle, actor))

    def ai_payload_value(self, battle: Battle, actor: Unit,
                               payload: dict[str, Any]) -> float | None:
        if actor.position is None or "x" not in payload or "y" not in payload:
            return -1.0
        destination = Position(int(payload["x"]), int(payload["y"]))
        if destination not in [route[-1] for route in self.routes(battle, actor)]:
            return -1.0
        actor_gain = (_nearest_enemy_distance(battle, actor, actor.position)
                      - _nearest_enemy_distance(battle, actor, destination)) * 3.0
        pull_gain = max(((_nearest_enemy_distance(battle, actor, ally.position)
                          - _nearest_enemy_distance(battle, actor, cell)) * 5.0)
                        for ally, cell in self.pull_targets_at(battle, actor, destination))
        return actor_gain + pull_gain - 8.0 if pull_gain > 0 else -1.0

    def ai_optional_choice_score(self, battle: Battle, actor: Unit,
                                 prompt: dict[str, Any], choice: str) -> float | None:
        if prompt.get("action_name") != "银制的飞钩选择队友与落格" or "@" not in choice:
            return None
        unit_id, coordinates = choice.rsplit("@", 1)
        ally = battle.units.get(unit_id)
        if ally is None or ally.position is None:
            return -1.0
        x, y = (int(part) for part in coordinates.split(",", 1))
        landing = Position(x, y)
        before = _nearest_enemy_distance(battle, actor, ally.position)
        after = _nearest_enemy_distance(battle, actor, landing)
        access = 12.0 if before > ally.targeting_range() >= after else 0.0
        danger = 8.0 if after <= 1 and ally.stat("defense") <= 2 else 0.0
        return (before - after) * 5.0 + access - danger - 2.0

    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        destination = context.destination()
        if destination is None or not _crew_units(context.battle, context.actor):
            return -1000.0
        battle, actor = context.battle, context.actor
        actor_gain = context.move_score_for(actor, destination) - context.move_score_for(actor, actor.position)
        pull_gain = max((context.move_score_for(ally, cell) - context.move_score_for(ally, ally.position)
                         for ally, cell in self.pull_targets_at(battle, actor, destination)),
                        default=-1000.0)
        value = actor_gain + pull_gain - 12.0
        return value if pull_gain > 2.0 and value > 8.0 else -1000.0


class SandOfTimeSkill(Skill):
    is_displacement_skill = True
    ai_enemy_impact_requirement = False
    ai_payload_value_owned = True

    def __init__(self) -> None:
        super().__init__("sand_of_time", "时之沙",
                         "0魔大招、每场一次；没有其他飞盗团单位在场时，破魔回溯范内直线单位到舵手两个已完成本人回合前，且不重置大招。",
                         max_uses_per_battle=1, target_mode="unit")

    def targets(self, battle: Battle, actor: Unit) -> list[Unit]:
        history = _sand_history(actor)
        if history is None or len(history.history) < 2 or actor.position is None:
            return []
        if any(unit is not actor and unit.alive and not unit.banished and unit.position is not None
               and CREW_NAME in unit.name for unit in battle.all_units()):
            return []
        return [unit for unit in battle.all_units() if unit.alive and not unit.banished
                and unit.position is not None and battle.unit_can_be_selected(unit, actor=actor)[0]
                and battle.unit_target_in_range_and_line(actor, unit, actor.targeting_range())
                and history.two_turns_ago(unit) is not None]

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        targets = self.targets(battle, actor)
        if not targets:
            return False, "飞盗团仍有其他单位，或尚无两轮前可回溯的合法目标。"
        if payload and payload.get("target_unit_id") and all(
                unit.unit_id != payload["target_unit_id"] for unit in targets):
            return False, "时之沙目标须在本人范内直线且有两轮前记录。"
        return True, ""

    def ignores_shield_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> bool:
        return True

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target is None or target not in self.targets(battle, actor):
            raise ActionError("时之沙目标已失效。")
        history = _sand_history(actor)
        moment = history.two_turns_ago(target) if history is not None else None
        if moment is None:
            raise ActionError("时之沙没有该单位的两轮前记录。")
        ctx = battle.validate_target(actor, target, action_name=self.name, is_skill=True,
                                     is_hostile=target.player_id != actor.player_id,
                                     ignore_shield=True)
        if ctx.cancelled:
            return
        if (target.position != moment.position and battle.can_place_unit(
                target, moment.position, ignore=target, mover=target)):
            try:
                battle.move_unit(target, moment.position, via_skill=True, forced=True,
                                 allow_anywhere=True, tags={self.code, "movement"})
            except ActionError:
                pass
        for status in list(target.statuses):
            target.remove_status(status, battle)
        target.base_stats = copy(moment.stats)
        memo = _moment_memo(battle)
        target.statuses = [deepcopy(status, memo).bind(target) for status in moment.statuses]
        target.current_hp = min(target.max_health, moment.hp)
        target.current_mana = min(target.max_mana(), moment.mana)
        battle.log_public_event(f"{actor.name} 以时之沙回溯了{target.name}；大招使用记录不重置。",
                                source=actor, target=target)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = self.targets(battle, actor)
        return {"cells": positions_to_dict([cell for target in targets for cell in battle.unit_cells(target)]),
                "target_unit_ids": [target.unit_id for target in targets],
                "secondary_cells": [], "requires_target": True}

    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        target = context.battle.units.get(str(context.payload.get("target_unit_id") or ""))
        history = _sand_history(context.actor)
        moment = history.two_turns_ago(target) if history is not None and target is not None else None
        if target is None or moment is None:
            return -1000.0
        value = self._recovery_value(target, moment)
        if target.position != moment.position and context.battle.can_place_unit(
                target, moment.position, ignore=target, mover=target):
            value += context.move_score_for(target, moment.position) - context.move_score_for(target, target.position)
        if target.player_id != context.actor.player_id:
            value = -value
        return value if value > 35.0 else -1000.0

    def ai_payload_value(self, battle: Battle, actor: Unit,
                               payload: dict[str, Any]) -> float | None:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        history = _sand_history(actor)
        moment = history.two_turns_ago(target) if history is not None and target is not None else None
        if target is None or moment is None or target not in self.targets(battle, actor):
            return -1.0
        value = self._recovery_value(target, moment)
        if target.player_id != actor.player_id:
            value = -value
        return value - 35.0

    @staticmethod
    def _recovery_value(target: Unit, moment: UnitMoment) -> float:
        stats = target.base_stats
        return ((moment.hp - target.current_hp) * 90.0
                + (moment.mana - target.current_mana) * 11.0
                + (moment.stats.attack - stats.attack) * 19.0
                + (moment.stats.defense - stats.defense) * 18.0
                + (moment.stats.speed - stats.speed) * 14.0
                + (moment.stats.attack_range - stats.attack_range) * 10.0)

    def simple_ai_cast_useful(self, battle: Battle, actor: Unit) -> bool:
        history = _sand_history(actor)
        return bool(history and any(
            self._recovery_value(unit, history.two_turns_ago(unit))
            * (1 if unit.player_id == actor.player_id else -1) > 35.0
            for unit in self.targets(battle, actor)))


class ScoutSlowStatus(StatusEffect):
    def __init__(self, created_turn: int) -> None:
        super().__init__("侦察兵压速", "下个本人回合速-1。")
        self.created_turn = created_turn

    def modify_stat(self, stat_name: str, value: float) -> float:
        return max(1.0, value - 1.0) if stat_name == "speed" else value

    def on_owner_turn_end(self, battle: Battle) -> None:
        if battle.turn_number != self.created_turn and self.owner is not None:
            self.owner.remove_status(self, battle)


class ScoutAttackSlowTrait(Trait):
    def __init__(self) -> None:
        super().__init__("侦察兵普攻压速", "普攻真实命中后，受体下个本人回合速-1。")

    def on_basic_attack_finished(self, battle: Battle, actor: Unit, payload: dict[str, Any],
                                 damage_contexts: list[DamageContext], missed: bool) -> None:
        if actor is not self.owner or missed:
            return
        for ctx in damage_contexts:
            if damage_followup_effect_applies(ctx) and ctx.target.alive:
                ctx.target.add_status(ScoutSlowStatus(battle.turn_number), source=actor)
                battle.log_public_event(f"{ctx.target.name} 下个本人回合速-1。", source=actor, target=ctx.target)


class HelmsmanShipPermissionTrait(Trait):
    def __init__(self) -> None:
        super().__init__("舵手乘船机动", "乘骑飞龙船时，该船速+2，穿人后逐次造成固定4伤。")


class ScoutShipGuardPermissionTrait(Trait):
    def __init__(self) -> None:
        super().__init__("侦察兵乘船免伤", "乘骑飞龙船时，船每次受伤独立有1/3概率免去。")


def _resolve_lock_claw_strike(battle: Battle, actor: Unit, action: QueuedAction) -> None:
    cells = battle.payload_positions(action.payload, "cells")
    if not cells:
        return
    for target in battle.effect_units_at_cells(cells, ignore=actor):
        battle.resolve_damage(DamageContext(source=actor, target=target,
            attack_power=actor.stat("attack"), is_skill=True,
            action_name="银制的锁爪", tags={"skill", "silver_lock_claw"}))


def _resolve_lock_claw_return(battle: Battle, actor: Unit, action: QueuedAction) -> None:
    ship = allied_ship(battle, actor.player_id)
    if ship is None or actor is ship or actor.position is None or not actor.alive:
        return
    if battle.mounted_unit_for(actor) is ship:
        return
    cells = [cell for cell in battle.unit_cells(ship)
             if cell != actor.position and battle.can_place_unit(actor, cell, ignore=actor, mover=actor)]
    options = [f"{cell.x},{cell.y}" for cell in cells]
    choice = battle.optional_position_choice(actor, options, action_name="锁爪后可瞬移回飞龙船",
                                             kind="optional_placement")
    if choice not in options:
        return
    x, y = (int(part) for part in choice.split(",", 1))
    try:
        battle.move_unit(actor, Position(x, y), via_skill=True, allow_anywhere=True,
                         tags={"silver_lock_claw", "teleport"})
    except ActionError:
        battle.log_public_event(f"{actor.name} 的锁爪归船落格已失效。", source=actor)


class SilverLockClawSkill(Skill):
    is_displacement_skill = True
    requires_damage_choice = True
    ai_payload_value_owned = True
    ai_optional_choice_owned = True
    ai_enemy_impact_requirement = False
    target_cells_are_geometry_only = True

    def __init__(self) -> None:
        super().__init__("silver_lock_claw", "银制的锁爪",
                         "费0.5魔；选一个方向独立攻击两次，然后可瞬移到最近己方飞龙船上的合法空格。",
                         mana_cost=0.5, target_mode="cell")

    def attack_cells(self, battle: Battle, actor: Unit) -> list[Position]:
        if actor.position is None:
            return []
        body = set(battle.unit_cells(actor))
        ring = {cell for origin in body for cell in battle.neighbors(origin)} - body
        return sorted((cell for cell in ring if battle.in_bounds(cell)),
                      key=lambda cell: (cell.y, cell.x))

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if payload and "x" in payload and "y" in payload and Position(
                int(payload["x"]), int(payload["y"])) not in self.attack_cells(battle, actor):
            return False, "锁爪须选择本人邻格的一个方向。"
        return True, ""

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cell = Position(int(payload["x"]), int(payload["y"]))
        if cell not in self.attack_cells(battle, actor):
            raise ActionError("锁爪攻击方向已失效。")
        for index in (1, 2):
            battle.queue_skill_effect_action(actor=actor, display_name=self.name,
                effect_code="silver_lock_claw_strike", effect_resolver=_resolve_lock_claw_strike,
                payload={"cells": [cell.to_dict()], "refresh_cell_targets": True}, target_cells=[cell],
                segment_index=index, segment_count=2)
        battle.queue_skill_effect_action(actor=actor, display_name="锁爪归船",
            effect_code="silver_lock_claw_return", effect_resolver=_resolve_lock_claw_return,
            target_cells=[], target_units=[], include_cell_units=False, hostile=False)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        cells = self.attack_cells(battle, actor)
        ship = allied_ship(battle, actor.player_id)
        return {"cells": positions_to_dict(cells), "target_unit_ids": [],
                "secondary_cells": positions_to_dict(battle.unit_cells(ship)) if ship else [],
                "requires_target": True, "strike_count": 2, "ship_return_optional": ship is not None}

    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        cell = context.destination()
        if cell is None:
            return -1000.0
        score = sum((1.0 if target.player_id != context.actor.player_id else -1.5)
                    * context.estimate_damage(target, context.actor.stat("attack")) * 140.0
                    for target in context.battle.effect_units_at_cells([cell], ignore=context.actor))
        return score * 2 - 13.0 if score > 0 else -1000.0

    def simple_ai_cast_useful(self, battle: Battle, actor: Unit) -> bool:
        return any(target.player_id != actor.player_id for cell in self.attack_cells(battle, actor)
                   for target in battle.effect_units_at_cells([cell], ignore=actor))

    def ai_payload_value(self, battle: Battle, actor: Unit,
                               payload: dict[str, Any]) -> float | None:
        if "x" not in payload or "y" not in payload:
            return -1.0
        cell = Position(int(payload["x"]), int(payload["y"]))
        if cell not in self.attack_cells(battle, actor):
            return -1.0
        value = sum((1.0 if target.player_id != actor.player_id else -1.6)
                    * (15.0 if target.total_shields() > 0 else
                       0.0 if target.magic_immunity else
                       battle.damage_rule.calculate_damage(actor.stat("attack"), target.stat("defense")) * 100.0)
                    for target in battle.effect_units_at_cells([cell], ignore=actor))
        return value * 2.0 - 12.0

    def ai_optional_choice_score(self, battle: Battle, actor: Unit,
                                 prompt: dict[str, Any], choice: str) -> float | None:
        if prompt.get("action_name") != "锁爪后可瞬移回飞龙船" or "," not in choice:
            return None
        ship = allied_ship(battle, actor.player_id)
        if ship is None or actor.position is None:
            return -1.0
        x, y = (int(part) for part in choice.split(",", 1))
        landing = Position(x, y)
        if landing not in battle.unit_cells(ship):
            return -1.0
        enemies = [unit for unit in battle.all_units() if unit.player_id != actor.player_id
                   and unit.alive and not unit.banished and unit.position is not None]
        current_threat = sum(battle.unit_distance_to_cell(enemy, actor.position)
                             <= enemy.targeting_range() + 1 for enemy in enemies)
        ship_threat = sum(battle.unit_distance_to_cell(enemy, landing)
                          <= enemy.targeting_range() + 1 for enemy in enemies)
        lost_access = (_nearest_enemy_distance(battle, actor, landing)
                       - _nearest_enemy_distance(battle, actor, actor.position)) * 2.0
        return (current_threat - ship_threat) * 12.0 + 9.0 - lost_access + (
            9.0 if actor.current_hp <= actor.max_health * 0.5 else 0.0)


class HelmsmanAIPolicy(ShipCrewMovePolicy):
    _simple_skill_priority = {**ShipCrewMovePolicy._simple_skill_priority,
                              "silver_grappling_hook": 3, "sand_of_time": 5}


class ScoutAIPolicy(ShipCrewMovePolicy):
    _simple_skill_priority = {**ShipCrewMovePolicy._simple_skill_priority,
                              "silver_lock_claw": 2}
