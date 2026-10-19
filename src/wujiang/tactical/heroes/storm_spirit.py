"""Source-locked rules for 风暴之灵 (Excel row 306)."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, ActionMiss, Battle, BattleFieldEffect, DamageContext, MoveContext,
    Position, Skill, StatusEffect, Trait, Unit,
)
from wujiang.tactical.heroes.common import (
    match_payload_pattern, pattern_selection_preview, payload_position,
    payload_target_unit, positions_to_dict,
)
from wujiang.tactical.heroes.next_five import ALL_DIRECTIONS, apply_piercing_status_effect, remote_rectangle_patterns


def _direction(start: Position, end: Position) -> tuple[int, int]:
    dx, dy = end.x - start.x, end.y - start.y
    direction = (0 if dx == 0 else (1 if dx > 0 else -1),
                 0 if dy == 0 else (1 if dy > 0 else -1))
    if direction not in ALL_DIRECTIONS or (dx and dy and abs(dx) != abs(dy)):
        raise ActionError("必须选择八方向直线上的格子。")
    return direction


class StormSpiritTrait(Trait):
    def __init__(self) -> None:
        super().__init__("风暴之灵", "双方武将轮始回魔3加近域破坏累计；合格施法重置普攻并强化下一次2×2普攻。")
        self.destroyed_nearby = 0
        self.enhanced_attack_pending = False

    def on_any_turn_start(self, battle: Battle, active_unit: Unit) -> None:
        owner = self.owner
        if owner is None or not owner.alive or owner.position is None or owner.banished:
            return
        gained = owner.gain_mana(3 + self.destroyed_nearby)
        if gained:
            battle.log_public_event(f"{owner.name} 的风暴回魔 +{gained:g}（近域破坏累计 {self.destroyed_nearby}）。", source=owner)

    def on_unit_destroyed(self, battle: Battle, destroyed_unit: Unit) -> None:
        owner = self.owner
        cells = getattr(destroyed_unit, "last_destroyed_cells", [])
        if (owner is None or not owner.alive or owner.position is None or owner.banished
                or not cells or not any(origin.distance_to(cell) <= 3
                                     for origin in battle.unit_cells(owner) for cell in cells)):
            return
        self.destroyed_nearby += 1
        battle.log_public_event(f"{owner.name} 附近单位被破坏，风暴轮始回魔提高至 {3 + self.destroyed_nearby}。", source=owner)

    def on_owner_action_declared(self, battle: Battle, action_type: str, payload: dict[str, Any]) -> None:
        owner = self.owner
        if owner is None or action_type != "skill":
            return
        code = str(payload.get("skill_code") or "")
        if code not in {"electronic_storm", "afterimage", "lightning_chain"}:
            return
        if code == "electronic_storm" and owner.get_skill(code).mana_cost_for_payload(battle, owner, payload) <= 2:
            return
        owner.attacks_used = 0
        self.enhanced_attack_pending = True
        battle.log_public_event(f"{owner.name} 施法后重置普攻次数；下一次普攻强化为2×2、攻+1、半破魔。", source=owner)

    def patterns(self, battle: Battle, actor: Unit) -> list[list[Position]]:
        return remote_rectangle_patterns(battle, actor, 2, 2)

    def basic_attack_area_cells(self, battle: Battle, actor: Unit,
                                payload: dict[str, Any] | None = None) -> list[Position] | None:
        if not self.enhanced_attack_pending or (payload or {}).get("reaction_attack"):
            return None
        declared = (payload or {}).get("attack_cells") or (payload or {}).get("cells")
        if not declared:
            if (payload or {}).get("queued_resolution"):
                raise ActionError("风暴余响普攻必须选择一个合法2×2区域。")
            return None
        return match_payload_pattern({"cells": declared}, self.patterns(battle, actor))

    def basic_attack_payload_metadata(self, battle: Battle, actor: Unit,
                                      payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.enhanced_attack_pending or (payload or {}).get("reaction_attack"):
            return {}
        return {"area_attack": True, "single_hit_per_recipient": True, "attack_bonus": 1,
                "half_ignore_shield": True, "attack_name": "风暴余响",
                "attack_note": "下一次普攻选择2×2范围，攻+1且半破魔。"}

    def basic_attack_preview(self, battle: Battle, actor: Unit,
                             payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        if not self.enhanced_attack_pending or (payload or {}).get("reaction_attack"):
            return None
        patterns = self.patterns(battle, actor)
        preview = pattern_selection_preview(patterns)
        covered = {cell for pattern in patterns for cell in pattern}
        preview.update({"target_unit_ids": [unit.unit_id for unit in battle.all_units()
                                             if unit is not actor and any(cell in covered for cell in battle.unit_cells(unit))],
                        "secondary_cells": [], "requires_target": True})
        return preview

    def on_basic_attack_finished(self, battle: Battle, actor: Unit, payload: dict[str, Any],
                                 damage_contexts: list[DamageContext], missed: bool) -> None:
        if actor is self.owner and payload.get("attack_name") == "风暴余响":
            self.enhanced_attack_pending = False

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data.update({"nearby_destroyed": self.destroyed_nearby,
                     "turn_start_mana_recovery": 3 + self.destroyed_nearby,
                     "enhanced_attack_pending": self.enhanced_attack_pending})
        return data


def _resolve_storm_crossing(battle: Battle, actor: Unit, action: Any) -> None:
    payload = action.payload
    target = battle.units.get(str(payload.get("crossed_unit_id") or ""))
    cells = battle.payload_positions(payload, "cells")
    if (target is None or not target.alive or target.position is None or target.player_id == actor.player_id
            or not any(cell in battle.unit_cells(target) for cell in cells)):
        battle.log("【电子风暴】穿过的目标已离开原格，未命中。")
        return
    battle.resolve_damage(DamageContext(source=actor, target=target,
        attack_power=float(payload["attack_power"]), is_skill=True,
        action_name="电子风暴", tags={"skill", "movement", "electronic_storm"}))
    battle.check_win_condition()


class ElectronicStormSkill(Skill):
    target_cells_are_geometry_only = True
    excludes_caster_from_effect = True
    excludes_allies_from_effect = True

    def __init__(self) -> None:
        super().__init__("electronic_storm", "电子风暴", "随时；直线位移n格恰费n魔，可穿人；n至少5且穿敌时对每名敌方受体造成当前攻技能伤害。",
                         timing="instant", target_mode="cell")

    def mana_cost_text(self) -> str:
        return "所选直线移动格数n"

    def mana_cost_for_payload(self, battle: Battle, actor: Unit,
                              payload: dict[str, Any] | None = None) -> float:
        if actor.position is None or not payload or payload.get("x") is None or payload.get("y") is None:
            return 1.0
        destination = payload_position(payload)
        return float(actor.position.distance_to(destination))

    def path(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        if actor.position is None:
            raise ActionError("风暴之灵不在战场上。")
        destination = payload_position(payload)
        _direction(actor.position, destination)
        distance = actor.position.distance_to(destination)
        if distance <= 0:
            raise ActionError("电子风暴至少移动一格。")
        return battle.find_path(actor, destination, max_distance=distance,
                                exact_distance=distance, straight_only=True, ignore_units=True)

    def queued_payload_metadata(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> dict[str, Any]:
        if actor.cannot_move:
            raise ActionError("无法移动时不能使用电子风暴。")
        path = self.path(battle, actor, payload)
        return {"storm_path": positions_to_dict(path), "storm_distance": len(path) - 1}

    def queued_reaction_payload_metadata(self, battle: Battle, actor: Unit,
                                         payload: dict[str, Any]) -> dict[str, Any]:
        return self.queued_payload_metadata(battle, actor, payload)

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return battle.payload_positions(payload, "storm_path")[1:] or self.path(battle, actor, payload)[1:]

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        path = battle.payload_positions(payload, "storm_path") or self.path(battle, actor, payload)
        if len(path) - 1 < 5:
            return []
        return list(dict.fromkeys(unit for unit, _ in battle.path_crossing_events(actor, path)
                                  if unit.player_id != actor.player_id))

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        declared = battle.payload_positions(payload, "storm_path")
        destination = declared[-1] if declared else payload_position(payload)
        actor.position = getattr(actor, "_resolution_actual_position", None) or actor.position
        distance = int(payload.get("storm_distance") or actor.position.distance_to(destination))
        try:
            planned = self.path(battle, actor, {"x": destination.x, "y": destination.y})
            crossings = battle.path_crossing_events(actor, planned) if distance >= 5 else []
            battle.move_unit(actor, destination, via_skill=True, straight_only=True,
                             ignore_units=True, max_distance=distance, exact_distance=distance,
                             tags={self.code})
        except ActionError as exc:
            if payload.get("queued_resolution"):
                raise ActionMiss(f"【电子风暴】已付魔，但直线路径或落点变化，位移落空：{exc}") from None
            raise
        if distance < 5 or not actor.alive:
            return
        seen: set[str] = set()
        enemy_crossings = [(unit, cell) for unit, cell in crossings
                           if unit.player_id != actor.player_id and unit.unit_id not in seen
                           and not seen.add(unit.unit_id)]
        for index, (unit, cell) in enumerate(enemy_crossings, 1):
            battle.queue_skill_effect_action(actor=actor, display_name=self.name,
                effect_code="electronic_storm_crossing", effect_resolver=_resolve_storm_crossing,
                payload={"crossed_unit_id": unit.unit_id, "cells": [cell.to_dict()],
                         "attack_power": actor.stat("attack")},
                target_units=[unit], target_cells=[cell], include_cell_units=False,
                segment_index=index, segment_count=len(enemy_crossings))

    def react(self, battle: Battle, actor: Unit, payload: dict[str, Any], queued_action: Any) -> None:
        try:
            self.execute(battle, actor, payload)
        except ActionError as exc:
            battle.log_public_event(f"{actor.name} 已付电子风暴的魔，但连锁改变路径或落点，位移落空：{exc}", source=actor)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        if actor.position is None or actor.cannot_move:
            return {"cells": [], "target_unit_ids": [], "secondary_cells": [], "requires_target": True}
        cells = battle.reachable_positions(actor, max_distance=min(int(actor.current_mana),
                          max(battle.width, battle.height)), straight_only=True, ignore_units=True)
        return {"cells": positions_to_dict(cells), "target_unit_ids": [],
                "secondary_cells": [], "requires_target": True,
                "mana_cost_text": "距离n格消耗n魔；至少5格且穿敌才造成伤害"}


class AfterimageField(BattleFieldEffect):
    def __init__(self, source: Unit, position: Position, direction: tuple[int, int]) -> None:
        super().__init__("残影", "无占位；本人轮沿固定方向前进，触碰非施术者单位伤3，第三格后消失。")
        self.source_unit_id = source.unit_id
        self.position = position
        self.direction = direction
        self.step = 1

    def affected_cells(self, battle: Battle) -> list[Position]:
        return [self.position] if battle.in_bounds(self.position) else []

    def board_marker(self, battle: Battle) -> str:
        return "残影"

    def touch(self, battle: Battle, units: list[Unit]) -> None:
        source = battle.units.get(self.source_unit_id)
        if source is None or not source.alive:
            battle.remove_field_effect(self)
            return
        seen: set[str] = set()
        for unit in units:
            recipient = battle.effect_recipient(unit)
            if (recipient.unit_id == source.unit_id or recipient.unit_id in seen
                    or not recipient.alive or recipient.position is None):
                continue
            seen.add(recipient.unit_id)
            battle.resolve_damage(DamageContext(source=source, target=recipient,
                attack_power=3, is_skill=True, action_name="残影", tags={"skill", "afterimage"}))
        battle.cleanup_dead_units()

    def on_turn_start(self, battle: Battle, active_unit: Unit | None) -> None:
        if active_unit is None or active_unit.unit_id != self.source_unit_id:
            return
        source = battle.units.get(self.source_unit_id)
        if source is None or not source.alive or source.position is None or source.banished:
            battle.remove_field_effect(self)
            return
        self.position = self.position.offset(*self.direction)
        self.step += 1
        battle.log_public_event(f"{source.name} 的残影前进至第 {self.step} 格。", source=source)
        if battle.in_bounds(self.position):
            self.touch(battle, battle.units_at_cells([self.position]))
        if self.step >= 3 or not battle.in_bounds(self.position):
            battle.remove_field_effect(self)

    def on_unit_moved(self, battle: Battle, ctx: MoveContext) -> None:
        if self not in battle.field_effects or ctx.unit.unit_id == self.source_unit_id:
            return
        if any(self.position in battle.unit_cells_at(ctx.unit, anchor) for anchor in ctx.path[1:]):
            self.touch(battle, [ctx.unit])

    def on_unit_entered(self, battle: Battle, unit: Unit) -> None:
        if self in battle.field_effects and unit.unit_id != self.source_unit_id and self.position in battle.unit_cells(unit):
            self.touch(battle, [unit])

    def on_before_dead_cleanup(self, battle: Battle) -> None:
        source = battle.units.get(self.source_unit_id)
        if source is None or not source.alive:
            battle.remove_field_effect(self)

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data.update({"source_unit_id": self.source_unit_id, "step": self.step,
                     "direction": {"dx": self.direction[0], "dy": self.direction[1]}})
        return data


class AfterimageSkill(Skill):
    target_cells_are_geometry_only = True

    def __init__(self) -> None:
        super().__init__("afterimage", "残影", "费0.5魔，每本人轮一次；身前第一格留下残影，随后本人轮逐格前进至第三格。",
                         mana_cost=0.5, max_uses_per_turn=1, target_mode="cell")

    def chosen_cell(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> Position:
        if actor.position is None:
            raise ActionError("风暴之灵不在战场上。")
        cell = payload_position(payload)
        if actor.position.distance_to(cell) != 1:
            raise ActionError("残影必须放置在身前相邻一格。")
        _direction(actor.position, cell)
        if not battle.in_bounds(cell) or (cell.x, cell.y) in battle.blocked_cells:
            raise ActionError("残影首格不合法。")
        return cell

    def queued_payload_metadata(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> dict[str, Any]:
        return {"afterimage_cell": self.chosen_cell(battle, actor, payload).to_dict()}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return [self.chosen_cell(battle, actor, payload)]

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return [unit for unit in battle.effect_units_at_cells([self.chosen_cell(battle, actor, payload)])
                if unit is not actor]

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cell = Position(**payload["afterimage_cell"]) if payload.get("afterimage_cell") else self.chosen_cell(battle, actor, payload)
        origin = Position(int(payload.get("declared_source_x", actor.position.x)),
                          int(payload.get("declared_source_y", actor.position.y)))
        field = AfterimageField(actor, cell, _direction(origin, cell))
        battle.add_field_effect(field, source=actor)
        field.touch(battle, battle.units_at_cells([cell]))

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        if actor.position is None:
            return {"cells": [], "target_unit_ids": [], "secondary_cells": [], "requires_target": True}
        cells = [actor.position.offset(dx, dy) for dx, dy in ALL_DIRECTIONS]
        cells = [cell for cell in cells if battle.in_bounds(cell) and (cell.x, cell.y) not in battle.blocked_cells]
        return {"cells": positions_to_dict(cells), "target_unit_ids": [],
                "secondary_cells": [], "requires_target": True}


class LightningChainLockStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("雷电锁链封锁", "下一个本人轮无法普通移动或使用主动技能，到该轮末解除。")
        self.activated = False
        self.flag_name = "cannot_normal_move"

    @property
    def provided_flags(self) -> frozenset[str]:
        return frozenset({self.flag_name}) if self.activated else frozenset()

    def on_owner_turn_start(self, battle: Battle) -> None:
        if self.owner is not None:
            self.activated = True
            self.owner.cannot_normal_move = True

    def on_owner_turn_end(self, battle: Battle) -> None:
        if self.activated and self.owner is not None:
            self.owner.remove_status(self, battle)

    def blocks_skill_use(self, battle: Battle, actor: Unit, skill: Skill) -> tuple[bool, str]:
        if self.activated and actor is self.owner and skill.timing == "active":
            return True, "雷电锁链封锁了本回合的主动技能。"
        return False, ""

    def on_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is not None:
            owner.cannot_normal_move = any(
                (getattr(status, "flag_name", None) == "cannot_normal_move"
                 and getattr(status, "value", True) and getattr(status, "flag_active", True)
                 and (not isinstance(status, LightningChainLockStatus) or status.activated))
                or "cannot_normal_move" in getattr(status, "provided_flags", ())
                for status in owner.statuses)

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data["active_on_current_turn"] = self.activated
        return data


class LightningChainSkill(Skill):
    def __init__(self) -> None:
        super().__init__("lightning_chain", "雷电锁链", "费1魔、每本人轮一次；范内直线单体无伤破魔，推一格并封锁其下个本人轮主动技能和普通移动。",
                         mana_cost=1, max_uses_per_turn=1, target_mode="enemy")

    def strike_cell(self, battle: Battle, actor: Unit, target: Unit,
                    payload: dict[str, Any]) -> Position:
        if actor.position is None or target.position is None:
            raise ActionError("雷电锁链的目标不在战场上。")
        candidates = [cell for cell in battle.unit_cells(target)
                      if battle.cells_are_straight_aligned(actor.position, cell)
                      and actor.position.distance_to(cell) <= actor.targeting_range()]
        if not candidates:
            raise ActionError("雷电锁链目标不在范内直线。")
        if payload.get("x") is not None and payload.get("y") is not None:
            chosen = Position(int(payload["x"]), int(payload["y"]))
            if chosen in candidates:
                return chosen
        return min(candidates, key=lambda cell: (actor.position.distance_to(cell), cell.y, cell.x))

    def queued_payload_metadata(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> dict[str, Any]:
        target = payload_target_unit(battle, payload)
        if target.player_id == actor.player_id:
            raise ActionError("雷电锁链只能选择敌方单位。")
        return {"lightning_direction": _direction(actor.position, self.strike_cell(battle, actor, target, payload))}

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        if target.player_id == actor.player_id:
            raise ActionError("雷电锁链只能选择敌方单位。")
        status = LightningChainLockStatus()
        if not apply_piercing_status_effect(battle, actor, target, action_name=self.name,
                status=status, is_skill=True, tags={"skill", self.code}, cannot_evade=False):
            return
        direction = tuple(payload.get("lightning_direction") or _direction(
            actor.position, self.strike_cell(battle, actor, target, payload)))
        if target.position is not None:
            destination = target.position.offset(*direction)
            if battle.in_bounds(destination):
                try:
                    battle.move_unit(target, destination, via_skill=True, forced=True,
                                     max_distance=1, exact_distance=1, tags={self.code})
                except ActionError:
                    battle.log_public_event(f"{target.name} 不能沿雷电锁链方向位移，封锁仍生效。", source=actor, target=target)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = [unit for unit in battle.all_units()
                   if unit.player_id != actor.player_id and unit.position is not None and unit.alive
                   and battle.unit_can_be_selected(unit, actor=actor)[0]
                   and battle.unit_target_in_range_and_line(actor, unit, actor.targeting_range())]
        return {"cells": positions_to_dict([cell for unit in targets for cell in battle.unit_cells(unit)]),
                "target_unit_ids": [unit.unit_id for unit in targets],
                "secondary_cells": [], "requires_target": True}

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return [payload_target_unit(battle, payload)]
