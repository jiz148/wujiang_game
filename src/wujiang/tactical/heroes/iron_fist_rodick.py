"""R130 Rodick: stored pressure, steel fist and evasion denial."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, DamageContext, Position, Skill, StatusEffect,
    TargetContext, Trait, Unit,
)
from wujiang.tactical.heroes.ai_candidate_plan import SkillCandidatePlan
from wujiang.tactical.heroes.common import PierceSkill, match_payload_pattern, positions_to_dict
from wujiang.tactical.heroes.mechanics.area_helpers import area_patterns_preview
from wujiang.tactical.heroes.mechanics.next_five_geometry import nearby_rectangle_patterns


def _area(battle: Battle, actor: Unit, radius: int) -> list[Position]:
    occupied = battle.unit_cells(actor)
    return [Position(x, y) for x in range(battle.width) for y in range(battle.height)
            if occupied and min(cell.distance_to(Position(x, y)) for cell in occupied) <= radius]


def _pressure(actor: Unit) -> "RodickPressureTrait | None":
    return next((trait for trait in actor.traits if isinstance(trait, RodickPressureTrait)), None)


class RodickPressureTrait(Trait):
    def __init__(self) -> None:
        super().__init__("铁腕蓄力", "己方轮末11×11内有敌时攻守各+1；本人普攻或带伤技能完成后清空本蓄力。")
        self.stacks = 0

    def modify_stat(self, stat_name: str, value: float) -> float:
        return value + self.stacks if stat_name in {"attack", "defense"} else value

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        owner = self.owner
        if owner is None or not owner.alive or ended_player_id != owner.player_id:
            return
        area = set(_area(battle, owner, 5))
        if any(unit.player_id != owner.player_id and unit.alive and unit.position is not None
               and any(cell in area for cell in battle.unit_cells(unit))
               for unit in battle.all_units()):
            self.stacks += 1
            battle.log_public_event(f"{owner.name} 的铁腕蓄力达到{self.stacks}层。", source=owner)

    def reset(self, battle: Battle) -> None:
        if self.stacks > 0:
            self.stacks = 0
            if self.owner is not None:
                battle.log_public_event(f"{self.owner.name} 释放铁腕蓄力，攻守恢复。", source=self.owner)


class RodickPressureResetTrait(Trait):
    def __init__(self) -> None:
        super().__init__("蓄力释放", "普攻或带伤技能完成后清空铁腕蓄力。")

    def on_basic_attack_finished(self, battle: Battle, actor: Unit,
                                 payload: dict[str, Any], damage_contexts: list[DamageContext],
                                 missed: bool) -> None:
        if actor is self.owner:
            pressure = _pressure(actor)
            if pressure is not None:
                pressure.reset(battle)


class RodickAllDamagePierceTrait(Trait):
    all_damage_half_pierce = True

    def __init__(self) -> None:
        super().__init__("全伤半破魔", "本人造成的全部伤害半破魔。")

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        if ctx.actor is self.owner and not ctx.is_skill:
            ctx.half_ignore_shield = True

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if ctx.source is self.owner:
            ctx.half_ignore_shield = True


class RodickPierceSkill(PierceSkill):
    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        try:
            super().execute(battle, actor, payload)
        finally:
            pressure = _pressure(actor)
            if pressure is not None:
                pressure.reset(battle)


class SteelFistSkill(Skill):
    ai_candidate_plan = SkillCandidatePlan(prefilter_limit=64)
    ai_payload_value_owned = True

    def __init__(self) -> None:
        super().__init__("steel_fist", "钢拳", "每4本人轮一次；选择近身2×3区域，造成当前攻的技能伤害。",
                         cooldown_turns=4, target_mode="cell")

    def patterns(self, battle: Battle, actor: Unit) -> list[list[Position]]:
        return nearby_rectangle_patterns(battle, actor, 2, 3) + nearby_rectangle_patterns(battle, actor, 3, 2)

    def _cells(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        return battle.payload_positions(payload, "declared_steel_fist_cells") or match_payload_pattern(
            payload, self.patterns(battle, actor))

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return area_patterns_preview(battle, actor, self.patterns(battle, actor))

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return self._cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return list(battle.effect_units_at_cells(self._cells(battle, actor, payload), ignore=actor))

    def queued_payload_metadata(self, battle: Battle, actor: Unit,
                                payload: dict[str, Any]) -> dict[str, Any]:
        return {"declared_steel_fist_cells": positions_to_dict(self._cells(battle, actor, payload))}

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "cells": positions_to_dict(cells)} for cells in self.patterns(battle, actor)]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_prefilter_score(self, battle: Battle, actor: Unit, payload: dict[str, Any],
                           shared: Any) -> float:
        try:
            cells = self._cells(battle, actor, payload)
        except (ActionError, TypeError, ValueError):
            return -1000.0
        return sum((1 if unit.player_id != actor.player_id else -1.5)
                   * max(0, actor.stat("attack") - unit.stat("defense")) * 100
                   for unit in battle.effect_units_at_cells(cells, ignore=actor))

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        from wujiang.tactical.direct_expectation import damage_result
        from wujiang.tactical.rooms.ai_probe import ai_probe_rollback

        try:
            cells = self._cells(battle, actor, payload)
        except (ActionError, TypeError, ValueError):
            return -1000.0
        score = 0.0
        for target in battle.effect_units_at_cells(cells, ignore=actor):
            with ai_probe_rollback(battle):
                source = battle.get_unit(actor.unit_id)
                recipient = battle.get_unit(target.unit_id)
                hp_before = recipient.current_hp
                ctx = battle.resolve_damage(DamageContext(
                    source=source, target=recipient, attack_power=source.stat("attack"),
                    is_skill=True, action_name=self.name,
                    area_cell_hits=battle.unit_hit_count_for_cells(recipient, cells),
                    tags={"skill", self.code}))
                value = min(hp_before, max(0.0, damage_result(ctx, ctx.actual_damage))) * 100.0
            score += value if target.player_id != actor.player_id else -1.5 * value
        return score - 20.0 if score > 0 else -1000.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit,
                                  payload: dict[str, Any], targets: list[Unit] | None = None,
                                  shared: Any = None, action: dict[str, Any] | None = None) -> bool:
        return any(unit.player_id != actor.player_id
                   for unit in self.get_target_units_for_payload(battle, actor, payload))

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        try:
            cells = self._cells(battle, actor, payload)
            for target in battle.effect_units_at_cells(cells, ignore=actor):
                battle.resolve_damage(DamageContext(
                    source=actor, target=target, attack_power=actor.stat("attack"),
                    is_skill=True, action_name=self.name,
                    area_cell_hits=battle.unit_hit_count_for_cells(target, cells),
                    tags={"skill", self.code}))
        finally:
            pressure = _pressure(actor)
            if pressure is not None:
                pressure.reset(battle)


class DangerAuraStatus(StatusEffect):
    def __init__(self, turn_number: int) -> None:
        super().__init__("危险气场", "本轮无法回避。")
        self.turn_number = turn_number

    def blocks_skill_use(self, battle: Battle, actor: Unit, skill: Skill) -> tuple[bool, str]:
        return (True, "危险气场中无法回避。") if skill.code == "evasion" else (False, "")

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if ctx.target is self.owner:
            ctx.cannot_evade = True

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        if battle.turn_number >= self.turn_number and self.owner is not None:
            self.owner.remove_status(self, battle)


class DangerAuraSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("danger_aura", "DANGER！危险气场",
                         "每2本人轮一次；身体周围11×11当前单位本轮无法回避。",
                         cooldown_turns=2, target_mode="none")

    def _targets(self, battle: Battle, actor: Unit) -> list[Unit]:
        return list(battle.effect_units_at_cells(_area(battle, actor, 5)))

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return _area(battle, actor, 5)

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return self._targets(battle, actor)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {"cells": positions_to_dict(_area(battle, actor, 5)),
                "target_unit_ids": [unit.unit_id for unit in self._targets(battle, actor)],
                "secondary_cells": [], "requires_target": False}

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        score = 0.0
        for target in self._targets(battle, actor):
            if target is actor or target.skill_non_damage_effects_blocked():
                continue
            value = 32.0 if target.dodge_charges > 0 or "evasion" in target.skill_map() else 0.0
            score += value if target.player_id != actor.player_id else -1.5 * value
        return score - 12.0 if score > 0 else -1000.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        for target in self._targets(battle, actor):
            checked = battle.validate_target(actor, target, action_name=self.name, is_skill=True,
                                             is_hostile=target.player_id != actor.player_id)
            if checked.cancelled:
                continue
            recipient = checked.target
            old = recipient.get_status("危险气场")
            if old is not None:
                recipient.remove_status(old, battle)
            recipient.add_status(DangerAuraStatus(battle.turn_number), source=actor)
            battle.record_skill_effect(recipient)
        battle.log_public_event(f"{actor.name} 展开危险气场。", source=actor)

