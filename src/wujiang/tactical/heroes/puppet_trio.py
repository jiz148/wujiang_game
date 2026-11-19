"""Personal rules for R341–R343, using the shared puppet attachment lifecycle."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.prediction import random
from wujiang.tactical.engine.core import (
    ActionError, Battle, DamageContext, Position, Skill, StatusEffect, Unit,
)
from wujiang.tactical.heroes.common import payload_direction
from wujiang.tactical.heroes.mechanics.next_five_geometry import ALL_DIRECTIONS, square_around_cells
from wujiang.tactical.heroes.mechanics.next_five_effects import apply_piercing_status_effect
from wujiang.tactical.heroes.mechanics.puppet_equipment import PuppetEquipmentTrait, PuppetHostStatus


def _present(unit: Unit | None) -> bool:
    return unit is not None and unit.alive and not unit.banished and unit.position is not None


def _area_hit_value(battle: Battle, target: Unit, attack_power: float,
                    cells: list[Position], hits: int) -> float:
    """Estimate repeated area hits using the battle's actual damage rule."""
    shields = min(hits, target.total_shields())
    unshielded = hits - shields
    power = attack_power + max(0, battle.unit_hit_count_for_cells(target, cells) - 1)
    damage = min(target.current_hp, unshielded * battle.damage_rule.calculate_damage(
        power, target.stat("defense")))
    if target.is_clone and unshielded:
        damage = target.current_hp
    return damage * 100 + shields * 12


class CurseGazeStatus(StatusEffect):
    def __init__(self, source_id: str) -> None:
        super().__init__("诅咒凝视", "速-3至最低1；每次使用主动技能时失去最大血1/4；持续2轮。", duration=2)
        self.source_unit_id = source_id

    def modify_stat(self, stat_name: str, value: float) -> float:
        return max(1.0, value - 3.0) if stat_name == "speed" else value

    def on_any_skill_declared(self, battle: Battle, actor: Unit, skill: Skill) -> None:
        if actor is not self.owner or not skill.is_active or not actor.alive:
            return
        source = battle.units.get(self.source_unit_id)
        battle.resolve_damage(DamageContext(
            source=source, target=actor, attack_power=0, raw_damage=0.25,
            is_skill=True, action_name="诅咒凝视", ignore_shield=True,
            tags={"curse_gaze", "status"},
        ))


class CurseGazeSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("curse_gaze", "诅咒凝视", "2轮一次；破魔；速-3至1，主动技能使用时血-1/4，持续2轮。",
                         cooldown_turns=2, target_mode="unit")

    def valid_target(self, battle: Battle, actor: Unit, target: Unit | None) -> bool:
        return (_present(target) and battle.unit_can_be_selected(target, actor=actor)[0]
                and battle.unit_target_in_range_and_line(actor, target, actor.targeting_range()))

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if payload:
            return ((True, "") if self.valid_target(battle, actor, battle.units.get(str(payload.get("target_unit_id") or "")))
                    else (False, "请选择范内合法单位。"))
        return ((True, "") if any(self.valid_target(battle, actor, unit) for unit in battle.all_units())
                else (False, "范内没有可诅咒单位。"))

    def ignores_shield_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> bool:
        return True

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = battle.resolve_declared_target_unit(actor, payload)
        if not self.valid_target(battle, actor, target):
            battle.log_public_event(f"{actor.name} 的诅咒凝视目标已离开原格。", source=actor)
            return
        recipient = battle.effect_recipient(target)
        status = CurseGazeStatus(actor.unit_id)
        if apply_piercing_status_effect(battle, actor, recipient, action_name=self.name,
                                        status=status, is_skill=True, tags={self.code},
                                        cannot_evade=False):
            battle.record_skill_effect(recipient)
            battle.log_public_event(f"{recipient.name} 被诅咒凝视，速下降且主动施法将损血。",
                                    source=actor, target=recipient)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = [unit for unit in battle.all_units() if self.valid_target(battle, actor, unit)]
        return {"cells": [cell.to_dict() for unit in targets for cell in battle.unit_cells(unit)],
                "target_unit_ids": [unit.unit_id for unit in targets],
                "secondary_cells": [], "requires_target": True}

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float | None:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if not self.valid_target(battle, actor, target) or target.player_id == actor.player_id:
            return -1000.0
        assert target is not None
        if target.skill_non_damage_effects_blocked() or target.magic_immunity or target.direct_effects_blocked():
            return -1000.0
        speed_loss = max(0, target.stat("speed") - max(1, target.stat("speed") - 3))
        active = sum(skill.is_active for skill in target.action_skills())
        return speed_loss * 14 + active * 11 - (18 if target.has_status("诅咒凝视") else 0)

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload) or -1000.0


class CursedFaceHostStatus(PuppetHostStatus):
    def on_any_basic_attack_finished(self, battle: Battle, actor: Unit, payload: dict[str, Any],
                                     damage_contexts: list[DamageContext], missed: bool) -> None:
        owner = self.owner
        if owner is None or not owner.alive or not actor.alive or actor.unit_id == owner.unit_id:
            return
        targeted = (str(payload.get("target_unit_id") or "") == owner.unit_id
                    or any(ctx.target.unit_id == owner.unit_id for ctx in damage_contexts))
        if not targeted:
            return
        battle.queue_skill_effect_action(
            actor=owner, display_name="诅咒之颜反伤", effect_code="puppet_counter",
            payload={"counter_target_id": actor.unit_id}, target_units=[actor],
            effect_resolver=_resolve_cursed_face_counter,
        )


def _resolve_cursed_face_counter(battle: Battle, actor: Unit, queued_action: Any) -> None:
    target = battle.units.get(str(queued_action.payload.get("counter_target_id") or ""))
    if not _present(target):
        return
    battle.resolve_damage(DamageContext(
        source=actor, target=target, attack_power=2, is_skill=False,
        action_name="诅咒之颜反伤", ignore_physical_immunity=True,
        tags={"puppet_counter"},
    ))


class CursedFaceEquipmentTrait(PuppetEquipmentTrait):
    def __init__(self) -> None:
        super().__init__("诅咒之颜装备", defense=1, speed=1)

    def make_host_status(self, puppet: Unit) -> PuppetHostStatus:
        return CursedFaceHostStatus(puppet, self)

    def ai_equipment_value(self, battle: Battle, host: Unit) -> float:
        nearby = sum(1 for enemy in battle.all_units() if _present(enemy)
                     and enemy.player_id != host.player_id
                     and battle.distance_between_units(host, enemy) <= enemy.targeting_range())
        return super().ai_equipment_value(battle, host) + min(nearby, 2) * 10


class IndianDollHostStatus(PuppetHostStatus):
    def modify_attack_actions_per_turn(self, value: int) -> int:
        return value + 1

    def on_target_action_declared(self, battle: Battle, actor: Unit,
                                  action_type: str, payload: dict[str, Any]) -> None:
        if action_type == "skill":
            self._lock_skill_user(battle, actor)

    def on_skill_effect_finished(self, battle: Battle, queued_action: Any) -> None:
        # Reaction skills use a separate declaration path. Their completed
        # recipient notification gives the same restriction after resolution.
        actor = battle.units.get(queued_action.actor_id)
        if actor is not None:
            self._lock_skill_user(battle, actor)

    def _lock_skill_user(self, battle: Battle, actor: Unit) -> None:
        owner = self.owner
        if owner is None or actor.unit_id == owner.unit_id or not actor.alive:
            return
        if actor.has_status("印第安娃娃禁主动"):
            return
        lock = IndianDollSkillLock()
        lock.is_skill_effect = False
        actor.add_status(lock, source=owner)
        if lock in actor.statuses:
            battle.log_public_event(f"{actor.name} 对 {owner.name} 用技能后，本回合不能再用主动技能。",
                                    source=owner, target=actor)


class IndianDollSkillLock(StatusEffect):
    def __init__(self) -> None:
        super().__init__("印第安娃娃禁主动", "本回合不能再使用主动技能。", duration=1,
                         tick_scope="any_turn_end")

    def blocks_skill_use(self, battle: Battle, actor: Unit, skill: Skill) -> tuple[bool, str]:
        if actor is self.owner and skill.is_active:
            return True, "印第安娃娃使本回合不能再使用主动技能。"
        return False, ""


class IndianDollEquipmentTrait(PuppetEquipmentTrait):
    def __init__(self) -> None:
        super().__init__("印第安娃娃装备")

    def make_host_status(self, puppet: Unit) -> PuppetHostStatus:
        return IndianDollHostStatus(puppet, self)

    def ai_equipment_value(self, battle: Battle, host: Unit) -> float:
        attackable = any(_present(enemy) and enemy.player_id != host.player_id
                         and battle.distance_between_units(host, enemy)
                         <= host.normal_move_distance() + host.targeting_range()
                         for enemy in battle.all_units())
        return (24 + max(0, host.stat("attack") - 2) * 8) if attackable else 10


class FrenzyAxeSkill(Skill):
    """A committed direction with three independently defended attack segments."""
    ai_payload_value_owned = True

    def __init__(self) -> None:
        super().__init__("frenzy_axe", "狂躁之斧", "2轮一次；选一个方向连续攻击3次。",
                         cooldown_turns=2, target_mode="cell", direction_mode="required")

    def _direction(self, actor: Unit, payload: dict[str, Any]) -> tuple[int, int]:
        return payload_direction(payload)

    def _line(self, battle: Battle, actor: Unit, direction: tuple[int, int]) -> list[Position]:
        assert actor.position is not None
        return battle.line_positions(actor.position, direction, actor.targeting_range())

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if payload and ("direction" in payload or "dx" in payload):
            try:
                return ((True, "") if self.get_target_units_for_payload(battle, actor, payload)
                        else (False, "该方向没有可攻击单位。"))
            except (ActionError, KeyError, TypeError, ValueError):
                return False, "请选择合法攻击方向。"
        return ((True, "") if any(battle.effect_units_at_cells(self._line(battle, actor, direction))
                                for direction in ALL_DIRECTIONS)
                else (False, "周围没有可攻击方向。"))

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        cells = [cell for direction in ALL_DIRECTIONS for cell in self._line(battle, actor, direction)]
        return {"cells": [cell.to_dict() for cell in cells], "target_unit_ids": [],
                "secondary_cells": [], "requires_target": True}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return self._line(battle, actor, self._direction(actor, payload))

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return battle.effect_units_at_cells(self.get_target_cells_for_payload(battle, actor, payload))

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        line = self.get_target_cells_for_payload(battle, actor, payload)
        for index in range(3):
            battle.queue_area_damage_effect(actor=actor, display_name="狂躁之斧", cells=line,
                attack_power=actor.stat("attack"), tags={"attack", "frenzy_axe"},
                refresh_cell_targets=True, segment_index=index + 1, segment_count=3)

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload) or -1000.0

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit, payload: dict[str, Any],
                                  targets: list[Unit] | None = None, shared: Any = None,
                                  action: dict[str, Any] | None = None) -> bool | None:
        if targets is None:
            return None
        return any(target.player_id != actor.player_id
                   and (not target.magic_immunity or self.ignores_magic_immunity_for_payload(
                       battle, actor, payload))
                   for target in targets)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float | None:
        try:
            cells = self.get_target_cells_for_payload(battle, actor, payload)
        except (ActionError, KeyError, TypeError, ValueError):
            return -1000.0
        score = sum((1 if target.player_id != actor.player_id else -1.3)
                    * _area_hit_value(battle, target, actor.stat("attack"), cells, 3)
                    for target in battle.effect_units_at_cells(cells)
                    if not target.magic_immunity or self.ignores_magic_immunity_for_payload(
                        battle, actor, payload))
        return score - 14


class SkirtHostStatus(PuppetHostStatus):
    def on_owner_turn_end(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or not _present(owner) or self.attached_puppet(battle) is None:
            return
        rolled = battle.roll_die(6, action_name="魅惑裙摆轮末掷骰", beneficiary_player_id=owner.player_id)
        power = rolled / 2
        cells = [cell for cell in square_around_cells(battle, battle.unit_cells(owner), radius=1)
                 if cell not in set(battle.unit_cells(owner))]
        battle.log_public_event(f"{owner.name} 的魅惑裙摆掷出 {rolled}，周围伤害为 {power:g}。", source=owner)
        battle.queue_skill_effect_action(
            actor=owner, display_name="魅惑裙摆", effect_code="skirt_pulse",
            payload={"attack_power": power, "refresh_cell_targets": True},
            target_cells=cells, effect_resolver=_resolve_skirt_pulse,
        )


def _resolve_skirt_pulse(battle: Battle, actor: Unit, queued_action: Any) -> None:
    power = float(queued_action.payload["attack_power"])
    for unit in battle.effect_units_at_cells(queued_action.target_cells):
        if unit.unit_id == actor.unit_id:
            continue
        battle.resolve_damage(DamageContext(
            source=actor, target=unit, attack_power=power, is_skill=False,
            action_name="魅惑裙摆", ignore_physical_immunity=True,
            tags={"skirt_pulse", "trait"},
        ))


class SkirtEquipmentTrait(PuppetEquipmentTrait):
    def __init__(self) -> None:
        super().__init__("魅惑裙摆装备", speed=1, attack_range=1)

    def make_host_status(self, puppet: Unit) -> PuppetHostStatus:
        return SkirtHostStatus(puppet, self)

    def ai_equipment_value(self, battle: Battle, host: Unit) -> float:
        ring = set(square_around_cells(battle, battle.unit_cells(host), radius=1)) - set(battle.unit_cells(host))
        pressure = sum((1 if unit.player_id != host.player_id else -1.3) * 8
                       for unit in battle.effect_units_at_cells(list(ring)))
        return super().ai_equipment_value(battle, host) + pressure


class GorgeousDanceSkill(Skill):
    ai_enemy_impact_requirement = False
    ai_payload_value_owned = True

    def __init__(self) -> None:
        super().__init__("gorgeous_dance", "华丽狂舞", "2轮一次；周围5×5双方单位受到固定3伤。",
                         cooldown_turns=2, target_mode="self")

    def area(self, battle: Battle, actor: Unit) -> list[Position]:
        return [cell for cell in square_around_cells(battle, battle.unit_cells(actor), radius=2)
                if cell not in set(battle.unit_cells(actor))]

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return self.area(battle, actor)

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return battle.effect_units_at_cells(self.area(battle, actor))

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        battle.queue_area_damage_effect(actor=actor, display_name=self.name, cells=self.area(battle, actor),
                                        attack_power=3, tags={"skill", self.code})

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        cells = self.area(battle, actor)
        return {"cells": [cell.to_dict() for cell in cells],
                "target_unit_ids": [unit.unit_id for unit in battle.effect_units_at_cells(cells)],
                "secondary_cells": [], "requires_target": False}

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload) or -1000.0

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float | None:
        cells = self.area(battle, actor)
        score = sum((1 if target.player_id != actor.player_id else -1.3)
                    * _area_hit_value(battle, target, 3, cells, 1)
                    for target in battle.effect_units_at_cells(cells)
                    if not target.magic_immunity or self.ignores_magic_immunity_for_payload(
                        battle, actor, payload))
        return score - 12
