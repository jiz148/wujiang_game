"""R339 soft armor dragon: damage adaptation, tether and five-step assault."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    Battle, DamageContext, HealContext, MoveContext, Position,
    Skill, StatusEffect, Trait, Unit,
)
from wujiang.tactical.heroes.common import DashMoveSkill, positions_to_dict


def _present(unit: Unit | None) -> bool:
    return bool(unit is not None and unit.alive and not unit.banished and unit.position is not None)


class SoftArmorDamageTrait(Trait):
    def __init__(self) -> None:
        super().__init__("软甲适应", "每次实际受伤，当前武将回合结束前守+1。")
        self.bonus = 0

    def on_battle_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if self.owner is ctx.target and _present(self.owner) and ctx.actual_damage > 0:
            self.bonus += 1
            battle.log_public_event(f"{self.owner.name} 受伤后守+1（本回合共+{self.bonus}）。",
                                    source=self.owner, target=self.owner)

    def modify_stat(self, stat_name: str, value: float) -> float:
        return value + self.bonus if stat_name == "defense" else value

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        if not battle.is_army_turn():
            self.bonus = 0


class SoftArmorLevelTrait(Trait):
    def __init__(self) -> None:
        super().__init__("等级弹性", "在场时可当作等级2、3、4或5使用；原生等级仍为4。")

    def treatable_levels(self, battle: Battle) -> tuple[int, ...]:
        return (2, 3, 4, 5) if _present(self.owner) else ()


class SoftArmorEntangleStatus(StatusEffect):
    def __init__(self, source_id: str) -> None:
        super().__init__("缠绕", "在自己或施术龙真正位移之前，不能移动或使用位移技能。")
        self.source_unit_id = source_id

    @property
    def prevents_movement(self) -> bool:
        owner = self.owner
        battle_ref = getattr(owner, "_battle_ref", None) if owner is not None else None
        battle = battle_ref() if battle_ref is not None else None
        return bool(battle is not None and _present(battle.units.get(self.source_unit_id)))

    def blocks_skill_use(self, battle: Battle, actor: Unit, skill: Skill) -> tuple[bool, str]:
        if actor is self.owner and self.prevents_movement and skill.is_displacement_skill:
            return True, "缠绕期间不能使用位移技能。"
        return False, ""

    def on_unit_moved(self, battle: Battle, ctx: MoveContext) -> None:
        owner = self.owner
        if owner is not None and ctx.start != ctx.end and ctx.unit.unit_id in {owner.unit_id, self.source_unit_id}:
            owner.remove_status(self, battle)

    def on_unit_destroyed(self, battle: Battle, destroyed_unit: Unit) -> None:
        if self.owner is not None and destroyed_unit.unit_id == self.source_unit_id:
            self.owner.remove_status(self, battle)

    def on_any_turn_start(self, battle: Battle, active_unit: Unit) -> None:
        if self.owner is not None and not _present(battle.units.get(self.source_unit_id)):
            self.owner.remove_status(self, battle)


class SoftArmorEntangleSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("entangle", "缠绕", "1魔，每本人回合一次；范1命中单位禁移动及位移技能，任一端位移时解除。",
                         mana_cost=1, max_uses_per_turn=1, target_mode="unit")

    def direct_unit_target_range(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> int:
        return 1

    def _eligible(self, battle: Battle, actor: Unit, target: Unit) -> bool:
        return (_present(target) and battle.unit_can_be_selected(target, actor=actor)[0]
                and battle.unit_target_in_range_and_line(actor, target, 1))

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if payload:
            target = battle.units.get(str(payload.get("target_unit_id") or ""))
            if target is None or not self._eligible(battle, actor, target):
                return False, "请选择范1内可命中的单位。"
        elif not any(self._eligible(battle, actor, unit) for unit in battle.all_units()):
            return False, "范1内没有可缠绕的单位。"
        return True, ""

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = battle.resolve_declared_target_unit(actor, payload)
        if target is None or not _present(target):
            battle.log(f"{actor.name} 的缠绕目标已离开原格，效果落空。")
            return
        result = battle.validate_target(actor, target, action_name=self.name, is_skill=True,
                                        is_hostile=target.player_id != actor.player_id,
                                        record_effect_on_validation=False)
        if result.cancelled or not _present(result.target):
            return
        recipient = result.target
        if recipient.skill_non_damage_effects_blocked():
            return
        status = SoftArmorEntangleStatus(actor.unit_id)
        recipient.add_status(status, source=actor)
        if status in recipient.statuses:
            for old in list(recipient.statuses):
                if (old is not status and isinstance(old, SoftArmorEntangleStatus)
                        and old.source_unit_id == actor.unit_id):
                    recipient.remove_status(old, battle)
            battle.record_skill_effect(recipient)
            battle.log_public_event(f"{recipient.name} 被{actor.name}缠绕。", source=actor, target=recipient)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = [unit for unit in battle.all_units() if self._eligible(battle, actor, unit)]
        return {"cells": positions_to_dict([cell for unit in targets for cell in battle.unit_cells(unit)]),
                "target_unit_ids": [unit.unit_id for unit in targets],
                "secondary_cells": [], "requires_target": True}

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float | None:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target is None or not self._eligible(battle, actor, target):
            return -1000.0
        if (target.player_id == actor.player_id or target.cannot_move or target.magic_immunity
                or target.direct_effects_blocked() or target.skill_non_damage_effects_blocked()
                or target.total_shields() > 0
                or target.dodge_charges > 0):
            return -1000.0
        if any(isinstance(status, SoftArmorEntangleStatus) and status.source_unit_id == actor.unit_id
               for status in target.statuses):
            return -1000.0
        mobility = target.normal_move_distance()
        displacement = sum(skill.is_displacement_skill for skill in target.skills)
        if mobility <= 0 and not displacement:
            return -1000.0
        urgency = 0.0
        target_slot = battle.unit_turn_slot_id(target)
        if target_slot in battle.turn_order_unit_ids:
            turns_ahead = (battle.turn_order_unit_ids.index(target_slot) - battle.turn_slot_index) % len(
                battle.turn_order_unit_ids)
            urgency = max(0, 4 - turns_ahead) * 5.0
        return (12.0 + min(4, mobility) * 8.0 + min(2, displacement) * 12.0
                + min(5, target.level) * 4.0 + urgency)

    def ai_priority_score(self, context: Any) -> float:
        value = self.ai_payload_value(context.battle, context.actor, context.payload)
        return value if value is not None else -1000.0


class EarthDragonAssaultSkill(DashMoveSkill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("earth_dragon_assault", "地龙突袭", "每本人回合一次，移动恰好5格，然后回复最大血的一半。",
                         max_distance=5, exact_distance=5, mana_cost=0, max_uses_per_turn=1,
                         ignore_units=True)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        super().execute(battle, actor, payload)
        if _present(actor):
            battle.heal(HealContext(source=actor, target=actor, amount=actor.max_health / 2,
                                    action_name=self.name))

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float | None:
        if actor.position is None:
            return -1000.0
        try:
            destination = Position(int(payload["x"]), int(payload["y"]))
        except (KeyError, TypeError, ValueError):
            return -1000.0
        if destination not in battle.reachable_positions(actor, max_distance=5, exact_distance=5,
                                                           ignore_units=True):
            return -1000.0
        heal = min(actor.max_health / 2, max(0.0, actor.max_health - actor.current_hp))
        enemies = [unit for unit in battle.enemy_units(actor.player_id) if _present(unit)]
        if not enemies:
            return heal * 100.0 - 10.0
        before = min(battle.distance_between_units(actor, enemy) for enemy in enemies)
        after = min(cell.distance_to(body)
                    for cell in actor.footprint_cells_at(destination)
                    for enemy in enemies for body in battle.unit_cells(enemy))
        position_gain = max(-6, min(6, before - after)) * 7.0
        def incoming_threat(body: list[Position]) -> float:
            return sum(
                battle.damage_rule.calculate_damage(enemy.stat("attack"), actor.stat("defense"))
                for enemy in enemies
                if min(cell.distance_to(target) for cell in body
                       for target in battle.unit_cells(enemy)) <= enemy.targeting_range()
            )
        threat_change = incoming_threat(actor.footprint_cells_at(destination)) - incoming_threat(
            battle.unit_cells(actor))
        breaking_tethers = sum(
            isinstance(status, SoftArmorEntangleStatus) and status.source_unit_id == actor.unit_id
            for unit in battle.all_units() for status in unit.statuses
        )
        return heal * 100.0 + position_gain - threat_change * 70.0 - breaking_tethers * 22.0 - 5.0

    def ai_priority_score(self, context: Any) -> float:
        value = self.ai_payload_value(context.battle, context.actor, context.payload)
        return value if value is not None else -1000.0
