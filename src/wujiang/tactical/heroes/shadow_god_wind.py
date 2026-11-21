"""R367: Wind King's next-attack support and shared-turn limits."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    Battle, BattleFieldEffect, DamageContext, Skill, StatusEffect,
    TargetContext, Trait, Unit,
)
from wujiang.tactical.heroes.common import positions_to_dict


def _present(unit: Unit | None) -> bool:
    return bool(unit is not None and unit.alive and not unit.banished and unit.position is not None)


class ShadowSpearCharge(StatusEffect):
    """One declared attack receives half piercing, including all of its segments."""

    def __init__(self) -> None:
        super().__init__("影神力。神枪", "下一次攻击半破魔。", duration=None)
        self.active_declaration_id: str | None = None

    def _empowered(self, battle: Battle) -> bool:
        queued = battle.resolving_action
        return bool(queued is not None and self.active_declaration_id is not None
                    and queued.payload.get("declaration_id") == self.active_declaration_id)

    def basic_attack_payload_metadata(self, battle: Battle, actor: Unit,
                                      payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"half_ignore_shield": True} if self.active_declaration_id is None else {}

    def on_owner_action_declared(self, battle: Battle, action_type: str,
                                 payload: dict[str, Any]) -> None:
        owner = self.owner
        if owner is None or action_type not in {"attack", "skill"}:
            return
        queued = battle.resolving_action
        if action_type == "skill" and queued is not None:
            units = [battle.units.get(unit_id) for unit_id in queued.target_unit_ids]
            if not any(_present(unit) and unit.player_id != owner.player_id for unit in units):
                return
        if self.active_declaration_id is not None:
            owner.remove_status(self, battle)
            return
        self.active_declaration_id = str(payload.get("declaration_id") or "")
        payload["half_ignore_shield"] = True

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        if ctx.actor is self.owner and self._empowered(battle):
            ctx.half_ignore_shield = True

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if ctx.source is self.owner and self._empowered(battle):
            ctx.half_ignore_shield = True

    def on_basic_attack_finished(self, battle: Battle, actor: Unit,
                                 payload: dict[str, Any], damage_contexts: list[DamageContext],
                                 missed: bool = False) -> None:
        if actor is self.owner and self.active_declaration_id is not None:
            actor.remove_status(self, battle)

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        if self.owner is not None and self.active_declaration_id is not None:
            self.owner.remove_status(self, battle)


class ShadowSpearSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False
    requires_direct_unit_target_line = False

    def __init__(self) -> None:
        super().__init__("shadow_spear", "影神力。神枪",
                         "每本人轮一次，使包括本人在内的一名己方单位下一次攻击半破魔。",
                         max_uses_per_turn=1, target_mode="unit")

    def eligible(self, battle: Battle, actor: Unit) -> list[Unit]:
        return [unit for unit in battle.player_units(actor.player_id)
                if _present(unit) and battle.distance_between_units(actor, unit) <= actor.targeting_range()]

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if payload and payload.get("target_unit_id"):
            if str(payload["target_unit_id"]) not in {unit.unit_id for unit in self.eligible(battle, actor)}:
                return False, "神枪只能指定范内己方单位。"
        return True, ""

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = battle.units[str(payload["target_unit_id"])]
        old = next((status for status in target.statuses if isinstance(status, ShadowSpearCharge)), None)
        if old is not None:
            target.remove_status(old, battle)
        target.add_status(ShadowSpearCharge(), source=actor)
        battle.log_public_event(f"{target.name} 的下一次攻击获得神枪半破魔。", source=actor, target=target)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = self.eligible(battle, actor)
        return {"cells": positions_to_dict([unit.position for unit in targets if unit.position]),
                "target_unit_ids": [unit.unit_id for unit in targets],
                "secondary_cells": [], "requires_target": True}

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "target_unit_id": target.unit_id} for target in self.eligible(battle, actor)]

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target not in self.eligible(battle, actor) or target.has_status("影神力。神枪"):
            return -100.0
        if target.attacks_used >= target.attack_actions_per_turn() or not battle.basic_attack_target_ids_for_payload(target):
            return -20.0
        shielded = any(unit.total_shields() > 0 for unit in battle.enemy_units(actor.player_id)
                       if unit.unit_id in battle.basic_attack_target_ids_for_payload(target))
        return 55.0 + (45.0 if shielded else 0.0)

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)


class ShadowTwoDamageTrait(Trait):
    def __init__(self) -> None:
        super().__init__("疾风王二伤上限", "每实际武将回合最多受到两次正数生命伤害。")
        self.turn_number = -1
        self.damage_count = 0
        self.last_context: DamageContext | None = None

    def _sync(self, battle: Battle) -> None:
        if self.turn_number != battle.turn_number:
            self.turn_number = battle.turn_number
            self.damage_count = 0
            self.last_context = None

    def on_before_hp_loss(self, battle: Battle, ctx: DamageContext) -> None:
        self._sync(battle)
        if ctx.target is self.owner and self.damage_count >= 2 and ctx.raw_damage > 0:
            ctx.raw_damage = 0.0

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        self._sync(battle)
        if ctx.target is self.owner and ctx.actual_damage > 0 and ctx is not self.last_context:
            self.damage_count += 1
            self.last_context = ctx


class ShadowSummonedAttackLimitStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("疾风王召唤禁攻", "复出后2本人轮，对方全队每实际回合合计只可攻击一次。",
                         duration=2, tick_scope="owner_turn_end")


class ShadowSummonedAttackLimitField(BattleFieldEffect):
    def __init__(self, source_unit_id: str) -> None:
        super().__init__("疾风王召唤禁攻", "敌方每实际回合全队共一次普攻。")
        self.source_unit_id = source_unit_id
        self.turn_number = -1
        self.attacks_used = 0

    def _active(self, battle: Battle) -> Unit | None:
        owner = battle.units.get(self.source_unit_id)
        return owner if _present(owner) and owner.has_status("疾风王召唤禁攻") else None

    def can_attack_target(self, battle: Battle, actor: Unit, target: Unit) -> tuple[bool, str]:
        owner = self._active(battle)
        if owner is None or actor.player_id == owner.player_id:
            return True, ""
        if self.turn_number != battle.turn_number:
            self.turn_number, self.attacks_used = battle.turn_number, 0
        queued = battle.resolving_action
        if (self.attacks_used >= 1 and not (queued is not None and battle.is_declared_resolution(
                actor, queued.payload, {"attack"}))):
            return False, "疾风王压制：本实际回合敌方全队已攻击一次。"
        return True, ""

    def on_owner_action_declared(self, battle: Battle, action_type: str,
                                 payload: dict[str, Any]) -> None:
        owner = self._active(battle)
        actor = battle.units.get(str(payload.get("unit_id") or ""))
        if owner is None or actor is None or actor.player_id == owner.player_id or action_type != "attack":
            return
        if self.turn_number != battle.turn_number:
            self.turn_number, self.attacks_used = battle.turn_number, 0
        self.attacks_used += 1


class ShadowSummonedAttackLimitTrait(Trait):
    def __init__(self) -> None:
        super().__init__("召唤后禁攻", "真正召回后2本人轮，敌方全队每实际回合只能攻击一次。")
        self.field: ShadowSummonedAttackLimitField | None = None

    def on_restored_by_apocalypse(self, battle: Battle) -> None:
        owner = self.owner
        if owner is not None:
            owner.add_status(ShadowSummonedAttackLimitStatus(), source=owner)
            self.field = ShadowSummonedAttackLimitField(owner.unit_id)
            battle.add_field_effect(self.field, source=owner)

    def on_owner_removed(self, battle: Battle) -> None:
        if self.field in battle.field_effects:
            battle.remove_field_effect(self.field)
        self.field = None
