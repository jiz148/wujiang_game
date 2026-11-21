"""R355 热血考拉教练: moving formation, attack aura and piercing crush."""

from __future__ import annotations

from itertools import combinations
from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, BattleFieldEffect, DamageContext, HeroUnit, Position,
    Skill, Trait, Unit,
)
from wujiang.tactical.heroes.common import DashMoveSkill, StatModifierStatus, payload_direction, positions_to_dict
from wujiang.tactical.heroes.mechanics.next_five_geometry import ALL_DIRECTIONS, square_around_cells


def _present(unit: Unit | None) -> bool:
    return unit is not None and unit.alive and not unit.banished and unit.position is not None


def _aura_cells(battle: Battle, actor: Unit) -> list[Position]:
    return square_around_cells(battle, battle.unit_cells(actor), radius=2) if _present(actor) else []


def _within_aura(battle: Battle, actor: Unit, other: Unit) -> bool:
    area = set(_aura_cells(battle, actor))
    return _present(other) and any(cell in area for cell in battle.unit_cells(other))


class KoalaLargeLeapSkill(DashMoveSkill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("koala_large_leap", "飞跃（大）",
                         "普通技能：费1魔，每本人轮一次，直线飞行恰移动6格。",
                         max_distance=6, exact_distance=6, mana_cost=1,
                         max_uses_per_turn=1, straight_only=True, ignore_units=True)

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "x": cell.x, "y": cell.y} for cell in battle.reachable_positions(
                     actor, max_distance=6, exact_distance=6, straight_only=True, ignore_units=True)]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        try:
            landing = Position(int(payload["x"]), int(payload["y"]))
        except (KeyError, TypeError, ValueError):
            return -1000.0
        if landing not in battle.reachable_positions(actor, max_distance=6, exact_distance=6,
                                                     straight_only=True, ignore_units=True):
            return -1000.0
        enemies = [unit for unit in battle.enemy_units(actor.player_id) if _present(unit)]
        if not enemies or actor.position is None:
            return -1000.0
        before = min(battle.distance_between_units(actor, enemy) for enemy in enemies)
        after = min(min(cell.distance_to(enemy.position) for cell in actor.footprint_cells_at(landing))
                    for enemy in enemies)
        healthy = actor.current_hp > actor.max_health * 0.5
        return ((before - after) * 13.0 if healthy else (after - before) * 9.0) - 14.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)


class KoalaAngryTableField(BattleFieldEffect):
    """A fixed group gains attack when one of its members finishes an attack."""

    def __init__(self, source_unit_id: str, recipient_ids: set[str]) -> None:
        super().__init__("愤怒掀桌！", "周围5*5己方单位一轮内每攻击一次，受益单位攻+1。", duration=2)
        self.source_unit_id = source_unit_id
        self.recipient_ids = recipient_ids
        self.stacks = 0

    def affected_cells(self, battle: Battle) -> list[Position]:
        return [cell for unit_id in self.recipient_ids
                if (unit := battle.units.get(unit_id)) is not None and _present(unit)
                for cell in battle.unit_cells(unit)]

    def modify_unit_stat(self, battle: Battle, unit: Unit,
                         stat_name: str, value: float) -> float:
        if (stat_name == "attack" and self.stacks
                and unit.unit_id in self.recipient_ids):
            return value + self.stacks
        return value

    def on_basic_attack_finished(self, battle: Battle, actor: Unit,
                                 payload: dict[str, Any], damage_contexts: list[DamageContext],
                                 missed: bool) -> None:
        if actor.unit_id not in self.recipient_ids:
            return
        self.stacks += 1
        battle.log_public_event(f"{actor.name} 的普攻令【愤怒掀桌！】叠至{self.stacks}层。",
                                source=actor)

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data["stacks"] = self.stacks
        return data


class KoalaAngryTableSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("koala_angry_table", "愤怒掀桌！",
                         "每本人轮一次；周围5*5的己方单位（含自身）一轮内每攻击一次，受益单位攻+1。",
                         max_uses_per_turn=1, target_mode="self")

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        recipients = {unit.unit_id for unit in battle.player_units(actor.player_id)
                      if _within_aura(battle, actor, unit)}
        battle.add_field_effect(KoalaAngryTableField(actor.unit_id, recipients), source=actor)
        battle.log_public_event(f"{actor.name} 发动【愤怒掀桌！】。", source=actor)

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code}]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        if any(isinstance(field, KoalaAngryTableField) and field.source_unit_id == actor.unit_id
               for field in battle.field_effects):
            return -1000.0
        # Only the active hero's remaining attacks are guaranteed this turn. Nearby
        # allies can benefit from a reaction, but their later turns cannot spend
        # stacks before this one-turn field expires.
        remaining = max(0, actor.attack_actions_per_turn() - actor.attacks_used)
        if remaining < 2 or actor.cannot_attack or not battle.basic_attack_resource_allowed(
            actor, {"attack_cost": 1}
        )[0]:
            return -1000.0
        reachable = [unit for unit in battle.enemy_units(actor.player_id)
                     if _present(unit) and battle.unit_target_in_range_and_line(
                         actor, unit, actor.targeting_range())]
        if not any(unit.current_hp + unit.total_shields() > battle.damage_rule.calculate_damage(
            actor.stat("attack"), unit.stat("defense")) for unit in reachable):
            return -1000.0
        # The first hit builds a stack, the next one or two spend it. Casting is
        # free and does not consume a basic attack, so this setup must outrank a
        # single good hit when several hits can follow immediately.
        nearby_allies = sum(1 for unit in battle.player_units(actor.player_id)
                            if unit is not actor and _within_aura(battle, actor, unit))
        return 110.0 + 20.0 * remaining + min(nearby_allies, 2) * 5.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)


class KoalaCooperativeRollSkill(Skill):
    requires_direct_unit_target_line = False
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False
    is_displacement_skill = True

    def __init__(self) -> None:
        super().__init__("koala_cooperative_roll", "协力翻滚",
                         "每本人轮一次；选择附近至多两名己方单位，同向直线尽量移动4格。",
                         max_uses_per_turn=1, target_mode="unit")

    def targets(self, battle: Battle, actor: Unit) -> list[Unit]:
        return [unit for unit in battle.player_units(actor.player_id)
                if unit is not actor and _within_aura(battle, actor, unit)]

    def _selection(self, battle: Battle, actor: Unit,
                   payload: dict[str, Any]) -> tuple[list[Unit], tuple[int, int]]:
        ids = payload.get("target_unit_ids")
        if not isinstance(ids, list) or not 1 <= len(ids) <= 2 or len(set(map(str, ids))) != len(ids):
            raise ActionError("协力翻滚须选择一或两名不同己方单位。")
        direction = payload_direction(payload)
        if direction not in ALL_DIRECTIONS:
            raise ActionError("协力翻滚须选择一个八方向。")
        valid = self.targets(battle, actor)
        chosen = [battle.units.get(str(unit_id)) for unit_id in ids]
        if any(unit not in valid for unit in chosen):
            raise ActionError("协力翻滚只能选择附近己方单位。")
        return chosen, direction

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.cannot_move or not self.targets(battle, actor):
            return False, "当前没有可协力翻滚的己方单位。"
        if payload and ("target_unit_ids" in payload or "direction" in payload):
            try:
                self._selection(battle, actor, payload)
            except (ActionError, TypeError, ValueError) as error:
                return False, str(error)
        return True, ""

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = self.targets(battle, actor)
        return {"cells": positions_to_dict([cell for unit in targets for cell in battle.unit_cells(unit)]),
                "target_unit_ids": [unit.unit_id for unit in targets],
                "secondary_cells": positions_to_dict(battle.unit_cells(actor)), "requires_target": True,
                "selection": {"mode": "multi_unit", "min_targets": 1, "max_targets": 2,
                              "directions": [{"dx": dx, "dy": dy, "label": f"{dx:+d},{dy:+d}"}
                                             for dx, dy in ALL_DIRECTIONS],
                              "prompt": "选择一或两名附近己方单位，再选择共同翻滚方向。"}}

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        try:
            return self._selection(battle, actor, payload)[0]
        except (ActionError, TypeError, ValueError):
            return []

    @staticmethod
    def _front_first(battle: Battle, units: list[Unit], direction: tuple[int, int]) -> list[Unit]:
        dx, dy = direction
        return sorted(units, key=lambda unit: (-max(cell.x * dx + cell.y * dy
                                                    for cell in battle.unit_cells(unit)), unit.unit_id))

    @staticmethod
    def _destination(battle: Battle, unit: Unit,
                     direction: tuple[int, int]) -> Position | None:
        if not _present(unit) or unit.cannot_move:
            return None
        dx, dy = direction
        for steps in range(4, 0, -1):
            destination = unit.position.offset(dx * steps, dy * steps)
            if not battle.in_bounds(destination) or not battle.can_place_unit(unit, destination, mover=unit):
                continue
            try:
                battle.find_path(unit, destination, max_distance=steps, straight_only=True)
            except ActionError:
                continue
            return destination
        return None

    def planned_destinations(self, battle: Battle, actor: Unit, chosen: list[Unit],
                             direction: tuple[int, int]) -> list[tuple[Unit, Position, Position | None]]:
        units = [actor, *chosen]
        original = {unit.unit_id: unit.position for unit in units}
        planned: list[tuple[Unit, Position, Position | None]] = []
        try:
            for unit in self._front_first(battle, units, direction):
                start = unit.position
                destination = self._destination(battle, unit, direction)
                planned.append((unit, start, destination))
                if destination is not None:
                    unit.position = destination
            return planned
        finally:
            for unit in units:
                unit.position = original[unit.unit_id]

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        chosen, direction = self._selection(battle, actor, payload)
        for unit in self._front_first(battle, [actor, *chosen], direction):
            destination = self._destination(battle, unit, direction)
            if destination is None:
                battle.log_public_event(f"{unit.name} 的协力翻滚受阻。", source=actor, target=unit)
                continue
            try:
                battle.move_unit(unit, destination, via_skill=True, forced=True,
                                 straight_only=True, max_distance=4,
                                 tags={"koala_cooperative_roll"})
            except ActionError:
                battle.log_public_event(f"{unit.name} 的协力翻滚落点失效。", source=actor, target=unit)

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        targets = self.targets(battle, actor)
        base = {"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code}
        payloads = [{**base, "target_unit_ids": [unit.unit_id for unit in chosen],
                     "direction": {"dx": dx, "dy": dy}}
                    for count in (1, 2) for chosen in combinations(targets, count)
                    for dx, dy in ALL_DIRECTIONS]
        if len(payloads) <= 48:
            return payloads
        return sorted(payloads, key=lambda item: self._coarse_value(battle, actor, item),
                      reverse=True)[:32]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def _coarse_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        direction = payload_direction(payload)
        chosen = [battle.units.get(str(unit_id)) for unit_id in payload["target_unit_ids"]]
        enemies = [unit for unit in battle.enemy_units(actor.player_id) if _present(unit)]
        if not enemies:
            return -1000.0
        return sum(min((unit.position.distance_to(enemy.position) -
                        unit.position.offset(direction[0] * 4, direction[1] * 4).distance_to(enemy.position))
                       for enemy in enemies) for unit in [actor, *chosen])

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        try:
            chosen, direction = self._selection(battle, actor, payload)
        except (ActionError, TypeError, ValueError):
            return -1000.0
        enemies = [unit for unit in battle.enemy_units(actor.player_id) if _present(unit)]
        if not enemies:
            return -1000.0
        value = -18.0
        for unit, start, destination in self.planned_destinations(battle, actor, chosen, direction):
            if destination is None:
                continue
            before = min(start.distance_to(enemy.position) for enemy in enemies)
            after = min(destination.distance_to(enemy.position) for enemy in enemies)
            healthy = unit.current_hp > unit.max_health * 0.5
            value += (before - after) * (9.0 if healthy else -7.0)
        return value

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)


class KoalaCrushSlowStatus(StatModifierStatus):
    def __init__(self) -> None:
        super().__init__("泰山压顶减速", speed_delta=-2, duration=2,
                         tick_scope="owner_turn_end", description="速-2，最低1；同名不叠加。")


def _resolve_mountain_crush(battle: Battle, actor: Unit, queued: Any) -> None:
    cells = battle.payload_positions(queued.payload, "cells")
    for target in battle.effect_units_at_cells(cells):
        if target.player_id == actor.player_id:
            continue
        ctx = battle.resolve_damage(DamageContext(
            source=actor, target=target, attack_power=0, raw_damage=3,
            is_skill=True, action_name="泰山压顶", ignore_shield=True,
            tags={"skill", "pierce", "koala_mountain_crush"},
        ))
        recipient = ctx.target
        if ctx.cancelled or not _present(recipient):
            continue
        existing = recipient.get_status("泰山压顶减速")
        if existing is not None:
            existing.duration = 2
        else:
            recipient.add_status(KoalaCrushSlowStatus(), source=actor)
        battle.record_skill_effect(recipient)
        battle.log_public_event(f"{recipient.name} 被泰山压顶减速两轮。", source=actor, target=recipient)


class KoalaMountainCrushSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("koala_mountain_crush", "泰山压顶",
                         "每2本人轮一次；身体外邻敌方各受固定3破魔伤，速-2最低1两轮，不叠加。",
                         cooldown_turns=2, target_mode="self")

    def cells(self, battle: Battle, actor: Unit) -> list[Position]:
        body = set(battle.unit_cells(actor))
        return [cell for cell in square_around_cells(battle, body, radius=1) if cell not in body]

    def targets(self, battle: Battle, actor: Unit) -> list[Unit]:
        return [unit for unit in battle.effect_units_at_cells(self.cells(battle, actor))
                if unit.player_id != actor.player_id]

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        return (ok, reason) if not ok else ((True, "") if self.targets(battle, actor)
                                             else (False, "周围没有可压制的敌方单位。"))

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        cells = self.cells(battle, actor)
        return {"cells": positions_to_dict(cells),
                "target_unit_ids": [unit.unit_id for unit in self.targets(battle, actor)],
                "secondary_cells": [], "requires_target": False}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return self.cells(battle, actor)

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return self.targets(battle, actor)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cells = self.cells(battle, actor)
        battle.queue_skill_effect_action(actor=actor, display_name=self.name,
                                         effect_code="koala_mountain_crush",
                                         payload={"cells": positions_to_dict(cells),
                                                  "refresh_cell_targets": True},
                                         target_cells=cells,
                                         effect_resolver=_resolve_mountain_crush)

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code}]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        value = 0.0
        for target in self.targets(battle, actor):
            if target.magic_immunity:
                continue
            damage = min(3.0, target.current_hp)
            slow = 0.0 if target.has_status("泰山压顶减速") else max(0.0, target.stat("speed") - 1) * 14.0
            value += damage * 100.0 + slow + (12.0 if target.total_shields() else 0.0)
        return value - 32.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)


class KoalaZeroManaRecoveryTrait(Trait):
    def __init__(self) -> None:
        super().__init__("零魔韧性", "魔0时每次受实际伤，当前英雄回合末魔+1。")
        self.pending = 0

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is not None and ctx.target is owner and ctx.actual_damage > 0 and owner.current_mana <= 0:
            self.pending += 1

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        owner = self.owner
        pending, self.pending = self.pending, 0
        if owner is None or not owner.alive or not pending:
            return
        gained = owner.gain_mana(pending)
        if gained:
            battle.log_public_event(f"{owner.name} 的零魔韧性在回合结束时回复{gained:g}魔。", source=owner)

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data["pending_mana"] = self.pending
        return data
