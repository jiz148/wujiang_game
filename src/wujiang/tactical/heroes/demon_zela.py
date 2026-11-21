"""R116 Zela: demonic pull, leap impact and nearby demon strength."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, DamageContext, Position, QueuedAction, Skill,
    StatusEffect, TargetContext, Trait, Unit,
)
from wujiang.tactical.heroes.ai_candidate_plan import SkillCandidatePlan
from wujiang.tactical.heroes.common import (
    DashMoveSkill, match_payload_pattern, payload_position, positions_to_dict,
)
from wujiang.tactical.heroes.mechanics.area_helpers import area_patterns_preview
from wujiang.tactical.heroes.mechanics.next_five_effects import remote_rectangle_patterns
from wujiang.tactical.heroes.mechanics.piercing_control import piercing_nondamage_recipient


def _surrounding(battle: Battle, actor: Unit, at: Position | None = None) -> list[Position]:
    occupied = set(actor.footprint_cells_at(at)) if at is not None else set(battle.unit_cells(actor))
    return sorted({neighbor for cell in occupied for neighbor in battle.neighbors(cell)
                   if neighbor not in occupied}, key=lambda p: (p.y, p.x))


class ZelaDemonCompanyTrait(Trait):
    def __init__(self) -> None:
        super().__init__("恶魔共鸣", "身体周围每有一个其他恶魔单位，攻守各+1。")

    def modify_stat(self, stat_name: str, value: float) -> float:
        owner = self.owner
        ref = getattr(owner, "_battle_ref", None) if owner is not None else None
        battle = ref() if ref is not None else None
        if stat_name not in {"attack", "defense"} or owner is None or battle is None or not owner.alive:
            return value
        nearby = set(_surrounding(battle, owner))
        return value + sum(1 for unit in battle.all_units()
                           if unit is not owner and unit.alive and unit.position is not None
                           and unit.qualifies_as_race("恶魔")
                           and any(cell in nearby for cell in battle.unit_cells(unit)))


class ZelaAbsorbedStatus(StatusEffect):
    def __init__(self, source_unit_id: str, source_player_id: int) -> None:
        super().__init__("魔性吸收", "本轮受到来源普攻时半破魔。")
        self.source_unit_id = source_unit_id
        self.source_player_id = source_player_id

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        if ended_player_id == self.source_player_id and self.owner is not None:
            self.owner.remove_status(self, battle)


class DemonicAbsorptionSkill(Skill):
    ai_candidate_plan = SkillCandidatePlan(prefilter_limit=16)
    ai_payload_value_owned = True

    def __init__(self) -> None:
        super().__init__("demonic_absorption", "魔性吸收",
                         "每本人轮一次；范6选3×3区域，破魔拉近最多4格；受影响者本轮受本人普攻半破魔。",
                         max_uses_per_turn=1, target_mode="cell")

    def _marks_basic(self, source: Unit | None, target: Unit, is_skill: bool,
                     tags: set[str]) -> bool:
        return (source is self.owner and not is_skill and "attack" in tags
                and any(isinstance(status, ZelaAbsorbedStatus)
                        and status.source_unit_id == source.unit_id for status in target.statuses))

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        if self._marks_basic(ctx.actor, ctx.target, ctx.is_skill, ctx.tags):
            ctx.half_ignore_shield = True

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if self._marks_basic(ctx.source, ctx.target, ctx.is_skill, ctx.tags):
            ctx.half_ignore_shield = True

    def patterns(self, battle: Battle, actor: Unit) -> list[list[Position]]:
        return remote_rectangle_patterns(battle, actor, 3, 3, attack_range=6)

    def _cells(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        return (battle.payload_positions(payload, "declared_absorption_cells")
                or match_payload_pattern(payload, self.patterns(battle, actor)))

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
        return {"declared_absorption_cells": positions_to_dict(self._cells(battle, actor, payload)),
                "ignore_shield": True}

    def ignores_shield_for_payload(self, battle: Battle, actor: Unit,
                                   payload: dict[str, Any]) -> bool:
        return True

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "cells": positions_to_dict(cells)} for cells in self.patterns(battle, actor)]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_prefilter_score(self, battle: Battle, actor: Unit, payload: dict[str, Any],
                           shared: Any) -> float:
        return self.ai_payload_value(battle, actor, payload)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        try:
            targets = self.get_target_units_for_payload(battle, actor, payload)
        except (ActionError, ValueError, TypeError):
            return -1000.0
        score = 0.0
        for target in targets:
            if target.skill_non_damage_effects_blocked() or target.magic_immunity:
                continue
            value = 25.0 + (30.0 if target.position is not None and
                            min((a.distance_to(b) for a in battle.unit_cells(actor)
                                 for b in battle.unit_cells(target)), default=0) > 1 else 0.0)
            score += value if target.player_id != actor.player_id else -value * 1.5
        return score - 12.0 if score > 0 else -1000.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit,
                                  payload: dict[str, Any], targets: list[Unit] | None = None,
                                  shared: Any = None, action: dict[str, Any] | None = None) -> bool:
        return any(unit.player_id != actor.player_id
                   for unit in self.get_target_units_for_payload(battle, actor, payload))

    def _pull(self, battle: Battle, actor: Unit, target: Unit) -> None:
        if target.position is None or actor.position is None:
            return
        current_distance = min((a.distance_to(b) for a in battle.unit_cells(actor)
                                for b in battle.unit_cells(target)), default=0)
        if current_distance <= 1:
            return
        choices = [cell for cell in battle.reachable_positions(target, max_distance=4, straight_only=True)
                   if (new_distance := min((a.distance_to(b) for a in battle.unit_cells(actor)
                                            for b in target.footprint_cells_at(cell)), default=99)) < current_distance
                   and new_distance >= 1]
        choices.sort(key=lambda cell: (min(a.distance_to(b) for a in battle.unit_cells(actor)
                                       for b in target.footprint_cells_at(cell)),
                                      target.position.distance_to(cell), cell.y, cell.x))
        if choices:
            try:
                battle.move_unit(target, choices[0], via_skill=True, forced=True,
                                 max_distance=4, straight_only=True, tags={self.code})
            except ActionError:
                battle.log_public_event(f"{target.name} 的魔性吸收拉近因路径受阻而落空。",
                                        source=actor, target=target)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        for target in self.get_target_units_for_payload(battle, actor, payload):
            if not target.alive:
                continue
            recipient = piercing_nondamage_recipient(battle, actor, target,
                                                       action_name=self.name, code=self.code)
            if recipient is None:
                continue
            old = next((status for status in recipient.statuses
                        if isinstance(status, ZelaAbsorbedStatus)
                        and status.source_unit_id == actor.unit_id), None)
            if old is not None:
                recipient.remove_status(old, battle)
            recipient.add_status(ZelaAbsorbedStatus(actor.unit_id, actor.player_id), source=actor)
            self._pull(battle, actor, recipient)
            battle.record_skill_effect(recipient)


class DemonPounceSkill(DashMoveSkill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("demon_pounce", "飞扑", "费1魔；每本人轮一次；移动最多2格并伤身体外一周。",
                         max_distance=2, mana_cost=1, max_uses_per_turn=1)

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "x": cell.x, "y": cell.y}
                for cell in battle.reachable_positions(actor, max_distance=2)]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        from wujiang.tactical.direct_expectation import damage_result
        from wujiang.tactical.rooms.ai_probe import ai_probe_rollback

        try:
            destination = payload_position(payload)
        except (ActionError, TypeError, ValueError):
            return -1000.0
        if destination not in battle.reachable_positions(actor, max_distance=2):
            return -1000.0
        cells = _surrounding(battle, actor, destination)
        score = 0.0
        for target in battle.effect_units_at_cells(cells, ignore=actor):
            with ai_probe_rollback(battle):
                source = battle.get_unit(actor.unit_id)
                recipient = battle.get_unit(target.unit_id)
                source.position = destination
                hp_before = recipient.current_hp
                ctx = battle.resolve_damage(DamageContext(
                    source=source, target=recipient, attack_power=source.stat("attack"),
                    is_skill=True, action_name="飞扑震击", tags={"skill", self.code}))
                value = min(hp_before, max(0.0, damage_result(ctx, ctx.actual_damage))) * 100.0
            score += value if target.player_id != actor.player_id else -value * 1.5
        return score - 15.0 if score > 0 else -1000.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        super().execute(battle, actor, payload)
        if actor.position is None or not actor.alive:
            return
        cells = _surrounding(battle, actor)
        battle.queue_skill_effect_action(
            actor=actor, display_name="飞扑震击", effect_code="demon_pounce_impact",
            target_units=list(battle.effect_units_at_cells(cells, ignore=actor)),
            target_cells=cells, include_cell_units=False, effect_resolver=self._impact)

    def _impact(self, battle: Battle, actor: Unit, queued: QueuedAction) -> None:
        for unit_id in queued.target_unit_ids:
            target = battle.units.get(unit_id)
            if target is not None and target.alive:
                battle.resolve_damage(DamageContext(
                    source=actor, target=target, attack_power=actor.stat("attack"),
                    is_skill=True, action_name="飞扑震击", tags={"skill", self.code}))

