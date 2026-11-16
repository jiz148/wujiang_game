"""Shared attachment lifecycle for the three source-row puppet heroes.

An equipped puppet remains the same living unit, but its host owns the detach
action while the puppet has no board position or independent turn.
"""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import ActionError, Battle, Position, Skill, Trait, Unit
from wujiang.tactical.heroes.common import StatModifierStatus, payload_position
from wujiang.tactical.heroes.mechanics.next_five_geometry import square_around_cells


class PuppetEquipmentTrait(Trait):
    def __init__(self, name: str, *, defense: int = 0, speed: int = 0,
                 attack_range: int = 0) -> None:
        super().__init__(name, "作为道具时给予宿主加成及专属特性。")
        self.defense_bonus = defense
        self.speed_bonus = speed
        self.range_bonus = attack_range

    def make_host_status(self, puppet: Unit) -> PuppetHostStatus:
        return PuppetHostStatus(puppet, self)

    def ai_equipment_value(self, battle: Battle, host: Unit) -> float:
        return (self.defense_bonus * 12 + self.speed_bonus * 12 + self.range_bonus * 13)


class PuppetHostStatus(StatModifierStatus):
    """One attachment; multiple puppets can coexist on the same host."""

    def __init__(self, puppet: Unit, trait: PuppetEquipmentTrait) -> None:
        super().__init__(trait.name, defense_delta=trait.defense_bonus,
                         speed_delta=trait.speed_bonus, range_delta=trait.range_bonus,
                         description=f"{puppet.name}的装备加成。")
        self.puppet_id = puppet.unit_id
        self.detaching = False
        self.detach_skill = PuppetUnequipSkill(puppet.unit_id, puppet.name)

    def bind(self, owner: Unit) -> PuppetHostStatus:
        super().bind(owner)
        self.detach_skill.bind(owner)
        return self

    def provided_components(self) -> tuple[Skill, ...]:
        return (self.detach_skill,)

    def attached_puppet(self, battle: Battle) -> Unit | None:
        puppet = battle.units.get(self.puppet_id)
        if (puppet is None or not puppet.alive or self.owner is None
                or getattr(puppet, "equipped_to_id", None) != self.owner.unit_id):
            return None
        return puppet

    def _release_after_host_loss(self, battle: Battle) -> None:
        host = self.owner
        puppet = self.attached_puppet(battle)
        if host is None or puppet is None:
            return
        puppet.equipped_to_id = None
        if host.alive and host.position is not None:
            body = set(battle.unit_cells(host))
            candidates = [cell for cell in square_around_cells(battle, list(body), radius=1)
                          if cell not in body]
        else:
            old = getattr(host, "last_position", None) or host.position
            candidates = [old] if old is not None else []
        landing = next((cell for cell in candidates
                        if battle.can_place_unit(puppet, cell, ignore=host, mover=puppet)), None)
        if landing is not None:
            puppet.position = landing
            battle.log_public_event(f"{puppet.name} 随 {host.name} 的装备结束而回场。",
                                    source=puppet, target=host)
            return
        puppet.alive = False
        puppet.position = candidates[0] if candidates else None
        battle.capture_destruction_position(puppet)
        if puppet.unit_id not in {unit.unit_id for unit in battle.destroyed_units}:
            battle.destroyed_units.append(puppet)
        battle.remove_unit(puppet)

    def on_owner_removed(self, battle: Battle) -> None:
        if not self.detaching:
            self._release_after_host_loss(battle)

    def on_removed(self, battle: Battle) -> None:
        if not self.detaching:
            self._release_after_host_loss(battle)

    def sync_linked_state(self, battle: Battle) -> None:
        if self.owner is not None and self in self.owner.statuses and self.attached_puppet(battle) is None:
            self.owner.remove_status(self, battle)


class PuppetEquipSkill(Skill):
    ai_enemy_impact_requirement = False
    ai_payload_value_owned = True

    def __init__(self) -> None:
        super().__init__("puppet_equip", "装备", "随时；每回合一次；装备到范内一个己方单位。",
                         max_uses_per_turn=1, target_mode="unit", timing="instant")

    @staticmethod
    def equipment_trait(actor: Unit) -> PuppetEquipmentTrait:
        trait = next((item for item in actor.traits if isinstance(item, PuppetEquipmentTrait)), None)
        if trait is None:
            raise ActionError("该傀儡缺少装备特性。")
        return trait

    def valid_hosts(self, battle: Battle, actor: Unit) -> list[Unit]:
        if actor.position is None or getattr(actor, "equipped_to_id", None):
            return []
        return [host for host in battle.all_units()
                if host.unit_id != actor.unit_id and host.player_id == actor.player_id
                and host.alive and not host.banished and host.position is not None
                and battle.unit_can_be_selected(host, actor=actor)[0]
                and battle.unit_target_in_range_and_line(actor, host, actor.targeting_range())]

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        hosts = self.valid_hosts(battle, actor)
        if not hosts:
            return False, "范内没有可装备的己方单位。"
        if payload and payload.get("target_unit_id") not in {host.unit_id for host in hosts}:
            return False, "请选择范内合法的己方宿主。"
        return True, ""

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        host = battle.resolve_declared_target_unit(actor, payload)
        if host not in self.valid_hosts(battle, actor):
            battle.log_public_event(f"{actor.name} 的装备目标已离开原格。", source=actor)
            return
        assert host is not None
        status = self.equipment_trait(actor).make_host_status(actor)
        status.is_skill_effect = False
        host.add_status(status, source=actor)
        if status not in host.statuses:
            battle.log_public_event(f"{host.name} 未受到 {actor.name} 的装备效果。", source=actor, target=host)
            return
        actor.equipped_to_id = host.unit_id
        actor.position = None
        battle.log_public_event(f"{actor.name} 装备到 {host.name} 身上。", source=actor, target=host)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        hosts = self.valid_hosts(battle, actor)
        return {"cells": [cell.to_dict() for host in hosts for cell in battle.unit_cells(host)],
                "target_unit_ids": [host.unit_id for host in hosts],
                "secondary_cells": [], "requires_target": True}

    def ai_priority_score(self, context: Any) -> float:
        actor, battle = context.actor, context.battle
        score = self.ai_payload_value(battle, actor, context.payload)
        if score is None or score <= -1000:
            return -1000.0
        return score - max(0.0, context.best_attack_score()) * 0.65

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float | None:
        host = battle.units.get(str(payload.get("target_unit_id") or ""))
        if host not in self.valid_hosts(battle, actor):
            return -1000.0
        trait = self.equipment_trait(actor)
        threatened = any(battle.distance_between_units(host, enemy) <= enemy.targeting_range() + enemy.normal_move_distance()
                         for enemy in battle.all_units() if enemy.player_id != actor.player_id
                         and enemy.alive and not enemy.banished and enemy.position is not None)
        return (trait.ai_equipment_value(battle, host)
                + (4 if threatened and trait.defense_bonus else 0) - 12)


class PuppetUnequipSkill(Skill):
    ai_enemy_impact_requirement = False
    ai_payload_value_owned = True

    def __init__(self, puppet_id: str, puppet_name: str) -> None:
        super().__init__(f"puppet_unequip:{puppet_id}", f"卸下·{puppet_name}",
                         "随时；每回合一次；选宿主周围合法空格，原傀儡回场。",
                         max_uses_per_turn=1, target_mode="cell", timing="instant")
        self.puppet_id = puppet_id

    def host_status(self, actor: Unit) -> PuppetHostStatus | None:
        return next((status for status in actor.statuses
                     if isinstance(status, PuppetHostStatus) and status.puppet_id == self.puppet_id), None)

    def available_cells(self, battle: Battle, actor: Unit) -> list[Position]:
        status = self.host_status(actor)
        puppet = status.attached_puppet(battle) if status is not None else None
        if puppet is None or actor.position is None:
            return []
        body = set(battle.unit_cells(actor))
        return [cell for cell in square_around_cells(battle, list(body), radius=1)
                if cell not in body and battle.can_place_unit(puppet, cell, ignore=puppet, mover=puppet)]

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        puppet = battle.units.get(self.puppet_id)
        if puppet is not None and getattr(puppet, "_last_puppet_unequip_turn", None) == battle.turn_number:
            return False, "该傀儡本回合已卸下过。"
        cells = self.available_cells(battle, actor)
        if not cells:
            return False, "宿主周围没有傀儡可落的合法空格。"
        if payload and ("position" in payload or "cell" in payload or "x" in payload):
            try:
                if payload_position(payload) not in cells:
                    return False, "请选择宿主周围合法空格。"
            except (ActionError, KeyError, TypeError, ValueError):
                return False, "请选择宿主周围合法空格。"
        return True, ""

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        landing = payload_position(payload)
        if landing not in self.available_cells(battle, actor):
            raise ActionError("卸下落点已失效。")
        status = self.host_status(actor)
        assert status is not None
        puppet = status.attached_puppet(battle)
        assert puppet is not None
        status.detaching = True
        actor.remove_status(status, battle)
        puppet._last_puppet_unequip_turn = battle.turn_number
        puppet.equipped_to_id = None
        puppet.position = landing
        battle.log_public_event(f"{actor.name} 卸下 {puppet.name}，傀儡在周围回场。",
                                source=actor, target=puppet)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {"cells": [cell.to_dict() for cell in self.available_cells(battle, actor)],
                "target_unit_ids": [], "secondary_cells": [], "requires_target": True}

    def ai_priority_score(self, context: Any) -> float:
        score = self.ai_payload_value(context.battle, context.actor, context.payload)
        if score is None or score <= -1000:
            return -1000.0
        return score

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float | None:
        try:
            landing = payload_position(payload)
        except (ActionError, KeyError, TypeError, ValueError):
            return -1000.0
        puppet = battle.units.get(self.puppet_id)
        if puppet is None or landing not in self.available_cells(battle, actor):
            return -1000.0
        reach = sum(15 for enemy in battle.all_units() if enemy.player_id != actor.player_id
                    and enemy.alive and not enemy.banished and enemy.position is not None
                    if max(abs(enemy.position.x - landing.x), abs(enemy.position.y - landing.y))
                    <= puppet.targeting_range())
        ready = sum(1 for skill in puppet.action_skills()
                    if skill.is_active and skill.cooldown_remaining == 0)
        trait = PuppetEquipSkill.equipment_trait(puppet)
        return reach + min(ready, 2) * 10 - trait.ai_equipment_value(battle, actor) - 18
