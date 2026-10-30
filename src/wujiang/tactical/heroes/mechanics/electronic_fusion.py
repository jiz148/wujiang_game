"""Electronic fusion, shot, detachment, and repair."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.army import is_army_soldier
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import Unit
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.heroes.common import FlyingTrait
from wujiang.tactical.heroes.common import StatModifierStatus
from wujiang.tactical.heroes.common import payload_position
from wujiang.tactical.heroes.common import payload_target_unit
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.mechanics.lao_mage import MageCloakEquippedStatus
from wujiang.tactical.heroes.mechanics.soul_wraith import PassThroughMovementTrait
from wujiang.tactical.heroes.mechanics.next_five_geometry import square_around_cells


class ElectronicFusionStatus(StatModifierStatus):
    def __init__(self, wing_id: str) -> None:
        super().__init__(f"电子融合·{wing_id}", attack_delta=1, defense_delta=1, range_delta=1,
                         description="装甲电子翼：攻守范+1、飞行、每本人轮一次能力牺牲射击。")
        self.wing_id = wing_id
        self.detaching = False
        self.shot = ElectronicFusionShotSkill(wing_id)
        self.detach = ElectronicFusionDetachSkill(wing_id)

    def bind(self, owner: HeroUnit) -> "ElectronicFusionStatus":
        super().bind(owner)
        self.shot.bind(owner)
        self.detach.bind(owner)
        owner.has_flying = True
        owner.ignore_units_while_moving = True
        return self

    def provided_components(self) -> tuple[Skill, Skill]:
        return self.shot, self.detach

    def _destroy_attached_wing(self, battle: Battle) -> None:
        owner = self.owner
        wing = battle.units.get(self.wing_id)
        if self.detaching or wing is None or not wing.alive or owner is None:
            return
        wing.alive = False
        wing.position = owner.position or getattr(owner, "last_position", None)
        battle.capture_destruction_position(wing)
        if wing.unit_id not in {unit.unit_id for unit in battle.destroyed_units}:
            battle.destroyed_units.append(wing)
        battle.log_public_event(f"{wing.name} 随宿主 {owner.name} 被破坏。", source=owner, target=wing)
        battle.remove_unit(wing)

    def on_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is not None:
            owner.has_flying = any(isinstance(trait, FlyingTrait) for trait in owner.traits) or any(
                isinstance(status, ElectronicFusionStatus) for status in owner.statuses
            ) or any(isinstance(status, MageCloakEquippedStatus) for status in owner.statuses)
            owner.ignore_units_while_moving = owner.has_flying or any(
                isinstance(trait, PassThroughMovementTrait) for trait in owner.traits
            )
        self._destroy_attached_wing(battle)

    def on_owner_removed(self, battle: Battle) -> None:
        self._destroy_attached_wing(battle)

    def sync_linked_state(self, battle: Battle) -> None:
        owner = self.owner
        wing = battle.units.get(self.wing_id)
        if owner is not None and self in owner.statuses and (
            not owner.alive or wing is None or not wing.alive or getattr(wing, "equipped_to_id", None) != owner.unit_id
        ):
            owner.remove_status(self, battle)


class ElectronicFusionSkill(Skill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        battle, actor, payload = context.battle, context.actor, context.payload
        try:
            host = battle.get_unit(str(payload["target_unit_id"]))
            if host not in self.valid_hosts(battle, actor):
                return -1000.0
        except (ActionError, KeyError, TypeError, ValueError):
            return -1000.0
        if host.current_hp <= 0.25:
            return -1000.0
        with context.probe_rollback():
            before = context.action_state()
            try:
                self.execute(battle, actor, payload)
            except (ActionError, KeyError, TypeError, ValueError):
                return -1000.0
            if getattr(actor, "equipped_to_id", None) != host.unit_id:
                return -1000.0
            benefit = context.action_delta(before) + 12.0
        host_threat = max((enemy.stat("attack") - host.stat("defense")
                           for enemy in context.hostile_units()
                           if battle.distance_between_units(host, enemy)
                           <= enemy.normal_move_distance() + enemy.targeting_range()),
                          default=-10.0)
        if host_threat >= 0 and host.current_hp <= 0.5:
            benefit -= 45.0
        if actor.attacks_used < actor.attack_actions_per_turn():
            benefit -= max(0.0, context.best_attack_score()) * 0.6
        return benefit - 47.0

    def __init__(self) -> None:
        super().__init__("electronic_fusion", "电子融合", "装备到范内一名己方电子系武将身上。", target_mode="unit")

    def valid_hosts(self, battle: Battle, actor: Unit) -> list[Unit]:
        return [unit for unit in battle.all_units()
                if unit.alive and unit.position is not None and not unit.banished
                and unit.player_id == actor.player_id and "电子" in unit.name
                and unit.name != "电子修理工厂" and unit.unit_id != actor.unit_id
                and battle.unit_can_be_selected(unit, actor=actor)[0]
                and battle.unit_target_in_range_and_line(actor, unit, actor.targeting_range())]

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if not self.valid_hosts(battle, actor):
            return False, "范内没有可融合的己方电子系武将。"
        if payload and payload.get("target_unit_id") not in {unit.unit_id for unit in self.valid_hosts(battle, actor)}:
            return False, "请选择范内合法的己方电子系武将。"
        return True, ""

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        host = payload_target_unit(battle, payload)
        if host not in self.valid_hosts(battle, actor):
            raise ActionError("电子融合宿主已失效。")
        status = ElectronicFusionStatus(actor.unit_id)
        # Equipment does not count as a damaging or disabling skill effect on a clone.
        status.is_skill_effect = False
        host.add_status(status, source=actor)
        if status not in host.statuses:
            battle.log_public_event(f"{host.name} 未受到电子融合装备效果。", source=actor, target=host)
            return
        actor.equipped_to_id = host.unit_id
        actor.position = None
        battle.log_public_event(f"{actor.name} 电子融合到 {host.name} 身上，宿主攻守范+1并获得飞行。",
                                source=actor, target=host)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        hosts = self.valid_hosts(battle, actor)
        return {"cells": positions_to_dict([cell for host in hosts for cell in battle.unit_cells(host)]),
                "target_unit_ids": [host.unit_id for host in hosts], "secondary_cells": [], "requires_target": True}


class ElectronicFusionShotSkill(Skill):
    target_cells_are_geometry_only = True

    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        actor, battle, payload = context.actor, context.battle, context.payload
        try:
            cell = self.selected_cell(payload)
            target = next((unit for unit in battle.effect_units_at_cells([cell])
                           if unit.player_id != actor.player_id), None)
            if target is None or str(payload.get("choice_code") or "") not in self.available_stats(actor):
                return -1000.0
        except (ActionError, KeyError, TypeError, ValueError):
            return -1000.0
        value = context.paid_skill_value()
        if value <= 0:
            return -1000.0
        stat = str(payload["choice_code"])
        future_cost = {"attack": 13.0, "defense": 16.0, "speed": 12.0,
                       "attack_range": 31.0, "mana": 30.0}[stat]
        if actor.stat(stat) <= (1 if stat != "mana" else 0) + 1:
            future_cost += 12.0
        return value - future_cost

    def __init__(self, wing_id: str) -> None:
        self.wing_id = wing_id
        super().__init__(f"fusion_piercing_shot__{wing_id}", "电子翼破魔射击",
                         "永久降低宿主一项能力1，对范3一个实际受体造成固定4破魔伤害。",
                         max_uses_per_turn=1, target_mode="cell")

    def available_stats(self, actor: Unit) -> list[str]:
        floors = {"attack": 1, "defense": 1, "speed": 1, "attack_range": 1, "mana": 0}
        return [name for name, floor in floors.items() if actor.stat(name) > floor]

    def available_cells(self, battle: Battle, actor: Unit) -> list[Position]:
        return sorted({cell for target in battle.all_units()
                       if target.alive and target.position is not None and not target.banished
                       and battle.unit_can_be_selected(target, actor=actor)[0]
                       for cell in battle.unit_cells(target)
                       if battle.unit_distance_to_cell(actor, cell) <= 3}, key=lambda cell: (cell.y, cell.x))

    def selected_cell(self, payload: dict[str, Any]) -> Position:
        cells = payload.get("cells")
        if isinstance(cells, list) and len(cells) == 1:
            return Position(**cells[0])
        return payload_position(payload)

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if not self.available_stats(actor) or not self.available_cells(battle, actor):
            return False, "没有可支付的能力值或范内目标。"
        if payload and (str(payload.get("choice_code") or "") not in self.available_stats(actor)
                        or self.selected_cell(payload) not in self.available_cells(battle, actor)):
            return False, "请选择可降低的能力值与范3内目标。"
        return True, ""

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        return [self.selected_cell(payload)]

    def get_target_units_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Unit]:
        return battle.effect_units_at_cells([self.selected_cell(payload)])

    def ignores_shield_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> bool:
        return True

    def pay_stat_cost(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        stat = str(payload.get("choice_code") or "")
        if stat not in self.available_stats(actor):
            raise ActionError("电子翼破魔射击的能力代价已失效。")
        setattr(actor.base_stats, stat, getattr(actor.base_stats, stat) - 1)
        if stat == "mana":
            actor.current_mana = min(actor.current_mana, actor.max_mana())
        battle.log_public_event(f"{actor.name} 为电子翼破魔射击永久降低{stat} 1点。", source=actor)

    def prepay_resources(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> None:
        if payload is None:
            raise ActionError("电子翼破魔射击需要选择能力代价。")
        if str(payload.get("choice_code") or "") not in self.available_stats(actor):
            raise ActionError("电子翼破魔射击的能力代价已失效。")
        super().prepay_resources(battle, actor, payload)
        self.pay_stat_cost(battle, actor, payload)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cell = self.selected_cell(payload)
        if not payload.get("resources_prepaid"):
            if cell not in self.available_cells(battle, actor):
                raise ActionError("电子翼破魔射击目标已失效。")
            self.pay_stat_cost(battle, actor, payload)
        battle.queue_area_damage_effect(actor=actor, display_name=self.name, cells=[cell], attack_power=4,
                                        ignore_shield=True, tags={"skill", "fusion_piercing_shot"},
                                        refresh_cell_targets=True)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        cells = self.available_cells(battle, actor)
        labels = {"attack": "攻", "defense": "守", "speed": "速", "attack_range": "范", "mana": "魔"}
        return {"cells": positions_to_dict(cells), "target_unit_ids": [
                    unit.unit_id for unit in battle.effect_units_at_cells(cells)
                    if battle.unit_can_be_selected(unit, actor=actor)[0]],
                "secondary_cells": [], "requires_target": True,
                "selection": {"mode": "choice_pattern",
                              "choice_prompt": "先选择永久降低的能力，再点击范3内的一名目标。",
                              "cell_prompt": "点击要承受固定4点破魔伤害的目标格。",
                              "choices": [
                    {"code": stat, "label": f"{labels[stat]}-1", "patterns": [positions_to_dict([cell]) for cell in cells]}
                    for stat in self.available_stats(actor)]}}


class ElectronicFusionDetachSkill(Skill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        battle, actor, payload = context.battle, context.actor, context.payload
        wing = battle.units.get(self.wing_id)
        if wing is None or not wing.alive or actor.current_hp > 0.5:
            return -1000.0
        try:
            cell = Position(int(payload["x"]), int(payload["y"]))
            if cell not in self.available_cells(battle, actor):
                return -1000.0
        except (ActionError, KeyError, TypeError, ValueError):
            return -1000.0
        threats = [enemy for enemy in context.hostile_units()
                   if context.distance_to_position(enemy, cell)
                   <= enemy.normal_move_distance() + enemy.targeting_range()]
        if not threats:
            return 28.0 + (0.5 - actor.current_hp) * 70.0
        return -1000.0

    def __init__(self, wing_id: str) -> None:
        self.wing_id = wing_id
        super().__init__(f"electronic_unfuse__{wing_id}", "解除电子融合", "将原电子翼放到宿主身体外邻合法空格。", target_mode="cell")

    def available_cells(self, battle: Battle, actor: Unit) -> list[Position]:
        wing = battle.units.get(self.wing_id)
        if wing is None or not wing.alive or getattr(wing, "equipped_to_id", None) != actor.unit_id:
            return []
        return [cell for cell in square_around_cells(battle, battle.unit_cells(actor), radius=1)
                if battle.can_place_unit(wing, cell, ignore=wing, mover=wing)]

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if not self.available_cells(battle, actor):
            return False, "宿主周围没有电子翼可回场的合法空格。"
        if payload and payload_position(payload) not in self.available_cells(battle, actor):
            return False, "请选择宿主周围完整合法空格。"
        return True, ""

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cell = payload_position(payload)
        if cell not in self.available_cells(battle, actor):
            raise ActionError("电子翼解除融合落点已失效。")
        status = next((status for status in actor.statuses
                       if isinstance(status, ElectronicFusionStatus) and status.wing_id == self.wing_id), None)
        if status is None:
            raise ActionError("电子翼融合状态已失效。")
        wing = battle.units[self.wing_id]
        status.detaching = True
        actor.remove_status(status, battle)
        wing.equipped_to_id = None
        wing.position = cell
        battle.log_public_event(f"{actor.name} 解除电子融合，{wing.name} 在周围回场。", source=actor, target=wing)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {"cells": positions_to_dict(self.available_cells(battle, actor)),
                "target_unit_ids": [], "secondary_cells": [], "requires_target": True}


class ElectronicRepairSkill(Skill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        battle, actor, payload = context.battle, context.actor, context.payload
        try:
            target = self.candidate_by_payload(battle, payload)
            destination = Position(int(payload["x"]), int(payload["y"]))
        except (ActionError, KeyError, TypeError, ValueError):
            return -1000.0
        if target.player_id != actor.player_id or destination not in self.legal_destinations(battle, actor, target):
            return -1000.0
        with context.probe_rollback():
            try:
                self.execute(battle, actor, payload)
            except (ActionError, KeyError, TypeError, ValueError):
                return -1000.0
            if not target.alive or target.position != destination:
                return -1000.0
            return 90.0 + 45.0 * target.current_hp + context.position_value(target) * 0.8

    def __init__(self) -> None:
        super().__init__("electronic_repair", "电子维修",
                         "每回合一次，在自身周围合法位置以满生命和满魔力修复一名已被破坏的机甲武将。",
                         max_uses_per_turn=1, target_mode="cell")

    def destroyed_candidates(self, battle: Battle) -> list[Unit]:
        return [unit for unit in battle.destroyed_units
                if unit.unit_id not in battle.units and not unit.alive
                and isinstance(unit, HeroUnit) and unit.race == "机甲"
                and not unit.is_summon and not unit.is_clone and not is_army_soldier(unit)
                and not unit.banished]

    def candidate_by_payload(self, battle: Battle, payload: dict[str, Any]) -> Unit:
        unit_id = str(payload.get("revive_unit_id") or "").strip()
        for unit in self.destroyed_candidates(battle):
            if unit.unit_id == unit_id:
                return unit
        raise ActionError("请选择一名已被破坏的机甲武将。")

    def legal_destinations(self, battle: Battle, actor: Unit, target: Unit) -> list[Position]:
        if actor.position is None:
            return []
        cells = {neighbor for cell in battle.unit_cells(actor) for neighbor in battle.neighbors(cell)}
        return [cell for cell in sorted(cells, key=lambda item: (item.y, item.x))
                if battle.can_place_unit(target, cell, ignore=target, mover=target)]

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok or not payload:
            return ok, reason
        try:
            target = self.candidate_by_payload(battle, payload)
            if payload_position(payload) not in self.legal_destinations(battle, actor, target):
                return False, "该位置不能修复机甲武将。"
        except (ActionError, KeyError, TypeError, ValueError) as exc:
            return False, str(exc)
        return True, ""

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        try:
            target = self.candidate_by_payload(battle, payload)
            destination = payload_position(payload)
            if destination not in self.legal_destinations(battle, actor, target):
                battle.log(f"{actor.name} 的电子维修落点已失效。")
                return
        except (ActionError, KeyError, TypeError, ValueError):
            battle.log(f"{actor.name} 的电子维修目标已失效。")
            return
        for status in list(target.statuses):
            if status.duration is not None:
                target.remove_status(status, battle)
        target.alive = True
        target.banished = False
        target.current_hp = target.max_health
        target.current_mana = target.max_mana()
        target.turn_ready = False
        target.can_act_on_entry_turn = True
        target.move_used = False
        target.normal_move_actions_used = 0
        target.normal_move_steps_used = 0
        target.attacks_used = 0
        target.performed_active_skill = False
        target.moved_this_turn = False
        target.actions_taken_this_turn = []
        target.clear_end_of_turn_shields()
        battle.add_unit(target, destination, trigger_enter_effects=False)
        battle.destroyed_units = [unit for unit in battle.destroyed_units if unit.unit_id != target.unit_id]
        battle.notify_destroyed_hero_count_changed()
        battle.log_public_event(f"{actor.name} 修复了 {target.name}。", source=actor, target=target)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        candidates = []
        all_cells: list[Position] = []
        for target in self.destroyed_candidates(battle):
            cells = self.legal_destinations(battle, actor, target)
            if cells:
                candidates.append({"id": target.unit_id, "name": target.name,
                                   "cells": positions_to_dict(cells)})
                all_cells.extend(cells)
        return {"cells": positions_to_dict(list(dict.fromkeys(all_cells))),
                "target_unit_ids": [], "secondary_cells": positions_to_dict(battle.unit_cells(actor)),
                "requires_target": True,
                "selection": {"mode": "revive_unit_cell", "candidates": candidates}}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        return [payload_position(payload)]


