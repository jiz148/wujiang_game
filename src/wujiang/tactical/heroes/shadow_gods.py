"""Shared, object-owned graveyard return for the five Shadow Gods."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import ActionError, Battle, DamageContext, Position, Skill, StatusEffect, Unit
from wujiang.tactical.heroes.common import payload_position, positions_to_dict


class ShadowApocalypseEntryTurn(StatusEffect):
    """Lend a revived hero the summoner's turn slot for this turn only."""

    def __init__(self, turn_slot_id: str) -> None:
        super().__init__("默示录当回合行动", "本回合随召唤者行动。", duration=None)
        self.turn_slot_id = turn_slot_id
        self.previous_slot_id: str | None = None

    def bind(self, owner: Unit) -> ShadowApocalypseEntryTurn:
        super().bind(owner)
        self.previous_slot_id = getattr(owner, "preserved_turn_slot_id", None)
        owner.preserved_turn_slot_id = self.turn_slot_id
        return self

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        if self.owner is not None and battle.current_turn_slot_unit_id() == self.turn_slot_id:
            self.owner.remove_status(self, battle)

    def on_removed(self, battle: Battle) -> None:
        if self.owner is not None:
            self.owner.preserved_turn_slot_id = self.previous_slot_id


def restore_shadow_unit(battle: Battle, actor: Unit, target: Unit,
                        cell: Position, *, action_name: str) -> bool:
    """Shared return transaction for Apocalypse and the Last King's kill reward."""
    if not battle.unit_entry_allowed(target)[0] or not battle.can_place_unit(
            target, cell, ignore=target, mover=target):
        return False
    for status in list(target.statuses):
        if status.duration is not None:
            target.remove_status(status, battle)
    target.alive = True
    target.banished = False
    target.player_id = actor.player_id
    if target.is_summon:
        target.summoner_id = actor.unit_id
    target.current_hp = target.max_health
    target.current_mana = target.max_mana()
    target.turn_ready = True
    target.can_act_on_entry_turn = True
    target.move_used = False
    target.normal_move_actions_used = 0
    target.normal_move_steps_used = 0
    target.attacks_used = 0
    target.performed_active_skill = False
    target.moved_this_turn = False
    target.actions_taken_this_turn = []
    target.clear_end_of_turn_shields()
    battle.add_unit(target, cell, trigger_enter_effects=False)
    battle.destroyed_units = [unit for unit in battle.destroyed_units if unit.unit_id != target.unit_id]
    target.add_status(ShadowApocalypseEntryTurn(battle.current_turn_slot_unit_id()))
    for component in list(target.iter_components()):
        callback = getattr(component, "on_restored_by_apocalypse", None)
        if callable(callback):
            callback(battle)
    battle.notify_destroyed_hero_count_changed()
    battle.log_public_event(f"{actor.name} 使用{action_name}召回 {target.name}，其当回合可行动。",
                            source=actor, target=target)
    return True


class ShadowApocalypseSkill(Skill):
    """Select a lower-level destroyed display name and a legal adjacent landing."""

    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("shadow_apocalypse", "默示录",
                         "每本人轮一次，血-1/4，召回一名更低级且显示名含影神的战死单位；当回合可行动。",
                         max_uses_per_turn=1, target_mode="cell")

    def destroyed_candidates(self, battle: Battle, actor: Unit) -> list[Unit]:
        return [unit for unit in battle.destroyed_units
                if not unit.alive and not unit.banished and unit.unit_id not in battle.units
                and "影神" in unit.name and unit.level < actor.level
                and battle.unit_entry_allowed(unit)[0]]

    def legal_destinations(self, battle: Battle, actor: Unit, target: Unit) -> list[Position]:
        if actor.position is None:
            return []
        cells = {cell for origin in battle.unit_cells(actor) for cell in battle.neighbors(origin)}
        return [cell for cell in sorted(cells, key=lambda item: (item.y, item.x))
                if battle.can_place_unit(target, cell, ignore=target, mover=target)]

    def choices(self, battle: Battle, actor: Unit) -> list[tuple[Unit, Position]]:
        return [(target, cell) for target in self.destroyed_candidates(battle, actor)
                for cell in self.legal_destinations(battle, actor, target)]

    def _choice(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> tuple[Unit, Position]:
        unit_id = str(payload.get("revive_unit_id") or "")
        cell = payload_position(payload)
        for target, position in self.choices(battle, actor):
            if target.unit_id == unit_id and position == cell:
                return target, cell
        raise ActionError("请选择一名更低级的战死影神和其合法回场格。")

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if not self.choices(battle, actor):
            return False, "没有可召回的更低级战死影神或合法落格。"
        if payload and ("revive_unit_id" in payload or "x" in payload or "y" in payload):
            try:
                self._choice(battle, actor, payload)
            except (ActionError, TypeError, ValueError) as exc:
                return False, str(exc)
        return True, ""

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target, cell = self._choice(battle, actor, payload)
        battle.resolve_damage(DamageContext(
            source=actor, target=actor, attack_power=0, raw_damage=actor.max_health / 4,
            ignore_shield=True, ignore_magic_immunity=True, ignore_physical_immunity=True,
            is_skill=True, action_name=self.name, tags={"skill", self.code, "friendly_hp_cost"},
        ))
        if not actor.alive or not restore_shadow_unit(
                battle, actor, target, cell, action_name=self.name):
            battle.log_public_event("默示录已付血费，但召回条件失效。", source=actor)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        candidates = []
        cells: list[Position] = []
        for target in self.destroyed_candidates(battle, actor):
            landings = self.legal_destinations(battle, actor, target)
            if landings:
                candidates.append({"id": target.unit_id, "name": target.name,
                                   "cells": positions_to_dict(landings)})
                cells.extend(landings)
        return {"cells": positions_to_dict(list(dict.fromkeys(cells))),
                "target_unit_ids": [], "secondary_cells": positions_to_dict(battle.unit_cells(actor)),
                "requires_target": True,
                "selection": {"mode": "revive_unit_cell", "candidates": candidates}}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return [self._choice(battle, actor, payload)[1]]

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "revive_unit_id": target.unit_id, "x": cell.x, "y": cell.y}
                for target, cell in self.choices(battle, actor)]

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        try:
            target, _ = self._choice(battle, actor, payload)
        except (ActionError, TypeError, ValueError):
            return -1000.0
        cost = actor.max_health / 4
        if actor.current_hp <= cost and not any(getattr(component, "converts_friendly_hp_loss", False)
                                                for component in actor.iter_components()):
            return -1000.0
        return target.stat("attack") * 25.0 + target.level * 15.0 - cost * 80.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)
