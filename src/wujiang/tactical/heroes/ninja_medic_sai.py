"""R044 Ninja Medic Sai: targeted care and a two-stage frontal strike."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, DamageContext, HealContext, Position, QueuedAction,
    Skill, StatusEffect, Unit, direct_scoring,
)
from wujiang.tactical.heroes.common import (
    PassiveEvasionSkill, match_payload_pattern, pattern_selection_preview,
    positions_to_dict,
)


STAT_NAMES = ("attack", "defense", "speed", "attack_range", "mana")
STAT_LABELS = ("攻", "守", "速", "范", "魔")


def selectable_units(battle: Battle, actor: Unit) -> list[Unit]:
    return [unit for unit in battle.all_units()
            if unit.alive and not unit.banished and unit.position is not None
            and battle.unit_can_be_selected(unit, actor=actor)[0]
            and any(battle.cells_are_straight_aligned(a, b)
                    and a.distance_to(b) <= actor.targeting_range()
                    for a in battle.unit_cells(actor) for b in battle.unit_cells(unit))]


def chosen_unit(battle: Battle, actor: Unit, payload: dict[str, Any]) -> Unit:
    target = battle.units.get(str(payload.get("resolved_target_unit_id") or payload.get("target_unit_id") or ""))
    if target not in selectable_units(battle, actor):
        raise ActionError("请选择当前范内可选中的单位。")
    return target


class AcupointStatus(StatusEffect):
    def __init__(self, stat_name: str) -> None:
        label = "期望" if stat_name == "expected" else STAT_LABELS[STAT_NAMES.index(stat_name)]
        super().__init__(f"点穴·{label}", f"{label}+1，持续到下次本人轮结束。")
        self.stat_name = stat_name
        self.armed = False

    def modify_stat(self, stat_name: str, value: float) -> float:
        if self.stat_name == "expected" and stat_name in STAT_NAMES:
            return value + 0.2
        return value + 1 if stat_name == self.stat_name else value

    def on_owner_turn_start(self, battle: Battle) -> None:
        self.armed = True

    def on_owner_turn_end(self, battle: Battle) -> None:
        if self.armed and self.owner is not None:
            self.owner.remove_status(self, battle)
            if self.stat_name in {"mana", "expected"}:
                self.owner.clamp_mana()


class MedicUnitSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = selectable_units(battle, actor)
        return {"cells": positions_to_dict([cell for target in targets for cell in battle.unit_cells(target)]),
                "target_unit_ids": [target.unit_id for target in targets],
                "secondary_cells": [], "requires_target": True}

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return [chosen_unit(battle, actor, payload)]

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return battle.unit_cells(chosen_unit(battle, actor, payload))

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "target_unit_id": unit.unit_id, "x": unit.position.x, "y": unit.position.y}
                for unit in selectable_units(battle, actor)]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)


class AcupointSkill(MedicUnitSkill):
    def __init__(self) -> None:
        super().__init__("acupoint", "点穴", "每本人轮一次；目标随机一项能力+1至下次本人轮末。",
                         max_uses_per_turn=1, target_mode="unit")

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = chosen_unit(battle, actor, payload)
        result = battle.validate_target(actor, target, action_name=self.name, is_skill=True,
                                        is_hostile=target.player_id != actor.player_id)
        if result.cancelled:
            battle.log_public_event(result.reason or "点穴未命中。", source=actor, target=target)
            return
        if direct_scoring():
            result.target.add_status(AcupointStatus("expected"), source=actor)
            return
        stat_index = battle.roll_die(5, action_name=self.name) - 1
        stat_name = STAT_NAMES[stat_index]
        recipient = result.target
        existing = recipient.get_status(f"点穴·{STAT_LABELS[stat_index]}")
        if existing is not None:
            recipient.remove_status(existing, battle)
        recipient.add_status(AcupointStatus(stat_name), source=actor)
        battle.log_public_event(f"{actor.name} 点穴命中 {recipient.name}，掷得{STAT_LABELS[stat_index]}+1。",
                                source=actor, target=recipient)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        try:
            target = chosen_unit(battle, actor, payload)
        except ActionError:
            return -1000.0
        if target.player_id != actor.player_id:
            return -1000.0
        if all(target.get_status(f"点穴·{label}") is not None for label in STAT_LABELS):
            return -15.0
        return 17.0 + 3.0 * target.stat("attack") + 2.0 * target.stat("speed")


class SeamlessHealSkill(MedicUnitSkill):
    def __init__(self) -> None:
        super().__init__("seamless_heal", "无缝之缝补", "每本人轮一次；令命中单位生命回复至满。",
                         max_uses_per_turn=1, target_mode="unit")

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = chosen_unit(battle, actor, payload)
        result = battle.validate_target(actor, target, action_name=self.name, is_skill=True,
                                        is_hostile=target.player_id != actor.player_id)
        if result.cancelled:
            battle.log_public_event(result.reason or "缝补未命中。", source=actor, target=target)
            return
        recipient = result.target
        battle.heal(HealContext(source=actor, target=recipient,
                                amount=max(0.0, recipient.max_health - recipient.current_hp),
                                action_name=self.name))

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        try:
            target = chosen_unit(battle, actor, payload)
        except ActionError:
            return -1000.0
        if target.player_id != actor.player_id or target.current_hp >= target.max_health:
            return -1000.0
        if any(component.modify_direct_heal_amount(1.0) <= 0 for component in target.iter_components()):
            return -1000.0
        return 30.0 + (target.max_health - target.current_hp) * 115.0


class SpiderSenseSkill(PassiveEvasionSkill):
    def __init__(self) -> None:
        super().__init__()
        self.code = "spider_sense"
        self.name = "蜘蛛感应"
        self.description = "每本人轮一次；受敌方动作影响时被动连锁移动恰五格。"
        self.mana_cost = 0
        self.max_uses_per_turn = 1

    def evade_cells(self, battle: Battle, actor: Unit) -> list[Position]:
        if actor.position is None or actor.cannot_move or getattr(actor, "attached_to_unit_id", None):
            return []
        return sorted(battle.reachable_positions(actor, max_distance=5, exact_distance=5,
                                                 straight_only=False, ignore_units=False),
                      key=lambda cell: (cell.y, cell.x))

    def react(self, battle: Battle, actor: Unit,
              payload: dict[str, Any], queued_action: QueuedAction) -> None:
        destination = Position(int(payload["x"]), int(payload["y"]))
        if destination not in self.evade_cells(battle, actor):
            raise ActionError("蜘蛛感应需要合法的五格落点。")
        before = battle.effect_recipient(actor).unit_id
        battle.move_unit(actor, destination, via_skill=True, straight_only=False,
                         ignore_units=False, triggered_by_reaction=True,
                         max_distance=5, tags={"evasion", "spider_sense"})
        if queued_action.action_type == "attack":
            queued_action.payload.setdefault("evaded_unit_ids", []).append(actor.unit_id)
            queued_action.payload.setdefault("completed_evasion_unit_ids", []).append(before)
        battle.log_public_event(f"{actor.name} 发动蜘蛛感应，移动到 ({destination.x}, {destination.y})。",
                                source=actor)


class MedicalAssassinationSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = True

    def __init__(self) -> None:
        super().__init__("medical_assassination", "医学暗杀术",
                         "本场一次；身前2×4破魔伤，实际受伤者再各减血1/4。",
                         max_uses_per_battle=1, target_mode="cell")

    def patterns(self, battle: Battle, actor: Unit) -> list[list[Position]]:
        if actor.position is None:
            return []
        result: list[list[Position]] = []
        seen: set[tuple[Position, ...]] = set()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            lx, ly = -dy, dx
            for side_start in (-1, 0):
                cells = [origin.offset(dx * forward + lx * side,
                                       dy * forward + ly * side)
                         for origin in battle.unit_cells(actor)
                         for forward in range(1, 5) for side in (side_start, side_start + 1)]
                cells = sorted({cell for cell in cells if battle.in_bounds(cell)
                                and cell not in battle.unit_cells(actor)}, key=lambda cell: (cell.y, cell.x))
                key = tuple(cells)
                if cells and key not in seen:
                    result.append(cells)
                    seen.add(key)
        return result

    def chosen_cells(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {**pattern_selection_preview(self.patterns(battle, actor)),
                "target_unit_ids": [], "secondary_cells": [], "requires_target": True}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return list(battle.effect_units_at_cells(self.chosen_cells(battle, actor, payload), ignore=actor))

    def ignores_shield_for_payload(self, battle: Battle, actor: Unit,
                                   payload: dict[str, Any]) -> bool:
        return True

    def queued_payload_metadata(self, battle: Battle, actor: Unit,
                                payload: dict[str, Any]) -> dict[str, Any]:
        return {**super().queued_payload_metadata(battle, actor, payload), "ignore_shield": True}

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "cells": positions_to_dict(cells)} for cells in self.patterns(battle, actor)]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit,
                                  payload: dict[str, Any], targets: list[Unit] | None = None,
                                  shared: Any = None, action: dict[str, Any] | None = None) -> bool:
        return any(target.player_id != actor.player_id and not target.magic_immunity
                   for target in self.get_target_units_for_payload(battle, actor, payload))

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        try:
            cells = self.chosen_cells(battle, actor, payload)
        except ActionError:
            return -1000.0
        value = 0.0
        for target in battle.effect_units_at_cells(cells, ignore=actor):
            if target.magic_immunity and target.player_id != actor.player_id:
                continue
            hits = battle.unit_hit_count_for_cells(target, cells)
            damage = max(0.0, actor.stat("attack") - target.stat("defense") + hits - 1)
            value += (50.0 + damage * 60.0) * (1 if target.player_id != actor.player_id else -1.6)
        return value - 20.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        hurt: list[Unit] = []
        for target in battle.effect_units_at_cells(cells, ignore=actor):
            hit = battle.resolve_damage(DamageContext(
                source=actor, target=target, attack_power=actor.stat("attack"),
                is_skill=True, action_name=self.name, ignore_shield=True,
                area_cell_hits=battle.unit_hit_count_for_cells(target, cells),
                tags={"skill", "area", "medical_assassination"},
            ))
            if hit.actual_damage > 0 and target.alive:
                hurt.append(target)
        if hurt and actor.alive:
            battle.queue_skill_effect_action(
                actor=actor, display_name="医学暗杀术·续伤",
                effect_code="medical_assassination_after",
                target_units=hurt,
                target_cells=[cell for target in hurt for cell in battle.unit_cells(target)],
                payload={"target_unit_ids": [target.unit_id for target in hurt]},
                effect_resolver=self.resolve_after,
            )

    @staticmethod
    def resolve_after(battle: Battle, actor: Unit, queued: QueuedAction) -> None:
        for unit_id in queued.payload.get("target_unit_ids", []):
            target = battle.units.get(unit_id)
            if target is None or not target.alive or target.banished:
                continue
            before = target.current_hp
            target.take_damage_fraction(0.25)
            battle.log_public_event(f"{target.name} 受到医学暗杀术续伤，生命减少 {before - target.current_hp:g}。",
                                    source=actor, target=target)
        battle.cleanup_dead_units()
