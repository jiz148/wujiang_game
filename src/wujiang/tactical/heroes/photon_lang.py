"""R048 光子朗: temporary photon body, straight drive, allied convergence."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, ActionMiss, Battle, MoveContext, Position, Skill, StatusEffect,
    Unit,
)
from wujiang.tactical.heroes.common import payload_direction, positions_to_dict
from wujiang.tactical.heroes.mechanics.next_five_geometry import ALL_DIRECTIONS


class PhotonizedStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("光子化", "攻+2且生命伤害归零，持续三本人轮。",
                         duration=3, tick_scope="owner_turn_start")

    def modify_stat(self, stat_name: str, value: float) -> float:
        return value + 2 if stat_name == "attack" else value

    def limit_hp_loss(self, amount: float) -> float:
        return 0.0


class PhotonizeSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False
    is_ultimate = True

    def __init__(self) -> None:
        super().__init__("photonize", "光子化", "本场一次；三本人轮攻+2且不受生命伤。",
                         max_uses_per_battle=1)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        if actor.has_status("光子化"):
            return -100.0
        nearby = sum(enemy.player_id != actor.player_id and enemy.position is not None
                     and battle.distance_between_units(actor, enemy) <= 4
                     for enemy in battle.all_units())
        return 48.0 + nearby * 18.0 if nearby else 15.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        actor.add_status(PhotonizedStatus(), source=actor)
        battle.log_public_event(f"{actor.name} 光子化，三轮内攻+2且不受生命伤。", source=actor)


class PhotonDriveDebt(StatusEffect):
    def __init__(self) -> None:
        super().__init__("光子推进后禁移", "下一次己方进攻轮不能主动移动。")
        self.active = False

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        owner = self.owner
        if owner is None:
            return
        if self.active:
            if ended_player_id == owner.player_id:
                owner.remove_status(self, battle)
        elif ended_player_id != owner.player_id:
            # The enemy turn following the drive has ended. The next team
            # attack turn, even when another allied hero is active, is locked.
            self.active = True

    def can_use_normal_move(self, battle: Battle, actor: Unit) -> tuple[bool, str]:
        return (False, "光子推进后的本轮不能移动。") if self.active else (True, "")

    def blocks_skill_use(self, battle: Battle, actor: Unit, skill: Skill) -> tuple[bool, str]:
        if self.active and skill.is_displacement_skill:
            return True, "光子推进后的本轮不能使用位移技能。"
        return False, ""

    def on_before_move(self, battle: Battle, ctx: MoveContext) -> None:
        if (self.active and ctx.unit is self.owner and not ctx.forced
                and (not ctx.via_skill or (battle.resolving_action is not None
                                           and battle.resolving_action.actor_id == ctx.unit.unit_id))):
            ctx.cancelled = True
            ctx.reason = "光子推进后的本轮不能主动移动。"


class PhotonDriveSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False
    ai_complete_direction_payloads = True
    is_displacement_skill = True

    def __init__(self) -> None:
        super().__init__("photon_drive", "光子推进", "每2本人轮一次；沿选定方向移至首单位前，下次进攻轮禁移。",
                         cooldown_turns=2)

    @staticmethod
    def landing(battle: Battle, actor: Unit, direction: tuple[int, int]) -> Position | None:
        if actor.position is None or actor.cannot_move:
            return None
        dx, dy = direction
        previous = actor.position
        limit = max(battle.width, battle.height)
        for step in range(1, limit + 1):
            cell = actor.position.offset(dx * step, dy * step)
            if not battle.in_bounds(cell):
                break
            if not battle.terrain_step_allowed(actor, previous, cell, skill_displacement=True):
                break
            if not battle.can_traverse_position(actor, cell, ignore_units=False):
                break
            previous = cell
        return previous if previous != actor.position else None

    def available(self, battle: Battle, actor: Unit) -> dict[tuple[int, int], Position]:
        return {direction: cell for direction in ALL_DIRECTIONS
                if (cell := self.landing(battle, actor, direction)) is not None}

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        routes = self.available(battle, actor)
        if not routes:
            return False, "所有方向均被挡住，无法推进。"
        if payload is not None and "direction" in payload:
            try:
                if payload_direction(payload) not in routes:
                    return False, "该方向无法推进。"
            except (ActionError, TypeError, ValueError):
                return False, "请选择合法的光子推进方向。"
        return True, ""

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        routes = self.available(battle, actor)
        return {"cells": [], "target_unit_ids": [],
                "secondary_cells": positions_to_dict(routes.values()),
                "requires_target": True,
                "selection": {"mode": "direction_only",
                              "prompt": "选择光子推进方向。",
                              "directions": [{"dx": dx, "dy": dy, "label": f"{dx:+d},{dy:+d}"}
                                             for dx, dy in routes]}}

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "direction": {"dx": dx, "dy": dy}}
                for dx, dy in self.available(battle, actor)]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        try:
            landing = self.available(battle, actor).get(payload_direction(payload))
        except (ActionError, TypeError, ValueError):
            return -1000.0
        if landing is None:
            return -1000.0
        enemies = [unit for unit in battle.enemy_units(actor.player_id) if unit.position is not None]
        if not enemies or actor.position is None:
            return -8.0
        old_distance = min(battle.unit_distance_to_cell(enemy, actor.position) for enemy in enemies)
        new_distance = min(battle.unit_distance_to_cell(enemy, landing) for enemy in enemies)
        return (old_distance - new_distance) * 13.0 - 12.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        landing = self.available(battle, actor).get(payload_direction(payload))
        if landing is None:
            raise ActionMiss("光子推进方向已被阻挡。")
        try:
            moved = battle.move_unit(actor, landing, via_skill=True,
                                     straight_only=True, max_distance=max(battle.width, battle.height),
                                     tags={self.code})
        except ActionError as exc:
            raise ActionMiss("光子推进落点已经失效。") from exc
        if moved.cancelled or actor.position != landing:
            raise ActionMiss("光子推进未完成。")
        actor.add_status(PhotonDriveDebt(), source=actor)


class PhotonSanctuarySkill(Skill):
    ai_payload_value_owned = True
    ai_optional_choice_owned = True
    ai_enemy_impact_requirement = False
    requires_choice_replay_when_used = True

    def __init__(self) -> None:
        super().__init__("photon_sanctuary", "光子圣域", "每本人轮一次；使其他己方单位移动到本单位周围。",
                         max_uses_per_turn=1)

    @staticmethod
    def allies(battle: Battle, actor: Unit) -> list[Unit]:
        return [unit for unit in battle.all_units()
                if unit is not actor and unit.alive and not unit.banished
                and unit.position is not None and unit.player_id == actor.player_id]

    @staticmethod
    def landings(battle: Battle, actor: Unit, ally: Unit) -> list[Position]:
        if actor.position is None or ally.position is None or ally.cannot_move:
            return []
        ring = {cell for source in battle.unit_cells(actor) for cell in battle.neighbors(source)}
        ring.difference_update(battle.unit_cells(actor))
        return sorted((cell for cell in ring
                       if battle.can_place_unit(ally, cell, ignore=ally, mover=ally)
                       and cell not in battle.unit_cells(ally)), key=lambda cell: (cell.y, cell.x))

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if not any(self.landings(battle, actor, ally) for ally in self.allies(battle, actor)):
            return False, "没有能移动到光子圣域周围的友军。"
        return True, ""

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return self.allies(battle, actor)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        allies = self.allies(battle, actor)
        return {"cells": positions_to_dict([cell for ally in allies for cell in battle.unit_cells(ally)]),
                "target_unit_ids": [ally.unit_id for ally in allies],
                "secondary_cells": positions_to_dict([cell for ally in allies
                                                       for cell in self.landings(battle, actor, ally)]),
                "requires_target": False}

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        value = 0.0
        for ally in self.allies(battle, actor):
            options = self.landings(battle, actor, ally)
            if options:
                value += max(self._landing_value(battle, actor, ally, cell)
                             for cell in options)
        return value - 8.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    @staticmethod
    def _landing_value(battle: Battle, actor: Unit, ally: Unit, cell: Position) -> float:
        if ally.position is None:
            return -1000.0
        cluster_gain = max(0, battle.distance_between_units(actor, ally) - 2) * 8.0
        enemies = [enemy for enemy in battle.enemy_units(actor.player_id)
                   if enemy.position is not None]
        if not enemies:
            return cluster_gain
        old_distance = min(battle.unit_distance_to_cell(enemy, ally.position)
                           for enemy in enemies)
        new_distance = min(battle.unit_distance_to_cell(enemy, cell)
                           for enemy in enemies)
        reach = ally.targeting_range()
        attack_gain = (max(0, old_distance - reach) - max(0, new_distance - reach)) * 18.0
        safety_gain = ((new_distance - old_distance) * 6.0
                       if ally.current_hp <= ally.max_health / 2 else 0.0)
        return cluster_gain + attack_gain + safety_gain

    def ai_optional_choice_score(self, battle: Battle, actor: Unit,
                                 prompt: dict[str, Any], choice: str) -> float | None:
        context = prompt.get("choice_context") or {}
        if actor is not self.owner or not context.get("photon_sanctuary"):
            return None
        ally = battle.units.get(str(context.get("ally_id") or ""))
        if ally is None:
            return -1000.0
        x, y = (int(part) for part in choice.split(",", 1))
        return self._landing_value(battle, actor, ally, Position(x, y))

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        for ally in self.allies(battle, actor):
            options = [f"{cell.x},{cell.y}" for cell in self.landings(battle, actor, ally)]
            if not options:
                continue
            selected = battle.optional_position_choice(
                actor, options, action_name=f"光子圣域：选择{ally.name}的落格",
                kind="required_placement",
                choice_context={"photon_sanctuary": True, "ally_id": ally.unit_id})
            if selected not in options:
                continue
            x, y = (int(part) for part in selected.split(",", 1))
            checked = battle.validate_target(actor, ally, action_name=self.name,
                                             is_skill=True, is_hostile=False)
            if checked.cancelled:
                continue
            try:
                battle.move_unit(ally, Position(x, y), via_skill=True, allow_anywhere=True,
                                 max_distance=max(battle.width, battle.height), tags={self.code})
            except ActionError:
                continue
