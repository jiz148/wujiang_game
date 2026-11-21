"""R372 Void Spirit: directional shade, phase return, pulse, and void path."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, DamageContext, Position, QueuedAction, Skill, StatusEffect, Unit,
)
from wujiang.tactical.heroes.common import payload_direction, payload_position, positions_to_dict
from wujiang.tactical.heroes.mechanics.next_five_effects import apply_piercing_status_effect
from wujiang.tactical.heroes.spirit_remnants import (
    SpiritPointTrait, SpiritRemnant, remnants_for, spirit_points, square_cells, visible_units_at,
)


_DIRECTIONS = ((0, -1), (1, 0), (0, 1), (-1, 0))
_SHADE = "残阴"
_POINTS = "太虚计数点"


def _direction_options() -> list[dict[str, Any]]:
    return [{"dx": dx, "dy": dy, "label": f"{dx:+d},{dy:+d}"} for dx, dy in _DIRECTIONS]


def _present(unit: Unit | None) -> bool:
    return unit is not None and unit.alive and not unit.banished and unit.position is not None


class VoidPointTrait(SpiritPointTrait):
    def __init__(self) -> None:
        super().__init__(_POINTS, 2, gain_on_owner_only=True,
                         spending_skill_codes=frozenset({"spirit_void_path"}))


class ShadeRootStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("残阴禁移", "到下次本人轮末不能普通移动。", duration=1)

    def can_use_normal_move(self, battle: Battle, actor: Unit,
                            destination: Position | None = None) -> tuple[bool, str]:
        return False, "残阴禁移：不能普通移动。"


class ShadeRemnant(SpiritRemnant):
    """The source's actual turns, not unrelated unit turns, age this marker."""

    def __init__(self, source: Unit, cell: Position, direction: tuple[int, int]) -> None:
        super().__init__(_SHADE, source.unit_id, cell, direction=direction, duration=3)

    def line(self, battle: Battle) -> list[Position]:
        return battle.line_positions(self.cell, self.direction, 3)

    def first_enemy(self, battle: Battle, source: Unit) -> Unit | None:
        for cell in self.line(battle):
            units = visible_units_at(battle, [cell], ignore=source)
            enemy = next((unit for unit in units if unit.player_id != source.player_id), None)
            if enemy is not None:
                return enemy
        return None

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        source = battle.units.get(self.source_unit_id)
        if source is None or not source.alive or ended_player_id != source.player_id:
            return
        if not battle.unit_belongs_to_current_turn(source):
            return
        target = self.first_enemy(battle, source)
        if target is not None:
            battle.queue_skill_effect_action(
                actor=source, display_name="残阴牵引", effect_code="spirit_dark_remnant_hit",
                target_units=[target], target_cells=battle.unit_cells(target),
                include_cell_units=False,
                payload={"ignore_shield": True, "tags": ["skill", "piercing", "spirit_dark_remnant"],
                         "shade_x": self.cell.x, "shade_y": self.cell.y,
                         "target_must_occupy_declared_cell": True},
                effect_resolver=self.resolve_hit,
            )
        self.duration -= 1
        if self.duration <= 0:
            battle.remove_field_effect(self)

    @staticmethod
    def resolve_hit(battle: Battle, source: Unit, queued: QueuedAction) -> None:
        shade_cell = Position(int(queued.payload["shade_x"]), int(queued.payload["shade_y"]))
        for unit_id in queued.target_unit_ids:
            target = battle.units.get(unit_id)
            if not _present(target):
                continue
            battle.resolve_damage(DamageContext(source=source, target=target,
                attack_power=source.stat("attack"), is_skill=True,
                action_name="残阴牵引伤害", tags={"skill", "spirit_dark_remnant"}))
            if not _present(target):
                continue
            old = target.position
            dx = (shade_cell.x > old.x) - (shade_cell.x < old.x)
            dy = (shade_cell.y > old.y) - (shade_cell.y < old.y)
            landing = old
            for _ in range(3):
                candidate = landing.offset(dx, dy)
                if (not battle.in_bounds(candidate) or
                        candidate.distance_to(shade_cell) >= landing.distance_to(shade_cell) or
                        not battle.can_place_unit(target, candidate, mover=target)):
                    break
                landing = candidate
            if landing != old:
                try:
                    battle.move_unit(target, landing, via_skill=True, forced=True,
                                     allow_anywhere=True, max_distance=3,
                                     tags={"spirit_dark_remnant"})
                except ActionError:
                    battle.log_public_event(f"{target.name} 的残阴牵引位移受阻。", source=source, target=target)
            apply_piercing_status_effect(battle, source, target, action_name="残阴牵引",
                status=ShadeRootStatus(), is_skill=True,
                tags={"skill", "spirit_dark_remnant"},
                ignore_targeting_restrictions=True)


class DarkRemnantSkill(Skill):
    ai_payload_value_owned = True
    ai_complete_direction_payloads = True

    def __init__(self) -> None:
        super().__init__("spirit_dark_remnant", _SHADE,
                         "3本人轮一次；放朝向残阴，持续3轮，每本人轮末伤害并牵引朝向首敌。",
                         cooldown_turns=3, target_mode="cell", direction_mode="required")

    def cells(self, battle: Battle, actor: Unit) -> list[Position]:
        return [cell for y in range(battle.height) for x in range(battle.width)
                if (cell := Position(x, y)) != actor.position
                and battle.can_place_unit(actor, cell, ignore=actor, mover=actor)
                and not any(remnant.cell == cell for remnant in remnants_for(battle, actor, _SHADE))]

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        cells = self.cells(battle, actor)
        if not cells:
            return False, "没有可放置残阴的位置。"
        if payload and payload.get("x") is not None:
            if payload_position(payload) not in cells or payload_direction(payload) not in _DIRECTIONS:
                return False, "请选择合法残阴格和正交方向。"
        return True, ""

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        battle.add_field_effect(ShadeRemnant(actor, payload_position(payload), payload_direction(payload)),
                                source=actor)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {"cells": positions_to_dict(self.cells(battle, actor)),
                "target_unit_ids": [], "secondary_cells": [], "requires_target": True,
                "selection": {"mode": "cell_direction", "directions": _direction_options()}}

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        candidates = []
        for cell in self.cells(battle, actor):
            for direction in _DIRECTIONS:
                payload = {"type": "skill", "unit_id": actor.unit_id,
                           "skill_code": self.code, "x": cell.x, "y": cell.y,
                           "direction": {"dx": direction[0], "dy": direction[1]}}
                value = self.ai_payload_value(battle, actor, payload)
                if value > 0:
                    candidates.append((value, payload))
        return [payload for _, payload in sorted(candidates, key=lambda item: item[0], reverse=True)[:24]]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        marker = ShadeRemnant(actor, payload_position(payload), payload_direction(payload))
        target = marker.first_enemy(battle, actor)
        return 55.0 + target.stat("speed") * 8.0 if target is not None else -30.0

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit,
                                  payload: dict[str, Any], targets: list[Unit] | None = None,
                                  shared: Any = None, action: dict[str, Any] | None = None) -> bool:
        return self.ai_payload_value(battle, actor, payload) > 0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)


class PhaseReturnStatus(StatusEffect):
    requires_damage_choice = True

    def __init__(self, origin: Position) -> None:
        super().__init__("异化回场", "本实际回合末在原点9×9最近合法位置回场并爆发。")
        self.origin = origin

    def legal_landings(self, battle: Battle, owner: Unit) -> list[Position]:
        legal = [cell for y in range(battle.height) for x in range(battle.width)
                 for cell in (Position(x, y),)
                 if abs(cell.x - self.origin.x) <= 4 and abs(cell.y - self.origin.y) <= 4
                 and battle.can_place_unit(owner, cell, ignore=owner, mover=owner)]
        if self.origin in legal:
            return [self.origin]
        nearest = min((cell.distance_to(self.origin) for cell in legal), default=None)
        return [cell for cell in legal if cell.distance_to(self.origin) == nearest]

    def on_owner_turn_end(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or not owner.alive or not owner.banished:
            return
        choices = self.legal_landings(battle, owner)
        if not choices:
            owner.banish_turns_remaining = max(owner.banish_turns_remaining, 2)
            battle.log_public_event(f"{owner.name} 的异化回场暂无合法格，等待下一次本人轮末。", source=owner)
            return
        cell = choices[0]
        if len(choices) > 1:
            options = [f"{landing.x},{landing.y}" for landing in choices]
            choice = battle.optional_position_choice(owner, options, action_name="异化最近合法回场格",
                kind="required_placement", choice_context={"spirit_phase_return": True})
            if choice not in options:
                return
            x, y = (int(part) for part in choice.split(",", 1))
            cell = Position(x, y)
        battle.restore_banished_unit(owner, cell)
        owner.remove_status(self, battle)
        cells = square_cells(battle, cell, 1)
        battle.queue_skill_effect_action(actor=owner, display_name="异化回场爆发",
            effect_code="spirit_phase_burst", target_cells=cells,
            target_units=visible_units_at(battle, cells, ignore=owner),
            include_cell_units=False, effect_resolver=self.resolve_burst)

    @staticmethod
    def resolve_burst(battle: Battle, actor: Unit, queued: QueuedAction) -> None:
        for unit_id in queued.target_unit_ids:
            target = battle.units.get(unit_id)
            if _present(target):
                battle.resolve_damage(DamageContext(source=actor, target=target,
                    attack_power=actor.stat("attack"), is_skill=True,
                    action_name="异化回场爆发", tags={"skill", "spirit_phase"}))

    def ai_optional_choice_score(self, battle: Battle, actor: Unit,
                                 prompt: dict[str, Any], choice: str) -> float | None:
        if actor is not self.owner or not (prompt.get("choice_context") or {}).get("spirit_phase_return"):
            return None
        x, y = (int(part) for part in choice.split(",", 1))
        cell = Position(x, y)
        return sum(55.0 if unit.player_id != actor.player_id else -65.0
                   for unit in visible_units_at(battle, square_cells(battle, cell, 1), ignore=actor))


class PhaseSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("spirit_phase", "异化", "随时使用；3本人轮一次；本回合末从原处9×9回场并周围爆发。",
                         cooldown_turns=3, timing="instant", target_mode="self")

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        return (True, "") if ok and actor.position is not None and not actor.banished else (False, reason or "不在场上。")

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        origin = actor.position
        if origin is None:
            return
        actor.add_status(PhaseReturnStatus(origin), source=actor)
        battle.banish_unit(actor, 1)

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code}]

    def ai_reaction_payloads(self, battle: Battle, actor: Unit, queued_action: QueuedAction,
                             option: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "chain_react", "unit_id": actor.unit_id, "action_code": self.code}]

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        if actor.position is None:
            return -1000.0
        nearby = visible_units_at(battle, square_cells(battle, actor.position, 1), ignore=actor)
        burst = sum(60.0 if unit.player_id != actor.player_id else -80.0 for unit in nearby)
        danger = 35.0 if actor.current_hp < actor.max_health / 2 else 0.0
        return burst + danger - 15.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def ai_reaction_score(self, context: Any) -> float:
        return 70.0 + self.ai_payload_value(context.battle, context.actor, context.payload)


class ResonanceBufferStatus(StatusEffect):
    def __init__(self, extra_health: float) -> None:
        super().__init__("共鸣额外生命", "到下一本人轮初；只吸普攻伤，余量存在时挡普攻非伤附效。")
        self.extra_health = extra_health

    def on_owner_turn_start(self, battle: Battle) -> None:
        if self.owner is not None:
            self.owner.remove_status(self, battle)

    def on_final_hp_loss(self, battle: Battle, ctx: DamageContext) -> None:
        if (ctx.target is not self.owner or ctx.is_skill or "attack" not in ctx.tags
                or ctx.raw_damage <= 0 or self.extra_health <= 0):
            return
        blocked = min(self.extra_health, ctx.raw_damage)
        self.extra_health = round(self.extra_health - blocked, 4)
        ctx.raw_damage = max(0.0, ctx.raw_damage - blocked)
        battle.log_public_event(f"共鸣脉冲吸收 {blocked:g} 普攻伤。", source=ctx.source, target=ctx.target)

    def blocks_basic_attack_non_damage_effects(self) -> bool:
        return self.extra_health > 0

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data["extra_health"] = self.extra_health
        return data


class ResonancePulseSkill(Skill):
    ai_payload_value_owned = True

    def __init__(self) -> None:
        super().__init__("spirit_resonance", "共鸣脉冲",
                         "3本人轮一次；周围5×5伤害，每实际受体给本人最大血1/4普攻额外生命。",
                         cooldown_turns=3, target_mode="self")

    def targets(self, battle: Battle, actor: Unit) -> list[Unit]:
        return visible_units_at(battle, square_cells(battle, actor.position, 2), ignore=actor) if actor.position else []

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        return (True, "") if ok and self.targets(battle, actor) else (False, reason or "周围没有受体。")

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {"cells": positions_to_dict(square_cells(battle, actor.position, 2)) if actor.position else [],
                "target_unit_ids": [unit.unit_id for unit in self.targets(battle, actor)],
                "secondary_cells": [], "requires_target": False}

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return self.targets(battle, actor)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        targets = self.targets(battle, actor)
        battle.queue_skill_effect_action(actor=actor, display_name=self.name,
            effect_code="spirit_resonance_hit", target_units=targets,
            target_cells=square_cells(battle, actor.position, 2),
            include_cell_units=False, effect_resolver=self._resolve_pulse)

    def _resolve_pulse(self, battle: Battle, actor: Unit, queued: QueuedAction) -> None:
        count = 0
        for unit_id in queued.target_unit_ids:
            target = battle.units.get(unit_id)
            if not _present(target):
                continue
            count += 1
            battle.resolve_damage(DamageContext(source=actor, target=target,
                attack_power=actor.stat("attack"), is_skill=True,
                action_name=self.name, tags={"skill", self.code}))
        if count and actor.alive:
            old = actor.get_status("共鸣额外生命")
            extra = count * actor.max_health / 4 + (old.extra_health if isinstance(old, ResonanceBufferStatus) else 0)
            if old is not None:
                actor.remove_status(old, battle)
            actor.add_status(ResonanceBufferStatus(extra), source=actor)

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code}]

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        targets = self.targets(battle, actor)
        return sum(90.0 if unit.player_id != actor.player_id else -110.0 for unit in targets) + len(targets) * 15.0

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit,
                                  payload: dict[str, Any], targets: list[Unit] | None = None,
                                  shared: Any = None, action: dict[str, Any] | None = None) -> bool:
        return self.ai_payload_value(battle, actor, payload) > 0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)


class VoidPathSkill(Skill):
    is_displacement_skill = True
    ai_payload_value_owned = True
    ai_complete_direction_payloads = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("spirit_void_path", "太虚之径",
                         "耗1太虚点；正交穿人至多5格，对经过的单位造成伤害。",
                         target_mode="cell", direction_mode="required")

    def route(self, battle: Battle, actor: Unit,
              direction: tuple[int, int]) -> tuple[Position, list[Position], list[Unit]]:
        if actor.position is None or direction not in _DIRECTIONS:
            raise ActionError("请选择太虚之径的正交方向。")
        cells = battle.line_positions(actor.position, direction, 5)
        landings = [cell for cell in cells if battle.can_place_unit(actor, cell, ignore=actor, mover=actor)]
        if not landings:
            raise ActionError("太虚之径没有可落脚的格子。")
        landing = landings[-1]
        crossed = cells[:cells.index(landing) + 1]
        return landing, crossed, visible_units_at(battle, crossed, ignore=actor)

    def directions(self, battle: Battle, actor: Unit) -> list[tuple[int, int]]:
        found = []
        for direction in _DIRECTIONS:
            try:
                self.route(battle, actor, direction)
            except ActionError:
                continue
            found.append(direction)
        return found

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if spirit_points(actor, _POINTS).points < 1:
            return False, "太虚计数点不足。"
        directions = self.directions(battle, actor)
        if not directions:
            return False, "太虚之径没有可走的方向。"
        if payload and ("direction" in payload or "dx" in payload):
            if payload_direction(payload) not in directions:
                return False, "太虚之径方向不可达。"
        return True, ""

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        directions = self.directions(battle, actor) if actor.position else []
        cells = list(dict.fromkeys(cell for direction in directions
            for cell in battle.line_positions(actor.position, direction, 5)))
        return {"cells": positions_to_dict(cells), "target_unit_ids": [],
                "secondary_cells": [], "requires_target": True,
                "selection": {"mode": "direction_only", "directions": [
                    {"dx": dx, "dy": dy, "label": f"{dx:+d},{dy:+d}"} for dx, dy in directions]}}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return self.route(battle, actor, payload_direction(payload))[1]

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return self.route(battle, actor, payload_direction(payload))[2]

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        destination, cells, targets = self.route(battle, actor, payload_direction(payload))
        battle.move_unit(actor, destination, via_skill=True, straight_only=True,
                         ignore_units=True, allow_anywhere=True, max_distance=5,
                         tags={self.code})
        for target in targets:
            battle.queue_skill_effect_action(actor=actor, display_name=self.name,
                effect_code="spirit_void_path_hit", target_units=[target],
                target_cells=battle.unit_cells(target), include_cell_units=False,
                effect_resolver=self._resolve_hit)

    def _resolve_hit(self, battle: Battle, actor: Unit, queued: QueuedAction) -> None:
        for unit_id in queued.target_unit_ids:
            target = battle.units.get(unit_id)
            if _present(target):
                battle.resolve_damage(DamageContext(source=actor, target=target,
                    attack_power=actor.stat("attack"), is_skill=True,
                    action_name=self.name, tags={"skill", self.code}))

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "direction": {"dx": dx, "dy": dy}} for dx, dy in self.directions(battle, actor)]

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        landing, _, targets = self.route(battle, actor, payload_direction(payload))
        score = sum(85.0 if unit.player_id != actor.player_id else -105.0 for unit in targets)
        nearest = min((landing.distance_to(unit.position) for unit in battle.enemy_units(actor.player_id)
                       if unit.position is not None), default=8)
        return score + max(0, 5 - nearest) * 3.0 - 12.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)
